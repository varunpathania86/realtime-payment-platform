package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"testing/fstest"
	"time"
)

const (
	testOrigin    = "http://localhost:8080"
	testDashboard = "bfa817e5-b670-5c83-842c-ef5d57da6f8a"
)

// fakeSuperset mirrors the issuer flow: login, CSRF token bound to a session cookie, then guest_token.
func fakeSuperset(t *testing.T, requests *[]map[string]any, failures *atomic.Int32) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if failures.Load() > 0 {
			http.Error(w, "down", http.StatusServiceUnavailable)
			return
		}
		switch r.URL.Path {
		case "/api/v1/security/login":
			var body map[string]any
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body["username"] != "issuer" || body["password"] != "issuer-password" || body["provider"] != "db" {
				http.Error(w, "bad login", http.StatusUnauthorized)
				return
			}
			writeJSON(w, http.StatusOK, map[string]string{"access_token": "issuer-jwt"})
		case "/api/v1/security/csrf_token/":
			if r.Header.Get("Authorization") != "Bearer issuer-jwt" {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "csrf-session", Path: "/"})
			writeJSON(w, http.StatusOK, map[string]string{"result": "csrf-value"})
		case "/api/v1/security/guest_token/":
			cookie, err := r.Cookie("session")
			if r.Header.Get("Authorization") != "Bearer issuer-jwt" || r.Header.Get("X-CSRFToken") != "csrf-value" ||
				err != nil || cookie.Value != "csrf-session" {
				http.Error(w, "csrf failed", http.StatusBadRequest)
				return
			}
			var body map[string]any
			_ = json.NewDecoder(r.Body).Decode(&body)
			*requests = append(*requests, body)
			writeJSON(w, http.StatusOK, map[string]string{"token": "guest-jwt"})
		default:
			t.Errorf("unexpected Superset call %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}))
}

func testEnv(superset string) func(string) string {
	values := map[string]string{
		"PORTAL_SESSION_KEY":       strings.Repeat("k", 32),
		"PORTAL_DEV_USERS":         `{"ops-viewer":{"password":"viewer-password","permissions":["bi:view"]},"payments-clerk":{"password":"clerk-password","permissions":[]}}`,
		"PORTAL_ALLOWED_ORIGINS":   testOrigin + " http://127.0.0.1:8080",
		"BI_SUPERSET_URL":          superset,
		"BI_SUPERSET_PUBLIC_URL":   "http://bi.localhost:8080",
		"BI_DASHBOARD_ID":          testDashboard,
		"BI_TOKEN_ISSUER_USER":     "issuer",
		"BI_TOKEN_ISSUER_PASSWORD": "issuer-password",
	}
	return func(name string) string { return values[name] }
}

type portalClient struct {
	t       *testing.T
	server  http.Handler
	cookies []*http.Cookie
}

func (c *portalClient) do(method, path, origin, body string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, strings.NewReader(body))
	if origin != "" {
		request.Header.Set("Origin", origin)
	}
	if body != "" {
		request.Header.Set("Content-Type", "application/json")
	}
	for _, cookie := range c.cookies {
		request.AddCookie(cookie)
	}
	w := httptest.NewRecorder()
	c.server.ServeHTTP(w, request)
	if cookies := w.Result().Cookies(); len(cookies) > 0 {
		c.cookies = cookies
	}
	return w
}

func newTestPortal(t *testing.T, superset string) (*portal, *portalClient) {
	t.Helper()
	app, err := newPortalFromEnv(testEnv(superset))
	if err != nil || app == nil {
		t.Fatalf("configuration failed: %v", err)
	}
	return app, &portalClient{t: t, server: handler(fstest.MapFS{"index.html": {Data: []byte("portal")}}, app)}
}

func TestViewerReceivesOneDashboardGuestToken(t *testing.T) {
	var requests []map[string]any
	var failures atomic.Int32
	superset := fakeSuperset(t, &requests, &failures)
	defer superset.Close()
	_, client := newTestPortal(t, superset.URL)

	login := client.do("POST", "/auth/login", testOrigin, `{"username":"ops-viewer","password":"viewer-password"}`)
	if login.Code != http.StatusOK || !strings.Contains(login.Body.String(), `"supersetDomain":"http://bi.localhost:8080"`) {
		t.Fatalf("login: %d %s", login.Code, login.Body.String())
	}
	cookie := login.Result().Cookies()[0]
	if !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode || cookie.Path != "/" || cookie.MaxAge <= 0 {
		t.Fatalf("session cookie is not protected: %+v", cookie)
	}
	if strings.Contains(login.Body.String(), "issuer") || strings.Contains(login.Body.String(), "password") {
		t.Fatal("login response leaked credentials")
	}
	if session := client.do("GET", "/auth/session", "", ""); session.Code != http.StatusOK ||
		!strings.Contains(session.Body.String(), `"username":"ops-viewer"`) {
		t.Fatalf("session: %d %s", session.Code, session.Body.String())
	}
	token := client.do("POST", "/bi/guest-token", testOrigin, "")
	if token.Code != http.StatusOK || strings.TrimSpace(token.Body.String()) != `{"token":"guest-jwt"}` {
		t.Fatalf("guest token: %d %s", token.Code, token.Body.String())
	}
	if token.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("guest tokens must not be cached")
	}
	if len(requests) != 1 {
		t.Fatalf("expected one guest-token request, got %d", len(requests))
	}
	encoded, _ := json.Marshal(requests[0])
	want := `{"resources":[{"id":"` + testDashboard + `","type":"dashboard"}],"rls":[],"user":{"first_name":"ops-viewer","last_name":"(local dev identity)","username":"ops-viewer"}}`
	if string(encoded) != want {
		t.Fatalf("guest-token request %s", encoded)
	}

	if logout := client.do("POST", "/auth/logout", testOrigin, ""); logout.Code != http.StatusNoContent ||
		logout.Result().Cookies()[0].MaxAge >= 0 {
		t.Fatalf("logout must clear the cookie: %d", logout.Code)
	}
}

func TestGuestTokenRequiresSessionPermissionAndOrigin(t *testing.T) {
	var requests []map[string]any
	var failures atomic.Int32
	superset := fakeSuperset(t, &requests, &failures)
	defer superset.Close()
	app, anonymous := newTestPortal(t, superset.URL)

	if w := anonymous.do("POST", "/bi/guest-token", testOrigin, ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous: %d", w.Code)
	}
	if w := anonymous.do("GET", "/auth/session", "", ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous session: %d", w.Code)
	}
	clerk := &portalClient{t: t, server: anonymous.server}
	login := clerk.do("POST", "/auth/login", testOrigin, `{"username":"payments-clerk","password":"clerk-password"}`)
	if login.Code != http.StatusOK || strings.Contains(login.Body.String(), "supersetDomain") {
		t.Fatalf("clerk login: %d %s", login.Code, login.Body.String())
	}
	if w := clerk.do("POST", "/bi/guest-token", testOrigin, ""); w.Code != http.StatusForbidden {
		t.Fatalf("clerk without bi:view: %d", w.Code)
	}

	viewer := &portalClient{t: t, server: anonymous.server}
	viewer.do("POST", "/auth/login", testOrigin, `{"username":"ops-viewer","password":"viewer-password"}`)
	for _, origin := range []string{"", "http://evil.localhost:8080", "null", "http://localhost:8080.evil"} {
		if w := viewer.do("POST", "/bi/guest-token", origin, ""); w.Code != http.StatusForbidden {
			t.Fatalf("origin %q: %d", origin, w.Code)
		}
	}
	if w := viewer.do("GET", "/bi/guest-token", testOrigin, ""); w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("GET guest token: %d", w.Code)
	}

	tampered := viewer.cookies[0].Value
	payload, signature, _ := strings.Cut(tampered, ".")
	suffix := "AA"
	if strings.HasSuffix(signature, suffix) {
		suffix = "BB"
	}
	forged := &portalClient{t: t, server: anonymous.server,
		cookies: []*http.Cookie{{Name: sessionCookie, Value: payload + "." + signature[:len(signature)-2] + suffix}}}
	if w := forged.do("POST", "/bi/guest-token", testOrigin, ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("forged cookie: %d", w.Code)
	}
	app.now = func() time.Time { return time.Now().Add(sessionTTL + time.Minute) }
	if w := viewer.do("POST", "/bi/guest-token", testOrigin, ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("expired cookie: %d", w.Code)
	}
	app.now = time.Now
	delete(app.users, "ops-viewer")
	if w := viewer.do("POST", "/bi/guest-token", testOrigin, ""); w.Code != http.StatusUnauthorized {
		t.Fatalf("removed user: %d", w.Code)
	}
	if len(requests) != 0 {
		t.Fatalf("rejected callers reached Superset %d times", len(requests))
	}
}

func TestLoginRejectsBadCredentialsAndRequests(t *testing.T) {
	_, client := newTestPortal(t, "")
	for _, tc := range []struct {
		name, origin, body string
		status             int
	}{
		{"wrong password", testOrigin, `{"username":"ops-viewer","password":"viewer-passwor"}`, http.StatusUnauthorized},
		{"unknown user", testOrigin, `{"username":"root","password":"viewer-password"}`, http.StatusUnauthorized},
		{"empty", testOrigin, `{}`, http.StatusUnauthorized},
		{"malformed", testOrigin, `{"username":`, http.StatusBadRequest},
		{"oversized", testOrigin, `{"username":"` + strings.Repeat("a", maxRequestBody) + `"}`, http.StatusBadRequest},
		{"cross origin", "http://attacker.example", `{"username":"ops-viewer","password":"viewer-password"}`, http.StatusForbidden},
	} {
		t.Run(tc.name, func(t *testing.T) {
			client.cookies = nil
			w := client.do("POST", "/auth/login", tc.origin, tc.body)
			if w.Code != tc.status || len(w.Result().Cookies()) != 0 {
				t.Fatalf("got %d with cookies %v", w.Code, w.Result().Cookies())
			}
		})
	}
	request := httptest.NewRequest("POST", "/auth/login", strings.NewReader(`{"username":"ops-viewer","password":"viewer-password"}`))
	request.Header.Set("Origin", testOrigin)
	request.Header.Set("Content-Type", "text/plain")
	w := httptest.NewRecorder()
	client.server.ServeHTTP(w, request)
	if w.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("form-style login: %d", w.Code)
	}
}

func TestBIDisabledOrUnavailable(t *testing.T) {
	_, client := newTestPortal(t, "")
	client.do("POST", "/auth/login", testOrigin, `{"username":"ops-viewer","password":"viewer-password"}`)
	if w := client.do("GET", "/auth/session", "", ""); strings.Contains(w.Body.String(), "supersetDomain") {
		t.Fatal("session advertised BI while Superset is not configured")
	}
	if w := client.do("POST", "/bi/guest-token", testOrigin, ""); w.Code != http.StatusNotFound {
		t.Fatalf("disabled BI: %d", w.Code)
	}

	var requests []map[string]any
	var failures atomic.Int32
	failures.Store(1)
	superset := fakeSuperset(t, &requests, &failures)
	defer superset.Close()
	_, down := newTestPortal(t, superset.URL)
	down.do("POST", "/auth/login", testOrigin, `{"username":"ops-viewer","password":"viewer-password"}`)
	w := down.do("POST", "/bi/guest-token", testOrigin, "")
	if w.Code != http.StatusBadGateway || strings.Contains(w.Body.String(), "503") {
		t.Fatalf("unavailable Superset: %d %s", w.Code, w.Body.String())
	}
}

func TestPortalConfiguration(t *testing.T) {
	if app, err := newPortalFromEnv(func(string) string { return "" }); app != nil || err != nil {
		t.Fatal("an unconfigured identity must disable the portal routes without failing")
	}
	base := testEnv("http://bi-dashboards:8088")
	for name, override := range map[string]map[string]string{
		"short key":        {"PORTAL_SESSION_KEY": "short"},
		"no users":         {"PORTAL_DEV_USERS": "{}"},
		"invalid users":    {"PORTAL_DEV_USERS": "[]"},
		"weak password":    {"PORTAL_DEV_USERS": `{"ops-viewer":{"password":"short","permissions":["bi:view"]}}`},
		"no origins":       {"PORTAL_ALLOWED_ORIGINS": " "},
		"no dashboard":     {"BI_DASHBOARD_ID": ""},
		"no issuer secret": {"BI_TOKEN_ISSUER_PASSWORD": ""},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := newPortalFromEnv(func(key string) string {
				if value, ok := override[key]; ok {
					return value
				}
				return base(key)
			}); err == nil {
				t.Fatal("expected a configuration error")
			}
		})
	}
}
