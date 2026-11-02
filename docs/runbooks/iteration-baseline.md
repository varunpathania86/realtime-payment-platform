# Runbook: Release an iteration baseline

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-11-02

## Symptoms

A sprint needs an immutable solution/module baseline, or an interrupted
baseline release must be resumed without rebuilding artifacts.

## Impact

This workflow freezes tested artifacts and creates prerelease GitHub Releases.
It does not deploy, advance the PI schedule, create an RC, or update stable
catalog versions. Stable solution version `0.0.0` can coexist with a prerelease
baseline such as `0.1.0-it1`.

## Diagnosis

1. Confirm this workflow has been merged into `main`. In GitHub Actions select
    **Release iteration baseline**, not **Deploy** or module publication.
2. Verify the selected main commit's **Validate and build modules** and
    **Publish tested module artifacts** runs completed successfully.
3. Inspect the active PI in `solution.yaml`. New baselines must use the current
    UTC iteration. Iteration 1 of PI-2026.4 runs October 26-November 8, 2026.
4. Configure the `release-publication` environment with actual required reviewers
    and access policies. Reusing the environment name does not create reviewers.
    Restrict `automation/release-state` and namespaced release tags against human
    replacement/deletion; allow the trusted workflow its required writes.

## Mitigation

Dispatch from **main** with:

- `source_commit`: the full 40-character main commit SHA to freeze.
- `solution_version`: an explicitly approved independent solution target and
  iteration, for example `0.1.0-it1` (an example, not an automatically selected
  product version).

Approve the environment only after reviewing the selected source and version.
The workflow calls `make release-baseline`; it installs only existing trusted
catalog dependencies and pinned Helm. Do not execute it against a dirty
developer checkout with write credentials.

The workflow discovers all packaged catalog modules, selects the latest
published main ancestor for each and refuses changes since its publication
to module source, tool pins or the catalog. Incomplete image/chart publication,
failed builds or failed publication runs block release.

The first successful freeze records `release-baselines/solution-<version>.json`
on `automation/release-state`. That is a frozen selection, not proof that all
promotion steps finished. The workflow then:

1. Retags each image's exact manifest bytes as `<module-target>-itN`.
2. Retains each chart's original version and digest, including its image pin.
3. Creates `modules/<name>/v<module-target>-itN` annotated tags/releases.
4. Creates `solution/v<solution-target>-itN` and a solution release mapping
    module baseline versions to source commits and image/chart digests.
5. Uploads JSON manifests as workflow artifacts. Durable copies also remain
    in release bodies, annotated tag messages and the frozen state record.

If interrupted, rerun with the **same source SHA and solution version**.
Existing frozen selections are reused even after the iteration boundary.
Conflicting image tags, Git tags, release bodies or source selections fail
instead of being overwritten. Never delete or rewrite state/tags to bypass
a conflict; inspect the original record and registry history.

Only one baseline per module target/iteration can be frozen. A different
solution version is not a way to replace an already frozen module baseline;
conflicting module selections fail. Use subsequent integrated revisions for
ongoing development and the next approved iteration for a new baseline.

## Verification

- The workflow and every promotion/verification step must succeed.
- Inspect the solution GitHub prerelease and its JSON manifest.
- Compare image digests with the selected publication records; baseline and
  integrated image tags must resolve to identical digests.
- Confirm chart digests and package versions remain those from publication.
- Rerun the same inputs: versions/digests and frozen manifests must not change.
- Confirm `solution.yaml`, module stable versions and PI dates did not change.

Offline checks: `make test-catalog` covers release selection, schema validation,
trust gates, conflicts and reruns. `make ci` also validates workflow syntax,
secret detection, tests and builds. They do not prove live GHCR permissions,
GitHub environment protection or a completed release.

## Prevention and follow-up

Keep local `dev-*` commands independent of releases. Deployment is deliberately
not part of this workflow; the existing Deploy workflow is still a placeholder.
RC/production promotion, GitOps, stable-version PRs and cleanup remain later
work. See [ADR-020](../adr/ADR-020-iteration-baseline-release.md) and
[publication recovery](../release-publication.md).
