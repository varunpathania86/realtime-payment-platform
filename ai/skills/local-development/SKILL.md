---
name: local-development
description: "Use when implementing or verifying the local Minikube development loop, Docker image loading, Terraform/Helm deployment, source updates, smoke tests, and clean-cluster recovery."
---

# Verify local application development

1. Read `AGENTS.md`, `ai/instructions/local-development.md`, the current stage in
    `docs/master-plan.md`, and `docs/developer-setup.md`. Stay within stage scope.
2. Run `make ci`. Confirm deterministic unit tests cover new behavior and failure
    paths without relying on a cluster or publication credentials.
3. Choose a dedicated unused Minikube profile; never delete another person's
    profile. Record the name and use it consistently.
4. Run `make dev-up MINIKUBE_PROFILE=<profile>`. Verify API build identity with
    `make smoke MINIKUBE_PROFILE=<profile>`, then the browser status page through
    `make dev-access MINIKUBE_PROFILE=<profile>`. Stop forwarding before the next
    update/smoke command, or use a different `LOCAL_PORT`.
5. Make a deliberate application source edit, run `make dev-update` for that
    profile, and verify a changed image reference and visible new output.
    Restore the verification-only edit and redeploy. Do not revert user changes.
6. Delete only the test profile's API pod, wait for its Deployment rollout, and
    rerun smoke checks to prove recovery.
7. Run `make dev-down` and `make cluster-down` for the test profile, then recreate
    with `make dev-up` and verify again without manual configuration edits.
8. Stop verification-only port forwards. Destroy only the dedicated test stack
    and profile; preserve any user-requested development environment.
9. Record actual commands/results in the task response and master-plan status.
    Distinguish local verification from pending GitHub/GHCR publication. If a
    resource or network limitation prevents verification, report it explicitly;
    do not claim Stage 1 exit criteria passed.
