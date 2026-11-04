# Templates

Copy a template instead of writing a document from scratch, so every sub-project documents things the same way.

| Template | Use for | Copy to |
|---|---|---|
| [adr.md](adr.md) | One architecture decision | `docs/adr/ADR-NNN-title.md` |
| [sdd.md](sdd.md) | Software Design Document (system or sub-project level) | `docs/architecture/<name>-sdd.md` |
| [lld.md](lld.md) | Low-Level Design of one component | `docs/architecture/<name>-lld.md` |
| [runbook.md](runbook.md) | Operational procedure | `docs/runbooks/<name>.md` |
| [project-readme.md](project-readme.md) | Sub-project README | `<project>/README.md` |
| [project-makefile.mk](project-makefile.mk) | Sub-project Makefile | `<project>/Makefile` |
| [solution.yaml](solution.yaml) | Solution catalog | `solution.yaml` |
| [module.yaml](module.yaml) | Independently versioned module catalog entry | `modules/<module-name>/module.yaml` |
| [module-release.yaml](module-release.yaml) | Immutable module release record | `releases/modules/<module-name>/<version>.yaml` |
| [solution-release.yaml](solution-release.yaml) | Immutable integrated solution release record | `releases/solutions/<version>.yaml` |
| [development-topology.json](development-topology.json) | Example configurable three-cluster workload/node sizing | Private JSON passed through `DEV_CONFIG` |
| [analytics-benchmark-topology.json](analytics-benchmark-topology.json) | Compact three-cluster 50/100 events/s benchmark allocation; run memory preflight first | `DEV_CONFIG` for the owned benchmark base |
| [analytics-low-memory-topology.json](analytics-low-memory-topology.json) | Same safe node budgets, opt-in coordinated low-memory Flink process/container budgets | `DEV_CONFIG` after memory preflight; does not certify three-cluster fit |

Rules:

- Keep all template headings; write "N/A" with a reason instead of deleting a section.
- Use Mermaid for diagrams.
- Sub-project documents live in `<project>/docs/` and use the same templates.
- A module begins with its catalog manifest; add implementation files only when its deliverable requires them.
