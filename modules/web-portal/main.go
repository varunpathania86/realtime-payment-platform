package main

import (
	"bytes"
	"context"
	"errors"
	"io/fs"
	"log/slog"
	"mime"
	"net/http"
	"os"
	"os/signal"
	"path"
	"strings"
	"syscall"
	"time"
)

var version = "dev"

func handler(files fs.FS, app http.Handler) http.Handler {
	proxy := &telemetryProxy{endpoint: strings.TrimRight(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"), "/"),
		client: &http.Client{Timeout: 2 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}}}
	return instrumentPortal(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/telemetry/") {
			proxy.serve(w, r)
			return
		}
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Cache-Control", "no-store")
		if r.URL.Path == "/healthz" || r.URL.Path == "/readyz" {
			if r.Method != http.MethodGet {
				w.Header().Set("Allow", "GET")
				http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte("{\"status\":\"ok\"}\n"))
			return
		}
		if strings.HasPrefix(r.URL.Path, "/auth/") || strings.HasPrefix(r.URL.Path, "/bi/") {
			if app == nil {
				http.NotFound(w, r)
				return
			}
			app.ServeHTTP(w, r)
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		// Never turn a misrouted API request into a successful HTML response.
		if r.URL.Path == "/api" || strings.HasPrefix(r.URL.Path, "/api/") {
			http.NotFound(w, r)
			return
		}
		name := strings.TrimPrefix(path.Clean("/"+r.URL.Path), "/")
		if name == "" {
			name = "index.html"
		}
		content, err := fs.ReadFile(files, name)
		if err != nil && path.Ext(name) == "" && name != "assets" && !strings.HasPrefix(name, "assets/") {
			name = "index.html"
			content, err = fs.ReadFile(files, name)
		}
		if err != nil {
			http.NotFound(w, r)
			return
		}
		if contentType := mime.TypeByExtension(path.Ext(name)); contentType != "" {
			w.Header().Set("Content-Type", contentType)
		}
		http.ServeContent(w, r, name, time.Time{}, bytes.NewReader(content))
	}))
}

func newServer(addr string, files fs.FS, app http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           handler(files, app),
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
			slog.Error("graceful_shutdown_failed")
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
	slog.SetDefault(slog.New(correlationHandler{slog.NewJSONHandler(os.Stdout, nil)}).
		With("service.name", "web-portal", "service.version", version))
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	shutdown, err := configureTelemetry(ctx)
	if err != nil {
		slog.Error("telemetry_configuration_failed")
		os.Exit(1)
	}
	defer func() {
		ctx, stop := context.WithTimeout(context.Background(), 3*time.Second)
		defer stop()
		if err := shutdown(ctx); err != nil {
			slog.Warn("telemetry_shutdown_failed")
		}
	}()
	addr := os.Getenv("HTTP_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	dir := os.Getenv("STATIC_DIR")
	if dir == "" {
		dir = "dist"
	}
	files := os.DirFS(dir)
	if _, err := fs.Stat(files, "index.html"); err != nil {
		slog.Error("static_build_unavailable", "hint", "run make build")
		os.Exit(1)
	}
	app, err := portalFromEnvironment()
	if err != nil {
		slog.Error("portal_configuration_failed", "error", err.Error())
		os.Exit(1)
	}
	var routes http.Handler
	if app != nil {
		routes = app
	}
	slog.Info("server_listening", "service.name", "web-portal", "address", addr)
	if err := run(ctx, newServer(addr, files, routes)); err != nil {
		slog.Error("server_failed")
		os.Exit(1)
	}
}
