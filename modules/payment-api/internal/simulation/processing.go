// Package simulation creates synthetic processing outcomes from payment initiations.
package simulation

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/event"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

// Processing is the JSON contract consumed by the Flink processing jobs.
type Processing struct {
	EventID          string `json:"event_id"`
	EventType        string `json:"event_type"`
	SchemaVersion    int    `json:"schema_version"`
	EventTime        string `json:"event_time"`
	PaymentID        string `json:"payment_id"`
	AccountID        string `json:"account_id"`
	AmountMinor      int64  `json:"amount_minor"`
	Currency         string `json:"currency"`
	Country          string `json:"country"`
	PaymentMethod    string `json:"payment_method"`
	Outcome          string `json:"outcome"`
	AccountCreatedAt string `json:"account_created_at,omitempty"`
	DeviceID         string `json:"device_id,omitempty"`
}

// FromInitiation makes a reproducible fixture outcome, not a payment decision.
func FromInitiation(source event.Initiated, accountID string) (Processing, error) {
	if err := source.Validate(accountID); err != nil {
		return Processing{}, fmt.Errorf("invalid initiation: %w", err)
	}
	eventTime := source.Time
	if source.Payload.SimulationEventTime != "" {
		eventTime = source.Payload.SimulationEventTime
	}
	timestamp, err := time.Parse(time.RFC3339Nano, eventTime)
	if err != nil || timestamp.Nanosecond()%1000 != 0 {
		return Processing{}, errors.New("event time must have at most microsecond precision")
	}

	// Replays of the same payment keep the processing identity and simulated label.
	hash := sha256.Sum256([]byte("payment.processing.v1:" + source.Payload.PaymentID))
	outcome := "authorized"
	if hash[16]%2 == 1 {
		outcome = "rejected"
	}
	if source.Payload.SimulationOutcome != "" {
		outcome = source.Payload.SimulationOutcome
	}
	return Processing{
		EventID:          payment.ProcessingID(source.Payload.PaymentID),
		EventType:        "payment.processing.v1",
		SchemaVersion:    1,
		EventTime:        timestamp.UTC().Format("2006-01-02T15:04:05.000000Z"),
		PaymentID:        source.Payload.PaymentID,
		AccountID:        accountID,
		AmountMinor:      source.Payload.AmountMinor,
		Currency:         source.Payload.Currency,
		Country:          source.Payload.Country,
		PaymentMethod:    source.Payload.PaymentMethod,
		Outcome:          outcome,
		AccountCreatedAt: source.Payload.AccountCreatedAt, DeviceID: source.Payload.DeviceID,
	}, nil
}
