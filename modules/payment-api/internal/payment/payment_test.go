package payment

import (
	"math"
	"strings"
	"testing"
)

func TestValidation(t *testing.T) {
	valid := Command{AccountID: "12345678-1234-1234-1234-123456789abc", AmountMinor: math.MaxInt64, Currency: "USD"}
	if err := Validate("key", valid); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"", " ", "has space", "bad\nkey", "é", strings.Repeat("x", 129)} {
		if Validate(key, valid) == nil {
			t.Fatalf("accepted key %q", key)
		}
	}
	for _, c := range []Command{
		{AccountID: valid.AccountID, AmountMinor: 0, Currency: "USD"},
		{AccountID: valid.AccountID, AmountMinor: -1, Currency: "USD"},
		{AccountID: "bad", AmountMinor: 1, Currency: "USD"},
		{AccountID: valid.AccountID, AmountMinor: 1, Currency: "ZZZ"},
		{AccountID: valid.AccountID, AmountMinor: 1, Currency: "usd"},
	} {
		if Validate("key", c) == nil {
			t.Fatalf("accepted command %+v", c)
		}
	}
}

func TestUUID(t *testing.T) {
	first, err := NewID()
	if err != nil {
		t.Fatal(err)
	}
	second, err := NewID()
	if err != nil || first == second || !ValidID(first) || first[14] != '4' {
		t.Fatalf("invalid generated IDs %q %q: %v", first, second, err)
	}
}
