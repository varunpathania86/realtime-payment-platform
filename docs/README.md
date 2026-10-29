# Documentation

All design documentation lives here, written in Markdown with Mermaid diagrams. Create new documents from [`/templates`](../templates/README.md).

| Folder / file | Content |
|---|---|
| [master-plan.md](master-plan.md) | Frozen requirements, conventions, and staged roadmap |
| [module-development.md](module-development.md) | Module boundaries, catalog types, and adding modules |
| [release-publication.md](release-publication.md) | Trusted OCI publication, activation, counters, and recovery |
| [architecture/repository-overview.md](architecture/repository-overview.md) | Module catalog and delivery flow |
| [developer-setup.md](developer-setup.md) | Developer environment setup (`make setup`) |
| [adr/ADR-001-terraform-local-development.md](adr/ADR-001-terraform-local-development.md) | Terraform ownership and independent local image/deployment loop |
| [runbooks/local-development.md](runbooks/local-development.md) | Local image, deployment, ingress, and recovery troubleshooting |
| [runbooks/security-checks.md](runbooks/security-checks.md) | Separate solution/module IaC scans and advisory merge policy |
| [adr/ADR-014-stage-1-artifact-and-security-boundaries.md](adr/ADR-014-stage-1-artifact-and-security-boundaries.md) | Stage 1 image/chart publication and quality/security separation |
| [architecture/](architecture/) | SDD, LLD, C4 and data-flow diagrams |
| [architecture/payment-api-lld.md](architecture/payment-api-lld.md) | Stage 2 HTTP, persistence, migrations, and idempotency design |
| [adr/ADR-015-stage-2-payment-storage-and-idempotency.md](adr/ADR-015-stage-2-payment-storage-and-idempotency.md) | UUIDs, PostgreSQL, local credentials, and concurrency decisions |
| [runbooks/payment-database.md](runbooks/payment-database.md) | Payment readiness, migration failures, and database recovery |
| [adr/](adr/) | Architecture Decision Records |
| [runbooks/](runbooks/) | Operational procedures |

Later stages add `benchmarks/`, `incident-reports/`, `threat-model/`, and `interviews/` as described in the master plan.
