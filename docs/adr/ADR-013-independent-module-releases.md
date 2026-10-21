# ADR-013: Independently versioned modules in the monorepo

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Varun Pathania

## Context

The solution contains capabilities with different implementation and release cadences, including APIs, UI, streaming jobs, contracts, analytics, an ML model, and LLM knowledge. A single repository-wide version would force unrelated modules to release together and would not identify the exact module artifacts in an integrated solution.

## Decision

Keep one monorepo, catalog solution identity and module names in `solution.yaml`, and give every independently released capability a `modules/<name>/module.yaml` manifest. Module stable versions and the integrated solution stable version are separate. Catalog versions record stable production versions only; release manifests and OCI digests identify iteration, RC, and promoted artifacts.

Module Git tags are namespaced (`modules/<module-name>/v<version>`); solution tags use `solution/v<version>`. Workflows discover modules dynamically and do not infer release behavior from module names or the descriptive `type`.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| One repository-wide version | Simple tagging and release process | Couples unrelated module releases and obscures artifact identity |
| Separate repository per module | Strong repository-level isolation | Excessive coordination and overhead for this single-solution project |
| Monorepo with independent module versions | Atomic cross-module changes with independent release cadence | Requires catalog, dynamic workflows, and explicit dependency/release handling |

## Consequences

- Start module and solution stable versions at `0.0.0`; explicit approved impact determines target versions, with `none` defaulting to patch for now.
- `solution.yaml` is the catalog; every module manifest name must equal its directory name.
- Shared platform infrastructure and cross-module tests remain solution-level unless they acquire an independent product boundary.
- Promotion must reuse immutable OCI digests; production promotion updates stable catalog versions through PRs, never direct pushes to protected `main`.
- The release workflow, schemas, tests, and documentation must enforce idempotency, least privilege, and recovery for partial promotion failures.
- ADR-002 remains the decision to use one monorepo; this ADR supersedes its earlier single-version release detail.
