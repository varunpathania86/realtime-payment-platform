# ADR-015: PostgreSQL-backed synthetic payments and idempotency

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** varunpathania86

## Context

Stage 2 requires durable payment creation, repeatable migrations, bounded database
failures, an HTTP contract, and integration tests before introducing Kafka.
Concurrent retries must not create duplicate payments.

## Decision

Use the Go standard HTTP library and `database/sql` with the pgx driver in the
existing payment-api module. PostgreSQL stores payments and their unique
idempotency keys in one row. A conflicting insert waits for the winner; a new
READ COMMITTED query returns the committed payment or reports a payload conflict.
No database/Kafka dual writes or event publication are introduced.

Use UUID v4 payment identifiers from Go's cryptographic random source, consistent
with existing Avro UUID fields. Require synthetic lowercase UUID account IDs.
Store positive signed 64-bit integer amounts and an explicitly supported subset
of ISO 4217 currencies. All payments remain `initiated`.

Embed ordered, checksum-tracked SQL migration 1 in the binary. Apply it through
an explicit `migrate` command in a restricted init container. Serialize migration
runs with a transaction-scoped PostgreSQL advisory lock; never rewrite applied
migrations. Readiness checks database connectivity and the expected migration;
liveness never depends on the database.

Keep the OpenAPI contract in Git and validate actual handler response schemas in
offline tests. Use Docker-backed Testcontainers for database tests separately,
enabled in the dynamic module CI contract with `INTEGRATION=1`.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| In-memory deduplication | Simple | Lost on restart; unsafe across replicas |
| Separate dedup table/cache | Flexible expiration | Extra transaction/state complexity not needed yet |
| ORM or migration CLI | Rich ecosystem | More abstractions/tooling for one initial schema |
| ULIDs | Time-sortable | Different identity convention from existing UUID contracts |

## Consequences

- Idempotency keys are global to this unauthenticated synthetic API, retained for
  the payment lifetime. Adding identity requires an explicit tenant/key scope.
- Same-key/same-payload replay returns the original HTTP 201 representation;
  changed account/amount/currency returns 409. Database uniqueness survives
  replica races, restarts, and a response lost after commit.
- New currencies require synchronized validation, database migration, and contract
  changes; arbitrary three-letter strings are not accepted as ISO currencies.
- Local Terraform owns PostgreSQL storage, service, and generated credentials.
  The local role owns its database for migration simplicity; this is not a
  production credential/TLS/backup policy.
- Local passwords and Terraform state are private Git-ignored development data.
  Application releases consume an existing Secret; published charts contain no
  credentials and require consumers to provision their database/Secret.
- Kafka, outbox, Apicurio, payment authorization/settlement, full identity, and
  production database hardening remain in their planned later stages.
