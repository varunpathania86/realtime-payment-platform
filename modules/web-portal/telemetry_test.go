package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestTelemetryProxyLimitsAndHeaderIsolation(t *testing.T) {
	forwarded := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded++
		if r.URL.Path != "/v1/traces" || r.Header.Get("Cookie") != "" || r.Header.Get("Authorization") != "" {
			t.Error("unsafe forwarding")
		}
		body, _ := io.ReadAll(r.Body)
		if string(body) != `{"resourceSpans":[]}` {
			t.Errorf("unexpected payload %s", body)
		}
		w.WriteHeader(200)
	}))
	defer upstream.Close()
	proxy := &telemetryProxy{endpoint: upstream.URL, client: upstream.Client()}
	for _, test := range []struct {
		origin, body string
		status       int
	}{
		{"http://portal.test", `{"resourceSpans":[]}`, 204},
		{"http://foreign.test", `{}`, 403},
		{"", `{}`, 403},
		{"http://portal.test", strings.Repeat("x", 65537), 413},
		{"http://portal.test", `not-json`, 400},
	} {
		request := httptest.NewRequest("POST", "http://portal.test/telemetry/v1/traces", strings.NewReader(test.body))
		request.Header.Set("Origin", test.origin)
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Cookie", "not-forwarded")
		response := httptest.NewRecorder()
		proxy.serve(response, request)
		if response.Code != test.status {
			t.Fatalf("got %d want %d", response.Code, test.status)
		}
	}
	if forwarded != 1 {
		t.Fatal("invalid request reached collector")
	}
	for i := 0; i < 6; i++ {
		request := httptest.NewRequest("POST", "http://portal.test/telemetry/v1/traces", strings.NewReader(`{"resourceSpans":[]}`))
		request.Header.Set("Origin", "http://portal.test")
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		proxy.serve(response, request)
		if i == 5 && response.Code != 429 {
			t.Fatal("missing rate limit")
		}
	}
}

func TestTelemetryDisabledAndUnsafeConfiguration(t *testing.T) {
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
	response := httptest.NewRecorder()
	(&telemetryProxy{}).serve(response, httptest.NewRequest("GET", "/telemetry/config", nil))
	if !strings.Contains(response.Body.String(), `"enabled":false`) {
		t.Fatal("telemetry should be opt-in")
	}
	for _, endpoint := range []string{"ftp://collector", "http://collector?token=private", "http://collector/path"} {
		t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
		if _, err := configureTelemetry(context.Background()); err == nil {
			t.Fatal("unsafe endpoint accepted")
		}
	}
}
