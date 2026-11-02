package postgres

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/event"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/otel/propagation"
)

var ErrReceiptConflict = errors.New("event ID reused with different content")

func (s *Store) Quarantine(ctx context.Context, topic string, partition int, offset int64, key, value []byte, code string) error {
	if topic == "" || partition < 0 || offset < 0 || len(value) > 1<<20 || len(key) > 1<<20 {
		return errors.New("invalid or oversized dead-letter record")
	}
	switch code {
	case "invalid_avro", "invalid_event", "identity_conflict":
	default:
		return errors.New("invalid dead-letter failure code")
	}
	if key == nil {
		key = []byte{}
	}
	if value == nil {
		value = []byte{}
	}
	_, err := s.db.ExecContext(ctx, `INSERT INTO payment_event_dead_letters
		(topic, partition, record_offset, record_key, record_value, failure_code)
		VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (topic,partition,record_offset) DO NOTHING`,
		topic, partition, offset, key, value, code)
	return err
}

type Publication struct {
	Event       event.Initiated
	AccountID   string
	TraceParent string
	TraceState  string
}

type PublishResult struct {
	EventID  string
	State    string
	Attempts int
}

// RetryDelay caps both the exponent and delay, including for corrupted attempt counters.
func RetryDelay(attempt int) time.Duration {
	if attempt < 1 {
		return time.Second
	}
	if attempt > 6 {
		return time.Minute
	}
	return time.Second << (attempt - 1)
}

// RelayOne keeps the row locked through publish and acknowledgement. A process crash
// after broker acknowledgement rolls back the SQL transaction and permits redelivery.
func (s *Store) RelayOne(ctx context.Context, maxAttempts int, publish func(context.Context, Publication) error) (PublishResult, error) {
	if maxAttempts < 1 || maxAttempts > 100 {
		return PublishResult{}, errors.New("max publication attempts must be between 1 and 100")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return PublishResult{}, err
	}
	defer tx.Rollback()
	var data []byte
	var message Publication
	var result PublishResult
	err = tx.QueryRowContext(ctx, `SELECT o.event_id::text, o.account_id::text, o.event_data, o.attempts, o.traceparent, o.tracestate
		FROM payment_outbox o
		WHERE o.state = 'pending' AND o.available_at <= now()
		AND NOT EXISTS (SELECT 1 FROM payment_outbox earlier
			WHERE earlier.account_id = o.account_id AND earlier.sequence < o.sequence AND earlier.state <> 'published')
		ORDER BY o.sequence LIMIT 1 FOR UPDATE OF o SKIP LOCKED`).
		Scan(&result.EventID, &message.AccountID, &data, &result.Attempts, &message.TraceParent, &message.TraceState)
	if errors.Is(err, sql.ErrNoRows) {
		return PublishResult{}, nil
	}
	if err != nil {
		return PublishResult{}, err
	}
	if err := json.Unmarshal(data, &message.Event); err != nil {
		return PublishResult{}, fmt.Errorf("decode persisted outbox event: %w", err)
	}
	if message.Event.ID != result.EventID {
		return PublishResult{}, errors.New("persisted outbox event identity mismatch")
	}
	if err := message.Event.Validate(message.AccountID); err != nil {
		return PublishResult{}, err
	}
	result.Attempts++
	publishCtx := telemetry.Propagator.Extract(ctx, propagation.MapCarrier{
		"traceparent": message.TraceParent, "tracestate": message.TraceState,
	})
	publishErr := publish(publishCtx, message)
	if publishErr == nil {
		result.State = "published"
		_, err = tx.ExecContext(ctx, `UPDATE payment_outbox SET state = 'published', attempts = $2,
			published_at = now(), failure_code = NULL WHERE event_id = $1`, result.EventID, result.Attempts)
	} else {
		result.State = "pending"
		if result.Attempts >= maxAttempts {
			result.State = "dead"
		}
		_, err = tx.ExecContext(ctx, `UPDATE payment_outbox SET state = $2, attempts = $3,
			available_at = now() + $4 * interval '1 millisecond', failure_code = 'publication_failed'
			WHERE event_id = $1`, result.EventID, result.State, result.Attempts, RetryDelay(result.Attempts).Milliseconds())
	}
	if err != nil {
		return PublishResult{}, err
	}
	if err := tx.Commit(); err != nil {
		return PublishResult{}, err
	}
	return result, nil
}

// RecordReceipt makes the receipt the business effect; broker offsets must only be
// committed after this transaction commits. Duplicate content is a no-op.
func (s *Store) RecordReceipt(ctx context.Context, message Publication) (bool, error) {
	if err := message.Event.Validate(message.AccountID); err != nil {
		return false, err
	}
	data, err := json.Marshal(message.Event)
	if err != nil {
		return false, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `INSERT INTO payment_event_receipts (event_id, payment_id, account_id, event_data)
		VALUES ($1, $2, $3, $4) ON CONFLICT (event_id) DO NOTHING`,
		message.Event.ID, message.Event.Payload.PaymentID, message.AccountID, data)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return false, err
	}
	if count == 0 {
		var matches bool
		if err := tx.QueryRowContext(ctx, `SELECT account_id = $2::uuid AND event_data = $3::jsonb
			FROM payment_event_receipts WHERE event_id = $1`, message.Event.ID, message.AccountID, data).Scan(&matches); err != nil {
			return false, err
		}
		if !matches {
			return false, ErrReceiptConflict
		}
	}
	if err := tx.Commit(); err != nil {
		return false, err
	}
	return count == 1, nil
}
