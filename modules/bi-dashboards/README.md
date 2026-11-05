# BI dashboards

Apache Superset that serves the payment-operations dashboard, embedded in the
web portal through guest tokens. Part of
[realtime-payment-platform](../../README.md).

## Responsibilities

- Serve one provisioned dashboard: real-time charts from ClickHouse
  ([realtime-analytics](../realtime-analytics/README.md)) and, when the
  lakehouse is enabled, an Iceberg history chart through Trino
  ([lakehouse-query](../lakehouse-query/README.md)).
- Provision everything as code on every install or upgrade:
  - metadata migrations
  - least-privilege roles and the admin and token-issuer users
  - database connections, datasets, charts and the dashboard (stable UUIDs)
  - the embedded-dashboard allow-list
- Issue guest tokens only to the web portal's server-side token issuer. Guest
  tokens cover exactly one dashboard and carry no row-level-security clauses.
- Reject guest chart queries that do not match a saved chart on the embedded
  dashboard, or that carry free-form `WHERE`/`HAVING` SQL.

Superset never decides anything about payments. It reads ClickHouse with a
SELECT-only user, and reads Trino through the read-only `lakehouse` catalog.

## Layout

See the [Helm chart guide](chart/README.md) for resource relationships.

```text
.
├── chart/                    # Setup hook Job, Deployment, Service, Ingress
├── superset/
│   ├── superset_config.py    # Embedding, CSP, guest-token and security settings
│   ├── rpp_bi_security.py    # Guest chart-query hardening (security manager)
│   ├── rpp_bi_assets.py      # Declarative datasets, charts and dashboard
│   └── rpp_bi_bootstrap.py   # Idempotent roles, users, import and embedding
├── tests/
│   ├── superset_check.py     # Checks run inside the image (make integration)
│   └── fixtures/             # Chart-data payloads captured from the Superset frontend
├── Dockerfile                # Pinned Superset image plus database drivers
├── requirements.txt          # ClickHouse and Trino SQLAlchemy drivers
└── Makefile
```

## Prerequisites

The opt-in BI topology. It enables realtime-analytics with its SELECT-only
`rpp_bi_reader` user, and creates the `superset` metadata database in the
local PostgreSQL. Image builds need Docker; `make setup` pins the Superset
base image version and digest in `.tool-versions`.

## Quick start

```bash
make -C modules/bi-dashboards build test
make -C modules/bi-dashboards integration   # builds the image; checks run offline
make dev-up DEV_CONFIG=templates/bi-topology.json
make dev-access
```

Then open the portal's **BI** page. The Superset admin UI is at
`http://bi.localhost:8080`; read its password from the `bi-dashboards` Secret
as described in the [runbook](../../docs/runbooks/bi-dashboards.md).

## Make targets

| Target | Description |
|---|---|
| `build` | Lint the Helm chart strictly and compile the Python sources |
| `test` | Run offline chart, asset and frontend-payload tests (`INTEGRATION=1` adds `integration`) |
| `lint` | Same checks as `build` |
| `image` | Build the pinned Superset image as `rpp-tools/bi-dashboards:check` |
| `integration` | Check configuration and guest-query security inside the image, without network |
| `fmt` | Check shared whitespace rules |
| `clean` | Remove Python bytecode |

## Configuration

- **Secrets.** All secrets come from the Terraform-generated `bi-dashboards`
  Secret: Flask secret key, guest-token signing key, metadata-database
  password, and admin and token-issuer passwords. The ClickHouse reader
  password comes from the `realtime-analytics` Secret. Only the setup Job
  sees the admin, issuer and ClickHouse passwords.
- **Stored queries.** Each chart stores a `query_context` that mirrors the
  Superset frontend's request. Superset's guard compares guest requests with
  it. The captured fixture in `tests/fixtures/` keeps the two in sync; when you
  change a chart, re-capture the payloads as described in the runbook.
- **Embedding origins.** `embed.origins` controls both the CSP
  `frame-ancestors` and the embedded-dashboard allow-list.
- **No Redis or Celery.** Queries are synchronous, with no reports, alerts or
  thumbnails, and the chart refuses more than one replica.
- **Trino.** The Trino connection is set only when `trino.uri` is non-empty.
  Trino has no authentication until Stage 11.

## Documentation

- [Chart guide](chart/README.md)
- [BI dashboards runbook](../../docs/runbooks/bi-dashboards.md)
- [ADR-028: Embedded Superset BI](../../docs/adr/ADR-028-embedded-superset-bi.md)
