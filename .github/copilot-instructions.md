# Instructions for AI assistants

## Project

`realtime-payment-platform` is a monorepo for a production-style, self-hosted real-time payment-data and AI platform (Go, React/TypeScript, Python for ML/LLM, Kafka, Flink, ClickHouse, Iceberg, Terraform, Minikube). It uses synthetic data only. The authoritative scope, technology decisions, and roadmap are in `docs/master-plan.md`; read it before proposing designs. Do not add technology the plan excludes.

## Repository structure

- Sub-projects live under `apps/`, `packages/`, `platform/`, `streaming/`, `analytics/`, `ml/`, `knowledge/`, `contracts/`, `tests/`.
- Every sub-project has its own `README.md` and `Makefile`. The root `README.md` is only the index.
- `docs/` holds SDD, LLD, ADR, runbooks; `templates/` holds the templates for them.

## Process rules

1. Work in a branch named `feature/`, `bugfix/`, `patch/`, or `hotfix/` plus kebab-case. Never commit to `main`.
2. Deliver one roadmap stage per pull request; do not start work from a later stage.
3. Versions are SemVer tags with a `v` prefix (`v0.1.3`); artifacts share the version.
4. Commit messages: `type(scope): imperative summary` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`).
5. Keep changes small and focused; do not reformat unrelated files.

## Documentation rules

- Create documents from `templates/` (SDD, LLD, ADR, runbook, project README).
- Use Mermaid for diagrams. Put design docs in `docs/` or the sub-project's `docs/`.
- Record significant decisions as `docs/adr/ADR-NNN-title.md`.
- Update the sub-project README and the root README index when behavior or layout changes.

## Build, test, and static checks

- Every Makefile (root and sub-project) provides `build`, `test`, `lint`, `fmt`, `clean`. The root Makefile runs them across all sub-projects. Use `make`, not ad-hoc commands, in docs and CI.
- Before finishing a change run `make ci` (lint, test, build) and fix all failures.
- Shared lint and format rules live at the root (`.editorconfig`, `.pre-commit-config.yaml`, `.markdownlint-cli2.yaml`). Do not create per-project variants; language linters use one shared config per language at the root.
- Every behavior change needs unit tests in the same change. Tests must be deterministic and runnable offline.
- Never commit secrets, `.env` files, Terraform state, or real personal/cardholder data.

## CI/CD

- Build (`.github/workflows/build.yml`): runs `make ci` on pull requests and `main`.
- Publish (`publish.yml`): triggered by `v*.*.*` tags; creates a GitHub Release and publishes artifacts.
- Deploy (`deploy.yml`): manual and environment-gated; targets a self-hosted runner or VPS configured later.
- Workflow changes must pass `actionlint` (part of `make lint`). Pin third-party actions to a major version or SHA and use least-privilege `permissions`.

## Architecture guardrails

- Go is the primary backend language; Python only where the ML/LLM ecosystem needs it.
- LLMs are never in the payment critical path and never make authorization, approval, or numeric decisions.
- Prefer a modular monolith over adding services; justify new components with an ADR.
