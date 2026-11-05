package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

// The portal is the only issuer of assistant identity assertions (ADR-030). It signs two short-lived
// Ed25519 JWTs per question: one for the orchestrator and one the orchestrator relays to the MCP tools.

const (
	permissionAI           = "ai:invoke"
	assertionIssuer        = "web-portal"
	assertionTenant        = "platform"
	assertionLifetime      = 120 * time.Second
	orchestratorAudience   = "ai-orchestrator"
	toolsAudience          = "assistant-tools"
	toolsAssertionHeader   = "X-Assistant-Tools-Assertion"
	maxQuestionCharacters  = 1000
	maxAssistantResponse   = 256 << 10
	maxConcurrentQuestions = 2
	// Covers the orchestrator's worst case: routing (20 s) plus one answer and one citation retry (90 s each).
	assistantTimeout = 210 * time.Second
)

// assistantPermissions are the only permissions forwarded; the orchestrator also intersects them.
var (
	assistantPermissions = []string{permissionAI, "knowledge:read", "knowledge:restricted", "analytics:read", "payments:read"}
	assertionSubject     = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,63}$`)
)

type assistantProxy struct {
	endpoint string
	key      ed25519.PrivateKey
	client   *http.Client
	slots    chan struct{}
}

// newAssistantFromEnv returns nil when the assistant is not configured, so /assistant stays disabled.
func newAssistantFromEnv(getenv func(string) string) (*assistantProxy, error) {
	endpoint, keyFile := strings.TrimRight(getenv("AI_ORCHESTRATOR_URL"), "/"), getenv("AI_ASSERTION_PRIVATE_KEY_FILE")
	if endpoint == "" && keyFile == "" {
		return nil, nil
	}
	if endpoint == "" || keyFile == "" {
		return nil, errors.New("AI_ORCHESTRATOR_URL and AI_ASSERTION_PRIVATE_KEY_FILE must be set together")
	}
	if parsed, err := url.Parse(endpoint); err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return nil, errors.New("AI_ORCHESTRATOR_URL must be an http(s) URL")
	}
	raw, err := os.ReadFile(keyFile)
	if err != nil {
		return nil, fmt.Errorf("read assistant assertion key: %w", err)
	}
	key, err := parseSigningKey(raw)
	if err != nil {
		return nil, err
	}
	return &assistantProxy{endpoint: endpoint + "/v1/assistant/ask", key: key, slots: make(chan struct{}, maxConcurrentQuestions),
		client: &http.Client{Timeout: assistantTimeout, CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}}}, nil
}

func parseSigningKey(raw []byte) (ed25519.PrivateKey, error) {
	block, _ := pem.Decode(raw)
	if block == nil || block.Type != "PRIVATE KEY" {
		return nil, errors.New("assistant assertion key must be a PKCS#8 PEM private key")
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	key, ok := parsed.(ed25519.PrivateKey)
	if err != nil || !ok {
		return nil, errors.New("assistant assertion key must be Ed25519")
	}
	return key, nil
}

func randomHex(size int) string {
	value := make([]byte, size)
	_, _ = rand.Read(value)
	return hex.EncodeToString(value)
}

// assertion builds a compact EdDSA JWT; the claims match ai_core.identity.verify_assertion.
func (a *assistantProxy) assertion(audience, subject string, permissions []string, now time.Time) string {
	header, _ := json.Marshal(map[string]string{"alg": "EdDSA", "typ": "JWT"})
	claims, _ := json.Marshal(map[string]any{
		"iss": assertionIssuer, "aud": audience, "sub": subject, "tenant": assertionTenant,
		"iat": now.Unix(), "exp": now.Add(assertionLifetime).Unix(), "jti": randomHex(16), "permissions": permissions,
	})
	signed := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(claims)
	return signed + "." + base64.RawURLEncoding.EncodeToString(ed25519.Sign(a.key, []byte(signed)))
}

func forwardedPermissions(user devUser) []string {
	permissions := []string{}
	for _, permission := range assistantPermissions {
		if has(user.Permissions, permission) {
			permissions = append(permissions, permission)
		}
	}
	return permissions
}

func (p *portal) askAssistant(w http.ResponseWriter, r *http.Request) {
	name, user, ok := p.currentUser(r)
	switch {
	case !ok:
		http.Error(w, "not signed in", http.StatusUnauthorized)
		return
	case p.assistant == nil:
		http.NotFound(w, r)
		return
	case !has(user.Permissions, permissionAI) || !assertionSubject.MatchString(name):
		http.Error(w, "assistant access not granted", http.StatusForbidden)
		return
	}
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		http.Error(w, "expected application/json", http.StatusUnsupportedMediaType)
		return
	}
	var body struct {
		Question string `json:"question"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxRequestBody)).Decode(&body); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}
	question := strings.TrimSpace(body.Question)
	if question == "" || utf8.RuneCountInString(question) > maxQuestionCharacters {
		http.Error(w, fmt.Sprintf("question must have 1 to %d characters", maxQuestionCharacters), http.StatusBadRequest)
		return
	}
	select {
	case p.assistant.slots <- struct{}{}:
		defer func() { <-p.assistant.slots }()
	default:
		w.Header().Set("Retry-After", "5")
		http.Error(w, "the assistant is busy, retry shortly", http.StatusTooManyRequests)
		return
	}
	// Local CPU inference is slow, so this route extends the server's 10 s write deadline.
	if err := http.NewResponseController(w).SetWriteDeadline(time.Now().Add(assistantTimeout + 5*time.Second)); err != nil {
		slog.Warn("assistant_write_deadline_unavailable", "error", err.Error())
	}
	requestID, started := randomHex(16), p.now()
	status, payload, err := p.assistant.forward(r.Context(), requestID, name, forwardedPermissions(user), question, started)
	// The audit line never contains the question or the answer; the orchestrator audits content metadata.
	slog.InfoContext(r.Context(), "assistant_request", "request_id", requestID, "user", name, "upstream_status", status,
		"duration_ms", p.now().Sub(started).Milliseconds())
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		http.Error(w, "the assistant did not answer in time", http.StatusGatewayTimeout)
	case err != nil:
		slog.Error("assistant_unavailable", "request_id", requestID, "error", err.Error())
		http.Error(w, "the assistant is unavailable", http.StatusBadGateway)
	default:
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Request-ID", requestID)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(payload)
	}
}

func (a *assistantProxy) forward(ctx context.Context, requestID, subject string, permissions []string, question string,
	now time.Time) (int, []byte, error) {
	encoded, _ := json.Marshal(map[string]string{"question": question})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, a.endpoint, bytes.NewReader(encoded))
	if err != nil {
		return 0, nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-Request-ID", requestID)
	request.Header.Set("Authorization", "Bearer "+a.assertion(orchestratorAudience, subject, permissions, now))
	request.Header.Set(toolsAssertionHeader, a.assertion(toolsAudience, subject, permissions, now))
	response, err := a.client.Do(request)
	if err != nil {
		var timeout interface{ Timeout() bool }
		if errors.As(err, &timeout) && timeout.Timeout() {
			return 0, nil, fmt.Errorf("%w: %v", context.DeadlineExceeded, err)
		}
		return 0, nil, err
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(response.Body, maxAssistantResponse+1))
	switch {
	case err != nil:
		return response.StatusCode, nil, err
	case response.StatusCode != http.StatusOK:
		return response.StatusCode, nil, fmt.Errorf("orchestrator returned HTTP %d", response.StatusCode)
	case len(payload) > maxAssistantResponse || !json.Valid(payload):
		return response.StatusCode, nil, errors.New("orchestrator returned an invalid response")
	}
	return response.StatusCode, payload, nil
}
