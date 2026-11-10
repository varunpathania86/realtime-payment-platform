package main

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func signedOIDCTestToken(t *testing.T, key *rsa.PrivateKey, kid string, claims map[string]any) string {
	t.Helper()
	header, _ := json.Marshal(map[string]string{"alg": "RS256", "kid": kid})
	payload, _ := json.Marshal(claims)
	value := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(payload)
	hash := sha256.Sum256([]byte(value))
	signature, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, hash[:])
	if err != nil {
		t.Fatal(err)
	}
	return value + "." + base64.RawURLEncoding.EncodeToString(signature)
}

func TestOIDCLoginPKCEOpaqueSessionRefreshLogout(t *testing.T) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	var nonce, verifier string
	refreshes, logouts := 0, 0
	exchanges := 0
	extraAudience := false
	failRefresh := false
	badBinding := false
	missingSubject := false
	var issuer string
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/realms/rpp/protocol/openid-connect/certs":
			_ = json.NewEncoder(w).Encode(map[string]any{"keys": []any{map[string]string{
				"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "initial",
				"n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": "AQAB"}}})
		case "/realms/rpp/protocol/openid-connect/token":
			_ = r.ParseForm()
			switch r.Form.Get("grant_type") {
			case "authorization_code":
				if r.Form.Get("code_verifier") != verifier || r.Form.Get("client_secret") != strings.Repeat("s", 48) {
					t.Error("PKCE verifier or confidential client authentication missing")
				}
			case "refresh_token":
				if failRefresh {
					http.Error(w, "refresh rejected", http.StatusUnauthorized)
					return
				}
				refreshes++
			case "urn:ietf:params:oauth:grant-type:token-exchange":
				exchanges++
				if r.Form.Get("subject_token") == "" {
					t.Error("resource exchange missing human subject token")
				}
			}
			claims := map[string]any{"iss": issuer, "sub": "user-id", "aud": "web-portal",
				"iat": time.Now().Unix(), "exp": time.Now().Add(300 * time.Second).Unix(),
				"nonce": nonce, "preferred_username": "ops-viewer", "scope": "openid profile",
				"resource_access": map[string]any{"web-portal": map[string]any{"roles": []string{"bi:view"}}}}
			if audience := r.Form.Get("audience"); audience != "" {
				claims["aud"] = audience
				if extraAudience {
					claims["aud"] = []string{audience, toolsAudience}
				}
			}
			if missingSubject {
				claims["sub"] = ""
			}
			access := signedOIDCTestToken(t, key, "initial", claims)
			binding := sha256.Sum256([]byte(access))
			claims["at_hash"] = base64.RawURLEncoding.EncodeToString(binding[:16])
			if badBinding {
				claims["at_hash"] = "invalid-binding"
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"access_token": access,
				"id_token": signedOIDCTestToken(t, key, "initial", claims), "refresh_token": "server-only-refresh",
				"token_type": "Bearer", "expires_in": 600})
		case "/realms/rpp/protocol/openid-connect/logout":
			logouts++
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
	defer provider.Close()
	issuer = provider.URL + "/realms/rpp"
	env := map[string]string{"OIDC_ISSUER": issuer, "OIDC_INTERNAL_URL": issuer,
		"OIDC_PORTAL_ORIGIN": "http://localhost:8080", "OIDC_CLIENT_SECRET": strings.Repeat("s", 48), "OIDC_LOCAL_HTTP": "true"}
	p, err := newPortalFromEnv(func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	start := httptest.NewRecorder()
	login := httptest.NewRequest("GET", "http://localhost:8080/auth/login", nil)
	p.ServeHTTP(start, login)
	alias := httptest.NewRecorder()
	p.ServeHTTP(alias, httptest.NewRequest("GET", "http://127.0.0.1:8080/auth/login", nil))
	if alias.Code != 302 || alias.Header().Get("Location") != "http://localhost:8080/auth/login" || len(alias.Result().Cookies()) != 0 {
		t.Fatal("alias login did not canonicalize before creating its transaction cookie")
	}
	redirect, _ := url.Parse(start.Header().Get("Location"))
	if redirect.Query().Get("code_challenge_method") != "S256" || redirect.Query().Get("state") == "" || redirect.Query().Get("nonce") == "" {
		t.Fatal("missing login protections")
	}
	tx := start.Result().Cookies()[0]
	if !tx.HttpOnly || tx.SameSite != http.SameSiteLaxMode {
		t.Fatal("unsafe transaction cookie")
	}
	transaction := p.oidc.transactions[tx.Value]
	nonce, verifier = transaction.nonce, transaction.verifier
	hash := sha256.Sum256([]byte(verifier))
	if redirect.Query().Get("code_challenge") != base64.RawURLEncoding.EncodeToString(hash[:]) {
		t.Fatal("incorrect S256 challenge")
	}
	callback := httptest.NewRequest("GET", "/auth/callback?code=one-use-code&state="+transaction.state, nil)
	callback.AddCookie(tx)
	result := httptest.NewRecorder()
	p.ServeHTTP(result, callback)
	if result.Code != http.StatusFound {
		t.Fatalf("callback status %d: %s", result.Code, result.Body.String())
	}
	var session *http.Cookie
	for _, cookie := range result.Result().Cookies() {
		if cookie.Name == sessionCookie {
			session = cookie
		}
	}
	if session == nil || !session.HttpOnly || strings.Contains(session.Value, ".") || session.SameSite != http.SameSiteStrictMode {
		t.Fatal("browser must receive only an opaque HttpOnly Strict session")
	}
	if expiry := p.oidc.sessions[session.Value].token.Expiry; expiry.Nanosecond() != 0 || time.Until(expiry) > 300*time.Second {
		t.Fatal("session trusted unsigned expires_in rather than the verified access token expiry")
	}
	for _, invalid := range []string{"binding", "subject"} {
		badBinding, missingSubject = invalid == "binding", invalid == "subject"
		next := httptest.NewRecorder()
		p.ServeHTTP(next, login)
		nextCookie := next.Result().Cookies()[0]
		nextTransaction := p.oidc.transactions[nextCookie.Value]
		nonce, verifier = nextTransaction.nonce, nextTransaction.verifier
		nextCallback := httptest.NewRequest("GET", "/auth/callback?code=new-code&state="+nextTransaction.state, nil)
		nextCallback.AddCookie(nextCookie)
		rejected := httptest.NewRecorder()
		p.ServeHTTP(rejected, nextCallback)
		if rejected.Code != http.StatusUnauthorized || len(p.oidc.sessions) != 1 {
			t.Fatalf("invalid login %s created a session", invalid)
		}
	}
	badBinding, missingSubject = false, false
	if _, err := p.oidc.resourceToken(context.Background(), p.oidc.sessions[session.Value], "payment-api"); err != nil || exchanges != 1 {
		t.Fatalf("audience-specific exchange failed: %v", err)
	}
	extraAudience = true
	if _, err := p.oidc.resourceToken(context.Background(), p.oidc.sessions[session.Value], "payment-api"); err == nil {
		t.Fatal("token exchange accepted a replayable multi-API token")
	}
	extraAudience = false
	write := httptest.NewRequest("POST", "/api/payments", nil)
	write.AddCookie(session)
	write.Header.Set("Origin", "http://localhost:8080")
	denied := httptest.NewRecorder()
	p.payments(denied, write)
	if denied.Code != http.StatusForbidden {
		t.Fatal("viewer payment writes were not denied by the BFF")
	}
	replayed := httptest.NewRecorder()
	p.ServeHTTP(replayed, callback)
	if replayed.Code != http.StatusUnauthorized {
		t.Fatal("callback transaction replay accepted")
	}
	request := httptest.NewRequest("GET", "/auth/session", nil)
	request.AddCookie(session)
	p.oidc.sessions[session.Value].token.Expiry = time.Now().Add(200 * time.Second)
	if _, ok := p.oidcCurrentBefore(request, assistantTimeout+5*time.Second); !ok || refreshes != 1 {
		t.Fatal("slow assistant request did not proactively refresh its resource-token lifetime")
	}
	p.oidc.sessions[session.Value].token.Expiry = time.Now().Add(-time.Minute)
	response := httptest.NewRecorder()
	p.ServeHTTP(response, request)
	if response.Code != http.StatusOK || refreshes != 2 || strings.Contains(response.Body.String(), "token") {
		t.Fatalf("refresh failed or tokens leaked: %d", response.Code)
	}
	copy := *p.oidc.sessions[session.Value]
	tokenCopy := *copy.token
	tokenCopy.Expiry = time.Now().Add(-time.Minute)
	copy.token = &tokenCopy
	p.oidc.sessions["failed-refresh-session"] = &copy
	failRefresh = true
	bad := httptest.NewRequest("GET", "/auth/session", nil)
	bad.AddCookie(&http.Cookie{Name: sessionCookie, Value: "failed-refresh-session"})
	rejected := httptest.NewRecorder()
	p.ServeHTTP(rejected, bad)
	if rejected.Code != http.StatusUnauthorized || p.oidc.sessions["failed-refresh-session"] != nil {
		t.Fatal("failed refresh did not revoke the session")
	}
	failRefresh = false
	logout := httptest.NewRequest("POST", "/auth/logout", nil)
	logout.AddCookie(session)
	logout.Header.Set("Origin", "http://localhost:8080")
	response = httptest.NewRecorder()
	p.ServeHTTP(response, logout)
	if response.Code != http.StatusNoContent || logouts != 1 || len(p.oidc.sessions) != 0 {
		t.Fatal("logout did not revoke local and provider sessions")
	}
	for _, failure := range []string{"state", "nonce", "expired"} {
		t.Run(failure, func(t *testing.T) {
			start := httptest.NewRecorder()
			p.ServeHTTP(start, httptest.NewRequest("GET", "http://localhost:8080/auth/login", nil))
			cookie := start.Result().Cookies()[0]
			transaction := p.oidc.transactions[cookie.Value]
			nonce, verifier = transaction.nonce, transaction.verifier
			state := transaction.state
			switch failure {
			case "state":
				state = "unbound-state"
			case "nonce":
				nonce = "wrong-nonce"
			case "expired":
				transaction.expires = p.now().Add(-time.Minute)
				p.oidc.transactions[cookie.Value] = transaction
			}
			request := httptest.NewRequest("GET", "/auth/callback?code=code&state="+state, nil)
			request.AddCookie(cookie)
			response := httptest.NewRecorder()
			p.ServeHTTP(response, request)
			if response.Code != 401 {
				t.Fatalf("%s login protection failed: %d", failure, response.Code)
			}
		})
	}
}

func TestOIDCConfiguredInvalidFailsClosed(t *testing.T) {
	_, err := newPortalFromEnv(func(k string) string {
		if k == "OIDC_ISSUER" {
			return "http://issuer.invalid/realms/rpp"
		}
		if k == "PORTAL_DEV_USERS" {
			return `{"viewer":{"password":"development-password"}}`
		}
		return ""
	})
	if err == nil {
		t.Fatal("invalid configured OIDC fell back to local users")
	}
}
