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

- SemVer with a `v` prefix on tags: `v0.1.3`. Artifacts (images, charts) use the same version.
- Release: merge to `main`, then `git tag -a vX.Y.Z -m "vX.Y.Z"` and `git push origin vX.Y.Z`. The Publish workflow creates the GitHub Release.

## Local checks

```bash
make setup   # once: see docs/developer-setup.md
make lint    # shared static checks and per-project lint
make test
make build
make ci      # all of the above
```

## Adding a sub-project

1. Create the directory under `apps/`, `packages/`, or another root listed in the root [Makefile](Makefile).
2. Copy [templates/project-readme.md](templates/project-readme.md) and [templates/project-makefile.mk](templates/project-makefile.mk).
3. Implement `build`, `test`, `lint`, `fmt`, `clean` in its Makefile; the root Makefile discovers it automatically.
4. Document design in its `docs/` folder using the [templates](templates/README.md).

## Documentation

Write design docs in `docs/` with Mermaid diagrams, from the templates. Record significant decisions as ADRs in `docs/adr/`.
