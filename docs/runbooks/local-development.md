# Runbook: Local application deployment failure

- **Severity:** Low
- **Owner:** varunpathania86
- **Last reviewed:** 2026-10-23

## Symptoms

`make dev-up`, `make dev-update`, or `make smoke` fails; the browser cannot open
the portal; a change does not appear; or a remote workflow is failing.

## Impact

Only the selected local Minikube profile is involved. Remote CI, GHCR publication,
approval, PI boundaries, and the deploy placeholder are not prerequisites for
local application development.

## Diagnosis

1. Run `make doctor`, then `make cluster-status` and `make dev-status`. Pass the
    same `MINIKUBE_PROFILE` used during deployment to every command.
2. Inspect the affected deployment:

    ```bash
    kubectl --context rpp -n apps describe deployment payment-api
    kubectl --context rpp -n apps get pods
    kubectl --context rpp -n apps logs deployment/payment-api
    kubectl --context rpp -n platform logs deployment/traefik
    ```

3. `ErrImageNeverPull` means the image is not loaded into this profile. Run
    `make dev-images`, then `make terraform-apply`.
4. For forwarding errors, stop the foreground `make dev-access` process before
    smoke checks, or choose another port with `make smoke LOCAL_PORT=8081`.
    Forwarding diagnostics are in `.local/<profile>/smoke-forward.log`.
5. For a stale build, run `make dev-update`. Smoke checks compare the API version
    to the local image inventory, rather than merely accepting HTTP 200.
6. For missing provider/npm/base-image/chart downloads, check connectivity.
    Initial downloads still need internet even though no publication is required.

## Mitigation

- Fix local build/test errors and rerun `make ci`, then `make dev-update`.
- After a partial Terraform apply, rerun the local command; preserve state.
  Do not delete state, manually install the same Helm releases, or change the PI.
- If Minikube itself was deleted, recreate the same profile with `make dev-up`.
  Terraform refresh will detect missing resources.
- To roll back an application edit, restore the intended source through normal
  version control, then run `make dev-update`; do not retag a release artifact.
- Use `make dev-down` and `make cluster-down` only when intentionally destroying
  that profile. Both are destructive; inspect the selected profile first.

## Verification

```bash
make smoke
make dev-access
```

Open `http://127.0.0.1:8080`; confirm API health/readiness and the current version
appear. Stop access forwarding with Ctrl-C. For the full rebuild/recovery
acceptance procedure, see the [developer guide](../developer-setup.md).

## Prevention and follow-up

Keep source-derived local image identities, explicit kubeconfig contexts,
profile-specific state, probes, and smoke checks. Add database backup/recovery
procedures when persistent application data is introduced in Stage 2.
