# realtime-payment-platform

A production-style, self-hosted learning platform for real-time payment data and AI: Go, React, Kafka, Flink, ClickHouse, Iceberg, and LLM/RAG, running on Terraform and Minikube. It uses synthetic data only.

The full requirements and roadmap are in the [master plan](docs/master-plan.md).

## Status

The repository foundation is in place. The catalog currently defines 15 independently versioned modules; module implementations are added incrementally from the [master plan](docs/master-plan.md).

[Event contracts](modules/event-contracts/README.md) is the first implementation:
a synthetic Avro payment event, offline tests, and a non-runtime OCI data bundle.

Stage 1 adds the minimal [Go API](modules/payment-api/README.md) and
[React status portal](modules/web-portal/README.md), deployed locally with
Terraform, Helm, and Traefik. Stage 1 publication and identical-digest reruns are
verified. Stage 2 adds PostgreSQL-backed initiated payment create/read operations,
durable idempotency, migrations, an OpenAPI contract, and Testcontainers tests.
Stage 2 is merged and its main build/publication verified. Stage 3 adds
TLS/SCRAM Kafka, SQL-backed Apicurio, the transactional outbox, relay and
idempotent receipt-consumer modes. See
[event recovery](docs/runbooks/event-publication.md) and
[ADR-003](docs/adr/ADR-003-transactional-outbox.md).
See the [API guide](modules/payment-api/README.md) and
[database runbook](docs/runbooks/payment-database.md).

Stage 3 is merged and its main build, security and publication passed.
Stage 4 is merged: its selected architecture uses per-cluster
OpenTelemetry Collectors and shared observability backends in cluster A.
Instrumentation includes API/PostgreSQL tracing, durable outbox/Kafka context,
correlated JSON logs, bounded business metrics and browser status telemetry.
Use the [opt-in topology](templates/observability-topology.json) with `DEV_CONFIG`;
`make dev-observability` opens authenticated Grafana on loopback.
The [observability runbook](docs/runbooks/observability.md) covers dashboards,
alerts and local-only network limitations.
See [ADR-019](docs/adr/ADR-019-shared-observability-backends.md).
Stage 4 traces diagnose executions; Stage 6 records dataset, job-version and
transformation lineage with durable ClickHouse facts and window aggregates.

Stage 4 also brings forward a manual **Release iteration baseline** workflow:
freeze approved main artifacts into module/solution releases without rebuilding,
deploying, advancing the PI schedule, or changing stable catalog versions.
The first live solution baseline `0.1.0-it1` and three module baselines succeeded.
See the [baseline runbook](docs/runbooks/iteration-baseline.md).

Stage 5 adds [PyFlink event-time processing](modules/stream-processor/README.md):
separate synthetic outcomes, native Kafka-partition watermarks/idleness,
revision-aware windows and persistent MinIO checkpoints/savepoints.
Use the [streaming topology](templates/streaming-topology.json) to opt in.
Normal payment-only development does not start or build the optional runtime.
Stage 5 also replaces manual release SHA/version inputs with `release`, `module`,
`type` and `promote`, including approved RC/stable artifact promotion and
reviewed stable-catalog PRs. Deployment and GitOps remain deferred.

Stage 6 is in progress: it adds opt-in persistent
[realtime analytics (ClickHouse)](modules/realtime-analytics/README.md) and
checkpoint-aligned Flink sinks for normalized facts and revision-aware windows.
Use `templates/analytics-topology.json` to select the feature in cluster A;
default payment-only development remains unchanged. PostgreSQL continues as
the transactional source of truth.

The [bounded pipeline benchmark](modules/stream-processor/README.md#bounded-pipeline-benchmark)
provides a 50 events/s baseline and separate 100 events/s stress phase with
fact-visibility latency and Kafka lag reporting. Run its memory preflight before
starting the [compact three-cluster topology](templates/analytics-benchmark-topology.json);
these are test targets, not established capacity claims.
An [opt-in low-memory Flink profile](modules/stream-processor/chart/README.md#opt-in-low-memory-runtime)
coordinates process memory and container budgets without changing shared
defaults; procedures, measured results and raw evidence live in the
[benchmark reports](tests/benchmarks/README.md).

Stage 6 is merged as PR #9. Stage 7 adds the
[payment risk model](modules/payment-risk-model/README.md): backward-compatible
synthetic account/device context, prior-event features, reproducible logistic
regression and LightGBM comparison, immutable MinIO artifacts, checksum-pinned
ONNX inference in Flink, model-version telemetry and audit-preserving
late-history revisions. CI verifies three-way golden parity in a real Flink
worker. The [local validation procedure](docs/developer-setup.md#single-cluster-risk-validation)
uses one low-memory cluster; three-cluster acceptance remains resource-deferred.
Scores are advisory and never make payment authorization decisions.

Stage 7 is merged as PR #10. [ADR-025](docs/adr/ADR-025-interview-roadmap-and-read-only-mcp.md)
reorders the next stages: Iceberg and Trino (Stage 8), Superset embedded in
React (Stage 9), then one AI assistant stage combining pgvector RAG, LangChain,
LangGraph, a local llama.cpp model and platform-hosted read-only MCP tools
(Stage 10). Keycloak follows in Stage 11; until then Superset and the
assistant use a clearly labelled local development identity. See the
[roadmap](docs/master-plan.md#stage-10-ai-operations-assistant-with-rag-langchain-langgraph-and-mcp).

Stage 8 adds an opt-in Iceberg lakehouse: an
[Apache Polaris REST catalog](modules/lakehouse-catalog/README.md) with
PostgreSQL-backed metadata, a Flink job that commits bronze and curated
payment facts to MinIO per checkpoint, and [Trino](modules/lakehouse-query/README.md)
read-only and maintainer catalogs. `templates/lakehouse-topology.json` enables
it in cluster A; `make lakehouse-verify` demonstrates time travel and
schema/partition evolution, and `make lakehouse-maintain` compacts and expires
snapshots. See [ADR-026](docs/adr/ADR-026-polaris-iceberg-lakehouse.md) and the
[lakehouse runbook](docs/runbooks/lakehouse.md).

## Repository map

| Path | Purpose |
|---|---|
| [docs/developer-setup.md](docs/developer-setup.md) | Developer environment setup |
| [docs/](docs/README.md) | Master plan, SDD, LLD, ADR, runbooks (Mermaid) |
| [templates/](templates/README.md) | Templates for documents and sub-projects |
| [solution.yaml](solution.yaml) | Solution identity, stable version, PI schedule, modules, and registry roots |
| [modules/](modules/) | Independently versioned module manifests and, as implemented, their source and packaging |
| [schemas/](schemas/) | JSON Schemas for solution, module, release, and counter-state documents |
| [docs/module-development.md](docs/module-development.md) | Module boundaries, types, and release lifecycle |
| [AGENTS.md](AGENTS.md) | Tool-neutral instructions for AI assistants |
| [ai/](ai/) | Path-specific AI rules and reusable AI skills |
| [.github/](.github/) | Workflows, CODEOWNERS, PR template |
| `platform/`, `tests/`, `scripts/` | Shared solution infrastructure, cross-module tests, and tooling |

New to Kubernetes or Helm? The module chart guides explain each template and
show resource relationships with Mermaid:
[API and shared terminology](modules/payment-api/chart/README.md),
[portal](modules/web-portal/chart/README.md),
[Flink](modules/stream-processor/chart/README.md),
[ClickHouse](modules/realtime-analytics/chart/README.md),
[Polaris](modules/lakehouse-catalog/chart/README.md), and
[Trino](modules/lakehouse-query/chart/README.md).

Start with the [local platform walkthrough](docs/architecture/local-platform-guide.md)
to connect the repository layout, Make scripts, Terraform, Helm and Kubernetes.

## Quick start

Full environment instructions: [docs/developer-setup.md](docs/developer-setup.md).

```bash
git clone git@github.com:varunpathania86/realtime-payment-platform.git
cd realtime-payment-platform
MULTI_CLUSTER=1 make setup  # tools plus host prerequisites for three clusters
make ci       # lint, test, build
make dev-up   # three full-stack clusters: rpp-a/b/c
make dev-access  # foreground: open http://127.0.0.1:8080; Ctrl-C stops access
```

After editing application code, leave `dev-access` running, run `make dev-update`
in another terminal, and refresh the browser. Smoke checks automatically use a
temporary available loopback port, separate from browser access.
Local work needs **no GitHub Actions, GHCR login, publication approval, or active
PI**. A failed remote workflow does not disable local commands. Initial setup,
image/dependency downloads, and Terraform/chart initialization need internet;
local development is independent of publication, not a guarantee of air-gapped operation.

`make dev-down` destroys all three clusters' application/database resources
and data, but leaves their Minikube nodes running. Use `make dev-update`, not teardown,
for normal iteration.
`make cluster-down` deletes the three owned cluster nodes/network after teardown. See the
[developer guide](docs/developer-setup.md#4-local-application-development) for
profiles, image loading, Terraform commands, recovery, and recreation.

Development now defaults to three independent clusters, each **3072 MiB / 2 CPUs**,
with the full application stack, one Kafka broker/controller and one CNPG instance
per cluster. PostgreSQL has one global writer and two remote standbys; this is
one Kafka cluster and one shared payment database, not three separate datasets.
All nodes still share one host. No profile argument is required.
`DEV_PROFILE` optionally selects a different topology base (default `rpp`);
`MINIKUBE_PROFILE` selects one member for access/smoke only (default `<base>-a`).
Read the [host prerequisites](docs/developer-setup.md#stage-3-optional-three-cluster-kafka-lab).
The [configuration template](templates/development-topology.json) illustrates
larger CPU/RAM budgets and independent per-cluster broker, CNPG and application
counts: `make dev-up DEV_PROFILE=rpp-bench DEV_CONFIG=templates/development-topology.json`.
Omitted counts default to one; omitted budgets default to two CPUs / 3072 MiB.
The [full-stack guide](docs/developer-setup.md#stage-3-full-stack-three-cluster-lab)
explains required synchronous commits, Terraform-installed operators/charts,
and explicit switchover, fenced failover and destructive former-primary rejoin.
An existing explicitly selected base is never converted or destroyed
automatically. Stop unrelated labs to avoid doubled host resource usage.
The superseded `multi-*`, single-cluster lifecycle and low-level Terraform Make
targets have been removed; `dev-*`, `db-*` and `cluster-down` are the public workflow.
The isolated full-stack lab passed synchronous write-blocking, shared identity,
planned switchover, fenced promotion/rebuild, dirty-source updates and clean
recreation checks; see the [verification record](docs/master-plan.md#stage-3-kafka-and-reliable-event-publication).
`make dev-status` prints node and workload-container
CPU/RAM usage alongside configured limits/requests, readiness, and restarts.
For existing labs, run `make dev-metrics` with the appropriate optional base override once to enable
the bundled metrics-server; new `dev-up` runs enable it automatically.

Run `make help` for all targets.

Catalog validation is available through `make validate-catalog`; `make ci` includes catalog validation and unit tests. CI has a dynamic affected-module matrix; root checks also validate implemented modules. Trusted push CI can trigger protected branch/main OCI publication after the workflows are merged into `main` and the environment is configured. See the [publication runbook](docs/release-publication.md). Iteration baselines are part of Stage 4; Stage 5 adds protected RC/stable artifact promotion. Deployment, GitOps and cleanup remain deferred.

Stage 1 includes required TFLint quality checks and a separate
[advisory Trivy IaC workflow](.github/workflows/security.yml) for the solution and
catalog-discovered module packaging. Run `make security` separately from
`make ci`; findings are reported truthfully, not converted to passes.
Merge enforcement depends on repository rulesets, not on workflow separation;
see the [security runbook](docs/runbooks/security-checks.md).
Security scans retain JSON and Markdown in `.local/security/`. Run
`make quality-report` to retain `make ci` results as Markdown and text logs in
`.local/quality/`. Both workflows expose job summaries and downloadable report
artifacts; see [report locations](docs/developer-setup.md#quality-and-security-reports).

Publication now supports images and module Helm charts. Packaged charts reference
the verified image digest and have their own verified OCI digest. Main publication
and same-source reruns were verified on 2026-10-28 with unchanged image/chart
digests at version `0.0.1-it1-rv.1`. Stages 1-3 are accepted. Stage 4 adds
iteration-baseline manifests, namespaced Git tags and prerelease GitHub Releases.
Stage 5 brings forward RC/stable artifact promotion; deployment/GitOps remain later work.

## Workflow

- Branches: `feature/`, `bugfix/`, `patch/`, `hotfix/`; each roadmap stage is one pull request into `main`.
- Versions: modules and the integrated solution have separate lifecycles; see [module-development.md](docs/module-development.md).
- Details: [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation structure

- This root README is the index.
- Module READMEs are added as module implementations arrive, using [the template](templates/project-readme.md), and describe only that module.
