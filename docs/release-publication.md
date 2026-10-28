# Runbook: Module artifact publication

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-10-25

## Symptoms

A trusted push completes CI but has no artifact, publication is awaiting approval,
or a partial failure leaves a revision reservation without a publication record.

## Impact

Stage 1 publishes module OCI images/data bundles and accompanying Helm charts.
Modules without Dockerfiles are explicitly skipped; modules without charts
remain image-only, such as `event-contracts`. Promotion, cleanup, solution releases,
Git tags/GitHub Releases, and automated stable-version PRs are **not implemented**.
The existing deployment workflow is still a placeholder.

Branch tags are replaceable; main revision tags must not change digest.
Neither kind of publication changes stable versions in catalogs.

Local application development is a separate path: `make dev-up` and
`make dev-update` build host Docker images, load them into Minikube, and deploy
through Terraform/Helm. They require no GHCR login, GitHub Actions, approvals,
release reservations, or active PI. A failure here does not block local work.
See the [developer guide](developer-setup.md#4-local-application-development).
Application image builds receive toolchain pins from `.tool-versions` plus
version/commit build arguments in both local and publication paths.

## Diagnosis

### Events and responsibilities

| Event | Workflow | Responsibility |
|---|---|---|
| PR update, including forks | `build.yml` | Read-only validation, tests, and affected-module matrix |
| Non-automation branch push | `build.yml` | Same checks, without publishing credentials |
| Successful push CI completion | `publish.yml` | Use release tooling from the default-branch commit for this run; calculate publication matrix |
| Each packaged affected module | `publish-module.yml` | Protected publication, optional revision reservation, digest verification, evidence |
| State-branch update | None | Excluded from build triggers; never publishes |

Publication uses `workflow_run` so fork PRs never enter a credentialed path.
The workflow verifies the source run was a successful push in this repository,
checks out its exact tested SHA separately, and does not execute its Python
release scripts. Dockerfiles from that SHA execute only after environment
approval, on GitHub-hosted runners. Review source and Dockerfile changes before
approving; same-repository branch authors must be trusted.

The source and tooling checkouts do not persist checkout credentials.
The publisher receives repository-scoped `GITHUB_TOKEN` permissions:
`contents: write` for the state branch and `packages: write` for GHCR.
Planning additionally needs read access to Actions and pull requests.
No PAT, team, organization, or self-hosted runner is required.

### Required GitHub configuration

Before merging this slice:

1. Create the GitHub Environment **`release-publication`** under repository
    Settings → Environments and add an appropriate required reviewer.
    For a solo repository, allow the owner to approve their own runs.
    An environment name alone does **not** configure required approvals.
2. Protect `main`: require PRs, owner review where practical, and passing CI.
    Require review of `releases/impacts.yaml`, workflows, scripts, schemas, and
    `solution.yaml`; the repository CODEOWNERS assigns the owner to all paths.
3. Protect `automation/release-state` from human changes, force pushes, and
    deletion while allowing the workflow to create/update its state file.
    Do not require a PR for this automation-only branch.
4. Ensure repository Actions policy permits the job's requested token permissions.
    GHCR image roots are configured explicitly in `solution.yaml`.
    Check package Actions access if reusing an already-existing package.
5. Merge reviewed workflow/tooling updates into `main`. `workflow_run` workflows must exist on the
    default branch before they can run. Pushing this feature branch alone does
    not activate new publication tooling. In particular, the existing image-only
    default-branch tooling cannot publish charts just because this feature branch
    contains chart-publication code. No unreviewed tooling bootstrap is used.
6. Permit approved publication branches in the environment's deployment branch
    policy if branch artifacts are desired. A `main`-only policy blocks branch
    jobs even after PI start. Ensure Actions package access for the chart
    repositories under `registries.charts`, as well as image repositories.

No repository settings are changed automatically by this implementation.

### Versions and approved impact

The reviewed policy is `releases/impacts.yaml` on the trusted default-branch
tooling revision, not the source branch. Default impact is explicitly `patch`;
module overrides may be `none`, `patch`, `minor`, or `major`. `none` currently
means patch. Owner review of the policy plus environment approval is the approval
boundary. Do not infer impact from commit or branch text.

For example, a reviewed override `event-contracts: minor` proposes `0.1.0` from
`0.0.0`; without it the target is `0.0.1`. Overrides remain until explicitly
changed by a reviewed PR. The selected impact is retained in publication evidence.

Iteration is calculated from the current UTC date:

```text
floor((UTC date - PI start date) / iterationLengthDays) + 1
```

Publication fails before PI start and after its last day. The current PI starts
**2026-10-26**; refusal to publish an iteration artifact on 2026-10-21 is expected.
CI and offline module builds still run before PI start. Do not change the PI
schedule just to bypass the boundary check.

Normal branch tags use `<target>-itN-br.<hash>`.
The hash is the first four lowercase SHA-256 hexadecimal characters of the NFC
normalized branch name after removing `refs/heads/`; case is preserved.
It is not a hash of the commit. Different commits on the same branch reuse the
tag. Hash collisions with another branch fail rather than overwrite its artifact.
Stale branch runs also fail rather than overwrite a newer branch artifact.

Main commits use `<target>-itN-rv.<counter>`. RC-fix branches use
`<target>-rcN-br.<hash>` only when an explicit PR target, the maintenance branch
itself, or unambiguous ancestry identifies `release/<module>/<target>-rcN`.
The existence of an RC branch alone does not classify normal branches as fixes.
Ambiguous ancestry fails; open a PR to the intended maintenance branch.
RC fixes publish only the maintenance module and reject edits to other modules.
RC maintenance does not use the PI clock. Integrated RC revisions are not yet
published automatically; RC creation/promotion remains a later stage.

### Reservation and artifact authority

Main reservations live in `release-reservations.json` on
`automation/release-state`, outside protected `main` and outside `solution.yaml`.
They are validated against `schemas/release-state.schema.json`.

- Counter scope: module + target version + iteration + release line.
- Idempotency: module + source commit + release line. Reruns reuse their frozen
  target, iteration, impact, registry, and reserved version even if policy or the
  current iteration subsequently changes.
- New reservations also freeze the OCI chart registry. Existing image-only state
  remains valid; completed legacy reservations without a chart registry rerun as
  image-only. An incomplete old reservation with no frozen chart registry requires
  explicit recovery rather than silently selecting today's registry. Do not rewrite
  historical reservations.
- The GitHub Contents API compares the existing file's blob SHA on each update.
  Conflicts reread state and retry; they cannot overwrite another reservation.
- Distinct main commits can publish concurrently; atomic state updates allocate
  distinct counters. Concurrency serializes duplicate commits and branch retags
  without canceling an active publication. Superseded pending branch runs may
  be dropped by GitHub; the latest source is the intended branch artifact.
- Failed reservations may leave gaps. Never recycle a counter or delete state.

GHCR is authoritative for artifacts. Buildx pushes the image with SBOM and
provenance attestations, source/version/branch index annotations, then the
publisher rereads the tag and verifies its OCI digest.
The scratch contract bundle contains `/contracts/` and is not executable.
There is no chart for this non-runtime module.

### Helm chart publication and partial recovery

For an image-producing module with `chart/Chart.yaml`, the publisher:

1. Copies its chart to a temporary directory; source files stay unchanged.
2. Requires chart name to match module name, sets `version` and `appVersion` to
    the calculated module version, and adds source-commit/branch annotations.
3. Sets image values to the verified GHCR repository and OCI digest, the release
    tag for reference, and pull policy `IfNotPresent`. The Deployment must render
    the image as `repository@sha256:...`; local `Never` defaults do not leak into
    the published package.
4. Lints/renders the chart, packages it, authenticates Helm using the job token
    via stdin, and pushes to `registries.charts/<module>`.
5. Compares the digest reported by Helm with the registry manifest digest.
    Publication evidence records an additional `chart` object containing its
    repository, version, and digest.

Images and charts share a module version but have different OCI repositories
and digests. Main revision chart tags are immutable; branch chart tags may move.
Local source chart versions are packaging scaffolding, not stable catalog versions.
Promotion will need to preserve both digests without rebuilding; it is not
implemented by this slice.

If the image succeeds but chart publication fails, no completed publication
record is written. Rerun: matching-source image annotations allow image reuse,
and only missing chart work is repeated. If the chart was already pushed before
state recording failed, the publisher pulls it by digest and checks its
source/branch metadata and exact image reference before reuse. Wrong-source
main tags, branch-hash collisions, changed/missing recorded digests, or chart
values that reference another image fail closed. Never delete a reservation
or overwrite an immutable artifact to recover.

Main state records preserve image and optional chart digests, source, approved impact, CI test-run URL,
publication run URL, and SBOM/provenance index references. Every publication
uploads JSON evidence to the Actions run for 90 days. Branch evidence is temporary;
branch tags may move. Main state is durable; neither state nor artifacts are
deleted by this slice. These are publication records, not promoted release
manifests; promotion will create the immutable release records and namespaced tags.

## Mitigation

1. Find the original successful **Validate and build modules** run and the
    associated **Publish tested module artifacts** run under Actions.
2. If waiting for approval, inspect its source and approve `release-publication`.
3. If before PI start, wait until the configured date. If the schedule is
    genuinely wrong, correct it through a reviewed catalog PR.
4. For a reservation/build failure, rerun the publication workflow. Its reserved
    main version is reused. If the image was already pushed before the state
    update failed, the publisher verifies the matching source annotation and
    records the existing digest without rebuilding.
5. If an immutable tag is missing or changed after it was recorded, the workflow
    fails closed. Investigate GHCR/state history; do not overwrite the state,
    retag a rebuild, or treat a newly built digest as the approved artifact.
6. For a branch advanced while its job waited, use the newest push CI/publication
    run. Do not rerun stale branch publication.

## Verification

Run the offline checks:

```bash
make test-catalog
make -C modules/event-contracts test
make -C modules/event-contracts build
make ci
```

After the first approved GitHub run, compare the JSON evidence image and chart digests with GHCR,
inspect the OCI index's SBOM/provenance attestations, and rerun the same main
publication. Confirm it uses the identical version and both digests without a rebuild/repackage.
For an application, inspect the pulled chart's rendered Deployment to confirm it
references the exact verified image digest, not a local development tag.
GitHub/GHCR publication and token/environment configuration cannot be proven by
offline tests; a real approved run is required.

## Prevention and follow-up

Keep all permanent and temporary release state outside stable catalog fields.
Never grant publication to fork PRs. Do not enable the deploy placeholder as a
replacement for publishing or promotion.
Stage 17 owns protected iteration/RC/production promotion with exact digest
reuse, solution releases, stable-version PRs, and verified cleanup.
