# Realtime analytics

Persistent local ClickHouse storage for synthetic event facts and revision-aware
Flink aggregates. Part of [realtime-payment-platform](../../README.md).

## Responsibilities

- Store normalized processing facts and keyed window aggregate revisions.
- Partition by event month and order for day/currency/country/payment-method
  queries.
- Retain detailed facts for 90 days; use `FINAL` when querying replacement
  rows before background merges complete.
- Persist data on a local Kubernetes volume and use profile-generated
  credentials.
- Keep PostgreSQL as the transactional source of truth.

## Layout

See the [Helm chart guide](chart/README.md) for StatefulSet, Service, persistent
storage and schema hook relationships.

```text
.
├── benchmark.py     # Disposable-table synthetic load and query latency report
├── chart/           # StatefulSet, PVC, schema and internal service
├── Dockerfile       # Pinned ClickHouse image with repository metadata
└── Makefile
```

## Prerequisites

Use the opt-in analytics topology with the repository's normal
`make setup` toolchain and Docker-based local workflow. `make doctor` validates
the pinned ClickHouse image version; no host ClickHouse client is required.

## Quick start

```bash
make -C modules/realtime-analytics build test lint
make dev-up DEV_CONFIG=templates/analytics-topology.json
```

Analytics is opt-in. The topology enables streaming and ClickHouse only in
cluster A and requires a minimum 4 CPUs/6144 MiB there. Default payment-only
and streaming-only development do not select the ClickHouse image or chart.

## Make targets

| Target | Description |
|---|---|
| `build` | Lint the Helm chart |
| `test` | Run offline chart, feature-selection and benchmark tests |
| `lint` | Lint the Helm chart |
| `fmt` | Check shared whitespace rules |
| `benchmark` | Load a disposable synthetic dataset and report client p50/p95/p99 |
| `integration` | Build and test the pinned image and replacement semantics in Docker |
| `clean` | No generated build artifacts |

## Configuration

The chart uses one StatefulSet replica, a profile-owned persistent volume,
internal HTTP/native services, and a Kubernetes Secret. A post-install/upgrade
Job applies idempotent table creation statements. The Flink write-through
operator synchronously persists each record before forwarding it downstream.
The pod keeps a read-only root filesystem and mounts a small writable `users.d`
volume so the official entrypoint can generate the secret-backed user config.
Both tables are at-least-once; ClickHouse replacement keys and `FINAL` queries
make replayed facts/revisions logically replaceable. This is not a
cross-system transaction.

The `payment_facts` table is partitioned by month and expires rows after 90 days.
The `payment_window_aggregates` table uses the stable Flink result ID and
revision. Dataset, output, transformation and job-version identities travel
with records. Query actual results with `FINAL` where merges may not yet have
removed older revisions.

Run the benchmark against a local port-forward with `CLICKHOUSE_URL`,
`CLICKHOUSE_USER`, and `CLICKHOUSE_PASSWORD` set. It creates a unique disposable
table, generates synthetic rows, measures a warmed seven-day dashboard query,
prints hardware/data/partition/configuration evidence, and drops the table. The
default p95 acceptance target is 500 ms; a miss returns a nonzero exit code.

The module-owned `.trivyignore.yaml` records scoped, expiring exceptions for
the upstream ClickHouse UID/GID mapping and local Helm namespace/image
references; the Docker image itself declares a non-root user and health check.

## Documentation

- [Stage 6 architecture decision](../../docs/adr/ADR-023-clickhouse-analytics.md)
- [Master plan](../../docs/master-plan.md#stage-6-clickhouse-real-time-analytics)
- [Developer setup](../../docs/developer-setup.md)
