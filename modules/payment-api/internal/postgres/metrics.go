package postgres

import (
	"context"
	"log/slog"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/telemetry"
)

func (s *Store) ObserveBacklog(ctx context.Context) {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		probe, cancel := context.WithTimeout(ctx, 2*time.Second)
		count, age, err := s.backlogSnapshot(probe)
		cancel()
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			telemetry.BacklogErrors.Inc()
			slog.Warn("outbox_metrics_failed", "error_type", "dependency_unavailable")
		} else {
			telemetry.Backlog.Set(count)
			telemetry.BacklogAge.Set(age)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *Store) backlogSnapshot(ctx context.Context) (float64, float64, error) {
	var count, age float64
	err := s.db.QueryRowContext(ctx, `SELECT count(*),
		COALESCE(EXTRACT(EPOCH FROM now() - min(p.created_at)), 0)
		FROM payment_outbox o JOIN payments p ON p.id = o.payment_id WHERE o.state = 'pending'`).
		Scan(&count, &age)
	return count, age, err
}
