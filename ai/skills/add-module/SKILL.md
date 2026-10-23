---
name: add-module
description: Add an independently versioned module to the solution catalog and scaffold only the files appropriate to its deliverables.
---

# Add a module

1. Read `docs/master-plan.md` Section 4 and `docs/module-development.md`; verify that the proposed capability deserves an independent version lifecycle.
2. Choose a unique kebab-case module name and add it to `solution.yaml`.
3. Create `modules/<module-name>/module.yaml` from `templates/module.yaml`. Keep its `name` identical to the directory, start stable `version` at `0.0.0` for a new module, identify owners, and declare only actual module dependencies.
4. Choose the best-fit descriptive `type` from the module guide. Never make workflow behavior depend on the type string or hard-code this module name in workflow logic.
5. Do not scaffold runtime files by default. Add README, Makefile, source, tests, Dockerfile, Helm chart, or ecosystem package metadata when the module's implementation and outputs require them. Library, contract, model, and knowledge modules do not automatically need container images or charts.
6. If implementation adds a developer dependency, wire it through `make setup`, `make doctor`, and `docs/developer-setup.md` following `AGENTS.md`.
7. Update the root README and architecture documentation if the module catalog or module boundaries change.
8. Run the repository catalog validation and `make ci` when those validation targets are available.
9. If adding OCI packaging, read `docs/release-publication.md`. Publication discovers Dockerfiles dynamically, uses reviewed impact metadata, and skips unpackaged modules; do not add a per-module workflow. Non-runtime artifacts may use a data-only image without a Helm chart.
10. For runnable applications, add a module-owned `chart/Chart.yaml` and chart
    templates with probes, restricted security contexts, and resource bounds.
    The local workflow discovers Dockerfile/chart pairs from the catalog.
    Follow `ai/instructions/local-development.md`; verify local build/load/deploy
    without publication credentials or an active PI.
