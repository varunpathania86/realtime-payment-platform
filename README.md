# realtime-payment-platform

A production-style, self-hosted learning platform for real-time payment data and AI: Go, React, Kafka, Flink, ClickHouse, Iceberg, and LLM/RAG, running on Terraform and Minikube. It uses synthetic data only.

The full requirements and roadmap are in the [master plan](docs/master-plan.md).

## Status

Stage 0a (repository foundation) is in progress. No sub-projects exist yet.

## Repository map

| Path | Purpose |
|---|---|
| [docs/](docs/README.md) | Master plan, SDD, LLD, ADR, runbooks (Mermaid) |
| [templates/](templates/README.md) | Templates for documents and sub-projects |
| [.github/](.github/copilot-instructions.md) | Workflows, AI instructions, AI skills |
| `apps/`, `packages/`, `platform/`, `streaming/`, ... | Sub-projects, added stage by stage; each has its own `README.md` and `Makefile` |

## Quick start

Prerequisites: `git`, `make`, `python3`, and [pre-commit](https://pre-commit.com).

```bash
git clone git@github.com:varunpathania86/realtime-payment-platform.git
cd realtime-payment-platform
make doctor   # check tools
make hooks    # install git hooks
make ci       # lint, test, build
```

Run `make help` for all targets.

## Workflow

- Branches: `feature/`, `bugfix/`, `patch/`, `hotfix/`; each roadmap stage is one pull request into `main`.
- Versions: SemVer git tags such as `v0.1.3`.
- Details: [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation structure

- This root README is the index.
- Every sub-project has its own README (from [the template](templates/project-readme.md)) describing only that project.
