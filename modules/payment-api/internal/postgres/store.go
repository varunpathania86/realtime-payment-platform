package postgres

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"database/sql/driver"
	"embed"
	"errors"
	"fmt"
	"time"

	"github.com/XSAM/otelsql"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

//go:embed migrations/*.sql
var migrations embed.FS

var migrationNames = []string{"001_payments.sql", "002_outbox.sql", "003_event_dead_letters.sql", "004_outbox_write_guard.sql", "005_outbox_trace_context.sql"}

func migrationSQL(name string) (string, error) {
	data, err := migrations.ReadFile("migrations/" + name)
	return string(data), err
}

type Store struct{ db *sql.DB }

func Open(url string) (*Store, error) {
	if url == "" {
		return nil, errors.New("DATABASE_URL is required")
	}
	db, err := otelsql.Open("pgx", url,
		otelsql.WithAttributes(attribute.String("db.system.name", "postgresql")),
		otelsql.WithSpanOptions(otelsql.SpanOptions{
			DisableQuery: true, DisableErrSkip: true, OmitRows: true,
			OmitConnResetSession: true,
			RecordError:          func(error) bool { return false },
			SpanFilter: func(ctx context.Context, _ otelsql.Method, _ string, _ []driver.NamedValue) bool {
				return trace.SpanContextFromContext(ctx).IsValid()
			},
		}))
	if err != nil {
		return nil, errors.New("invalid DATABASE_URL")
	}
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)
	db.SetConnMaxIdleTime(5 * time.Minute)
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Migrate(ctx context.Context) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin migration: %w", err)
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock(72617001)`); err != nil {
		return fmt.Errorf("lock migrations: %w", err)
	}
	if _, err = tx.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (
		version integer PRIMARY KEY, checksum text NOT NULL)`); err != nil {
		return fmt.Errorf("create migration ledger: %w", err)
	}
	for index, name := range migrationNames {
		sqlText, err := migrationSQL(name)
		if err != nil {
			return err
		}
		version := index + 1
		checksum := fmt.Sprintf("%x", sha256.Sum256([]byte(sqlText)))
		var recorded string
		err = tx.QueryRowContext(ctx, `SELECT checksum FROM schema_migrations WHERE version = $1`, version).Scan(&recorded)
		switch {
		case errors.Is(err, sql.ErrNoRows):
			if _, err = tx.ExecContext(ctx, sqlText); err != nil {
				return fmt.Errorf("apply migration %d: %w", version, err)
			}
			if _, err = tx.ExecContext(ctx, `INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)`, version, checksum); err != nil {
				return fmt.Errorf("record migration %d: %w", version, err)
			}
		case err != nil:
			return fmt.Errorf("read migration ledger: %w", err)
		case recorded != checksum:
			return fmt.Errorf("migration %d checksum changed; restore the applied migration", version)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit migrations: %w", err)
	}
	return nil
}

func (s *Store) Ready(ctx context.Context) error {
	for index, name := range migrationNames {
		sqlText, err := migrationSQL(name)
		if err != nil {
			return err
		}
		var checksum string
		if err := s.db.QueryRowContext(ctx, `SELECT checksum FROM schema_migrations WHERE version = $1`, index+1).Scan(&checksum); err != nil {
			return err
		}
		if checksum != fmt.Sprintf("%x", sha256.Sum256([]byte(sqlText))) {
			return errors.New("database migration checksum mismatch")
		}
	}
	return nil
}

const columns = `id::text, account_id::text, amount_minor, currency, status, created_at`

func scan(row *sql.Row) (payment.Payment, error) {
	var p payment.Payment
	err := row.Scan(&p.ID, &p.AccountID, &p.AmountMinor, &p.Currency, &p.Status, &p.CreatedAt)
	p.CreatedAt = p.CreatedAt.UTC()
	return p, err
}

func (s *Store) Create(ctx context.Context, key string, command payment.Command) (payment.Payment, error) {
	if err := payment.Validate(key, command); err != nil {
		return payment.Payment{}, err
	}
	id, err := payment.NewID()
	if err != nil {
		return payment.Payment{}, err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return payment.Payment{}, err
	}
	defer tx.Rollback()
	p, err := scan(tx.QueryRowContext(ctx, `INSERT INTO payments
		(id, account_id, amount_minor, currency, status, idempotency_key)
		VALUES ($1, $2, $3, $4, 'initiated', $5)
		ON CONFLICT (idempotency_key) DO NOTHING RETURNING `+columns,
		id, command.AccountID, command.AmountMinor, command.Currency, key))
	if errors.Is(err, sql.ErrNoRows) {
		// A conflicting INSERT waits for the winner to commit. A fresh READ COMMITTED
		// statement can then see that row, including when both requests arrived together.
		p, err = scan(tx.QueryRowContext(ctx, `SELECT `+columns+` FROM payments WHERE idempotency_key = $1`, key))
		if err == nil && p.Command != command {
			return payment.Payment{}, payment.ErrConflict
		}
	}
	if err != nil {
		return payment.Payment{}, err
	}
	if p.ID == id {
		carrier := propagation.MapCarrier{}
		telemetry.Propagator.Inject(ctx, carrier)
		if _, err := tx.ExecContext(ctx, `UPDATE payment_outbox SET traceparent = $2, tracestate = $3 WHERE payment_id = $1`,
			p.ID, carrier.Get("traceparent"), carrier.Get("tracestate")); err != nil {
			return payment.Payment{}, err
		}
	}
	if err := tx.Commit(); err != nil {
		return payment.Payment{}, err
	}
	return p, nil
}

func (s *Store) Get(ctx context.Context, id string) (payment.Payment, error) {
	p, err := scan(s.db.QueryRowContext(ctx, `SELECT `+columns+` FROM payments WHERE id = $1`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return payment.Payment{}, payment.ErrNotFound
	}
	return p, err
}
