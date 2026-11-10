package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/httpapi"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/postgres"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/stream"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
)

var (
	version = "dev"
	commit  = "unknown"
)

func newServer(addr string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    1 << 20,
	}
}

func run(ctx context.Context, server *http.Server) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdown); err != nil {
			slog.Error("graceful_shutdown_failed", "error", err.Error())
			_ = server.Close()
		}
	}()
	err := server.ListenAndServe()
	if !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	<-stopped
	return nil
}

func main() {
	slog.SetDefault(slog.New(telemetry.CorrelationHandler{Handler: slog.NewJSONHandler(os.Stdout, nil)}))
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := execute(ctx); err != nil {
		slog.Error("application_failed", "error", err.Error())
		os.Exit(1)
	}
}

func execute(ctx context.Context) error {
	mode := ""
	if len(os.Args) == 2 {
		mode = os.Args[1]
	}
	if len(os.Args) > 2 || (mode != "" && mode != "migrate" && mode != "relay" && mode != "consumer") {
		return errors.New("usage: payment-api [migrate|relay|consumer]")
	}
	service := "payment-api"
	if mode != "" {
		service += "-" + mode
	}
	slog.SetDefault(slog.Default().With("service.name", service, "service.version", version))
	shutdown, err := telemetry.Configure(ctx, service, version)
	if err != nil {
		return err
	}
	defer func() {
		stop, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if err := shutdown(stop); err != nil {
			slog.Warn("telemetry_shutdown_failed", "error_type", "dependency_unavailable")
		}
	}()
	store, err := postgres.Open(os.Getenv("DATABASE_URL"))
	if err != nil {
		return err
	}
	defer store.Close()
	if mode == "migrate" {
		migrationCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		if err := store.Migrate(migrationCtx); err != nil {
			// Database errors can include connection details. Never log the DSN.
			return errors.New("database migration failed; check connectivity, credentials, and migration history")
		}
		slog.Info("migrations_applied")
		return nil
	}
	if mode == "relay" || mode == "consumer" {
		return runWorker(ctx, store, mode)
	}
	addr := os.Getenv("HTTP_ADDR")
	if addr == "" {
		addr = ":8080"
	}

	slog.Info("server_listening", "address", addr)
	handler, err := paymentIdentity(httpapi.New(store, slog.Default(), version, commit), os.Getenv)
	if err != nil {
		return err
	}
	return run(ctx, newServer(addr, handler))
}

func runWorker(ctx context.Context, store *postgres.Store, mode string) error {
	k, err := stream.NewKafka(os.Getenv("KAFKA_BROKERS"), os.Getenv("KAFKA_CA_FILE"), os.Getenv("KAFKA_USERNAME"), os.Getenv("KAFKA_PASSWORD"))
	if err != nil {
		return err
	}
	defer k.Close()
	if raw := os.Getenv("KAFKA_REPLICATION_FACTOR"); raw != "" {
		factor, err := strconv.Atoi(raw)
		if err != nil {
			return errors.New("invalid KAFKA_REPLICATION_FACTOR")
		}
		if err := k.SetReplicationFactor(factor); err != nil {
			return err
		}
	}
	registry, err := stream.NewRegistry(os.Getenv("REGISTRY_URL"))
	if err != nil {
		return err
	}
	topic := os.Getenv("PAYMENT_TOPIC")
	if topic == "" {
		return errors.New("PAYMENT_TOPIC is required for workers")
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	backlogStopped := make(chan struct{})
	go func() {
		defer close(backlogStopped)
		if mode == "relay" {
			store.ObserveBacklog(ctx)
		}
	}()
	defer func() {
		cancel()
		<-backlogStopped
	}()
	finished := make(chan error, 1)
	go func() {
		if mode == "relay" {
			finished <- stream.RunRelay(ctx, store, k, registry, topic, os.Getenv("EVENT_SCHEMA_FILE"))
		} else {
			finished <- stream.RunConsumer(ctx, store, k, registry, topic)
		}
		cancel()
	}()
	mux := http.NewServeMux()
	mux.Handle("GET /metrics", telemetry.Metrics())
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		probe, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		if err := store.Ready(probe); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	addr := os.Getenv("HTTP_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	serverErr := run(ctx, newServer(addr, mux))
	cancel()
	workerErr := <-finished
	if serverErr != nil {
		return serverErr
	}
	if errors.Is(workerErr, context.Canceled) && ctx.Err() != nil {
		return nil
	}
	return workerErr
}
