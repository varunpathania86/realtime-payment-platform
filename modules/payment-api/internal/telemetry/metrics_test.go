package telemetry

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"

	"go.opentelemetry.io/otel/trace"
)

func TestMetricsHaveBoundedLabelsAndTraceExemplars(t *testing.T) {
	sc := trace.NewSpanContext(trace.SpanContextConfig{
		TraceID: trace.TraceID{1}, SpanID: trace.SpanID{2}, TraceFlags: trace.FlagsSampled,
	})
	ctx := trace.ContextWithSpanContext(context.Background(), sc)
	RecordHTTP(ctx, "POST", "/payments", 201, .3)
	Backlog.Set(7)
	BacklogAge.Set(62)
	request := httptest.NewRequest("GET", "/metrics", nil)
	request.Header.Set("Accept", "application/openmetrics-text")
	response := httptest.NewRecorder()
	Metrics().ServeHTTP(response, request)
	body := response.Body.String()
	for _, expected := range []string{
		`rpp_http_requests_total{method="POST",route="/payments",status="201"}`,
		`# {trace_id="` + sc.TraceID().String() + `"}`,
		"rpp_outbox_pending 7", "rpp_outbox_oldest_pending_age_seconds 62",
	} {
		if !strings.Contains(body, expected) {
			t.Fatalf("missing %s in %s", expected, body)
		}
	}
	for _, forbidden := range []string{"payment_id=", "event_id=", "account_id=", "user_id="} {
		if strings.Contains(body, forbidden) {
			t.Fatalf("unbounded label %s", forbidden)
		}
	}
}
