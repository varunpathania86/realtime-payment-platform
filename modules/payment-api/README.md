# Payment API

PostgreSQL-backed synthetic payment API. Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Create/read initiated payments, persist idempotency keys, and serve health/build
metadata. Stage 3 atomically adds an outbox event per payment and provides relay
and receipt-consumer worker modes. Neither mode authorizes or settles payments.
Only synthetic UUIDs and integer money amounts are used.

Stage 11 `OIDC_ISSUER`/`OIDC_INTERNAL_URL` enable independent Keycloak RS256
verification for every payment endpoint: exact issuer, `payment-api` audience,
expiry, `openid` scope and resource-specific `payments:read` or
`payments:write`. Viewers cannot write. Browser traffic reaches these
endpoints through the portal's opaque session and audience-token exchange;
direct API callers supply their own valid audience-scoped bearer token.
Health, readiness, metrics and build metadata remain available to probes and
local smoke checks. Configured invalid identity fails closed.

## Layout

See the [Helm chart guide](chart/README.md) for template purposes, Kubernetes
terminology and a resource relationship diagram.

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
│   ├── event/
│   ├── stream/
│   └── postgres/migrations/
├── main.go
└── main_test.go
```

## Prerequisites

Run root `make setup` and `make doctor`; see [developer setup](../../docs/developer-setup.md).
Go and PostgreSQL/Kafka/Apicurio container versions come from root `.tool-versions`.
The pgx driver, Kafka/Avro libraries, OpenAPI validator, and Testcontainers are pinned in
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
New development bases run the stack in three independent clusters. Select a
member for access/smoke with `MINIKUBE_PROFILE=<base>-a` (or `-b`/`-c`).
Set chart `replicaCount`, `events.relayReplicas` and `events.consumerReplicas`
independently to positive integers; each defaults to one. The development
`DEV_CONFIG` maps these to per-cluster `applications.payment-api`, `relay`
and `consumer`; see [topology sizing](../../docs/developer-setup.md#configuring-counts-and-benchmark-node-budgets).
Replicas share database migration/outbox locks, durable idempotency, one consumer
group and event-ID-deduplicated receipts. Consumer replicas beyond the topic's
partition count may remain idle.
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
| `integration` | Run Docker-backed PostgreSQL/Kafka/Apicurio migration, crash/replay and quarantine tests |
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
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Unset (export disabled) | Optional OTLP/HTTP collector origin; no credentials, query, or path |
| `OTEL_CLUSTER_NAME` | Unset | Cluster resource identity when exporting traces |

### Stage 4 telemetry

The API and worker processes use OpenTelemetry for bounded-route HTTP spans,
standard `otelhttp` server instrumentation with privacy-safe request metadata,
ready-made `database/sql` tracing through `otelsql`, and custom Kafka
producer/receipt-processing spans. SQL text, parameter values, and raw database
errors are excluded. The optional exporter uses a bounded asynchronous queue
and short export/shutdown deadlines; collector availability does not gate
application startup. Export failures are logged explicitly without endpoint
credentials. The initial local trace sampling rate is 100%, not a production
sampling policy.
Unparented health probes and idle SQL polling do not create traces; HTTP RED
metrics still include probes, and browser-propagated status requests retain
their trace. SQL spans are recorded only inside an existing valid trace,
avoiding a stream of unrelated polling roots.

Migration 5 adds trace-context columns to the outbox without changing event
schemas or applied migrations. The creation transaction persists W3C
`traceparent`/`tracestate`; an idempotent retry cannot replace the original
context. The relay restores it and injects producer context into Kafka headers;
receipt processing continues that trace. Older/backfilled events with no
context remain valid and start a new trace. Unrestricted baggage is not copied.
Structured JSON request, publication and receipt logs correlate trace/span
IDs and relevant event/payment IDs without logging payloads or raw URLs.

`GET /metrics` exports bounded HTTP request counters/duration histograms,
Kafka publication duration/outcomes, receipt effects/deduplication and pending
outbox count/age. Sampled trace IDs appear only in exemplars, not metric labels.
Relays observe backlog every 15 seconds with a two-second SQL deadline.
The opt-in local workflow wires Collector origins and cluster identity into the
API/workers; existing commands work without monitoring. See the
[developer guide](../../docs/developer-setup.md#stage-4-opt-in-shared-observability)
and [observability runbook](../../docs/runbooks/observability.md).
For architecture and lineage boundaries, see
[ADR-019](../../docs/adr/ADR-019-shared-observability-backends.md).

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
For verified PostgreSQL TLS, enable `database.tls` and include `ca.crt` in that
Secret. The chart mounts it at `/etc/payment-database/ca.crt` in API, workers and
all migration containers; reference it with `sslrootcert` in `DATABASE_URL`.
Terraform wires this automatically for the
[distributed CNPG lab](../../docs/developer-setup.md#stage-3-full-stack-three-cluster-lab).
The server bounds header/read/write/idle time and allows ten seconds for graceful
shutdown on `SIGTERM` or `SIGINT`. The static binary runs as non-root in scratch.

### Event worker modes

The same binary supports `payment-api relay` and `payment-api consumer`.
Terraform enables both as separate Deployments from the same local image;
no new product module or public Service is introduced.
The API itself never calls Kafka or the registry in the request path.
Existing Stage 2 payments are backfilled once by migration 2.

Workers require `DATABASE_URL`, `KAFKA_BROKERS` (comma-separated host:port),
`KAFKA_CA_FILE`, `KAFKA_USERNAME`, `KAFKA_PASSWORD`, `REGISTRY_URL`
(including `/apis/ccompat/v7`), and `PAYMENT_TOPIC`.
The relay also requires `EVENT_SCHEMA_FILE`, mounted from the canonical
event-contracts schema. TLS hostname verification and SCRAM-SHA-512 are mandatory.
No plaintext or verification-disabled client fallback exists.
`KAFKA_REPLICATION_FACTOR` defaults to 1 and accepts only 1 or 3. The chart's
`events.replicationFactor` supplies it; the optional
[three-cluster lab](../../docs/developer-setup.md#stage-3-optional-three-cluster-kafka-lab)
sets 3 with minimum ISR 2. Relay startup tries all configured bootstrap brokers
and rejects mismatched existing topic replica assignments/configuration rather
than claiming to migrate them automatically.

The relay retains event identity through redelivery, partitions by account ID,
and retries at most eight times with persisted exponential delays capped at
60 seconds. Exhausted rows become durable SQL dead letters and block that
account's later events. The consumer persists receipts before offset commits,
rejects reused IDs with changed content, and quarantines invalid records in SQL.
See [event recovery](../../docs/runbooks/event-publication.md) for inspection
and deliberate requeue procedures.

Published chart workers are opt-in (`events.enabled: true`); consumers must
provide broker/registry settings, the existing credential Secret and CA, and the
schema ConfigMap described by `events` values. Default charts still deploy only
the API. All worker/init images use the same verified image digest.

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
- [Event publication recovery](../../docs/runbooks/event-publication.md)
- [Transactional outbox ADR](../../docs/adr/ADR-003-transactional-outbox.md)
- [Module development](../../docs/module-development.md)
- [Developer setup](../../docs/developer-setup.md)
