# Runbook: Module artifact publication

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-10-21

## Symptoms

A trusted push completes CI but has no artifact, publication is awaiting approval,
or a partial failure leaves a revision reservation without a publication record.

## Impact

This first release slice publishes module OCI images/data bundles only.
`event-contracts` is the first implemented module; modules without Dockerfiles
are explicitly skipped. Helm publication, promotion, cleanup, solution releases,
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
5. Merge the workflows into `main`. `workflow_run` workflows must exist on the
    default branch before they can run. Pushing this feature branch alone does
    not activate the new publication chain.

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

Main state records preserve the digest, source, approved impact, CI test-run URL,
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

After the first approved GitHub run, compare the JSON evidence digest with GHCR,
inspect the OCI index's SBOM/provenance attestations, and rerun the same main
publication. Confirm it uses the identical version and digest without a rebuild.
GitHub/GHCR publication and token/environment configuration cannot be proven by
offline tests; a real approved run is required.

## Prevention and follow-up

Keep all permanent and temporary release state outside stable catalog fields.
Never grant publication to fork PRs. Do not enable the deploy placeholder as a
replacement for publishing or promotion.
Next implement protected iteration/RC/production promotion with exact digest
reuse, solution releases, stable-version PRs, and verified cleanup.
