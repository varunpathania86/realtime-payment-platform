# ADR-002: Monorepo initially

- **Status:** Accepted; release model superseded by [ADR-013](ADR-013-independent-module-releases.md)
- **Date:** 2026-10-07
- **Deciders:** Varun Pathania

## Context

One developer builds several related sub-projects (Go APIs, React portal, Python AI services, streaming jobs, platform code). Independent ownership and release cycles do not exist yet.

## Decision

Keep everything in one repository, `realtime-payment-platform`. Shared rules (lint, templates, CI, and AI instructions) live at the root. The original single-version release decision was superseded by ADR-013; product modules now have independent versions while remaining in this monorepo.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Monorepo | One PR per stage, shared tooling, atomic changes | Larger CI scope as it grows |
| Multiple repositories | Independent releases | Cross-repo coordination overhead for one developer |

## Consequences

- CI must stay fast; path filters may be added when sub-projects grow.
- Split only when independent ownership or releases justify it (see master plan section 4.1).
