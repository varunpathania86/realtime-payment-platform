package postgres

import (
	"context"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

//go:embed migrations/001_payments.sql
var migration string

type Store struct{ db *sql.DB }

func Open(url string) (*Store, error) {
	if url == "" {
		return nil, errors.New("DATABASE_URL is required")
	}
	db, err := sql.Open("pgx", url)
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
	checksum := fmt.Sprintf("%x", sha256.Sum256([]byte(migration)))
	var recorded string
	err = tx.QueryRowContext(ctx, `SELECT checksum FROM schema_migrations WHERE version = 1`).Scan(&recorded)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		if _, err = tx.ExecContext(ctx, migration); err != nil {
			return fmt.Errorf("apply migration 1: %w", err)
		}
		if _, err = tx.ExecContext(ctx, `INSERT INTO schema_migrations (version, checksum) VALUES (1, $1)`, checksum); err != nil {
			return fmt.Errorf("record migration 1: %w", err)
		}
	case err != nil:
		return fmt.Errorf("read migration ledger: %w", err)
	case recorded != checksum:
		return errors.New("migration 1 checksum changed; restore the applied migration")
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit migrations: %w", err)
	}
	return nil
}

func (s *Store) Ready(ctx context.Context) error {
	var checksum string
	if err := s.db.QueryRowContext(ctx, `SELECT checksum FROM schema_migrations WHERE version = 1`).Scan(&checksum); err != nil {
		return err
	}
	if checksum != fmt.Sprintf("%x", sha256.Sum256([]byte(migration))) {
		return errors.New("database migration checksum mismatch")
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
	p, err := scan(s.db.QueryRowContext(ctx, `INSERT INTO payments
		(id, account_id, amount_minor, currency, status, idempotency_key)
		VALUES ($1, $2, $3, $4, 'initiated', $5)
		ON CONFLICT (idempotency_key) DO NOTHING RETURNING `+columns,
		id, command.AccountID, command.AmountMinor, command.Currency, key))
	if errors.Is(err, sql.ErrNoRows) {
		// A conflicting INSERT waits for the winner to commit. A fresh READ COMMITTED
		// statement can then see that row, including when both requests arrived together.
		p, err = scan(s.db.QueryRowContext(ctx, `SELECT `+columns+` FROM payments WHERE idempotency_key = $1`, key))
		if err == nil && p.Command != command {
			return payment.Payment{}, payment.ErrConflict
		}
	}
	return p, err
}

func (s *Store) Get(ctx context.Context, id string) (payment.Payment, error) {
	p, err := scan(s.db.QueryRowContext(ctx, `SELECT `+columns+` FROM payments WHERE id = $1`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return payment.Payment{}, payment.ErrNotFound
	}
	return p, err
}
