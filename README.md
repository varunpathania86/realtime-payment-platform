# realtime-payment-platform

A production-style, self-hosted learning platform for real-time payment data and AI: Go, React, Kafka, Flink, ClickHouse, Iceberg, and LLM/RAG, running on Terraform and Minikube. It uses synthetic data only.

The full requirements and roadmap are in the [master plan](docs/master-plan.md).

## Status

The repository foundation is in place. The catalog currently defines 15 independently versioned modules; module implementations are added incrementally from the [master plan](docs/master-plan.md).

## Repository map

| Path | Purpose |
|---|---|
| [docs/developer-setup.md](docs/developer-setup.md) | Developer environment setup |
| [docs/](docs/README.md) | Master plan, SDD, LLD, ADR, runbooks (Mermaid) |
| [templates/](templates/README.md) | Templates for documents and sub-projects |
| [solution.yaml](solution.yaml) | Solution identity, stable version, PI schedule, modules, and registry roots |
| [modules/](modules/) | Independently versioned module manifests and, as implemented, their source and packaging |
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
make cluster-up  # start local Minikube (needs Docker)
make ci       # lint, test, build
```

Run `make help` for all targets.

## Workflow

- Branches: `feature/`, `bugfix/`, `patch/`, `hotfix/`; each roadmap stage is one pull request into `main`.
- Versions: modules and the integrated solution have separate lifecycles; see [module-development.md](docs/module-development.md).
- Details: [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation structure

- This root README is the index.
- Module READMEs are added as module implementations arrive, using [the template](templates/project-readme.md), and describe only that module.
