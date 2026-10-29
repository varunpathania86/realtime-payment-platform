package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

const account = "12345678-1234-1234-1234-123456789abc"
const body = `{"account_id":"` + account + `","amount_minor":100,"currency":"USD"}`

type fakeStore struct {
	err     error
	created payment.Payment
	key     string
}

func (s *fakeStore) Ready(ctx context.Context) error {
	if _, ok := ctx.Deadline(); !ok {
		panic("missing dependency timeout")
	}
	return s.err
}
func (s *fakeStore) Get(context.Context, string) (payment.Payment, error) { return s.created, s.err }
func (s *fakeStore) Create(ctx context.Context, key string, command payment.Command) (payment.Payment, error) {
	if _, ok := ctx.Deadline(); !ok {
		panic("missing dependency timeout")
	}
	s.key = key
	if s.created.ID == "" {
		s.created = payment.Payment{ID: account, Command: command, Status: "initiated", CreatedAt: time.Unix(1, 0).UTC()}
	}
	return s.created, s.err
}

func request(store payment.Store, method, path, body, key string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	if key != "" {
		r.Header.Set("Idempotency-Key", key)
	}
	w := httptest.NewRecorder()
	New(store, slog.New(slog.NewJSONHandler(io.Discard, nil)), "test", "abc").ServeHTTP(w, r)
	return w
}

func TestStatusEndpoints(t *testing.T) {
	for _, prefix := range []string{"", "/api"} {
		for _, path := range []string{"/healthz", "/readyz", "/version"} {
			store := &fakeStore{}
			w := request(store, "GET", prefix+path, "", "")
			if w.Code != 200 || w.Header().Get("Cache-Control") != "no-store" || w.Header().Get("Content-Type") != "application/json" {
				t.Fatalf("%s: %d %v", path, w.Code, w.Header())
			}
			for _, verb := range []string{"HEAD", "POST", "DELETE", "OPTIONS"} {
				w := request(store, verb, prefix+path, "", "")
				if w.Code != 405 || w.Header().Get("Allow") != "GET" {
					t.Fatalf("%s %s: %d", verb, path, w.Code)
				}
			}
		}
	}
	store := &fakeStore{err: errors.New("database down")}
	if request(store, "GET", "/readyz", "", "").Code != 503 || request(store, "GET", "/healthz", "", "").Code != 200 {
		t.Fatal("readiness must depend on DB, liveness must not")
	}
}

func TestCreateAndRead(t *testing.T) {
	for _, prefix := range []string{"", "/api"} {
		store := &fakeStore{}
		first := request(store, "POST", prefix+"/payments", body, "key")
		second := request(store, "POST", prefix+"/payments", body, "key")
		read := request(store, "GET", first.Header().Get("Location"), "", "")
		if first.Code != 201 || second.Code != 201 || read.Code != 200 || first.Body.String() != read.Body.String() || first.Body.String() != second.Body.String() {
			t.Fatalf("unexpected create/read: %d %d %d", first.Code, second.Code, read.Code)
		}
		var p payment.Payment
		if err := json.Unmarshal(first.Body.Bytes(), &p); err != nil || p.Status != "initiated" || p.AmountMinor != 100 {
			t.Fatalf("invalid payment: %+v %v", p, err)
		}
	}
}

func TestInvalidRequestsNeverReachStore(t *testing.T) {
	for _, payload := range []string{
		"", "null", "[]", "{}", body + "{}", strings.Replace(body, "100", "1.5", 1),
		strings.Replace(body, "100", "9223372036854775808", 1),
		strings.Replace(body, "100", "null", 1),
		strings.Replace(body, "USD", "XXX", 1),
		strings.TrimSuffix(body, "}") + `,"extra":1}`, strings.Repeat("x", 4097),
	} {
		store := &fakeStore{}
		w := request(store, "POST", "/payments", payload, "key")
		if (w.Code != 400 && w.Code != 413) || store.key != "" {
			t.Fatalf("accepted %q: %d", payload[:min(len(payload), 100)], w.Code)
		}
	}
	if request(&fakeStore{}, "POST", "/payments", body, "").Code != 400 {
		t.Fatal("missing idempotency key accepted")
	}
	if request(&fakeStore{}, "POST", "/payments", body+strings.Repeat(" ", 4096), "key").Code != 413 {
		t.Fatal("body limit did not include trailing whitespace")
	}
	r := httptest.NewRequest("POST", "/payments", strings.NewReader(body))
	r.Header.Set("Content-Type", "text/plain")
	w := httptest.NewRecorder()
	New(&fakeStore{}, slog.Default(), "", "").ServeHTTP(w, r)
	if w.Code != 415 {
		t.Fatal("invalid content type accepted")
	}
}

func TestStoreErrorsAndPaths(t *testing.T) {
	for err, status := range map[error]int{payment.ErrConflict: 409, payment.ErrNotFound: 404, errors.New("password=secret"): 503} {
		w := request(&fakeStore{err: err}, "POST", "/payments", body, "key")
		if w.Code != status || strings.Contains(w.Body.String(), "secret") {
			t.Fatalf("%v: %d %s", err, w.Code, w.Body.String())
		}
	}
	for _, path := range []string{"/", "/api", "/api/", "/healthz/", "/api/unknown"} {
		if request(&fakeStore{}, "GET", path, "", "").Code != 404 {
			t.Fatalf("unknown path accepted: %s", path)
		}
	}
	if request(&fakeStore{}, "GET", "/payments/not-uuid", "", "").Code != 400 {
		t.Fatal("bad ID accepted")
	}
	if w := request(&fakeStore{}, "GET", "/payments", "", ""); w.Code != 405 || w.Header().Get("Allow") != "POST" {
		t.Fatal("collection GET accepted")
	}
}
