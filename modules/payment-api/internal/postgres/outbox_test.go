package postgres

import (
	"testing"
	"time"
)

func TestRetryDelay(t *testing.T) {
	for attempt, want := range map[int]time.Duration{
		-1: time.Second, 0: time.Second, 1: time.Second, 2: 2 * time.Second,
		6: 32 * time.Second, 7: time.Minute, 1000000: time.Minute,
	} {
		if got := RetryDelay(attempt); got != want {
			t.Fatalf("attempt %d: got %s want %s", attempt, got, want)
		}
	}
}
