package telemetry

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"testing"

	"go.opentelemetry.io/otel/trace"
)

func TestConfigureRejectsUnsafeEndpoints(t *testing.T) {
	for _, endpoint := range []string{"ftp://collector", "http://user:secret@collector", "http://collector?token=secret", "http://collector/path", "not-a-url"} {
		t.Run(endpoint, func(t *testing.T) {
			t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
			if _, err := Configure(context.Background(), "test", "test"); err == nil {
				t.Fatal("unsafe endpoint accepted")
			}
		})
	}
}

func TestConfigureDisabled(t *testing.T) {
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
	shutdown, err := Configure(context.Background(), "test", "test")
	if err != nil {
		t.Fatal(err)
	}
	if err := shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestCorrelationHandler(t *testing.T) {
	sc := trace.NewSpanContext(trace.SpanContextConfig{
		TraceID: trace.TraceID{1}, SpanID: trace.SpanID{2}, TraceFlags: trace.FlagsSampled,
	})
	ctx := trace.ContextWithSpanContext(context.Background(), sc)
	var buffer bytes.Buffer
	logger := slog.New(CorrelationHandler{Handler: slog.NewJSONHandler(&buffer, nil)})
	logger = logger.With("service.name", "test").WithGroup("event")
	logger.InfoContext(ctx, "payment_receipt", "event_id", "synthetic-event")
	var result struct {
		Event struct {
			TraceID string `json:"trace_id"`
			SpanID  string `json:"span_id"`
			EventID string `json:"event_id"`
		} `json:"event"`
	}
	if err := json.Unmarshal(buffer.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Event.TraceID != sc.TraceID().String() || result.Event.SpanID != sc.SpanID().String() || result.Event.EventID != "synthetic-event" {
		t.Fatalf("missing correlation: %s", buffer.String())
	}
	buffer.Reset()
	logger.Info("startup")
	if bytes.Contains(buffer.Bytes(), []byte("trace_id")) {
		t.Fatal("invented trace context for an untraced log")
	}
}
