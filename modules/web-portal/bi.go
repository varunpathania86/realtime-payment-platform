package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"os"
	"strings"
	"time"
)

// ADR-025 development identity is available only without OIDC configuration.
// Stage 11 uses Keycloak and server-side opaque sessions (oidc.go).

const (
	sessionCookie  = "rpp_portal_session"
	sessionTTL     = 8 * time.Hour
	permissionBI   = "bi:view"
	maxRequestBody = 4 << 10
)

type devUser struct {
	Password    string   `json:"password"`
	Permissions []string `json:"permissions"`
}

type supersetConfig struct {
	URL, PublicURL, DashboardID, User, Password string
}

type portal struct {
	oidc      *portalOIDC
	key       []byte
	users     map[string]devUser
	origins   map[string]bool
	superset  supersetConfig
	assistant *assistantProxy
	client    *http.Client
	now       func() time.Time
}

// newPortalFromEnv returns nil when the identity is not configured, so /auth, /bi and /assistant stay disabled.
func newPortalFromEnv(getenv func(string) string) (*portal, error) {
	if getenv("OIDC_ISSUER") != "" || getenv("OIDC_INTERNAL_URL") != "" || getenv("OIDC_CLIENT_SECRET") != "" {
		return newOIDCPortal(getenv)
	}
	key, users := getenv("PORTAL_SESSION_KEY"), getenv("PORTAL_DEV_USERS")
	if key == "" && users == "" {
		return nil, nil
	}
	if len(key) < 32 {
		return nil, errors.New("PORTAL_SESSION_KEY must have at least 32 characters")
	}
	p := &portal{key: []byte(key), origins: map[string]bool{}, now: time.Now,
		client: &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}}}
	if err := json.Unmarshal([]byte(users), &p.users); err != nil || len(p.users) == 0 {
		return nil, errors.New("PORTAL_DEV_USERS must be a non-empty JSON object")
	}
	for name, user := range p.users {
		if name == "" || len(user.Password) < 12 {
			return nil, fmt.Errorf("development user %q needs a password of at least 12 characters", name)
		}
	}
	for _, origin := range strings.Fields(getenv("PORTAL_ALLOWED_ORIGINS")) {
		p.origins[origin] = true
	}
	if len(p.origins) == 0 {
		return nil, errors.New("PORTAL_ALLOWED_ORIGINS must list the browser origins of the portal")
	}
	p.superset = supersetConfig{
		URL: strings.TrimRight(getenv("BI_SUPERSET_URL"), "/"), PublicURL: strings.TrimRight(getenv("BI_SUPERSET_PUBLIC_URL"), "/"),
		DashboardID: getenv("BI_DASHBOARD_ID"), User: getenv("BI_TOKEN_ISSUER_USER"), Password: getenv("BI_TOKEN_ISSUER_PASSWORD"),
	}
	if p.superset.URL != "" && (p.superset.PublicURL == "" || p.superset.DashboardID == "" ||
		p.superset.User == "" || p.superset.Password == "") {
		return nil, errors.New("BI_SUPERSET_URL needs BI_SUPERSET_PUBLIC_URL, BI_DASHBOARD_ID and token-issuer credentials")
	}
	assistant, err := newAssistantFromEnv(getenv)
	if err != nil {
		return nil, err
	}
	p.assistant = assistant
	return p, nil
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func (p *portal) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	routes := map[string]struct {
		method string
		serve  func(http.ResponseWriter, *http.Request)
	}{
		"/auth/login":   {http.MethodPost, p.login},
		"/auth/logout":  {http.MethodPost, p.logout},
		"/auth/session": {http.MethodGet, p.session},
		"/auth/config": {http.MethodGet, func(w http.ResponseWriter, _ *http.Request) {
			writeJSON(w, http.StatusOK, map[string]bool{"oidc": p.oidc != nil})
		}},
		"/bi/guest-token": {http.MethodPost, p.guestToken},
		"/assistant/ask":  {http.MethodPost, p.askAssistant},
	}
	route, ok := routes[r.URL.Path]
	if p.oidc != nil {
		switch r.URL.Path {
		case "/auth/login":
			route.method, route.serve, ok = http.MethodGet, p.oidcLogin, true
		case "/auth/callback":
			route.method, route.serve, ok = http.MethodGet, p.oidcCallback, true
		case "/auth/logout":
			route.serve = p.oidcLogout
		}
	}
	if !ok {
		http.NotFound(w, r)
		return
	}
	if r.Method != route.method {
		w.Header().Set("Allow", route.method)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	// SameSite=Strict already blocks cross-site cookies; the Origin check also covers same-site subdomains.
	if r.Method == http.MethodPost && !p.origins[r.Header.Get("Origin")] {
		http.Error(w, "origin not allowed", http.StatusForbidden)
		return
	}
	route.serve(w, r)
}

func (p *portal) sign(payload string) string {
	mac := hmac.New(sha256.New, p.key)
	mac.Write([]byte(payload))
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func (p *portal) passwordMatches(username, password string) bool {
	user, known := p.users[username]
	expected := user.Password
	if !known {
		expected = "unknown-user-placeholder"
	}
	// Comparing fixed-length digests keeps timing independent of password length and user existence.
	got, want := sha256.Sum256([]byte(password)), sha256.Sum256([]byte(expected))
	return subtle.ConstantTimeCompare(got[:], want[:]) == 1 && known
}

func (p *portal) currentUser(r *http.Request) (string, devUser, bool) {
	if p.oidc != nil {
		s, ok := p.oidcCurrent(r)
		if !ok {
			return "", devUser{}, false
		}
		return s.name, s.user, true
	}
	cookie, err := r.Cookie(sessionCookie)
	if err != nil {
		return "", devUser{}, false
	}
	payload, signature, found := strings.Cut(cookie.Value, ".")
	if !found || !hmac.Equal([]byte(signature), []byte(p.sign(payload))) {
		return "", devUser{}, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(payload)
	var claims struct {
		User    string `json:"u"`
		Expires int64  `json:"exp"`
	}
	if err != nil || json.Unmarshal(raw, &claims) != nil || p.now().Unix() >= claims.Expires {
		return "", devUser{}, false
	}
	// Permissions come from the current configuration, so removing a user revokes its sessions.
	user, ok := p.users[claims.User]
	return claims.User, user, ok
}

func has(permissions []string, wanted string) bool {
	for _, permission := range permissions {
		if permission == wanted {
			return true
		}
	}
	return false
}

func (p *portal) setSession(w http.ResponseWriter, value string, maxAge int) {
	secure := p.oidc != nil && p.oidc.secure
	http.SetCookie(w, &http.Cookie{Name: sessionCookie, Value: value, Path: "/", MaxAge: maxAge,
		HttpOnly: true, Secure: secure, SameSite: http.SameSiteStrictMode})
}

func (p *portal) login(w http.ResponseWriter, r *http.Request) {
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		http.Error(w, "expected application/json", http.StatusUnsupportedMediaType)
		return
	}
	var body struct{ Username, Password string }
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxRequestBody)).Decode(&body); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}
	if !p.passwordMatches(body.Username, body.Password) {
		slog.Warn("portal_login_rejected")
		http.Error(w, "invalid username or password", http.StatusUnauthorized)
		return
	}
	claims, _ := json.Marshal(map[string]any{"u": body.Username, "exp": p.now().Add(sessionTTL).Unix()})
	payload := base64.RawURLEncoding.EncodeToString(claims)
	p.setSession(w, payload+"."+p.sign(payload), int(sessionTTL.Seconds()))
	p.writeSession(w, body.Username, p.users[body.Username])
}

func (p *portal) logout(w http.ResponseWriter, _ *http.Request) {
	p.setSession(w, "", -1)
	w.WriteHeader(http.StatusNoContent)
}

func (p *portal) session(w http.ResponseWriter, r *http.Request) {
	name, user, ok := p.currentUser(r)
	if !ok {
		http.Error(w, "not signed in", http.StatusUnauthorized)
		return
	}
	p.writeSession(w, name, user)
}

func (p *portal) writeSession(w http.ResponseWriter, name string, user devUser) {
	response := map[string]any{"username": name, "permissions": append([]string{}, user.Permissions...)}
	if p.superset.URL != "" && has(user.Permissions, permissionBI) {
		response["bi"] = map[string]string{"dashboardId": p.superset.DashboardID, "supersetDomain": p.superset.PublicURL}
	}
	response["assistant"] = p.assistant != nil && has(user.Permissions, permissionAI)
	writeJSON(w, http.StatusOK, response)
}

func (p *portal) guestToken(w http.ResponseWriter, r *http.Request) {
	name, user, ok := p.currentUser(r)
	switch {
	case !ok:
		http.Error(w, "not signed in", http.StatusUnauthorized)
		return
	case p.superset.URL == "":
		http.NotFound(w, r)
		return
	case !has(user.Permissions, permissionBI):
		http.Error(w, "dashboard access not granted", http.StatusForbidden)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	token, err := p.issueGuestToken(ctx, name)
	if err != nil {
		slog.Error("guest_token_issue_failed", "error", err.Error())
		http.Error(w, "dashboard access unavailable", http.StatusBadGateway)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"token": token})
}

// issueGuestToken logs in as the token issuer and requests a token for exactly one dashboard and no RLS.
func (p *portal) issueGuestToken(ctx context.Context, username string) (string, error) {
	jar, _ := cookiejar.New(nil)
	client := *p.client
	client.Jar = jar
	call := func(method, path, bearer, csrf string, body any, out any) error {
		var reader io.Reader
		if body != nil {
			encoded, _ := json.Marshal(body)
			reader = bytes.NewReader(encoded)
		}
		request, err := http.NewRequestWithContext(ctx, method, p.superset.URL+path, reader)
		if err != nil {
			return err
		}
		request.Header.Set("Accept", "application/json")
		if body != nil {
			request.Header.Set("Content-Type", "application/json")
		}
		if bearer != "" {
			request.Header.Set("Authorization", "Bearer "+bearer)
		}
		if csrf != "" {
			request.Header.Set("X-CSRFToken", csrf)
			request.Header.Set("Referer", p.superset.URL+"/")
		}
		response, err := client.Do(request)
		if err != nil {
			return err
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			return fmt.Errorf("superset %s returned HTTP %d", path, response.StatusCode)
		}
		return json.NewDecoder(io.LimitReader(response.Body, 64<<10)).Decode(out)
	}
	var login struct {
		AccessToken string `json:"access_token"`
	}
	if err := call(http.MethodPost, "/api/v1/security/login", "", "", map[string]any{
		"username": p.superset.User, "password": p.superset.Password, "provider": "db", "refresh": false,
	}, &login); err != nil {
		return "", err
	}
	var csrf struct {
		Result string `json:"result"`
	}
	if err := call(http.MethodGet, "/api/v1/security/csrf_token/", login.AccessToken, "", nil, &csrf); err != nil {
		return "", err
	}
	var guest struct {
		Token string `json:"token"`
	}
	if err := call(http.MethodPost, "/api/v1/security/guest_token/", login.AccessToken, csrf.Result, map[string]any{
		"user":      map[string]string{"username": username, "first_name": username, "last_name": "(local dev identity)"},
		"resources": []map[string]string{{"type": "dashboard", "id": p.superset.DashboardID}},
		"rls":       []any{},
	}, &guest); err != nil {
		return "", err
	}
	if login.AccessToken == "" || csrf.Result == "" || guest.Token == "" {
		return "", errors.New("superset returned an empty token")
	}
	return guest.Token, nil
}

func portalFromEnvironment() (*portal, error) { return newPortalFromEnv(os.Getenv) }
