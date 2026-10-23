# realtime-payment-platform

A production-style, self-hosted learning platform for real-time payment data and AI: Go, React, Kafka, Flink, ClickHouse, Iceberg, and LLM/RAG, running on Terraform and Minikube. It uses synthetic data only.

The full requirements and roadmap are in the [master plan](docs/master-plan.md).

## Status

The repository foundation is in place. The catalog currently defines 15 independently versioned modules; module implementations are added incrementally from the [master plan](docs/master-plan.md).

[Event contracts](modules/event-contracts/README.md) is the first implementation:
a synthetic Avro payment event, offline tests, and a non-runtime OCI data bundle.

Stage 1 adds the minimal [Go API](modules/payment-api/README.md) and
[React status portal](modules/web-portal/README.md), deployed locally with
Terraform, Helm, and Traefik. Payment commands and PostgreSQL come in Stage 2.

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

## Quick start

Full environment instructions: [docs/developer-setup.md](docs/developer-setup.md).

```bash
git clone git@github.com:varunpathania86/realtime-payment-platform.git
cd realtime-payment-platform
make setup    # install hooks and pinned tools, then check the environment
make ci       # lint, test, build
make dev-up   # start Minikube, build/load local images, deploy, smoke-test
make dev-access  # foreground: open http://127.0.0.1:8080; Ctrl-C stops access
```

After editing application code, stop `dev-access`, run `make dev-update`, and
restart `make dev-access`. Alternatively use another `LOCAL_PORT` for smoke tests.
Local work needs **no GitHub Actions, GHCR login, publication approval, or active
PI**. A failed remote workflow does not disable local commands. Initial setup,
image/dependency downloads, and Terraform/chart initialization need internet;
local development is independent of publication, not a guarantee of air-gapped operation.

`make dev-down` destroys the local stack but leaves Minikube running.
`make cluster-down` deletes the cluster and all its data. See the
[developer guide](docs/developer-setup.md#4-local-application-development) for
profiles, image loading, Terraform commands, recovery, and recreation.

Run `make help` for all targets.

Catalog validation is available through `make validate-catalog`; `make ci` includes catalog validation and unit tests. CI has a dynamic affected-module matrix; root checks also validate implemented modules. Trusted push CI can trigger protected branch/main OCI publication after the workflows are merged into `main` and the environment is configured. See the [publication runbook](docs/release-publication.md). Promotion and cleanup remain later phases.

## Workflow

- Branches: `feature/`, `bugfix/`, `patch/`, `hotfix/`; each roadmap stage is one pull request into `main`.
- Versions: modules and the integrated solution have separate lifecycles; see [module-development.md](docs/module-development.md).
- Details: [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation structure

- This root README is the index.
- Module READMEs are added as module implementations arrive, using [the template](templates/project-readme.md), and describe only that module.
