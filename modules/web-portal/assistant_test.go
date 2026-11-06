package main

import (
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/fstest"
	"time"
)

const engineerLogin = `{"username":"ops-engineer","password":"engineer-password"}`

func writeKey(t *testing.T, key any) string {
	t.Helper()
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "private-key.pem")
	if err := os.WriteFile(file, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), 0o600); err != nil {
		t.Fatal(err)
	}
	return file
}

func assistantEnv(t *testing.T, orchestrator string, key ed25519.PrivateKey) func(string) string {
	base, keyFile := testEnv(""), writeKey(t, key)
	return func(name string) string {
		switch name {
		case "PORTAL_DEV_USERS":
			return `{"ops-engineer":{"password":"engineer-password","permissions":["bi:view","ai:invoke","knowledge:read","analytics:read","payments:read"]},` +
				`"payments-clerk":{"password":"clerk-password","permissions":["payments:read"]}}`
		case "AI_ORCHESTRATOR_URL":
			return orchestrator
		case "AI_ASSERTION_PRIVATE_KEY_FILE":
			return keyFile
		}
		return base(name)
	}
}

// decodeAssertion verifies the EdDSA signature and returns the header and claims.
func decodeAssertion(t *testing.T, token string, public ed25519.PublicKey) (map[string]any, map[string]any) {
	t.Helper()
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("assertion has %d parts", len(parts))
	}
	signature, _ := base64.RawURLEncoding.DecodeString(parts[2])
	if !ed25519.Verify(public, []byte(parts[0]+"."+parts[1]), signature) {
		t.Fatal("assertion signature does not verify")
	}
	var header, claims map[string]any
	for i, out := range []*map[string]any{&header, &claims} {
		raw, _ := base64.RawURLEncoding.DecodeString(parts[i])
		if err := json.Unmarshal(raw, out); err != nil {
			t.Fatal(err)
		}
	}
	return header, claims
}

type orchestratorCall struct {
	requestID, question string
	orchestrator        map[string]any
	tools               map[string]any
}

func fakeOrchestrator(t *testing.T, public ed25519.PublicKey, calls chan<- orchestratorCall, reply func(http.ResponseWriter)) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/assistant/ask" || r.Method != http.MethodPost {
			t.Errorf("unexpected orchestrator call %s %s", r.Method, r.URL.Path)
		}
		header, orchestrator := decodeAssertion(t, strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "), public)
		if header["alg"] != "EdDSA" {
			t.Errorf("unexpected header %v", header)
		}
		_, tools := decodeAssertion(t, r.Header.Get(toolsAssertionHeader), public)
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		calls <- orchestratorCall{r.Header.Get("X-Request-ID"), body["question"], orchestrator, tools}
		reply(w)
	}))
}

func answered(w http.ResponseWriter) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "answered", "answer": "Restart the job.",
		"citations": []map[string]string{{"id": "S1", "document_id": "docs/runbooks/flink-recovery.md", "heading": "Recovery"}}})
}

func newAssistantPortal(t *testing.T, orchestrator string, key ed25519.PrivateKey) (*portal, *portalClient) {
	t.Helper()
	app, err := newPortalFromEnv(assistantEnv(t, orchestrator, key))
	if err != nil || app == nil || app.assistant == nil {
		t.Fatalf("configuration failed: %v", err)
	}
	return app, &portalClient{t: t, server: handler(fstest.MapFS{"index.html": {Data: []byte("portal")}}, app)}
}

func TestAssistantForwardsTwoScopedAssertions(t *testing.T) {
	public, key, _ := ed25519.GenerateKey(rand.Reader)
	calls := make(chan orchestratorCall, 2)
	orchestrator := fakeOrchestrator(t, public, calls, answered)
	defer orchestrator.Close()
	app, client := newAssistantPortal(t, orchestrator.URL+"/", key)
	now := time.Unix(1_800_000_000, 0)
	app.now = func() time.Time { return now }

	if w := client.do("POST", "/auth/login", testOrigin, engineerLogin); !strings.Contains(w.Body.String(), `"assistant":true`) {
		t.Fatalf("session must advertise the assistant: %s", w.Body.String())
	}
	w := client.do("POST", "/assistant/ask", testOrigin, `{"question":"  How do I recover the Flink job?  "}`)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "flink-recovery.md") {
		t.Fatalf("ask: %d %s", w.Code, w.Body.String())
	}
	call := <-calls
	if call.question != "How do I recover the Flink job?" || len(call.requestID) != 32 ||
		w.Header().Get("X-Request-ID") != call.requestID {
		t.Fatalf("unexpected forwarded request %+v", call)
	}
	for audience, claims := range map[string]map[string]any{orchestratorAudience: call.orchestrator, toolsAudience: call.tools} {
		if claims["aud"] != audience || claims["iss"] != "web-portal" || claims["sub"] != "ops-engineer" ||
			claims["tenant"] != "platform" || claims["iat"] != float64(now.Unix()) ||
			claims["exp"] != float64(now.Add(assertionLifetime).Unix()) || len(claims["jti"].(string)) != 32 {
			t.Fatalf("unexpected %s claims %v", audience, claims)
		}
		permissions, _ := json.Marshal(claims["permissions"])
		// bi:view is not an assistant permission, and knowledge:restricted is not granted to this user.
		if string(permissions) != `["ai:invoke","knowledge:read","analytics:read","payments:read"]` {
			t.Fatalf("unexpected forwarded permissions %s", permissions)
		}
	}
	if call.orchestrator["jti"] == call.tools["jti"] {
		t.Fatal("each assertion needs its own id")
	}
}

func TestAssistantRejectsUnauthorizedAndInvalidRequests(t *testing.T) {
	public, key, _ := ed25519.GenerateKey(rand.Reader)
	calls := make(chan orchestratorCall, 1)
	orchestrator := fakeOrchestrator(t, public, calls, answered)
	defer orchestrator.Close()
	_, anonymous := newAssistantPortal(t, orchestrator.URL, key)
	if w := anonymous.do("POST", "/assistant/ask", testOrigin, `{"question":"hi"}`); w.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous: %d", w.Code)
	}
	_, clerk := newAssistantPortal(t, orchestrator.URL, key)
	if w := clerk.do("POST", "/auth/login", testOrigin, `{"username":"payments-clerk","password":"clerk-password"}`); !strings.Contains(w.Body.String(), `"assistant":false`) {
		t.Fatalf("clerk session must not advertise the assistant: %s", w.Body.String())
	}
	if w := clerk.do("POST", "/assistant/ask", testOrigin, `{"question":"hi"}`); w.Code != http.StatusForbidden {
		t.Fatalf("clerk: %d", w.Code)
	}
	_, engineer := newAssistantPortal(t, orchestrator.URL, key)
	engineer.do("POST", "/auth/login", testOrigin, engineerLogin)
	for name, test := range map[string]struct {
		method, origin, body string
		status               int
	}{
		"cross origin":  {"POST", "https://evil.example", `{"question":"hi"}`, http.StatusForbidden},
		"wrong method":  {"GET", "", "", http.StatusMethodNotAllowed},
		"not json":      {"POST", testOrigin, "", http.StatusUnsupportedMediaType},
		"invalid json":  {"POST", testOrigin, `{"question":`, http.StatusBadRequest},
		"blank":         {"POST", testOrigin, `{"question":"   "}`, http.StatusBadRequest},
		"too long":      {"POST", testOrigin, `{"question":"` + strings.Repeat("é", maxQuestionCharacters+1) + `"}`, http.StatusBadRequest},
		"oversize body": {"POST", testOrigin, `{"question":"` + strings.Repeat("a", maxRequestBody) + `"}`, http.StatusBadRequest},
	} {
		t.Run(name, func(t *testing.T) {
			if w := engineer.do(test.method, "/assistant/ask", test.origin, test.body); w.Code != test.status {
				t.Fatalf("got %d, want %d", w.Code, test.status)
			}
		})
	}
	if len(calls) != 0 {
		t.Fatal("rejected requests must not reach the orchestrator")
	}
	// Exactly the maximum length is accepted.
	if w := engineer.do("POST", "/assistant/ask", testOrigin, `{"question":"`+strings.Repeat("é", maxQuestionCharacters)+`"}`); w.Code != http.StatusOK {
		t.Fatalf("maximum question: %d", w.Code)
	}
}

func TestAssistantDisabledUnavailableAndBusy(t *testing.T) {
	_, disabled := newTestPortal(t, "")
	disabled.do("POST", "/auth/login", testOrigin, `{"username":"ops-viewer","password":"viewer-password"}`)
	if w := disabled.do("GET", "/auth/session", "", ""); !strings.Contains(w.Body.String(), `"assistant":false`) {
		t.Fatalf("disabled assistant advertised: %s", w.Body.String())
	}
	if w := disabled.do("POST", "/assistant/ask", testOrigin, `{"question":"hi"}`); w.Code != http.StatusNotFound {
		t.Fatalf("disabled: %d", w.Code)
	}
	if w := handlerWithoutPortal().do("POST", "/assistant/ask", testOrigin, `{"question":"hi"}`); w.Code != http.StatusNotFound {
		t.Fatalf("unconfigured portal: %d", w.Code)
	}

	public, key, _ := ed25519.GenerateKey(rand.Reader)
	for name, test := range map[string]struct {
		reply  func(http.ResponseWriter)
		status int
	}{
		"upstream error":   {func(w http.ResponseWriter) { http.Error(w, "boom", http.StatusInternalServerError) }, http.StatusBadGateway},
		"invalid response": {func(w http.ResponseWriter) { _, _ = w.Write([]byte("<html>")) }, http.StatusBadGateway},
		"oversize response": {func(w http.ResponseWriter) {
			_, _ = w.Write([]byte(`"` + strings.Repeat("a", maxAssistantResponse) + `"`))
		}, http.StatusBadGateway},
		"timeout": {func(http.ResponseWriter) { time.Sleep(200 * time.Millisecond) }, http.StatusGatewayTimeout},
	} {
		t.Run(name, func(t *testing.T) {
			orchestrator := fakeOrchestrator(t, public, make(chan orchestratorCall, 1), test.reply)
			defer orchestrator.Close()
			app, client := newAssistantPortal(t, orchestrator.URL, key)
			app.assistant.client.Timeout = 50 * time.Millisecond
			client.do("POST", "/auth/login", testOrigin, engineerLogin)
			w := client.do("POST", "/assistant/ask", testOrigin, `{"question":"hi"}`)
			if w.Code != test.status || strings.Contains(w.Body.String(), "boom") {
				t.Fatalf("got %d %q, want %d", w.Code, w.Body.String(), test.status)
			}
		})
	}

	app, client := newAssistantPortal(t, "http://127.0.0.1:1", key)
	client.do("POST", "/auth/login", testOrigin, engineerLogin)
	for range maxConcurrentQuestions {
		app.assistant.slots <- struct{}{}
	}
	if w := client.do("POST", "/assistant/ask", testOrigin, `{"question":"hi"}`); w.Code != http.StatusTooManyRequests ||
		w.Header().Get("Retry-After") == "" {
		t.Fatalf("busy: %d", w.Code)
	}
}

func handlerWithoutPortal() *portalClient {
	return &portalClient{server: handler(fstest.MapFS{"index.html": {Data: []byte("portal")}}, nil)}
}

type deadlineRecorder struct {
	*httptest.ResponseRecorder
	deadline time.Time
}

func (d *deadlineRecorder) SetWriteDeadline(deadline time.Time) error {
	d.deadline = deadline
	return nil
}

// The server's 10 s WriteTimeout would cut slow CPU answers; the route must extend it through all wrappers.
func TestAssistantExtendsWriteDeadlineThroughMiddleware(t *testing.T) {
	public, key, _ := ed25519.GenerateKey(rand.Reader)
	orchestrator := fakeOrchestrator(t, public, make(chan orchestratorCall, 1), answered)
	defer orchestrator.Close()
	_, client := newAssistantPortal(t, orchestrator.URL, key)
	client.do("POST", "/auth/login", testOrigin, engineerLogin)
	request := httptest.NewRequest("POST", "/assistant/ask", strings.NewReader(`{"question":"hi"}`))
	request.Header.Set("Origin", testOrigin)
	request.Header.Set("Content-Type", "application/json")
	for _, cookie := range client.cookies {
		request.AddCookie(cookie)
	}
	recorder := &deadlineRecorder{ResponseRecorder: httptest.NewRecorder()}
	client.server.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK || time.Until(recorder.deadline) < assistantTimeout {
		t.Fatalf("write deadline was not extended: %d %v", recorder.Code, recorder.deadline)
	}
}

func TestAssistantConfiguration(t *testing.T) {
	_, key, _ := ed25519.GenerateKey(rand.Reader)
	ecKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	notPEM := filepath.Join(t.TempDir(), "key.txt")
	_ = os.WriteFile(notPEM, []byte("not a key"), 0o600)
	base := assistantEnv(t, "http://ai-orchestrator:8080", key)
	for name, override := range map[string]map[string]string{
		"url without key":  {"AI_ASSERTION_PRIVATE_KEY_FILE": ""},
		"key without url":  {"AI_ORCHESTRATOR_URL": ""},
		"bad url scheme":   {"AI_ORCHESTRATOR_URL": "file:///etc/passwd"},
		"missing key file": {"AI_ASSERTION_PRIVATE_KEY_FILE": filepath.Join(t.TempDir(), "absent.pem")},
		"not pem":          {"AI_ASSERTION_PRIVATE_KEY_FILE": notPEM},
		"not ed25519":      {"AI_ASSERTION_PRIVATE_KEY_FILE": writeKey(t, ecKey)},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := newPortalFromEnv(func(name string) string {
				if value, ok := override[name]; ok {
					return value
				}
				return base(name)
			}); err == nil {
				t.Fatal("expected a configuration error")
			}
		})
	}
}
