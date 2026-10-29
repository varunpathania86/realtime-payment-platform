package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/httpapi"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/postgres"
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
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := execute(ctx); err != nil {
		slog.Error("application_failed", "error", err.Error())
		os.Exit(1)
	}
}

func execute(ctx context.Context) error {
	if len(os.Args) > 2 || (len(os.Args) == 2 && os.Args[1] != "migrate") {
		return errors.New("usage: payment-api [migrate]")
	}
	store, err := postgres.Open(os.Getenv("DATABASE_URL"))
	if err != nil {
		return err
	}
	defer store.Close()
	if len(os.Args) == 2 {
		migrationCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		if err := store.Migrate(migrationCtx); err != nil {
			// Database errors can include connection details. Never log the DSN.
			return errors.New("database migration failed; check connectivity, credentials, and migration history")
		}
		slog.Info("migrations_applied")
		return nil
	}
	addr := os.Getenv("HTTP_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	slog.Info("server_listening", "address", addr)
	return run(ctx, newServer(addr, httpapi.New(store, slog.Default(), version, commit)))
}
