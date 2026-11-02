package event

import (
	"errors"
	"strings"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

type Payload struct {
	PaymentID   string `json:"payment_id"`
	AmountMinor int64  `json:"amount_minor"`
	Currency    string `json:"currency"`
}

type Initiated struct {
	ID            string  `json:"event_id"`
	Type          string  `json:"event_type"`
	Time          string  `json:"event_time"`
	Producer      string  `json:"producer"`
	CorrelationID string  `json:"correlation_id"`
	CausationID   *string `json:"causation_id"`
	SchemaVersion int     `json:"schema_version"`
	Payload       Payload `json:"payload"`
}

func New(id string, p payment.Payment) Initiated {
	return Initiated{
		ID: id, Type: "payment.initiated.v1", Time: p.CreatedAt.UTC().Format(time.RFC3339Nano),
		Producer: "payment-api", CorrelationID: p.ID, SchemaVersion: 1,
		Payload: Payload{PaymentID: p.ID, AmountMinor: p.AmountMinor, Currency: p.Currency},
	}
}

func (e Initiated) Validate(accountID string) error {
	if !payment.ValidID(e.ID) || !payment.ValidID(e.CorrelationID) ||
		(e.CausationID != nil && !payment.ValidID(*e.CausationID)) ||
		e.Type != "payment.initiated.v1" || e.Producer != "payment-api" || e.SchemaVersion != 1 {
		return errors.New("invalid payment event envelope")
	}
	_, err := time.Parse(time.RFC3339Nano, e.Time)
	if err != nil || !strings.HasSuffix(e.Time, "Z") {
		return errors.New("event_time must be RFC3339 UTC")
	}
	if !payment.ValidID(e.Payload.PaymentID) {
		return errors.New("invalid payment event identity")
	}
	return payment.Validate("event", payment.Command{AccountID: accountID, AmountMinor: e.Payload.AmountMinor, Currency: e.Payload.Currency})
}
