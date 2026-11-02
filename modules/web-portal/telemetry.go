package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"

	"github.com/felixge/httpsnoop"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

func instrumentPortal(next http.Handler) http.Handler {
	type requestKey struct{}
	instrumented := otelhttp.NewHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		original := r.Context().Value(requestKey{}).(*http.Request)
		result := httpsnoop.CaptureMetrics(next, w, original.WithContext(r.Context()))
		slog.InfoContext(r.Context(), "http_request", "service.name", "web-portal",
			"method", r.Method, "route", r.URL.Path, "status", result.Code,
			"duration_ms", result.Duration.Milliseconds())
	}), "portal.http", otelhttp.WithPropagators(propagation.TraceContext{}),
		otelhttp.WithFilter(func(r *http.Request) bool {
			return r.Header.Get("Traceparent") != "" || (r.URL.Path != "/healthz" && r.URL.Path != "/readyz")
		}))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/telemetry/config" || r.URL.Path == "/telemetry/v1/traces" {
			next.ServeHTTP(w, r)
			return
		}
		safe := r.Clone(context.WithValue(r.Context(), requestKey{}, r))
		switch safe.URL.Path {
		case "/healthz", "/readyz":
		default:
			safe.URL.Path = "/portal"
		}
		safe.URL.RawPath, safe.URL.RawQuery = "", ""
		safe.URL.User, safe.URL.Host, safe.Host, safe.RemoteAddr = nil, "", "web-portal", ""
		safe.Header = make(http.Header)
		safe.Header.Set("Traceparent", r.Header.Get("Traceparent"))
		safe.Header.Set("Tracestate", r.Header.Get("Tracestate"))
		if safe.Method != http.MethodGet && safe.Method != http.MethodHead {
			safe.Method = "_OTHER"
		}
		instrumented.ServeHTTP(w, safe)
	})
}

type correlationHandler struct{ slog.Handler }

func (h correlationHandler) Handle(ctx context.Context, record slog.Record) error {
	sc := trace.SpanContextFromContext(ctx)
	if sc.IsValid() {
		record.AddAttrs(slog.String("trace_id", sc.TraceID().String()), slog.String("span_id", sc.SpanID().String()))
	}
	return h.Handler.Handle(ctx, record)
}
func (h correlationHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return correlationHandler{h.Handler.WithAttrs(attrs)}
}
func (h correlationHandler) WithGroup(name string) slog.Handler {
	return correlationHandler{h.Handler.WithGroup(name)}
}

func configureTelemetry(ctx context.Context) (func(context.Context) error, error) {
	raw := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if raw == "" {
		return func(context.Context) error { return nil }, nil
	}
	endpoint, err := url.Parse(raw)
	if err != nil || endpoint.Host == "" || endpoint.User != nil || endpoint.RawQuery != "" ||
		endpoint.Fragment != "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") ||
		(endpoint.Path != "" && endpoint.Path != "/") {
		return nil, errors.New("OTEL_EXPORTER_OTLP_ENDPOINT must be an HTTP(S) origin without credentials")
	}
	exporter, err := otlptracehttp.New(ctx, otlptracehttp.WithEndpointURL(raw),
		otlptracehttp.WithURLPath("/v1/traces"), otlptracehttp.WithTimeout(2*time.Second),
		otlptracehttp.WithRetry(otlptracehttp.RetryConfig{Enabled: false}))
	if err != nil {
		return nil, errors.New("cannot configure portal telemetry")
	}
	provider := sdktrace.NewTracerProvider(
		sdktrace.WithResource(resource.NewSchemaless(
			attribute.String("service.name", "web-portal"),
			attribute.String("service.version", version),
			attribute.String("service.instance.id", os.Getenv("HOSTNAME")),
			attribute.String("k8s.cluster.name", os.Getenv("OTEL_CLUSTER_NAME")),
			attribute.String("deployment.environment.name", "development"),
		)),
		sdktrace.WithBatcher(exporter, sdktrace.WithMaxQueueSize(128),
			sdktrace.WithMaxExportBatchSize(16), sdktrace.WithBatchTimeout(time.Second),
			sdktrace.WithExportTimeout(2*time.Second)),
	)
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(error) {
		slog.Warn("telemetry_export_failed", "error_type", "dependency_unavailable")
	}))
	return provider.Shutdown, nil
}

type telemetryProxy struct {
	endpoint string
	client   *http.Client
	mu       sync.Mutex
	window   time.Time
	requests int
}

func (p *telemetryProxy) serve(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.URL.Path == "/telemetry/config" && r.Method == http.MethodGet {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]bool{"enabled": p.endpoint != ""})
		return
	}
	if r.URL.Path != "/telemetry/v1/traces" || p.endpoint == "" {
		http.NotFound(w, r)
		return
	}
	origin, err := url.Parse(r.Header.Get("Origin"))
	if r.Method != http.MethodPost || err != nil || origin.Host != r.Host ||
		(origin.Scheme != "http" && origin.Scheme != "https") ||
		r.Header.Get("Content-Type") != "application/json" {
		http.Error(w, "telemetry request rejected", http.StatusForbidden)
		return
	}
	p.mu.Lock()
	if time.Since(p.window) >= time.Second {
		p.window, p.requests = time.Now(), 0
	}
	p.requests++
	limited := p.requests > 5
	p.mu.Unlock()
	if limited {
		http.Error(w, "telemetry rate limit", http.StatusTooManyRequests)
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64<<10))
	if err != nil {
		http.Error(w, "telemetry payload too large", http.StatusRequestEntityTooLarge)
		return
	}
	if !json.Valid(body) {
		http.Error(w, "invalid telemetry JSON", http.StatusBadRequest)
		return
	}
	request, err := http.NewRequestWithContext(r.Context(), http.MethodPost, p.endpoint+"/v1/traces", bytes.NewReader(body))
	if err != nil {
		slog.Error("telemetry_proxy_configuration_failed")
		http.Error(w, "telemetry unavailable", http.StatusServiceUnavailable)
		return
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := p.client.Do(request)
	if err != nil {
		slog.Warn("telemetry_export_failed", "error_type", "dependency_unavailable")
		http.Error(w, "telemetry unavailable", http.StatusServiceUnavailable)
		return
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		slog.Warn("telemetry_export_rejected", "status", response.StatusCode)
		http.Error(w, "telemetry unavailable", http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
