package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"mime"
	"net/http"
	"strings"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

type API struct {
	store   payment.Store
	logger  *slog.Logger
	version string
	commit  string
}

func New(store payment.Store, logger *slog.Logger, version, commit string) http.Handler {
	api := &API{store: store, logger: logger, version: version, commit: commit}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		api.serve(w, r)
		logger.Info("http_request", "method", r.Method, "route", route(r.URL.Path), "duration_ms", time.Since(start).Milliseconds())
	})
}

func route(path string) string {
	path = strings.TrimPrefix(path, "/api")
	switch path {
	case "/healthz", "/readyz", "/version", "/payments":
		return path
	default:
		if strings.HasPrefix(path, "/payments/") {
			return "/payments/{id}"
		}
		return "unmatched"
	}
}

func respond(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func fail(w http.ResponseWriter, status int, message string) {
	respond(w, status, map[string]string{"error": message})
}

func method(w http.ResponseWriter, r *http.Request, allowed string) bool {
	if r.Method == allowed {
		return true
	}
	w.Header().Set("Allow", allowed)
	fail(w, http.StatusMethodNotAllowed, "method not allowed")
	return false
}

func (api *API) serve(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	if strings.HasPrefix(path, "/api/") {
		path = strings.TrimPrefix(path, "/api")
	}
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	switch path {
	case "/healthz":
		if method(w, r, http.MethodGet) {
			respond(w, http.StatusOK, map[string]string{"status": "ok"})
		}
	case "/version":
		if method(w, r, http.MethodGet) {
			respond(w, http.StatusOK, map[string]string{"version": api.version, "commit": api.commit})
		}
	case "/readyz":
		if !method(w, r, http.MethodGet) {
			return
		}
		if err := api.store.Ready(ctx); err != nil {
			api.logger.Error("database_not_ready", "error_type", "dependency_unavailable")
			fail(w, http.StatusServiceUnavailable, "database unavailable or migrations not applied")
			return
		}
		respond(w, http.StatusOK, map[string]string{"status": "ok"})
	case "/payments":
		if method(w, r, http.MethodPost) {
			api.create(ctx, w, r)
		}
	default:
		if strings.HasPrefix(path, "/payments/") {
			if !method(w, r, http.MethodGet) {
				return
			}
			id := strings.TrimPrefix(path, "/payments/")
			if !payment.ValidID(id) {
				fail(w, http.StatusBadRequest, "payment id must be a lowercase UUID")
				return
			}
			p, err := api.store.Get(ctx, id)
			api.result(w, p, err, http.StatusOK)
			return
		}
		fail(w, http.StatusNotFound, "not found")
	}
}

func (api *API) create(ctx context.Context, w http.ResponseWriter, r *http.Request) {
	contentType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || contentType != "application/json" {
		fail(w, http.StatusUnsupportedMediaType, "Content-Type must be application/json")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	var command payment.Command
	if err := decoder.Decode(&command); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			fail(w, http.StatusRequestEntityTooLarge, "request body exceeds 4096 bytes")
		} else {
			fail(w, http.StatusBadRequest, "invalid payment JSON")
		}
		return
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			fail(w, http.StatusRequestEntityTooLarge, "request body exceeds 4096 bytes")
		} else {
			fail(w, http.StatusBadRequest, "request must contain one JSON object")
		}
		return
	}
	keys := r.Header.Values("Idempotency-Key")
	if len(keys) != 1 {
		fail(w, http.StatusBadRequest, "exactly one Idempotency-Key is required")
		return
	}
	if err := payment.Validate(keys[0], command); err != nil {
		fail(w, http.StatusBadRequest, err.Error())
		return
	}
	p, err := api.store.Create(ctx, keys[0], command)
	if err == nil {
		prefix := ""
		if strings.HasPrefix(r.URL.Path, "/api/") {
			prefix = "/api"
		}
		w.Header().Set("Location", prefix+"/payments/"+p.ID)
	}
	api.result(w, p, err, http.StatusCreated)
}

func (api *API) result(w http.ResponseWriter, p payment.Payment, err error, status int) {
	switch {
	case err == nil:
		respond(w, status, p)
	case errors.Is(err, payment.ErrNotFound):
		fail(w, http.StatusNotFound, "payment not found")
	case errors.Is(err, payment.ErrConflict):
		fail(w, http.StatusConflict, payment.ErrConflict.Error())
	default:
		api.logger.Error("payment_store_failed", "error_type", "dependency_unavailable")
		fail(w, http.StatusServiceUnavailable, "payment storage unavailable")
	}
}
