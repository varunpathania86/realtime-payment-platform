# ADR-003: Transactional outbox and idempotent payment receipts

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** varunpathania86

## Context

Stage 3 must publish every committed initiated payment without coupling HTTP
success to Kafka availability. A publisher can die after Kafka accepts a record
but before PostgreSQL records success. Consumers must tolerate that replay.

## Decision

Insert a stable event and payment in one PostgreSQL transaction. Run relay and
receipt-consumer modes from the existing payment-api image, deployed separately
for lifecycle isolation but kept within the same product module. The consumer's
only business effect is an event-ID-keyed PostgreSQL receipt, not authorization
or settlement. This scope was explicitly selected for Stage 3 verification.

Use Apache Kafka in single-node KRaft mode locally, TLS with hostname verification,
and SASL/SCRAM-SHA-512. The controller listener is process-local loopback only.
Use SQL-backed Apicurio Registry with its Confluent-compatible API, an Avro
schema-ID frame, and backward-transitive subject compatibility. Keep canonical
schemas and immutable compatibility baselines in event-contracts.
The default remains single-cluster; the optional cross-cluster Kafka learning
topology is defined in [ADR-016](ADR-016-local-cross-cluster-kafka.md).

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Write PostgreSQL then Kafka in HTTP | Few components | Lost events on crash, broker failure breaks requests |
| Kafka transactions alone | Broker atomicity | Does not atomically commit PostgreSQL effects |
| Dedicated new relay/consumer modules | Independent ownership | Unnecessary release boundaries for this stage |
| At-least-once outbox plus receipts | Durable, testable recovery | Duplicate records and extra SQL state are intentional |

## Consequences

- HTTP contracts remain unchanged: replay creates neither a second payment nor
  a second outbox row. Migration 1 remains immutable; migration 2 backfills existing
  payments with deterministic event IDs and introduces the outbox/receipt tables.
  Migration 3 adds the consumer quarantine table.
- Migration 4 installs database write guards under a payment-table write lock
  and backfills any missing rows. This covers Stage 2 binaries still accepting
  requests during a rolling upgrade. A BEFORE INSERT trigger serializes accounts;
  an AFTER INSERT trigger creates the event in the payment transaction, including
  for old writers. The Go API does not maintain a second event-construction path.
- Account advisory locks serialize same-account creation. Relay row locks use
  `SKIP LOCKED`; an earlier unpublished row blocks later rows for that account.
  Records use `account_id` as Kafka key, never event type. Independent accounts
  can progress during another account's retry delay.
- Broker acknowledgements precede SQL publication acknowledgement. Duplicates
  are possible by design; there is no exactly-once Kafka claim. Receipts commit
  before consumer offsets, with duplicate IDs checked against original content.
- Each relay operation is bounded to ten seconds. Eight failed publication
  attempts use persisted exponential delays (1, 2, 4, 8, 16, 32, then at most
  60 seconds); exhaustion moves the row to `dead`. A dead row blocks later
  account events until reviewed and requeued with the same event ID.
- Dead letters are durable PostgreSQL records, not a second Kafka topic: this
  remains available even when the broker itself is unavailable. Invalid consumer
  records are quarantined by source topic/partition/offset before offset commit.
  Registry/network/storage errors stop consumption without committing the offset.
- Local Kafka uses one broker, three topic partitions, replication factor one,
  and a PVC: this proves replay behavior, not broker high availability.
- Local TLS certificates and SCRAM credentials are Terraform-generated and
  private in ignored state. Registry schema IDs persist in a separate PostgreSQL
  schema. The local shared database role and unauthenticated internal registry
  are development concessions; production role separation, registry access
  control, certificate/credential rotation, and backups require later hardening.
- The crash-window test uses a real child-process kill, real TLS/SCRAM Kafka,
  Avro/Apicurio, PostgreSQL, and consumer deduplication. No production failpoint
  or Kafka dependency is added to the payment HTTP request path.
