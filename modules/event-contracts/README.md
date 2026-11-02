# Event contracts

Shared, independently versioned Avro contracts for synthetic payment events.
Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Define the boundary consumed by payment producers and stream processors.
The first contract is `payment.initiated.v1`. Stage 3 enforces backward
compatibility against every retained baseline, and payment-api registers the
canonical schema in Apicurio at relay startup. The HTTP OpenAPI contract lives
with payment-api. This module remains a data bundle, not a broker or executable.

## Layout

```text
.
├── schemas/payment-initiated-v1.avsc
├── examples/payment-initiated-v1.json
├── compatibility/payment-initiated-v1/001.avsc
├── tests/
├── module.yaml
├── Makefile
└── Dockerfile
```

## Prerequisites

Run root `make setup` to install the pinned Avro and validation libraries.
No broker, database, or Kubernetes cluster is needed.

## Quick start

```bash
make -C modules/event-contracts test
make -C modules/event-contracts build
```

## Make targets

| Target | Description |
|---|---|
| `build` | Validate examples and create deterministic `dist/contracts.tar` |
| `test` | Exercise Avro round trips, rejection cases, and backward compatibility |
| `lint` | Parse and validate schemas and examples |
| `fmt` | Format JSON schemas and examples |
| `clean` | Remove the generated archive |
| `security` | Separate advisory Trivy IaC scan of module packaging |

## Configuration

The event envelope follows the [master plan](../../docs/master-plan.md).
IDs are synthetic UUIDs, timestamps are UTC, and money uses positive integer
minor units with a three-letter currency code. Currency validation checks shape;
it does not yet maintain the ISO 4217 currency registry. Avro encodes the dotted
event type as a string because Avro enum symbols cannot contain dots.
Keep compatibility baselines immutable and append one for each retained schema
version. Both module lint/build and tests reject incompatible readers. The live
registry subject uses backward-transitive compatibility too. Kafka records carry
`account_id` as the partition key and a schema-ID Avro frame; the envelope
contains stable event/correlation IDs. See
[ADR-003](../../docs/adr/ADR-003-transactional-outbox.md).

The scratch OCI image is a **data bundle**, not an executable service. Consumers
can extract `/contracts/` from it. It has no entrypoint, ports, or Helm chart.
The catalog version remains the current stable version (`0.0.0` initially);
published versions and source commits are OCI annotations, not catalog edits.
The bundle declares numeric non-root user `65532:65532`. Its module-owned
`.trivyignore.yaml` exempts Docker HEALTHCHECK only: there is no executable
service to probe. The exception has a rationale and review/expiry date.

## Documentation

See [module development](../../docs/module-development.md) and
[the publication runbook](../../docs/release-publication.md).
