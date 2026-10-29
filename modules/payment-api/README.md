# Payment API

PostgreSQL-backed synthetic payment API. Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Create/read initiated payments, persist idempotency keys, and serve health/build
metadata. Stage 2 does not authorize or settle payments, publish events, or
introduce Kafka/Apicurio. Only synthetic UUIDs and integer money amounts are used.

## Layout

```text
.
├── Dockerfile
├── Makefile
├── README.md
├── go.mod
├── go.sum
├── openapi.yaml
├── internal/
│   ├── httpapi/
│   ├── payment/
│   └── postgres/migrations/
├── main.go
└── main_test.go
```

## Prerequisites

Run root `make setup` and `make doctor`; see [developer setup](../../docs/developer-setup.md).
Go and PostgreSQL container versions come from root `.tool-versions`.
The pgx driver, OpenAPI validator, and Testcontainers library are pinned in
`go.mod`/`go.sum`. Docker is already installed/verified by the shared setup.
The first build resolves Go libraries; offline unit tests require that cache.
Integration tests require a reachable Docker daemon and may first download images.

## Quick start

```bash
make build
make test
make integration
```

From the repository root, `make dev-up` deploys the local environment,
`make dev-update` rebuilds local images, and `make smoke` checks the routed endpoints.
This provisions PostgreSQL, generates private profile-local credentials, and
runs migrations before the API starts. Keep `make dev-access` running for ingress.
For a process outside Kubernetes, provide `DATABASE_URL` from your environment,
run `make migrate`, then `./bin/payment-api`. Do not commit credentials.

Example request through local ingress (synthetic account):

```bash
curl -i http://127.0.0.1:8080/api/payments \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: synthetic-example-1' \
  -d '{"account_id":"12345678-1234-1234-1234-123456789abc","amount_minor":1250,"currency":"USD"}'
```

Follow the returned `Location` with GET. Repeating the same key/account/amount/
currency returns the original payment and timestamp with HTTP 201. Reusing the
key for a changed payload returns 409. Keys are global and retained for the
payment lifetime; tenant scoping is deferred until identity is introduced.

## Make targets

| Target | Description |
|---|---|
| `build` | Build `bin/payment-api` |
| `test` | Run offline unit tests |
| `integration` | Run Docker-backed PostgreSQL migration/concurrency/outage tests |
| `migrate` | Build and apply embedded migration using `DATABASE_URL`, bounded to 30 seconds |
| `lint` | Run `go vet` and verify formatting |
| `fmt` | Format Go source |
| `clean` | Remove `bin/` |
| `help` | List targets |

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `HTTP_ADDR` | `:8080` | HTTP listen address |
| `DATABASE_URL` | Required | PostgreSQL DSN; Secret-injected in Kubernetes, never logged |
| `VERSION` | `dev` | Make variable / image build argument |
| `COMMIT` | `unknown` | Make variable / image build argument |
| `GO_VERSION` | None | Required image build argument from root `.tool-versions` |

Status routes accept only `GET`, including rejection of `HEAD` with `405` and
`Allow: GET`. Responses are JSON and are not cached:

- `/healthz`: `{"status":"ok"}`
- `/readyz`: `{"status":"ok"}` when database/migration is ready, otherwise 503
- `/version`: `{"version":"dev","commit":"unknown"}`

The same handlers are available at `/api/healthz`, `/api/readyz`, and `/api/version`
for standard Ingress routing that preserves the prefix. Unknown paths return `404`,
including unknown `/api/...` paths.
Payment routes are also available directly and under `/api`. POST requires a
single printable ASCII `Idempotency-Key` (1-128 characters, no spaces),
`application/json`, and one JSON object no larger than 4096 bytes. Extra fields
are rejected. Amounts must fit positive int64 minor units; supported ISO 4217
currencies are USD, EUR, GBP, INR, JPY, CAD, AUD, CHF, SGD. UUIDs are lowercase.
See [OpenAPI](openapi.yaml) for response shapes and status codes.

Database operations are bounded to two seconds with no application retry loop.
Readiness checks the migration checksum; liveness does not query the database.
Kubernetes checks these directly against the pod. If readiness fails, ingress
can return 503 for every API route because it has no ready backend; use direct
pod forwarding to verify independent liveness during an outage, as shown in the
[database runbook](../../docs/runbooks/payment-database.md).
Connection pooling is bounded to ten connections. The migration command takes a
transaction-scoped advisory lock and checks previously applied SQL for drift.
Do not edit applied migration files; add a new version and extend the runner.
The chart uses the same restricted/digest-pinned image for init and runtime.
Published chart users must provision PostgreSQL and an existing Secret
(`database.existingSecret`, default `payment-api-database`) containing `DATABASE_URL`.
The server bounds header/read/write/idle time and allows ten seconds for graceful
shutdown on `SIGTERM` or `SIGINT`. The static binary runs as non-root in scratch.

## Documentation

The Helm chart supports `image.digest` (takes precedence over `image.tag`).
Local Terraform uses loaded development tags with pull policy `Never`; OCI
publication packages a versioned copy referencing the verified GHCR image digest
with `IfNotPresent`. Source chart and stable catalog versions are not changed.
Run `make security` from this directory for a separate advisory Trivy IaC scan;
it is not part of the module's build/test/lint contract.
The module-owned `.trivyignore.yaml` records exact-file exceptions for
Kubernetes-managed health, release-supplied namespaces, and intentional local
images. Rationales and expiry dates appear in the report; see the
[triage record](../../docs/runbooks/security-checks.md#mitigation).

- [Master plan, Stage 1](../../docs/master-plan.md#stage-1-one-day-terraform-foundation)
- [Stage 2 LLD](../../docs/architecture/payment-api-lld.md)
- [Database recovery](../../docs/runbooks/payment-database.md)
- [Module development](../../docs/module-development.md)
- [Developer setup](../../docs/developer-setup.md)
