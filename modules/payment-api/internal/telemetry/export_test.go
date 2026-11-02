package telemetry

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/trace"
	collector "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

func TestConfigureExportsOTLPWithResourceIdentity(t *testing.T) {
	requests := make(chan *collector.ExportTraceServiceRequest, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/traces" || r.Method != "POST" {
			t.Errorf("unexpected OTLP request: %s %s", r.Method, r.URL.Path)
		}
		data, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		if err != nil {
			t.Error(err)
		}
		var request collector.ExportTraceServiceRequest
		if err := proto.Unmarshal(data, &request); err != nil {
			t.Error(err)
		}
		requests <- &request
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", server.URL)
	t.Setenv("OTEL_CLUSTER_NAME", "test-a")
	previous := otel.GetTracerProvider()
	previousErrors := otel.GetErrorHandler()
	t.Cleanup(func() {
		otel.SetTracerProvider(previous)
		otel.SetErrorHandler(previousErrors)
	})
	stop, err := Configure(context.Background(), "payment-api", "test")
	if err != nil {
		t.Fatal(err)
	}

	_, span := Start(context.Background(), "test publish", trace.SpanKindProducer)
	span.End()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := stop(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case request := <-requests:
		if len(request.ResourceSpans) != 1 || len(request.ResourceSpans[0].ScopeSpans[0].Spans) != 1 {
			t.Fatalf("unexpected OTLP shape: %v", request)
		}
		attrs := make(map[string]string)
		for _, attr := range request.ResourceSpans[0].Resource.Attributes {
			attrs[attr.Key] = attr.Value.GetStringValue()
		}
		if attrs["service.name"] != "payment-api" || attrs["service.version"] != "test" || attrs["k8s.cluster.name"] != "test-a" {
			t.Fatalf("missing resource identity: %v", attrs)
		}
	default:
		t.Fatal("shutdown did not flush OTLP spans")
	}
}

func TestUnavailableCollectorDoesNotPreventApplicationStartup(t *testing.T) {
	var logs bytes.Buffer
	previousLogger := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(previousLogger) })
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer server.Close()
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", server.URL)
	previous := otel.GetTracerProvider()
	previousErrors := otel.GetErrorHandler()
	t.Cleanup(func() {
		otel.SetTracerProvider(previous)
		otel.SetErrorHandler(previousErrors)
	})
	stop, err := Configure(context.Background(), "payment-api", "test")
	if err != nil {
		t.Fatal("collector availability must not gate application startup")
	}
	_, span := Start(context.Background(), "test publish", trace.SpanKindProducer)
	span.End()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := stop(ctx); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(logs.String(), "telemetry_export_failed") {
		t.Fatal("collector rejection was not surfaced through the error handler")
	}
	if strings.Contains(logs.String(), server.URL) {
		t.Fatal("collector endpoint leaked into the failure log")
	}
}
