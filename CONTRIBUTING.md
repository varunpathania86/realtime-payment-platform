# Contributing

## Branches

Create branches from an up-to-date `main`:

| Prefix | Use |
|---|---|
| `feature/` | New functionality or a roadmap stage, e.g. `feature/stage-1-terraform-foundation` |
| `bugfix/` | Fix for a defect found before release |
| `patch/` | Small non-breaking improvement or maintenance |
| `hotfix/` | Urgent fix for a released version |

Use kebab-case after the prefix. Never commit directly to `main`.

## Commits and pull requests

- Commit messages use the imperative mood with an optional scope: `feat(payment-api): add healthz endpoint`. Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.
- One pull request per stage or change, squash-merged. The PR must pass the Build workflow.
- Update the README and docs affected by the change.

## Versioning and releases

- Modules and the integrated solution are independently versioned. `module.yaml.version` and `solution.yaml.solution.version` record stable production versions only; initial manifests start at `0.0.0`.
- Use the module and solution lifecycle in [docs/module-development.md](docs/module-development.md). Module Git tags are namespaced (`modules/<module>/v<version>`); solution tags use `solution/v<version>`.
- Do not manually assign iteration, branch, revision, or RC numbers, and do not rebuild during promotion. The protected promotion workflow calculates versions and promotes immutable OCI digests.
- After production promotion, automation opens a PR to update the stable version field. Never update protected `main` directly.

## Local checks

```bash
make setup   # once: see docs/developer-setup.md
make lint    # shared static checks and per-project lint
make test
make build
make ci      # all of the above
```

For application changes, also verify the local development loop:

```bash
make dev-up
make dev-update
make smoke
make dev-access  # foreground browser access; Ctrl-C stops
```

Leave access forwarding running during updates; smoke checks automatically use
a separate available loopback port.
These commands require no GitHub workflow, GHCR login, approval, or active PI.
Use a dedicated `MINIKUBE_PROFILE` for destructive recreation tests.
See [the developer guide](docs/developer-setup.md#4-local-application-development)
for image loading, state ownership, and teardown.

TFLint is part of `make lint` and `make ci`. Run `make security` separately for
Trivy IaC reports, or `make -C modules/<name> security` for one packaged module.
The security workflow reports failures truthfully but is initially advisory in
merge rules; it must not mask findings or replace existing secret checks.
See [the security runbook](docs/runbooks/security-checks.md).
Use `make quality-report` when you want saved Markdown/log output from `make ci`.
Security commands save Markdown alongside JSON automatically. See
[report locations](docs/developer-setup.md#quality-and-security-reports).

## Developer dependencies

Any new developer dependency (tool, runtime, linter, system package) must be installed by `make setup`, verified by `make doctor`, and documented in [docs/developer-setup.md](docs/developer-setup.md), in the same pull request. Pin versions in [.tool-versions](.tool-versions).

## Adding a module

1. Confirm the boundary against [Section 4 of the master plan](docs/master-plan.md#4-repository-strategy); avoid creating a module just to mirror an infrastructure component.
2. Add the kebab-case module name to `solution.yaml` and create `modules/<name>/module.yaml` from [templates/module.yaml](templates/module.yaml). Keep the folder and manifest names identical.
3. Declare only real module dependencies. The manifest `version` is the current stable production version, not an iteration or RC version.
4. Add code, README, Makefile, Dockerfile, Helm chart, and tests as appropriate to what the module actually builds or deploys. Libraries, contracts, models, and knowledge data do not automatically need images or charts.
5. Document the module boundary and design using [docs/module-development.md](docs/module-development.md) and the [templates](templates/README.md).

## AI assistants

Project instructions for AI tools live in [AGENTS.md](AGENTS.md), the single source for all tools, with rules and skills in [ai/](ai/). Do not create vendor- or tool-named instruction files.

## Documentation

Write design docs in `docs/` with Mermaid diagrams, from the templates. Record significant decisions as ADRs in `docs/adr/`.
