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

	"github.com/felixge/httpsnoop"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

type API struct {
	store   payment.Store
	logger  *slog.Logger
	version string
	commit  string
}

func New(store payment.Store, logger *slog.Logger, version, commit string) http.Handler {
	api := &API{store: store, logger: logger, version: version, commit: commit}
	type originalRequestKey struct{}
	instrumented := otelhttp.NewHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		original := ctx.Value(originalRequestKey{}).(*http.Request)
		r = original.WithContext(ctx)
		method := r.Method
		switch method {
		case "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS":
		default:
			method = "_OTHER"
		}
		span := trace.SpanFromContext(ctx)
		span.SetAttributes(attribute.String("http.route", route(r.URL.Path)))
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		result := httpsnoop.CaptureMetrics(http.HandlerFunc(api.serve), w, r)
		telemetry.RecordHTTP(ctx, method, route(r.URL.Path), result.Code, result.Duration.Seconds())
		span.SetAttributes(attribute.Int("http.response.status_code", result.Code))
		if result.Code >= 500 {
			span.SetStatus(codes.Error, "request_failed")
		}
		logger.InfoContext(ctx, "http_request", "method", method, "route", route(r.URL.Path),
			"status", result.Code, "duration_ms", result.Duration.Milliseconds())
	}), "payment.http", otelhttp.WithPropagators(telemetry.Propagator),
		otelhttp.WithFilter(func(r *http.Request) bool {
			return r.Header.Get("Traceparent") != "" || (r.URL.Path != "/healthz" && r.URL.Path != "/readyz")
		}),
		otelhttp.WithSpanNameFormatter(func(_ string, r *http.Request) string {
			return r.Method + " " + r.URL.Path
		}))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/metrics" && r.Method == http.MethodGet {
			telemetry.Metrics().ServeHTTP(w, r)
			return
		}
		// Library instrumentation sees only bounded metadata, never raw IDs, query
		// strings, peer addresses, user agents or credential-bearing headers.
		safe := r.Clone(context.WithValue(r.Context(), originalRequestKey{}, r))
		safe.URL.Path, safe.URL.RawPath, safe.URL.RawQuery = route(r.URL.Path), "", ""
		safe.URL.User, safe.URL.Host, safe.Host, safe.RemoteAddr = nil, "", "payment-api", ""
		safe.Header = make(http.Header)
		safe.Header.Set("Traceparent", r.Header.Get("Traceparent"))
		safe.Header.Set("Tracestate", r.Header.Get("Tracestate"))
		switch safe.Method {
		case "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS":
		default:
			safe.Method = "_OTHER"
		}
		instrumented.ServeHTTP(w, safe)
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
			api.logger.ErrorContext(ctx, "database_not_ready", "error_type", "dependency_unavailable")
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
			api.result(ctx, w, p, err, http.StatusOK)
			return
		}
		fail(w, http.StatusNotFound, "not found")
	}
}

func (api *API) create(ctx context.Context, w http.ResponseWriter, r *http.Request) {
	ctx, span := telemetry.Start(ctx, "payment.create", trace.SpanKindInternal)
	defer span.End()
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
	if err != nil {
		span.SetStatus(codes.Error, "payment_creation_failed")
	}
	if err == nil {
		trace.SpanFromContext(ctx).SetAttributes(attribute.String("payment_id", p.ID))
		api.logger.InfoContext(ctx, "payment_created", "payment_id", p.ID)
		prefix := ""
		if strings.HasPrefix(r.URL.Path, "/api/") {
			prefix = "/api"
		}
		w.Header().Set("Location", prefix+"/payments/"+p.ID)
	}
	api.result(ctx, w, p, err, http.StatusCreated)
}

func (api *API) result(ctx context.Context, w http.ResponseWriter, p payment.Payment, err error, status int) {
	switch {
	case err == nil:
		respond(w, status, p)
	case errors.Is(err, payment.ErrNotFound):
		fail(w, http.StatusNotFound, "payment not found")
	case errors.Is(err, payment.ErrConflict):
		fail(w, http.StatusConflict, payment.ErrConflict.Error())
	default:
		api.logger.ErrorContext(ctx, "payment_store_failed", "error_type", "dependency_unavailable")
		fail(w, http.StatusServiceUnavailable, "payment storage unavailable")
	}
}
