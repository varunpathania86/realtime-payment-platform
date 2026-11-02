package telemetry

import (
	"context"
	"net/http"
	"strconv"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel/trace"
)

var Registry = prometheus.NewRegistry()
var HTTPRequests = prometheus.NewCounterVec(prometheus.CounterOpts{
	Name: "rpp_http_requests_total", Help: "HTTP requests by bounded method, route and status.",
}, []string{"method", "route", "status"})
var HTTPDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
	Name: "rpp_http_request_duration_seconds", Help: "HTTP request duration.",
	Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2, 5},
}, []string{"method", "route"})
var PublishDuration = prometheus.NewHistogramVec(prometheus.HistogramOpts{
	Name: "rpp_kafka_publish_duration_seconds", Help: "Kafka synchronous publication duration.",
}, []string{"outcome"})
var Receipts = prometheus.NewCounterVec(prometheus.CounterOpts{
	Name: "rpp_receipts_total", Help: "Durable consumer effects and deduplicated replays.",
}, []string{"outcome"})
var Backlog = prometheus.NewGauge(prometheus.GaugeOpts{
	Name: "rpp_outbox_pending", Help: "Pending outbox rows, including delayed retries.",
})
var BacklogAge = prometheus.NewGauge(prometheus.GaugeOpts{
	Name: "rpp_outbox_oldest_pending_age_seconds", Help: "Age of oldest pending outbox row.",
})
var BacklogErrors = prometheus.NewCounter(prometheus.CounterOpts{
	Name: "rpp_outbox_metrics_errors_total", Help: "Failed outbox backlog observations.",
})

func init() {
	Registry.MustRegister(HTTPRequests, HTTPDuration, PublishDuration, Receipts, Backlog, BacklogAge, BacklogErrors)
}

func Metrics() http.Handler {
	return promhttp.HandlerFor(Registry, promhttp.HandlerOpts{EnableOpenMetrics: true})
}

func Observe(ctx context.Context, histogram prometheus.Observer, seconds float64) {
	sc := trace.SpanContextFromContext(ctx)
	if observer, ok := histogram.(prometheus.ExemplarObserver); ok && sc.IsSampled() && sc.IsValid() {
		observer.ObserveWithExemplar(seconds, prometheus.Labels{"trace_id": sc.TraceID().String()})
		return
	}
	histogram.Observe(seconds)
}

func RecordHTTP(ctx context.Context, method, route string, status int, seconds float64) {
	HTTPRequests.WithLabelValues(method, route, strconv.Itoa(status)).Inc()
	Observe(ctx, HTTPDuration.WithLabelValues(method, route), seconds)
}
