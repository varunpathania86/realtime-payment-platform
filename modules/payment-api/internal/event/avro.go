package event

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/linkedin/goavro/v2"
)

type Codec struct {
	codec *goavro.Codec
}

func NewCodec(schema string) (*Codec, error) {
	codec, err := goavro.NewCodec(schema)
	if err != nil {
		return nil, fmt.Errorf("compile event schema: %w", err)
	}
	return &Codec{codec: codec}, nil
}

func (c *Codec) Encode(id uint32, e Initiated) ([]byte, error) {
	if id == 0 {
		return nil, errors.New("schema ID must be positive")
	}
	if e.SchemaVersion != 1 {
		return nil, errors.New("unsupported payment event schema version")
	}
	var cause any
	if e.CausationID != nil {
		cause = goavro.Union("string", *e.CausationID)
	}
	native := map[string]any{
		"event_id": e.ID, "event_type": e.Type, "event_time": e.Time, "producer": e.Producer,
		"correlation_id": e.CorrelationID, "causation_id": cause, "schema_version": int32(e.SchemaVersion),
		"payload": map[string]any{"payment_id": e.Payload.PaymentID, "amount_minor": e.Payload.AmountMinor, "currency": e.Payload.Currency},
	}
	header := make([]byte, 5)
	binary.BigEndian.PutUint32(header[1:], id)
	return c.codec.BinaryFromNative(header, native)
}

func SchemaID(data []byte) (uint32, error) {
	if len(data) < 6 || len(data) > 1<<20 || data[0] != 0 {
		return 0, errors.New("invalid or oversized registry-framed Avro event")
	}
	id := binary.BigEndian.Uint32(data[1:5])
	if id == 0 {
		return 0, errors.New("schema ID must be positive")
	}
	return id, nil
}

func (c *Codec) Decode(data []byte) (Initiated, error) {
	if _, err := SchemaID(data); err != nil {
		return Initiated{}, err
	}
	native, rest, err := c.codec.NativeFromBinary(data[5:])
	if err != nil || len(rest) != 0 {
		return Initiated{}, errors.New("invalid Avro event data")
	}
	record, ok := native.(map[string]any)
	if !ok {
		return Initiated{}, errors.New("Avro event must be a record")
	}
	if cause, ok := record["causation_id"].(map[string]any); ok {
		value, ok := cause["string"].(string)
		if !ok {
			return Initiated{}, errors.New("invalid event causation ID")
		}
		record["causation_id"] = value
	}
	text, err := json.Marshal(record)
	if err != nil {
		return Initiated{}, err
	}
	var e Initiated
	if err := json.Unmarshal(text, &e); err != nil {
		return Initiated{}, err
	}
	return e, nil
}
