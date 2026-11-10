package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"golang.org/x/oauth2"
)

const transactionCookie = "rpp_oidc_transaction"

type loginTransaction struct {
	state, nonce, verifier string
	expires                time.Time
}

type oidcSession struct {
	token   *oauth2.Token
	idToken string
	subject string
	name    string
	user    devUser
	expires time.Time
}

type portalOIDC struct {
	mu                sync.Mutex
	oauth             oauth2.Config
	idVerifier        *oidc.IDTokenVerifier
	apiVerifier       *oidc.IDTokenVerifier
	resourceVerifiers map[string]*oidc.IDTokenVerifier
	client            *http.Client
	issuer            string
	internal          string
	secure            bool
	transactions      map[string]loginTransaction
	sessions          map[string]*oidcSession
}

func opaqueID() string {
	value := make([]byte, 32)
	if _, err := rand.Read(value); err != nil {
		panic("operating system entropy unavailable")
	}
	return base64.RawURLEncoding.EncodeToString(value)
}

func newOIDCPortal(getenv func(string) string) (*portal, error) {
	issuer, internal := strings.TrimRight(getenv("OIDC_ISSUER"), "/"), strings.TrimRight(getenv("OIDC_INTERNAL_URL"), "/")
	origin, secret := getenv("OIDC_PORTAL_ORIGIN"), getenv("OIDC_CLIENT_SECRET")
	local := getenv("OIDC_LOCAL_HTTP") == "true"
	for _, address := range []string{issuer, internal, origin} {
		u, err := url.Parse(address)
		if err != nil || u.Host == "" || u.RawQuery != "" || u.Fragment != "" || u.User != nil ||
			(u.Scheme != "https" && !(local && u.Scheme == "http")) {
			return nil, errors.New("OIDC requires valid HTTPS endpoints (HTTP only with OIDC_LOCAL_HTTP=true)")
		}
	}
	if len(secret) < 32 {
		return nil, errors.New("OIDC_CLIENT_SECRET must have at least 32 characters")
	}
	client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	ctx := oidc.ClientContext(context.Background(), client)
	keys := oidc.NewRemoteKeySet(ctx, internal+"/protocol/openid-connect/certs")
	o := &portalOIDC{
		oauth: oauth2.Config{ClientID: "web-portal", ClientSecret: secret, RedirectURL: origin + "/auth/callback",
			Scopes: []string{"openid", "profile", "email"},
			Endpoint: oauth2.Endpoint{AuthURL: issuer + "/protocol/openid-connect/auth",
				TokenURL: internal + "/protocol/openid-connect/token", AuthStyle: oauth2.AuthStyleInParams}},
		idVerifier:  oidc.NewVerifier(issuer, keys, &oidc.Config{ClientID: "web-portal", SupportedSigningAlgs: []string{"RS256"}}),
		apiVerifier: oidc.NewVerifier(issuer, keys, &oidc.Config{ClientID: "web-portal", SupportedSigningAlgs: []string{"RS256"}}),
		client:      client, issuer: issuer, internal: internal, secure: !local,
		transactions: map[string]loginTransaction{}, sessions: map[string]*oidcSession{},
		resourceVerifiers: map[string]*oidc.IDTokenVerifier{},
	}
	for _, audience := range []string{"payment-api", orchestratorAudience, toolsAudience} {
		o.resourceVerifiers[audience] = oidc.NewVerifier(issuer, keys, &oidc.Config{ClientID: audience, SupportedSigningAlgs: []string{"RS256"}})
	}
	p := &portal{oidc: o, origins: map[string]bool{origin: true}, now: time.Now, client: client}
	p.superset = supersetConfig{URL: strings.TrimRight(getenv("BI_SUPERSET_URL"), "/"),
		PublicURL: strings.TrimRight(getenv("BI_SUPERSET_PUBLIC_URL"), "/"), DashboardID: getenv("BI_DASHBOARD_ID"),
		User: getenv("BI_TOKEN_ISSUER_USER"), Password: getenv("BI_TOKEN_ISSUER_PASSWORD")}
	if p.superset.URL != "" && (p.superset.PublicURL == "" || p.superset.DashboardID == "" || p.superset.User == "" || p.superset.Password == "") {
		return nil, errors.New("incomplete Superset configuration")
	}
	if endpoint := strings.TrimRight(getenv("AI_ORCHESTRATOR_URL"), "/"); endpoint != "" {
		p.assistant = &assistantProxy{endpoint: endpoint + "/v1/assistant/ask", slots: make(chan struct{}, maxConcurrentQuestions),
			client: &http.Client{Timeout: assistantTimeout, CheckRedirect: client.CheckRedirect}}
	}
	return p, nil
}

func (p *portal) oidcLogin(w http.ResponseWriter, r *http.Request) {
	o := p.oidc
	origin := strings.TrimSuffix(o.oauth.RedirectURL, "/auth/callback")
	public, _ := url.Parse(origin)
	incoming, err := url.Parse("//" + r.Host)
	port := func(u *url.URL) string {
		if u.Port() != "" {
			return u.Port()
		}
		if public.Scheme == "https" {
			return "443"
		}
		return "80"
	}
	if err != nil || !strings.EqualFold(incoming.Hostname(), public.Hostname()) || port(incoming) != port(public) {
		// Create the transaction cookie only on the registered callback origin, never on an alias.
		http.Redirect(w, r, origin+"/auth/login", http.StatusFound)
		return
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	for id, t := range o.transactions {
		if !p.now().Before(t.expires) {
			delete(o.transactions, id)
		}
	}
	if len(o.transactions) >= 1024 {
		http.Error(w, "login capacity exceeded", http.StatusServiceUnavailable)
		return
	}
	id := opaqueID()
	t := loginTransaction{state: opaqueID(), nonce: opaqueID(), verifier: opaqueID(), expires: p.now().Add(5 * time.Minute)}
	o.transactions[id] = t
	http.SetCookie(w, &http.Cookie{Name: transactionCookie, Value: id, Path: "/auth", HttpOnly: true,
		Secure: o.secure, SameSite: http.SameSiteLaxMode, MaxAge: 300})
	challenge := sha256.Sum256([]byte(t.verifier))
	http.Redirect(w, r, o.oauth.AuthCodeURL(t.state, oauth2.SetAuthURLParam("nonce", t.nonce),
		oauth2.SetAuthURLParam("code_challenge_method", "S256"),
		oauth2.SetAuthURLParam("code_challenge", base64.RawURLEncoding.EncodeToString(challenge[:]))), http.StatusFound)
}

func (p *portal) oidcCallback(w http.ResponseWriter, r *http.Request) {
	o := p.oidc
	cookie, err := r.Cookie(transactionCookie)
	if err != nil {
		http.Error(w, "invalid login transaction", http.StatusUnauthorized)
		return
	}
	o.mu.Lock()
	t, exists := o.transactions[cookie.Value]
	delete(o.transactions, cookie.Value)
	o.mu.Unlock()
	http.SetCookie(w, &http.Cookie{Name: transactionCookie, Path: "/auth", MaxAge: -1, HttpOnly: true, Secure: o.secure, SameSite: http.SameSiteLaxMode})
	if !exists || !p.now().Before(t.expires) || r.URL.Query().Get("state") != t.state || r.URL.Query().Get("code") == "" {
		http.Error(w, "invalid login transaction", http.StatusUnauthorized)
		return
	}
	ctx := oidc.ClientContext(r.Context(), o.client)
	token, err := o.oauth.Exchange(ctx, r.URL.Query().Get("code"), oauth2.SetAuthURLParam("code_verifier", t.verifier))
	if err != nil {
		http.Error(w, "login exchange failed", http.StatusUnauthorized)
		return
	}
	rawID, _ := token.Extra("id_token").(string)
	id, err := o.idVerifier.Verify(ctx, rawID)
	if err != nil || id.Subject == "" || id.Nonce != t.nonce {
		http.Error(w, "invalid login identity", http.StatusUnauthorized)
		return
	}
	if id.AccessTokenHash != "" && id.VerifyAccessToken(token.AccessToken) != nil {
		http.Error(w, "invalid login token binding", http.StatusUnauthorized)
		return
	}
	s := &oidcSession{token: token, idToken: rawID, subject: id.Subject, expires: p.now().Add(sessionTTL)}
	if err := o.validateAccess(ctx, s); err != nil {
		http.Error(w, "invalid access token", http.StatusUnauthorized)
		return
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	for key, session := range o.sessions {
		if !p.now().Before(session.expires) {
			delete(o.sessions, key)
		}
	}
	if len(o.sessions) >= 1024 {
		http.Error(w, "session capacity exceeded", http.StatusServiceUnavailable)
		return
	}
	if old, err := r.Cookie(sessionCookie); err == nil {
		delete(o.sessions, old.Value)
	}
	key := opaqueID()
	o.sessions[key] = s
	p.setSession(w, key, int(sessionTTL.Seconds()))
	http.Redirect(w, r, "/analytics", http.StatusFound)
}

func (o *portalOIDC) validateAccess(ctx context.Context, s *oidcSession) error {
	token, err := o.apiVerifier.Verify(ctx, s.token.AccessToken)
	if err != nil || token.Subject != s.subject {
		return errors.New("invalid access identity")
	}
	var claims struct {
		Name      string `json:"preferred_username"`
		Scope     string `json:"scope"`
		Issued    int64  `json:"iat"`
		Resources map[string]struct {
			Roles []string `json:"roles"`
		} `json:"resource_access"`
	}
	if err := token.Claims(&claims); err != nil || claims.Name == "" || claims.Issued <= 0 ||
		token.Expiry.Unix()-claims.Issued <= 0 || token.Expiry.Unix()-claims.Issued > 300 ||
		claims.Issued > time.Now().Unix()+5 || !has(strings.Fields(claims.Scope), "openid") {
		return errors.New("invalid access claims")
	}
	if s.token.Expiry.IsZero() || token.Expiry.Before(s.token.Expiry) {
		s.token.Expiry = token.Expiry
	}
	s.name = claims.Name
	s.user = devUser{}
	for _, resource := range []string{"web-portal", orchestratorAudience, toolsAudience, "payment-api"} {
		s.user.Permissions = append(s.user.Permissions, claims.Resources[resource].Roles...)
	}
	return nil
}

// Refresh is serialized with session lookup: rotating refresh tokens are never reused concurrently.
func (p *portal) oidcCurrent(r *http.Request) (*oidcSession, bool) {
	return p.oidcCurrentBefore(r, 30*time.Second)
}

func (p *portal) oidcCurrentBefore(r *http.Request, minimumLifetime time.Duration) (*oidcSession, bool) {
	o := p.oidc
	cookie, err := r.Cookie(sessionCookie)
	if err != nil {
		return nil, false
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	s, ok := o.sessions[cookie.Value]
	if !ok {
		return nil, false
	}
	if !p.now().Before(s.expires) {
		delete(o.sessions, cookie.Value)
		return nil, false
	}
	ctx := oidc.ClientContext(r.Context(), o.client)
	if !p.now().Add(minimumLifetime).Before(s.token.Expiry) {
		expired := *s.token
		expired.Expiry = time.Now().Add(-time.Minute)
		next, err := o.oauth.TokenSource(ctx, &expired).Token()
		if err != nil {
			delete(o.sessions, cookie.Value)
			return nil, false
		}
		s.token = next
	}
	if err := o.validateAccess(ctx, s); err != nil {
		delete(o.sessions, cookie.Value)
		return nil, false
	}
	copy := *s
	return &copy, true
}

func (p *portal) oidcLogout(w http.ResponseWriter, r *http.Request) {
	o := p.oidc
	var refresh string
	o.mu.Lock()
	if cookie, err := r.Cookie(sessionCookie); err == nil {
		if s := o.sessions[cookie.Value]; s != nil {
			refresh = s.token.RefreshToken
		}

		delete(o.sessions, cookie.Value)
	}
	o.mu.Unlock()
	p.setSession(w, "", -1)
	if refresh != "" {
		values := url.Values{"client_id": {o.oauth.ClientID}, "client_secret": {o.oauth.ClientSecret}, "refresh_token": {refresh}}
		req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, o.internal+"/protocol/openid-connect/logout", strings.NewReader(values.Encode()))
		if err == nil {
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
			res, err := o.client.Do(req)
			if err != nil {
				http.Error(w, "local session removed; identity logout unavailable", http.StatusBadGateway)
				return
			}
			res.Body.Close()
			if res.StatusCode != http.StatusNoContent && res.StatusCode != http.StatusOK {
				http.Error(w, "local session removed; identity logout rejected", http.StatusBadGateway)
				return
			}
		}
	}
	w.WriteHeader(http.StatusNoContent)
}

func (o *portalOIDC) resourceToken(ctx context.Context, session *oidcSession, audience string) (string, error) {
	verifier, ok := o.resourceVerifiers[audience]
	if !ok {
		return "", errors.New("unsupported resource")
	}
	form := url.Values{
		"client_id": {o.oauth.ClientID}, "client_secret": {o.oauth.ClientSecret},
		"grant_type":           {"urn:ietf:params:oauth:grant-type:token-exchange"},
		"subject_token":        {session.token.AccessToken},
		"subject_token_type":   {"urn:ietf:params:oauth:token-type:access_token"},
		"requested_token_type": {"urn:ietf:params:oauth:token-type:access_token"},
		"audience":             {audience},
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, o.oauth.Endpoint.TokenURL, strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	res, err := o.client.Do(req)
	if err != nil {
		return "", errors.New("resource token exchange unavailable")
	}
	defer res.Body.Close()
	var body struct {
		AccessToken string `json:"access_token"`
	}
	if res.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(res.Body, 64<<10)).Decode(&body) != nil {
		return "", errors.New("resource token exchange rejected")
	}
	token, err := verifier.Verify(ctx, body.AccessToken)
	if err != nil || token.Subject != session.subject {
		return "", errors.New("invalid resource token")
	}
	// Exchange must remove other API audiences, not mint a universally replayable token.
	for _, other := range []string{"payment-api", orchestratorAudience, toolsAudience} {
		if other != audience && has(token.Audience, other) {
			return "", errors.New("resource token includes another API audience")
		}
	}
	return body.AccessToken, nil
}
