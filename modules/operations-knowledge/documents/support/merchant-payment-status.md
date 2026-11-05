# Support playbook: explaining payment status to merchants

Synthetic internal support guidance for the Stage 10 assistant.

## Authorized payments

An authorized payment passed validation and was accepted by the synthetic
authorization rules. Tell the merchant the payment ID, amount, currency and the
time it was created. Never share another merchant's payments.

## Rejected payments

A rejected payment failed validation or the synthetic authorization rules.
Explain that the payment was not charged, ask the merchant to check the amount,
currency and payment method, and suggest a retry with a new idempotency key.
Do not quote risk scores to merchants: risk scores are advisory internal
signals and never decide authorization.

## Escalation

Escalate to payments engineering when more than five percent of a merchant's
payments are rejected within one hour, or when a payment ID cannot be found.
