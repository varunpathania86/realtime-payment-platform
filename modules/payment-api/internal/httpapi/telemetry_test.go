package httpapi

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func TestProbeSuppressionAndBusinessSpans(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	previous := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	t.Cleanup(func() { otel.SetTracerProvider(previous) })
	handler := New(&fakeStore{}, slog.New(slog.NewJSONHandler(&bytes.Buffer{}, nil)), "test", "test")
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if len(recorder.Ended()) != 0 {
		t.Fatal("unparented probe created a trace")
	}
	request := httptest.NewRequest(http.MethodPost, "/api/payments", strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Idempotency-Key", "business-span")
	handler.ServeHTTP(httptest.NewRecorder(), request)
	spans := recorder.Ended()
	if len(spans) != 2 || spans[0].Name() != "payment.create" ||
		spans[0].Parent().SpanID() != spans[1].SpanContext().SpanID() {
		t.Fatalf("business span is not a child of the standard HTTP span: %v", spans)
	}
}

func TestRequestTracingUsesBoundedRoutesAndCorrelatedLogs(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	previous := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	t.Cleanup(func() { otel.SetTracerProvider(previous) })
	var output bytes.Buffer
	logger := slog.New(telemetry.CorrelationHandler{Handler: slog.NewJSONHandler(&output, nil)})
	handler := New(&fakeStore{}, logger, "test", "test")
	r := httptest.NewRequest("GET", "/api/payments/"+account+"?token=do-not-export", nil)
	r.Header.Set("traceparent", "00-01000000000000000000000000000000-0200000000000000-01")
	handler.ServeHTTP(httptest.NewRecorder(), r)
	spans := recorder.Ended()
	if len(spans) != 1 || spans[0].Name() != "GET /payments/{id}" {
		t.Fatalf("unexpected spans: %v", spans)
	}
	if spans[0].SpanContext().TraceID().String() != "01000000000000000000000000000000" {
		t.Fatal("HTTP trace context was not continued")
	}
	for _, attr := range spans[0].Attributes() {
		if strings.Contains(attr.Value.Emit(), account) || strings.Contains(attr.Value.Emit(), "do-not-export") {
			t.Fatal("raw URL or identifier exported as HTTP attribute")
		}
	}
	if !strings.Contains(output.String(), `"trace_id":"01000000000000000000000000000000"`) ||
		strings.Contains(output.String(), "do-not-export") {
		t.Fatalf("unsafe or uncorrelated log: %s", output.String())
	}
}
