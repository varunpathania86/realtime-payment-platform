# Documentation

All design documentation lives here, written in Markdown with Mermaid diagrams. Create new documents from [`/templates`](../templates/README.md).

| Folder / file | Content |
|---|---|
| [master-plan.md](master-plan.md) | Frozen requirements, conventions, and staged roadmap |
| [module-development.md](module-development.md) | Module boundaries, catalog types, and adding modules |
| [release-publication.md](release-publication.md) | Trusted OCI publication, activation, counters, and recovery |
| [architecture/repository-overview.md](architecture/repository-overview.md) | Module catalog and delivery flow |
| [architecture/local-platform-guide.md](architecture/local-platform-guide.md) | Start here: repository layout and how Make, Docker, Minikube, Terraform, Helm and Kubernetes connect |
| [developer-setup.md](developer-setup.md) | Developer environment setup (`make setup`) |
| [adr/ADR-001-terraform-local-development.md](adr/ADR-001-terraform-local-development.md) | Terraform ownership and independent local image/deployment loop |
| [runbooks/local-development.md](runbooks/local-development.md) | Local image, deployment, ingress, and recovery troubleshooting |
| [runbooks/security-checks.md](runbooks/security-checks.md) | Separate solution/module IaC scans and advisory merge policy |
| [adr/ADR-014-stage-1-artifact-and-security-boundaries.md](adr/ADR-014-stage-1-artifact-and-security-boundaries.md) | Stage 1 image/chart publication and quality/security separation |
| [architecture/](architecture/) | SDD, LLD, C4 and data-flow diagrams |
| [architecture/payment-api-lld.md](architecture/payment-api-lld.md) | Stage 2 HTTP, persistence, migrations, and idempotency design |
| [adr/ADR-015-stage-2-payment-storage-and-idempotency.md](adr/ADR-015-stage-2-payment-storage-and-idempotency.md) | UUIDs, PostgreSQL, local credentials, and concurrency decisions |
| [runbooks/payment-database.md](runbooks/payment-database.md) | Payment readiness, migration failures, and database recovery |
| [adr/ADR-003-transactional-outbox.md](adr/ADR-003-transactional-outbox.md) | Atomic event writes, TLS/SCRAM Kafka, replay and receipt deduplication |
| [runbooks/event-publication.md](runbooks/event-publication.md) | Outbox retries, SQL dead letters, registry and consumer recovery |
| [adr/ADR-016-local-cross-cluster-kafka.md](adr/ADR-016-local-cross-cluster-kafka.md) | Three independent Minikube clusters, replicated Kafka, budgets and shared-host limitations |
| [adr/ADR-017-full-stack-cross-cluster-replication.md](adr/ADR-017-full-stack-cross-cluster-replication.md) | Opt-in full-stack placement, distributed CNPG, required synchronous durability and controlled recovery |
| [adr/ADR-018-configurable-kubernetes-topology.md](adr/ADR-018-configurable-kubernetes-topology.md) | Portable per-cluster workload counts, local node budgets and unified developer commands |
| [adr/ADR-019-shared-observability-backends.md](adr/ADR-019-shared-observability-backends.md) | Per-cluster OpenTelemetry collectors, shared backends, correlation standards and future lineage boundary |
| [runbooks/observability.md](runbooks/observability.md) | Opt-in Grafana access, metrics-to-traces-to-logs investigation and initial alerts |
| [adr/ADR-020-iteration-baseline-release.md](adr/ADR-020-iteration-baseline-release.md) | Stage 4 frozen module/solution baselines without deployment or PI schedule changes |
| [runbooks/iteration-baseline.md](runbooks/iteration-baseline.md) | Protected manual baseline release, exact digests, conflicts and safe reruns |
| [adr/ADR-021-automatic-release-promotion.md](adr/ADR-021-automatic-release-promotion.md) | Automatic module/solution iteration, RC and stable artifact promotion |
| [adr/ADR-022-pyflink-event-time-semantics.md](adr/ADR-022-pyflink-event-time-semantics.md) | PyFlink, separate synthetic outcomes, revision identities and checkpoint boundaries |
| [adr/ADR-023-clickhouse-analytics.md](adr/ADR-023-clickhouse-analytics.md) | Persistent ClickHouse tables, checkpoint-aligned batch sink, query keys, retention and benchmark |
| [adr/ADR-024-advisory-event-time-risk-features.md](adr/ADR-024-advisory-event-time-risk-features.md) | Optional synthetic context, event-time history, advisory model contract and staged ONNX acceptance |
| [adr/ADR-025-interview-roadmap-and-read-only-mcp.md](adr/ADR-025-interview-roadmap-and-read-only-mcp.md) | Iceberg, Superset and AI assistant first; local identity until Keycloak; read-only MCP tools |
| [runbooks/flink-recovery.md](runbooks/flink-recovery.md) | Synthetic fixtures, lateness, persistent checkpoints and savepoint/restore |
| [runbooks/clickhouse-analytics.md](runbooks/clickhouse-analytics.md) | ClickHouse readiness, sink recovery, replacement queries and failure boundaries |
| [../tests/benchmarks/](../tests/benchmarks/README.md) | Cross-module benchmark goals, targets, setup/cleanup, measured reports and raw JSON |
| [adr/](adr/) | Architecture Decision Records |
| [runbooks/](runbooks/) | Operational procedures |

Cross-module benchmark evidence lives under `tests/benchmarks/`, separately
from operational runbooks. Later stages add `incident-reports/`, `threat-model/`,
and `interviews/` as described in the master plan.
