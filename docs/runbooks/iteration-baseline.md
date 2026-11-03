# Runbook: Release and promote versions

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-11-03

## Symptoms

A tested solution or module needs an immutable iteration baseline, a release
candidate, or a stable production version. An interrupted release must resume
without rebuilding artifacts or selecting a different source.

## Impact

The manually dispatched workflow freezes or promotes exact image/chart
digests. It does not deploy, change the PI schedule, or write stable versions
directly to `main`. Iteration versions and RCs remain prereleases. Stable
releases are non-prerelease GitHub Releases and are followed by a PR that
records the production version in the catalog.

## Diagnosis

1. Confirm this workflow has been merged to `main`. In GitHub Actions select
    **Release and promote versions**, never **Deploy** or module publication.
2. Verify a successful same-repository `main` build for the dispatch commit.
    The workflow source is that exact dispatch commit; no SHA or version is
    entered manually.
3. For an iteration release, verify successful image/chart publication and
    unchanged source since each selected publication. Solution releases select
    every packaged catalog module and preserve the publication's reviewed
    per-module target and impact. A standalone module release inspects only the
    selected module and its dependency/build impact. Its protected dispatch
    `type` explicitly approves that module's impact and target, independently
    of the impact used for the integrated publication.
4. Inspect the active PI in `solution.yaml`. New iteration baselines use its
    current UTC iteration. Existing frozen records remain reusable after the
    calendar advances.
5. Before a stable dispatch, create/configure `release-stable` in repository
    **Settings → Environments**. Add at least one actual user/team under
    **Required reviewers**, save the environment, and verify its protection
    rule is visible. The stable workflow checks this rule through the GitHub
    REST API before promotion writes and again before creating/updating the
    catalog PR. An absent environment, absent reviewer rule, empty reviewer
    list, or API error stops the job. The stable catalog PR job uses the same
    protected environment and therefore requires its own environment approval.
    Before iteration/RC dispatches, configure `release-publication` as required
    by repository policy. Merely naming an environment does not create reviewer
    protection.
6. Preserve `actions: read` for the workflow token so it can inspect the
    environment. A permission-denied API response is a hard failure, not a
    reason to skip the live protection check.
7. Protect `automation/release-state`, release tags, and the stable catalog
    automation branch against unauthorized replacement/deletion. Permit the
    trusted workflow only the writes it needs.

## Mitigation

Dispatch from **main** with:

- `release`: `solution` (default) or `module`.
- `module`: a catalog module name only for a module release; blank for solution.
- `type`: `major`, `minor`, or `patch` (default). This is an explicit impact,
  not a version. The target is calculated from the current stable catalog
  version. The solution impact is independent of every module impact.
- `promote`: `iteration` (default), `release_candidate`, or `stable`.

No source SHA or version is accepted as user input. An iteration release uses
the exact SHA at workflow dispatch. Solution baselines retain each selected
module publication's reviewed target, approved impact, source commit, and
digests; the solution target is calculated independently. For a standalone
module, the protected dispatch's explicit `type` selects the impact and target
from that module's stable catalog version. This is a separate release approval;
it does not rewrite the integrated publication record or change any other
module's target. The frozen module manifest records the selected impact and
retains the source publication's version, target, impact, tests, provenance,
image digest, and chart version/digest as evidence.

The workflow calls `make release-baseline` using trusted tooling from the
workflow's `main` revision and read-only inspection of the selected source.
It checks successful same-repository build/publication evidence and verifies
the recorded OCI digests. It never rebuilds an image or chart.

For `iteration`, a new solution record keeps the established
`release-baselines/solution-<version>.json` shape and namespaced tags. Standalone
module records are stored under `release-baselines/module-<name>-<version>.json`.
Charts retain their original package version and digest because changing chart
metadata would change the approved artifact bytes.

For `release_candidate`, the workflow chooses the latest eligible frozen
iteration baseline for the same target, then records an immutable
`<target>-rcN` promotion under `release-promotions/`. It reuses the same
candidate when that target already has an RC from that frozen baseline. For
`stable`, it selects the highest eligible RC for the target, rejects conflicts
or ambiguity, and creates a non-prerelease release from the same source and
digests. Stable promotion runs behind the separate `release-stable` environment.
After promotion succeeds, a narrower-permission job opens a PR changing only
the stable `version` field in `solution.yaml` or the selected module's
`module.yaml`. The PR branch is under `automation/`; `main` is never written
directly.

If interrupted, use **Re-run jobs** on the original workflow run. Frozen
records are write-once and reruns reuse the original source, baseline/RC
selection, version and digests even if `main` or the PI iteration has moved.
Do not delete state, tags, or releases to bypass a conflict. An ambiguous
baseline/RC or mismatched catalog is a stop condition; inspect the immutable
state and resolve it through reviewed repository changes.

## Verification

- Confirm every promotion/verification step succeeds and inspect the JSON
  manifest artifact.
- Compare image digests with the selected publication records. Each promoted
  image tag must resolve to the original digest; chart digests and package
  versions remain unchanged.
- Confirm iteration and RC releases are prereleases, stable releases are not,
  and every Git tag/release maps to the recorded source commit and manifest.
- Confirm the stable catalog PR changes exactly one stable `version` field.
  Merge it through normal review; the workflow never writes `main`.
- Confirm `solution.yaml`, module stable versions, and PI dates remain unchanged
  until the catalog PR is reviewed and merged.

Offline checks:

- `python3 -m unittest tests.unit.test_release_baseline tests.unit.test_release_promotion -q` — deterministic baseline, selection, promotion, catalog-PR, and conflict tests.
- `actionlint .github/workflows/release-baseline.yml` — workflow syntax and expression validation.
- `make ci` — repository lint, catalog validation, unit tests, module tests, and builds.

These checks do not prove live GitHub environment protection, GHCR permissions,
or a successful remote promotion. No live dispatch is part of offline testing.

## Prevention and follow-up

Keep local `dev-*` commands independent of publication/release approvals.
Deployment and GitOps remain deferred. Configure real environment reviewers
and branch/tag protections in repository settings; workflow YAML alone cannot
establish those controls. See [ADR-021](../adr/ADR-021-automatic-release-promotion.md)
and [publication recovery](../release-publication.md).
