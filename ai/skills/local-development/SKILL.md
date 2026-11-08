---
name: local-development
description: "Use when implementing or verifying the local Minikube development loop, Docker image loading, Terraform/Helm deployment, source updates, smoke tests, and clean-cluster recovery."
---

# Verify local application development

1. Read `AGENTS.md`, `ai/instructions/local-development.md`, the current stage in
    `docs/master-plan.md`, and `docs/developer-setup.md`. Stay within stage scope.
2. Run `make ci`. Confirm deterministic unit tests cover new behavior and failure
    paths without relying on a cluster or publication credentials.
3. Use only the fixed single profile `rpp` or the three profiles
    `rpp-1`/`rpp-2`/`rpp-3` selected by `MULTI_CLUSTER=3`; do not create
    alternate profiles or delete unrelated user data.
4. Run `make dev-up MULTI_CLUSTER=3`. Verify API build identity with
    `make smoke MULTI_CLUSTER=3 MINIKUBE_PROFILE=rpp-1`, then the browser status
    page through `make dev-access MULTI_CLUSTER=3 MINIKUBE_PROFILE=rpp-1`. Leave forwarding running during
    update/smoke checks to verify automatic smoke-port selection does not compete
    with browser access. `LOCAL_PORT` controls browser access only.
5. Make a deliberate application source edit, run `make dev-update MULTI_CLUSTER=3`,
    and verify changed image references and visible output.
    Restore the verification-only edit and redeploy. Do not revert user changes.
6. Delete only the test profile's API pod, wait for its Deployment rollout, and
    rerun smoke checks to prove recovery.
7. Run `make dev-clean MULTI_CLUSTER=3` only when all profile data is disposable,
    then recreate with `make dev-up MULTI_CLUSTER=3` and verify again.
8. Stop verification-only port forwards. Destroy only the dedicated test stack
    and profile; preserve any user-requested development environment.
9. Record actual commands/results in the task response and master-plan status.
    Distinguish local verification from pending GitHub/GHCR publication. If a
    resource or network limitation prevents verification, report it explicitly;
    do not claim Stage 1 exit criteria passed.
