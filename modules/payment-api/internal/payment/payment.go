package payment

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"errors"
	"fmt"
	"regexp"
	"time"
)

var (
	ErrNotFound      = errors.New("payment not found")
	ErrConflict      = errors.New("idempotency key already used with a different payload")
	uuidPattern      = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	countryPattern   = regexp.MustCompile(`^[A-Z]{2}$`)
	timestampPattern = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$`)
)

type Command struct {
	AccountID        string     `json:"account_id"`
	AmountMinor      int64      `json:"amount_minor"`
	Currency         string     `json:"currency"`
	Country          string     `json:"country"`
	PaymentMethod    string     `json:"payment_method"`
	AccountCreatedAt string     `json:"account_created_at,omitempty"`
	DeviceID         string     `json:"device_id,omitempty"`
	Simulation       Simulation `json:"simulation,omitempty"`
}

type Simulation struct {
	EventTime string `json:"event_time,omitempty"`
	Outcome   string `json:"outcome,omitempty"`
}

type simulationPermissionKey struct{}

func WithSimulationPermission(ctx context.Context) context.Context {
	return context.WithValue(ctx, simulationPermissionKey{}, true)
}

func SimulationAllowed(ctx context.Context) bool {
	allowed, _ := ctx.Value(simulationPermissionKey{}).(bool)
	return allowed
}

func ProcessingID(paymentID string) string {
	hash := sha256.Sum256([]byte("payment.processing.v1:" + paymentID))
	id := hash[:16]
	id[6] = (id[6] & 0x0f) | 0x50
	id[8] = (id[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", id[:4], id[4:6], id[6:8], id[8:10], id[10:])
}

type Payment struct {
	ID string `json:"id"`
	Command
	Status            string    `json:"status"`
	CreatedAt         time.Time `json:"created_at"`
	ProcessingEventID string    `json:"processing_event_id"`
}

type Store interface {
	Create(context.Context, string, Command) (Payment, error)
	Get(context.Context, string) (Payment, error)
	Ready(context.Context) error
}

func ValidID(id string) bool { return uuidPattern.MatchString(id) }

func Validate(key string, command Command) error {
	if len(key) < 1 || len(key) > 128 {
		return errors.New("Idempotency-Key must contain 1 to 128 printable ASCII characters without spaces")
	}
	for _, character := range key {
		if character < 33 || character > 126 {
			return errors.New("Idempotency-Key must contain 1 to 128 printable ASCII characters without spaces")
		}
	}
	if !ValidID(command.AccountID) {
		return errors.New("account_id must be a lowercase UUID")
	}
	if command.AmountMinor <= 0 {
		return errors.New("amount_minor must be a positive int64")
	}
	if !countryPattern.MatchString(command.Country) {
		return errors.New("country must be an uppercase two-letter code")
	}
	switch command.PaymentMethod {
	case "card", "transfer", "wallet":
	default:
		return errors.New("payment_method must be card, transfer or wallet")
	}
	if command.DeviceID != "" && !ValidID(command.DeviceID) {
		return errors.New("device_id must be a lowercase synthetic UUID")
	}
	for _, timestamp := range []string{command.AccountCreatedAt, command.Simulation.EventTime} {
		if timestamp == "" {
			continue
		}
		parsed, err := time.Parse(time.RFC3339Nano, timestamp)
		if err != nil || !timestampPattern.MatchString(timestamp) || parsed.Nanosecond()%1000 != 0 {
			return errors.New("synthetic timestamps must be RFC3339 UTC with at most microsecond precision")
		}
	}
	if command.Simulation.Outcome != "" && command.Simulation.Outcome != "authorized" && command.Simulation.Outcome != "rejected" {
		return errors.New("simulation outcome must be authorized or rejected")
	}
	if command.AccountCreatedAt != "" {
		created, _ := time.Parse(time.RFC3339Nano, command.AccountCreatedAt)
		at := time.Now().UTC()
		if command.Simulation.EventTime != "" {
			at, _ = time.Parse(time.RFC3339Nano, command.Simulation.EventTime)
		}
		if created.After(at) {
			return errors.New("account_created_at must not follow event time")
		}
	}
	switch command.Currency {
	case "USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "CHF", "SGD":
		return nil
	default:
		return errors.New("currency must be a supported ISO 4217 code: USD, EUR, GBP, INR, JPY, CAD, AUD, CHF, SGD")
	}
}

func NewID() (string, error) {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", fmt.Errorf("generate payment UUID: %w", err)
	}
	value[6] = value[6]&0x0f | 0x40
	value[8] = value[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", value[:4], value[4:6], value[6:8], value[8:10], value[10:]), nil
}
