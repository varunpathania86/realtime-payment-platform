package event

import (
	"testing"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

func TestInitiated(t *testing.T) {
	p := payment.Payment{ID: "12345678-1234-1234-1234-123456789abc", Command: payment.Command{
		AccountID: "22345678-1234-1234-1234-123456789abc", AmountMinor: 1250, Currency: "USD",
	}, Status: "initiated", CreatedAt: time.Date(2026, 10, 29, 12, 0, 0, 123000000, time.UTC)}
	e := New("32345678-1234-1234-1234-123456789abc", p)
	if err := e.Validate(p.AccountID); err != nil {
		t.Fatal(err)
	}
	if e.Time != "2026-10-29T12:00:00.123Z" || e.Payload.PaymentID != p.ID || e.CorrelationID != p.ID {
		t.Fatalf("changed envelope: %+v", e)
	}
	for _, mutate := range []func(*Initiated){
		func(e *Initiated) { e.ID = "bad" },
		func(e *Initiated) { e.Type = "payment.settled.v1" },
		func(e *Initiated) { e.Time = "2026-10-29T12:00:00+01:00" },
		func(e *Initiated) { e.SchemaVersion = 2 },
		func(e *Initiated) { e.Payload.AmountMinor = 0 },
		func(e *Initiated) { e.Payload.Currency = "XXX" },
	} {
		bad := e
		mutate(&bad)
		if bad.Validate(p.AccountID) == nil {
			t.Fatalf("accepted invalid event: %+v", bad)
		}
	}
	if e.Validate("bad-account") == nil {
		t.Fatal("accepted invalid partition key")
	}
}
