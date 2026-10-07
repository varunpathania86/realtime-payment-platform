---
name: new-subproject
description: Scaffold a new sub-project (README, Makefile, docs) that follows the repository conventions. Use when adding an app, package, or other project to the monorepo.
---

# Scaffold a sub-project

1. Choose the location under an existing root (`apps/`, `packages/`, `platform/`, `streaming/`, `analytics/`, `ml/`, `knowledge/`, `contracts/`, `tests/`).
2. Copy `templates/project-readme.md` to `<project>/README.md` and fill it in.
3. Copy `templates/project-makefile.mk` to `<project>/Makefile` and implement `build`, `test`, `lint`, `fmt`, `clean`.
4. Create `<project>/docs/` and add an SDD or LLD from `templates/` when design is non-trivial.
5. Add the project to the repository map in the root `README.md`.
6. Run `make projects` to confirm discovery, then `make ci`.
