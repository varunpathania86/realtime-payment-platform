# Lakehouse query

Single-node Trino query engine for the local Iceberg lakehouse. Part of
[realtime-payment-platform](../../README.md).

## Responsibilities

- Query Iceberg tables through the Polaris REST catalog with two catalogs:
  - `lakehouse`: read-only, using the `trino-lakehouse-reader` principal.
    Use it for all ad-hoc and dashboard queries.
  - `lakehouse_admin`: the `lakehouse-maintainer` principal, for explicit
    schema/partition evolution and table maintenance only.
- Read table data directly from MinIO; Trino never touches PostgreSQL.
- Provide the SQL engine that Stage 9 Superset will use.

One coordinator also acts as the worker. This is a local learning deployment,
not a sized Trino cluster.

## Layout

See the [Helm chart guide](chart/README.md) for resource relationships.

```text
.
├── chart/      # Deployment, Service and catalog/config ConfigMap
├── Dockerfile  # Pinned upstream Trino image with repository metadata
└── Makefile
```

## Prerequisites

The opt-in lakehouse topology, which also installs
[Polaris](../lakehouse-catalog/README.md). No host Trino CLI is needed: the
root Make targets run the CLI inside the pod.

## Quick start

```bash
make -C modules/lakehouse-query build test lint
make dev-up DEV_CONFIG=templates/lakehouse-topology.json
make lakehouse-query SQL='SELECT currency, count(*) AS payments FROM payment_events GROUP BY 1'
```

## Make targets

| Target | Description |
|---|---|
| `build` | Lint the Helm chart strictly |
| `test` | Run offline chart and catalog configuration tests |
| `lint` | Lint the Helm chart strictly |
| `fmt` | Check shared whitespace rules |
| `clean` | No generated build artifacts |

## Configuration

The ConfigMap replaces the image defaults: it removes the sample `tpch` and
`memory` catalogs and the 2 GB file-system cache, and caps query memory
(`query.maxMemory` 512MB, per node 384MB) inside a 1.5 GiB container. The JVM
heap is a percentage of the container limit.

Catalog credentials come from the `lakehouse-query` Secret through
environment variables, never from the ConfigMap. Object-storage credentials
come from `streaming-storage`. Only one replica is supported (the chart fails
otherwise), and updates use the `Recreate` strategy to avoid running two
memory-heavy pods at once.

## Documentation

- [Chart guide](chart/README.md)
- [Lakehouse runbook](../../docs/runbooks/lakehouse.md)
- [Lakehouse catalog (Polaris)](../lakehouse-catalog/README.md)
