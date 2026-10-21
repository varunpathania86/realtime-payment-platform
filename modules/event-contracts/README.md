# Event contracts

Shared, independently versioned Avro contracts for synthetic payment events.
Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Define the boundary consumed by payment producers and stream processors.
The first contract is `payment.initiated.v1`; other event types, OpenAPI,
Apicurio registration, and compatibility enforcement follow in later stages.
This is a foundational contract slice, not an implementation of Kafka or the API.

## Layout

```text
.
├── schemas/payment-initiated-v1.avsc
├── examples/payment-initiated-v1.json
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
| `test` | Exercise Avro binary round trips and rejection cases |
| `lint` | Parse and validate schemas and examples |
| `fmt` | Format JSON schemas and examples |
| `clean` | Remove the generated archive |

## Configuration

The event envelope follows the [master plan](../../docs/master-plan.md).
IDs are synthetic UUIDs, timestamps are UTC, and money uses positive integer
minor units with a three-letter currency code. Currency validation checks shape;
it does not yet maintain the ISO 4217 currency registry. Avro encodes the dotted
event type as a string because Avro enum symbols cannot contain dots.

The scratch OCI image is a **data bundle**, not an executable service. Consumers
can extract `/contracts/` from it. It has no entrypoint, ports, or Helm chart.
The catalog version remains the current stable version (`0.0.0` initially);
published versions and source commits are OCI annotations, not catalog edits.

## Documentation

See [module development](../../docs/module-development.md) and
[the publication runbook](../../docs/release-publication.md).
