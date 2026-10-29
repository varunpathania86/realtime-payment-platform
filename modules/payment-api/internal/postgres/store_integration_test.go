//go:build integration

package postgres

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/httpapi"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

func TestPostgres(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	pins, err := os.ReadFile("../../../../.tool-versions")
	if err != nil {
		t.Fatal(err)
	}
	var image string
	for _, line := range strings.Split(string(pins), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && fields[0] == "postgres" {
			image = "postgres:" + fields[1]
		}
	}
	if image == "" {
		t.Fatal("postgres image must be pinned in .tool-versions")
	}
	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image: image, ExposedPorts: []string{"5432/tcp"},
			Env:        map[string]string{"POSTGRES_USER": "synthetic", "POSTGRES_PASSWORD": "integration-only", "POSTGRES_DB": "payments"},
			WaitingFor: wait.ForLog("database system is ready to accept connections").WithOccurrence(2).WithStartupTimeout(time.Minute),
		},
		Started: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := container.Terminate(cleanup); err != nil {
			t.Errorf("terminate test PostgreSQL: %v", err)
		}
	})
	host, err := container.Host(ctx)
	if err != nil {
		t.Fatal(err)
	}
	port, err := container.MappedPort(ctx, "5432/tcp")
	if err != nil {
		t.Fatal(err)
	}
	store, err := Open(fmt.Sprintf("postgres://synthetic:integration-only@%s:%s/payments?sslmode=disable", host, port.Port()))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if store.Ready(ctx) == nil {
		t.Fatal("unmigrated database was ready")
	}
	for range 2 {
		if err := store.Migrate(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.Ready(ctx); err != nil {
		t.Fatal(err)
	}
	command := payment.Command{AccountID: "12345678-1234-1234-1234-123456789abc", AmountMinor: 12345, Currency: "USD"}
	results := make(chan payment.Payment, 12)
	errorsFound := make(chan error, 12)
	var workers sync.WaitGroup
	for range 12 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			p, err := store.Create(ctx, "concurrent-key", command)
			if err != nil {
				errorsFound <- err
				return
			}
			results <- p
		}()
	}
	workers.Wait()
	close(results)
	close(errorsFound)
	for err := range errorsFound {
		t.Fatal(err)
	}
	var first payment.Payment
	for p := range results {
		if first.ID == "" {
			first = p
		}
		if p.ID != first.ID || !p.CreatedAt.Equal(first.CreatedAt) || p.Command != command || p.Status != "initiated" {
			t.Fatalf("concurrent replay changed payment: %+v", p)
		}
	}
	var count int
	if err := store.db.QueryRowContext(ctx, "SELECT count(*) FROM payments").Scan(&count); err != nil || count != 1 {
		t.Fatalf("expected one persisted payment: %d %v", count, err)
	}
	read, err := store.Get(ctx, first.ID)
	if err != nil || read.ID != first.ID {
		t.Fatalf("read: %+v %v", read, err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if replay, err := store.Create(ctx, "concurrent-key", command); err != nil || replay.ID != first.ID {
		t.Fatalf("migration rerun lost persisted data: %+v %v", replay, err)
	}
	command.AmountMinor++
	if _, err := store.Create(ctx, "concurrent-key", command); !errors.Is(err, payment.ErrConflict) {
		t.Fatalf("wanted conflict, got %v", err)
	}
	if _, err := store.Get(ctx, "00000000-0000-0000-0000-000000000000"); !errors.Is(err, payment.ErrNotFound) {
		t.Fatalf("wanted not found, got %v", err)
	}
	if _, err := store.db.ExecContext(ctx, "UPDATE schema_migrations SET checksum = 'changed'"); err != nil {
		t.Fatal(err)
	}
	if store.Migrate(ctx) == nil || store.Ready(ctx) == nil {
		t.Fatal("changed migration checksum was accepted")
	}
	api := httpapi.New(store, slog.New(slog.NewJSONHandler(io.Discard, nil)), "integration", "test")
	if err := container.Stop(ctx, nil); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/healthz", "/readyz", "/payments/" + first.ID} {
		start := time.Now()
		w := httptest.NewRecorder()
		api.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		want := 503
		if path == "/healthz" {
			want = 200
		}
		if w.Code != want || time.Since(start) > 3*time.Second {
			t.Fatalf("unbounded/incorrect outage behavior: %s %d", path, w.Code)
		}
	}
}
