package payment

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"regexp"
	"time"
)

var (
	ErrNotFound = errors.New("payment not found")
	ErrConflict = errors.New("idempotency key already used with a different payload")
	uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
)

type Command struct {
	AccountID   string `json:"account_id"`
	AmountMinor int64  `json:"amount_minor"`
	Currency    string `json:"currency"`
}

type Payment struct {
	ID string `json:"id"`
	Command
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
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
