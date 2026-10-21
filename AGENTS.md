# Instructions for AI assistants

This file is the single, tool-neutral source of project instructions, using the open [AGENTS.md](https://agents.md) convention. Do not create tool- or vendor-named instruction files; keep everything in this file and in `ai/`.

- Path-specific rules: `ai/instructions/` (read the matching file before editing those paths).
- Reusable skills (step-by-step procedures): `ai/skills/<name>/SKILL.md`.

## Project

`realtime-payment-platform` is a monorepo for a production-style, self-hosted real-time payment-data and AI platform (Go, React/TypeScript, Python for ML/LLM, Kafka, Flink, ClickHouse, Iceberg, Terraform, Minikube). It uses synthetic data only. The authoritative scope, technology decisions, and roadmap are in `docs/master-plan.md`; read it before proposing designs. Do not add technology the plan excludes.

## Repository structure

- Independently versioned product modules live under `modules/<module-name>/`; `solution.yaml` catalogs them, and every module has `module.yaml` whose `name` matches its folder.
- The module manifest may precede implementation files. Add a README, Makefile, Dockerfile, Helm chart, or language-specific structure only when the module's implementation needs it.
- Module `type` is descriptive (such as API, worker, stream processing, library, contract, model, or knowledge), not a workflow switch. Its meaning is documented in `docs/module-development.md`.
- Shared solution infrastructure, cross-module tests, docs, scripts, and workflows remain outside module directories. See `docs/master-plan.md` Section 4 and `docs/module-development.md`.
- `docs/` holds SDD, LLD, ADR, runbooks; `templates/` holds the templates for them.

## Process rules

1. Work in a branch named `feature/`, `bugfix/`, `patch/`, or `hotfix/` plus kebab-case. Never commit to `main`.
2. Deliver one roadmap stage per pull request; do not start work from a later stage.
3. Modules and the integrated solution are independently versioned. Catalog versions are stable-production versions only; follow the release lifecycle and namespaced tag rules in `docs/master-plan.md` and `docs/module-development.md`.
4. Commit messages: `type(scope): imperative summary` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`).
5. Keep changes small and focused; do not reformat unrelated files.

## Documentation rules

- Create documents from `templates/` (SDD, LLD, ADR, runbook, project README).
- Use Mermaid for diagrams. Put design docs in `docs/` or the sub-project's `docs/`.
- Record significant decisions as `docs/adr/ADR-NNN-title.md`.
- Update the sub-project README and the root README index when behavior or layout changes.
- When adding or renaming a module, update both `solution.yaml` and `modules/<name>/module.yaml`; workflow logic must discover modules dynamically, never use hard-coded module names or infer behavior from the descriptive `type`.

## Build, test, and static checks

- Implemented modules with a Makefile provide `build`, `test`, `lint`, `fmt`, `clean`; catalog-only modules need no Makefile yet. The root Makefile runs shared solution checks, and CI runs implemented affected modules dynamically. Use `make`, not ad-hoc commands, in docs and CI.
- Tool versions are pinned in `.tool-versions`; `make setup` installs them and `make doctor` checks them. Change versions there only.
- **Developer dependencies must go through `make setup`.** Whenever a change introduces a new developer dependency (CLI tool, SDK, runtime, linter, system package, pre-commit hook, container runtime, and so on), in the same change you must:
  1. Install it from `make setup` (via `scripts/install-tools.sh` or another script in `scripts/` called from the Makefile), pinned to an exact version in `.tool-versions` where applicable. Downloads must be checksum-verified, idempotent, and avoid `sudo` unless unavoidable.
  2. Verify it in `scripts/doctor.sh` (`make doctor`) with a clear hint on how to fix a problem.
  3. Document it in `docs/developer-setup.md`.
  Developers must never need undocumented manual installation steps; a fresh clone plus `make setup` must be enough. Project-level package managers (`go.mod`, `package.json`, `requirements`) are fine for libraries, but the toolchains that run them belong in `make setup`.
- Shell logic longer than a few lines goes into `scripts/*.sh` (checked by ShellCheck) and is called from the Makefile.
- Before finishing a change run `make ci` (lint, test, build) and fix all failures.
- Shared lint and format rules live at the root (`.editorconfig`, `.pre-commit-config.yaml`, `.markdownlint-cli2.yaml`). Do not create per-project variants; language linters use one shared config per language at the root.
- Every behavior change needs unit tests in the same change. Tests must be deterministic and runnable offline.
- Never commit secrets, `.env` files, Terraform state, or real personal/cardholder data.

## CI/CD

- Catalog validation (`.github/workflows/validate-catalog.yml`): reusable read-only workflow validating solution, module, and release manifests.
- Build (`.github/workflows/build.yml`): runs `make ci` and a dynamic affected-module matrix on pull requests and branch pushes. Fork PRs remain read-only and build/test-only.
- Publication (`publish.yml` / `publish-module.yml`): successful same-repository push CI triggers protected GHCR publication using trusted default-branch tooling. Main revisions use atomic GitHub Contents API reservations on `automation/release-state`; branch tags are replaceable. Read `docs/release-publication.md` before changing this path. Promotion, cleanup, and stable-version PRs are not implemented yet.
- Do not add a repository-wide `v*` publishing path. Preserve idempotent versions/digests, read-only fork CI, current-UTC PI boundaries, reviewed per-module impacts, and the separation between tested source and trusted publication tooling.
- Deploy (`deploy.yml`): manual and environment-gated; targets a self-hosted runner or VPS configured later.
- Workflow changes must pass `actionlint` (part of `make lint`). Pin third-party actions to a major version or SHA and use least-privilege `permissions`.

## Architecture guardrails

- Go is the primary backend language; Python only where the ML/LLM ecosystem needs it.
- LLMs are never in the payment critical path and never make authorization, approval, or numeric decisions.
- Prefer a modular monolith over adding services; justify new components with an ADR.
