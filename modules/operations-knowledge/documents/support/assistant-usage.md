# Operations assistant usage

Synthetic public guidance for the Stage 10 assistant.

## What the assistant can do

The operations assistant answers questions about platform runbooks, ADRs and
support playbooks with citations, and reads live operational data through
read-only tools: payment lookups, payment metrics, payment history and risk
model summaries.

## What the assistant cannot do

The assistant never changes payments, never approves or rejects a payment and
never runs arbitrary SQL. Restricted incident records require the
`knowledge:restricted` permission. Tool results are limited, redacted and
recorded in audit events without the question text.
