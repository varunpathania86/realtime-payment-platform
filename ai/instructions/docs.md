# Docs rules

Applies to: all Markdown files (`**/*.md`).

- Start with a single H1; use ATX headings and fenced code blocks with a language.
- Diagrams use Mermaid fenced blocks.
- Link to files with relative paths.
- New documents must follow the matching file in `/templates`.
- Markdown must pass `make lint` (markdownlint-cli2).
