# Docs rules

Applies to: all Markdown files (`**/*.md`).

- Start with a single H1; use ATX headings and fenced code blocks with a language.
- Diagrams use Mermaid fenced blocks.
- Link to files with relative paths.
- New documents must follow the matching file in `/templates`.
- Keep `docs/master-plan.md` authoritative for scope, module boundaries, and release policy; keep `docs/module-development.md` synchronized with module types and manifest semantics.
- When module/release policy changes, synchronize the affected templates, contributor instructions, and AI instructions in the same change. Do not copy an obsolete single-repository-version policy into new docs.
- Markdown must pass `make lint` (markdownlint-cli2).
