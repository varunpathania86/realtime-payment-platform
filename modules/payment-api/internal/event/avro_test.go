package event

import (
	"os"
	"testing"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

func TestAuthoritativeAvroContract(t *testing.T) {
	schema, err := os.ReadFile("../../../event-contracts/schemas/payment-initiated-v1.avsc")
	if err != nil {
		t.Fatal(err)
	}
	codec, err := NewCodec(string(schema))
	if err != nil {
		t.Fatal(err)
	}
	e := New("12345678-1234-4234-8234-123456789abc", payment.Payment{
		ID: "22345678-1234-4234-8234-123456789abc", CreatedAt: time.Date(2026, 10, 29, 12, 0, 0, 0, time.UTC),
		Command: payment.Command{AmountMinor: 9223372036854775807, Currency: "USD"},
	})
	data, err := codec.Encode(42, e)
	if err != nil {
		t.Fatal(err)
	}
	if id, err := SchemaID(data); err != nil || id != 42 {
		t.Fatalf("registry frame: %d %v", id, err)
	}
	decoded, err := codec.Decode(data)
	if err != nil || decoded != e {
		t.Fatalf("Avro changed event: %+v %v", decoded, err)
	}
	cause := "32345678-1234-4234-8234-123456789abc"
	caused := e
	caused.CausationID = &cause
	causedData, err := codec.Encode(42, caused)
	if err != nil {
		t.Fatal(err)
	}
	causedEvent, err := codec.Decode(causedData)
	if err != nil || causedEvent.CausationID == nil || *causedEvent.CausationID != cause {
		t.Fatalf("nullable causation ID changed: %+v %v", causedEvent, err)
	}
	for _, invalid := range [][]byte{nil, {1, 0, 0, 0, 42, 1}, append(append([]byte{}, data...), 1), data[:len(data)-1]} {
		if _, err := codec.Decode(invalid); err == nil {
			t.Fatalf("accepted malformed frame %x", invalid)
		}
	}
}
