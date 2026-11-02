//go:build integration

package postgres

import (
	"context"
	"errors"
	"testing"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

func testTraceContext(t *testing.T, ctx context.Context, store *Store) {
	t.Helper()
	origin := telemetry.Propagator.Extract(ctx, propagation.MapCarrier{
		"traceparent": "00-01000000000000000000000000000000-0200000000000000-01",
		"tracestate":  "rpp=synthetic",
	})
	command := payment.Command{AccountID: "00000000-0000-4000-8000-000000000004", AmountMinor: 100, Currency: "USD"}
	p, err := store.Create(origin, "traced-payment", command)
	if err != nil {
		t.Fatal(err)
	}
	other := telemetry.Propagator.Extract(ctx, propagation.MapCarrier{
		"traceparent": "00-03000000000000000000000000000000-0400000000000000-01",
	})
	replay, err := store.Create(other, "traced-payment", command)
	if err != nil || replay.ID != p.ID {
		t.Fatalf("idempotent retry: %v", err)
	}
	var persistedParent, persistedState string
	if err := store.db.QueryRowContext(ctx, `SELECT traceparent, tracestate FROM payment_outbox WHERE payment_id = $1`,
		p.ID).Scan(&persistedParent, &persistedState); err != nil {
		t.Fatal(err)
	}
	if persistedParent != "00-01000000000000000000000000000000-0200000000000000-01" || persistedState != "rpp=synthetic" {
		t.Fatal("retry replaced the original outbox trace context")
	}
	found := false
	for range 3 {
		_, err := store.RelayOne(ctx, 3, func(publishCtx context.Context, message Publication) error {
			if message.Event.Payload.PaymentID == p.ID {
				found = true
				got := trace.SpanContextFromContext(publishCtx)
				if got.TraceID().String() != "01000000000000000000000000000000" || !got.IsRemote() {
					t.Fatal("outbox relay lost the persisted originating context")
				}
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
		if found {
			break
		}
	}
	if !found {
		t.Fatal("traced outbox event was not published")
	}
}

func testOutbox(t *testing.T, ctx context.Context, store *Store, first payment.Payment) {
	t.Helper()
	pending, age, err := store.backlogSnapshot(ctx)
	if err != nil || pending != 2 || age < 0 {
		t.Fatalf("backlog observation: count=%v age=%v error=%v", pending, age, err)
	}
	var count int
	if err := store.db.QueryRowContext(ctx, `SELECT count(*) FROM payment_outbox`).Scan(&count); err != nil || count != 2 {
		t.Fatalf("expected one legacy backfill and one new payment event despite 12 concurrent retries: %d %v", count, err)
	}
	if _, err := store.db.ExecContext(ctx, `ALTER TABLE payment_outbox ADD CONSTRAINT test_insert_failure
		CHECK ((event_data->'payload'->>'amount_minor')::bigint <> 99999) NOT VALID`); err != nil {
		t.Fatal(err)
	}
	rejected := first.Command
	rejected.AmountMinor = 99999
	if _, err := store.Create(ctx, "rollback-key", rejected); err == nil {
		t.Fatal("outbox failure was hidden")
	}
	if err := store.db.QueryRowContext(ctx, `SELECT count(*) FROM payments WHERE idempotency_key = 'rollback-key'`).Scan(&count); err != nil || count != 0 {
		t.Fatalf("payment committed without its outbox: %d %v", count, err)
	}
	if _, err := store.db.ExecContext(ctx, `ALTER TABLE payment_outbox DROP CONSTRAINT test_insert_failure`); err != nil {
		t.Fatal(err)
	}
	// Publish the backfilled event first, then exercise the new payment's crash window.
	legacy, err := store.RelayOne(ctx, 3, func(ctx context.Context, message Publication) error {
		_, err := store.RecordReceipt(ctx, message)
		return err
	})
	if err != nil || legacy.State != "published" {
		t.Fatalf("legacy publication: %+v %v", legacy, err)
	}
	crashCtx, crash := context.WithCancel(ctx)
	var delivered Publication
	_, err = store.RelayOne(crashCtx, 3, func(ctx context.Context, message Publication) error {
		delivered = message
		if message.Event.Payload.PaymentID != first.ID {
			t.Fatalf("wrong event: %+v", message)
		}
		inserted, err := store.RecordReceipt(ctx, message)
		if err != nil || !inserted {
			t.Fatalf("first consumer effect: %v %v", inserted, err)
		}
		crash()
		return nil
	})
	if err == nil {
		t.Fatal("cancelled publication acknowledgement committed")
	}
	result, err := store.RelayOne(ctx, 3, func(ctx context.Context, message Publication) error {
		if message != delivered {
			t.Fatal("redelivery changed stable event identity/content")
		}
		inserted, err := store.RecordReceipt(ctx, message)
		if err != nil || inserted {
			t.Fatalf("duplicate consumer effect: %v %v", inserted, err)
		}
		return nil
	})
	if err != nil || result.State != "published" || result.Attempts != 1 {
		t.Fatalf("crash recovery: %+v %v", result, err)
	}
	if err := store.db.QueryRowContext(ctx, `SELECT count(*) FROM payment_event_receipts WHERE payment_id = $1`, first.ID).Scan(&count); err != nil || count != 1 {
		t.Fatalf("wanted exactly one consumer effect after redelivery: %d %v", count, err)
	}
	changed := delivered
	changed.Event.Payload.AmountMinor++
	if _, err := store.RecordReceipt(ctx, changed); !errors.Is(err, ErrReceiptConflict) {
		t.Fatalf("same event identity accepted changed content: %v", err)
	}
	second, err := store.Create(ctx, "next-payment", first.Command)
	if err != nil {
		t.Fatal(err)
	}
	third, err := store.Create(ctx, "third-payment", first.Command)
	if err != nil {
		t.Fatal(err)
	}
	otherCommand := first.Command
	otherCommand.AccountID = "00000000-0000-4000-8000-000000000003"
	other, err := store.Create(ctx, "other-account", otherCommand)
	if err != nil {
		t.Fatal(err)
	}
	result, err = store.RelayOne(ctx, 2, func(_ context.Context, message Publication) error {
		if message.Event.Payload.PaymentID != second.ID {
			t.Fatal("account order changed")
		}
		return errors.New("broker unavailable")
	})
	if err != nil || result.State != "pending" || result.Attempts != 1 {
		t.Fatalf("retry was not persisted: %+v %v", result, err)
	}
	result, err = store.RelayOne(ctx, 2, func(_ context.Context, message Publication) error {
		if message.Event.Payload.PaymentID != other.ID {
			t.Fatal("blocked account overtook retry, or other account starved")
		}
		return nil
	})
	if err != nil || result.State != "published" {
		t.Fatalf("independent account: %+v %v", result, err)
	}
	if _, err := store.db.ExecContext(ctx, `UPDATE payment_outbox SET available_at = now() WHERE payment_id = $1`, second.ID); err != nil {
		t.Fatal(err)
	}
	result, err = store.RelayOne(ctx, 2, func(context.Context, Publication) error { return errors.New("still unavailable") })
	if err != nil || result.State != "dead" || result.Attempts != 2 {
		t.Fatalf("retry budget did not stop: %+v %v", result, err)
	}
	result, err = store.RelayOne(ctx, 2, func(context.Context, Publication) error {
		t.Fatal("dead event did not block later account event")
		return nil
	})
	if err != nil || result.EventID != "" {
		t.Fatalf("expected no work: %+v %v", result, err)
	}
	if err := store.db.QueryRowContext(ctx, `SELECT count(*) FROM payment_outbox WHERE payment_id = $1 AND state = 'pending' AND attempts = 0`, third.ID).Scan(&count); err != nil || count != 1 {
		t.Fatalf("later event was lost or prematurely published: %d %v", count, err)
	}
	for range 2 {
		if err := store.Quarantine(ctx, "payments.initiated.v1", 0, 42, []byte(first.AccountID), []byte("malformed"), "invalid_avro"); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.db.QueryRowContext(ctx, `SELECT count(*) FROM payment_event_dead_letters`).Scan(&count); err != nil || count != 1 {
		t.Fatalf("dead-letter replay duplicated quarantine: %d %v", count, err)
	}
	if err := store.Quarantine(ctx, "payments.initiated.v1", 0, 43, nil, nil, "arbitrary"); err == nil {
		t.Fatal("accepted invalid quarantine failure code")
	}
	if _, err := store.db.ExecContext(ctx, `INSERT INTO payments (id,account_id,amount_minor,currency,status,idempotency_key)
		VALUES ('00000000-0000-4000-8000-000000000099','00000000-0000-4000-8000-000000000003',100,'USD','initiated','old-api-writer')`); err != nil {
		t.Fatal(err)
	}
	if err := store.db.QueryRowContext(ctx, `SELECT count(*) FROM payment_outbox WHERE payment_id = '00000000-0000-4000-8000-000000000099'`).Scan(&count); err != nil || count != 1 {
		t.Fatalf("old API writer bypassed atomic outbox guard: %d %v", count, err)
	}
}
