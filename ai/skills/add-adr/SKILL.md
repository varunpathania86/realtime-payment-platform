---
name: add-adr
description: Create a new Architecture Decision Record from the repository template. Use when a significant technical decision is made or changed.
---

# Add an ADR

1. Find the next number: list `docs/adr/` and take the highest `ADR-NNN` plus one.
2. Copy `templates/adr.md` to `docs/adr/ADR-NNN-kebab-title.md`.
3. Fill every section; set Status and today's date.
4. If it supersedes another ADR, update that ADR's status to `Superseded by ADR-NNN`.
5. Run `make lint`.
