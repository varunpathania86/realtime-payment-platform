package telemetry

import (
	"context"
	"errors"
	"log/slog"
	"net/url"
	"os"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

var Propagator = propagation.TraceContext{}

// Configure does not contact the collector; bounded asynchronous export must never
// make payment availability depend on telemetry availability.
func Configure(ctx context.Context, service, version string) (func(context.Context) error, error) {
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
	exporter, err := otlptracehttp.New(ctx,
		otlptracehttp.WithEndpointURL(raw), otlptracehttp.WithURLPath("/v1/traces"),
		otlptracehttp.WithTimeout(2*time.Second),
		otlptracehttp.WithRetry(otlptracehttp.RetryConfig{Enabled: false}))
	if err != nil {
		return nil, errors.New("cannot configure OTLP trace exporter")
	}
	provider := sdktrace.NewTracerProvider(
		sdktrace.WithResource(resource.NewSchemaless(
			attribute.String("service.name", service),
			attribute.String("service.version", version),
			attribute.String("service.instance.id", os.Getenv("HOSTNAME")),
			attribute.String("k8s.cluster.name", os.Getenv("OTEL_CLUSTER_NAME")),
			attribute.String("deployment.environment.name", "development"),
		)),
		sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.TraceIDRatioBased(1))),
		sdktrace.WithBatcher(exporter, sdktrace.WithMaxQueueSize(512),
			sdktrace.WithMaxExportBatchSize(64), sdktrace.WithBatchTimeout(time.Second),
			sdktrace.WithExportTimeout(2*time.Second)),
	)
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(Propagator)
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(error) {
		slog.Warn("telemetry_export_failed", "error_type", "dependency_unavailable")
	}))
	return provider.Shutdown, nil
}

func Start(ctx context.Context, name string, kind trace.SpanKind, attrs ...attribute.KeyValue) (context.Context, trace.Span) {
	return otel.Tracer("rpp/payment").Start(ctx, name, trace.WithSpanKind(kind), trace.WithAttributes(attrs...))
}

type CorrelationHandler struct{ slog.Handler }

func (h CorrelationHandler) Handle(ctx context.Context, record slog.Record) error {
	sc := trace.SpanContextFromContext(ctx)
	if sc.IsValid() {
		record.AddAttrs(slog.String("trace_id", sc.TraceID().String()), slog.String("span_id", sc.SpanID().String()))
	}
	return h.Handler.Handle(ctx, record)
}

func (h CorrelationHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return CorrelationHandler{h.Handler.WithAttrs(attrs)}
}

func (h CorrelationHandler) WithGroup(name string) slog.Handler {
	return CorrelationHandler{h.Handler.WithGroup(name)}
}
