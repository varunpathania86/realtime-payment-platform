# Lakehouse catalog

Apache Polaris REST catalog for the local Iceberg lakehouse, with idempotent
catalog, table, principal and grant setup. Part of
[realtime-payment-platform](../../README.md).

## Responsibilities

- Run Polaris as the Iceberg REST catalog; it stores metadata only, never
  table data. See [ADR-026](../../docs/adr/ADR-026-polaris-iceberg-lakehouse.md).
- Persist catalog metadata in a dedicated `polaris` database and role on the
  profile-local PostgreSQL, not in the `payments` schema.
- Keep table data and Iceberg metadata files in the `lakehouse` MinIO bucket.
- Converge the `lakehouse` catalog, `payments` namespace, the bronze
  `payment_events_raw` and curated `payment_events` tables, and three
  least-privilege principals:

| Principal | Privileges | Used by |
|---|---|---|
| `flink-lakehouse-writer` | Read + `TABLE_WRITE_DATA` (append snapshots; no create/alter/drop) | Flink `payment-lakehouse-v1` job |
| `trino-lakehouse-reader` | Read only | Trino `lakehouse` catalog |
| `lakehouse-maintainer` | `CATALOG_MANAGE_CONTENT` | Trino `lakehouse_admin` catalog for evolution and maintenance |

The OLTP source of truth stays PostgreSQL. Iceberg is the analytical
(OLAP) history; no LLM or lakehouse query is in the payment critical path.

## Layout

See the [Helm chart guide](chart/README.md) for resource relationships.

```text
.
├── setup_catalog.py  # Idempotent Polaris/MinIO convergence (stdlib only)
├── chart/            # Polaris Deployment, Service, config and setup hook
├── Dockerfile        # Pinned Polaris server plus admin tool and setup script
└── Makefile
```

## Prerequisites

The normal `make setup` toolchain and the opt-in lakehouse topology. Polaris
and Trino are container images pinned in `.tool-versions`; no host Java,
Polaris CLI or Trino CLI is needed. The lakehouse requires streaming and the
profile-local PostgreSQL (`external_database` is not supported).

## Quick start

```bash
make -C modules/lakehouse-catalog build test lint
make dev-up DEV_CONFIG=templates/lakehouse-topology.json
make streaming-fixtures
make lakehouse-verify
```

For a single-cluster verification on a resource-constrained host, follow
[lakehouse operations](../../docs/runbooks/lakehouse.md).

## Make targets

| Target | Description |
|---|---|
| `build` | Lint the chart strictly and compile the setup script |
| `test` | Run offline chart, SigV4 and catalog convergence tests |
| `lint` | Same checks as `build` |
| `fmt` | Check shared whitespace rules |
| `clean` | Remove compiled Python caches |

Root targets `lakehouse-query`, `lakehouse-verify` and `lakehouse-maintain`
operate on a running profile; see the runbook.

## Configuration

Local Terraform generates every credential (database password, token-signing
key and four client id/secret pairs) and installs this chart only when
`lakehouse_enabled` is true. An init container bootstraps the Polaris realm
and root principal idempotently. Tokens are signed with a symmetric key from a
Secret, so issued tokens survive restarts. A post-install/upgrade hook runs
`setup_catalog.py`, which creates missing objects, resets the three
principals' credentials to the Terraform-generated values and never alters
existing tables. Table evolution is an explicit maintainer task.

Tables use Iceberg format v2, zstd Parquet and hidden `day()` partitions on
`kafka_timestamp` (bronze) and `event_time` (curated).

## Documentation

- [Chart guide](chart/README.md)
- [Lakehouse runbook](../../docs/runbooks/lakehouse.md)
- [Lakehouse query (Trino)](../lakehouse-query/README.md)
- [Stream processor (Flink)](../stream-processor/README.md)
