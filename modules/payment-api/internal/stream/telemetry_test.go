package stream

import (
	"context"
	"testing"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/otel/trace"
)

func TestKafkaTracePropagationDoesNotCopyBaggage(t *testing.T) {
	sc := trace.NewSpanContext(trace.SpanContextConfig{
		TraceID: trace.TraceID{1}, SpanID: trace.SpanID{2}, TraceFlags: trace.FlagsSampled,
	})
	ctx := trace.ContextWithSpanContext(context.Background(), sc)
	headers := Headers{}
	telemetry.Propagator.Inject(ctx, &headers)
	extracted := telemetry.Propagator.Extract(context.Background(), &headers)
	got := trace.SpanContextFromContext(extracted)
	if got.TraceID() != sc.TraceID() || got.SpanID() != sc.SpanID() || !got.IsRemote() {
		t.Fatal("Kafka propagation lost producer context")
	}
	headers.Set("traceparent", headers.Get("traceparent"))
	if len(headers.Keys()) != 1 || headers.Get("baggage") != "" {
		t.Fatal("duplicate propagation headers or unexpected baggage")
	}
	headers.Set("traceparent", "invalid")
	if trace.SpanContextFromContext(telemetry.Propagator.Extract(context.Background(), &headers)).IsValid() {
		t.Fatal("invalid context accepted")
	}
}
