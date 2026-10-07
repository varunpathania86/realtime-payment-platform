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

Rules:

- Keep all template headings; write "N/A" with a reason instead of deleting a section.
- Use Mermaid for diagrams.
- Sub-project documents live in `<project>/docs/` and use the same templates.
