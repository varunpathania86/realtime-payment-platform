# ADR-002: Monorepo initially

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Varun Pathania

## Context

One developer builds several related sub-projects (Go APIs, React portal, Python AI services, streaming jobs, platform code). Independent ownership and release cycles do not exist yet.

## Decision

Keep everything in one repository, `realtime-payment-platform`. Each sub-project has its own README and Makefile; shared rules (lint, templates, CI, AI instructions) live at the root. One SemVer tag versions the whole repository.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Monorepo | One PR per stage, shared tooling, atomic changes | Larger CI scope as it grows |
| Multiple repositories | Independent releases | Cross-repo coordination overhead for one developer |

## Consequences

- CI must stay fast; path filters may be added when sub-projects grow.
- Split only when independent ownership or releases justify it (see master plan section 4.1).
