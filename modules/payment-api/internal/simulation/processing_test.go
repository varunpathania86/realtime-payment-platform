package simulation

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/event"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

const account = "12345678-1234-1234-1234-123456789abc"

func initiation() event.Initiated {
	return event.Initiated{
		ID: "00000000-0000-4000-8000-000000000001", Type: "payment.initiated.v1",
		Time: "2026-11-03T00:00:10.123456Z", Producer: "payment-api", CorrelationID: account,
		SchemaVersion: 1,
		Payload: event.Payload{
			PaymentID: account, AmountMinor: 1250, Currency: "EUR", Country: "DE", PaymentMethod: "transfer",
		},
	}
}

func TestProcessingFromInitiation(t *testing.T) {
	source := initiation()
	first, err := FromInitiation(source, account)
	if err != nil {
		t.Fatal(err)
	}
	second, err := FromInitiation(source, account)
	if err != nil || first != second {
		t.Fatalf("replayed initiation produced a different result: %+v %+v %v", first, second, err)
	}
	if !payment.ValidID(first.EventID) || first.EventID == source.ID || first.PaymentID != source.Payload.PaymentID ||
		first.EventTime != source.Time || first.AccountID != account || first.AmountMinor != 1250 ||
		first.Currency != "EUR" || first.Country != "DE" || first.PaymentMethod != "transfer" ||
		(first.Outcome != "authorized" && first.Outcome != "rejected") {
		t.Fatalf("invalid processing event: %+v", first)
	}
	encoded, err := json.Marshal(first)
	if err != nil || !strings.Contains(string(encoded), `"event_type":"payment.processing.v1"`) ||
		!strings.Contains(string(encoded), `"schema_version":1`) {
		t.Fatalf("invalid processing JSON: %s %v", encoded, err)
	}
	source.ID = "00000000-0000-4000-8000-000000000002"
	source.Payload.PaymentID = source.ID
	other, err := FromInitiation(source, account)
	if err != nil || first.EventID == other.EventID {
		t.Fatalf("distinct initiations must have distinct event IDs: %+v %v", other, err)
	}
}

func TestProcessingRejectsInvalidInput(t *testing.T) {
	for _, test := range []struct {
		name, account, country, method, eventTime string
	}{
		{"account", "bad", "DE", "card", initiation().Time},
		{"missing country", account, "", "card", initiation().Time},
		{"lowercase country", account, "de", "card", initiation().Time},
		{"invalid country", account, "D1", "card", initiation().Time},
		{"missing method", account, "DE", "", initiation().Time},
		{"invalid method", account, "DE", "cash", initiation().Time},
		{"invalid time", account, "DE", "card", "invalid"},
		{"nanosecond time", account, "DE", "card", "2026-11-03T00:00:10.123456789Z"},
	} {
		t.Run(test.name, func(t *testing.T) {
			source := initiation()
			source.Time = test.eventTime
			source.Payload.Country = test.country
			source.Payload.PaymentMethod = test.method
			if _, err := FromInitiation(source, test.account); err == nil {
				t.Fatal("invalid input was accepted")
			}
		})
	}
}

func TestProcessingPropagatesPersistedSyntheticContext(t *testing.T) {
	source := initiation()
	source.Payload.Country = "GB"
	source.Payload.PaymentMethod = "wallet"
	source.Payload.SimulationEventTime = "2026-11-02T23:59:10.123456Z"
	source.Payload.SimulationOutcome = "rejected"
	source.Payload.AccountCreatedAt = "2026-01-01T00:00:00Z"
	source.Payload.DeviceID = account
	record, err := FromInitiation(source, account)
	if err != nil {
		t.Fatal(err)
	}

	if record.Country != "GB" || record.PaymentMethod != "wallet" ||
		record.EventTime != source.Payload.SimulationEventTime || record.Outcome != "rejected" ||
		record.AccountCreatedAt != source.Payload.AccountCreatedAt || record.DeviceID != account {
		t.Fatalf("persisted context changed: %+v", record)
	}
	replay, err := FromInitiation(source, account)
	if err != nil || record != replay {
		t.Fatalf("controlled simulation changed on replay: %+v %v", replay, err)
	}
}

func TestProcessingUsesFixedMicrosecondsForPythonWorkers(t *testing.T) {
	source := initiation()
	source.Payload.SimulationEventTime = "2026-11-03T00:00:10.12345Z"
	record, err := FromInitiation(source, account)
	if err != nil || record.EventTime != "2026-11-03T00:00:10.123450Z" {
		t.Fatalf("noncanonical processing timestamp: %+v %v", record, err)
	}
}
