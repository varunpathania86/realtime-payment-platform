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

1. Run `make doctor`, then `make dev-status`. The default profile is `rpp`.
    For the three-cluster lab pass `MULTI_CLUSTER=3`; access/smoke default to
    member `rpp-1`, or select `rpp-2`/`rpp-3` with `MINIKUBE_PROFILE`.
2. Inspect the affected deployment:

    ```bash
    kubectl --context rpp -n apps describe deployment payment-api
    kubectl --context rpp -n apps get pods
    kubectl --context rpp -n apps logs deployment/payment-api
    kubectl --context rpp -n platform logs deployment/traefik
    ```

3. `ErrImageNeverPull` means the image is not loaded into this profile. Run
    `make dev-update` for the single profile or `make dev-update MULTI_CLUSTER=3`
    to rebuild/load/deploy all three lab profiles.
4. Smoke checks automatically use an available loopback port and can run while
    `make dev-access` stays open. For smoke forwarding errors, check cluster
    connectivity and the Traefik Service. Forwarding diagnostics are in
    `.local/<profile>/smoke-forward.log`. If browser access reports a port conflict,
    choose another browser port with `make dev-access LOCAL_PORT=8081`.
5. For a stale build, run `make dev-update`. Smoke checks compare the API version
    to the local image inventory, rather than merely accepting HTTP 200.
6. For missing provider/npm/base-image/chart downloads, check connectivity.
    Initial downloads still need internet even though no publication is required.
7. A cold multi-cluster start can report `etcdserver: request timed out` while
    installing ingress CRDs. Confirm the selected control plane is healthy,
    retain all state, and retry `make dev-update MULTI_CLUSTER=3`.
    Isolated full-stack recreation recovered this way without manually
    installing releases, deleting state, or increasing node limits.

### Multi-cluster resource snapshots

Run `make dev-status MULTI_CLUSTER=3` for node and workload-container
CPU/RAM usage, requests/limits, readiness, and restarts. It targets the three
owned contexts explicitly and does not change or restart workloads.
`N/A` plus a warning/nonzero result means metrics are unavailable, not zero usage.
For older labs, run `make dev-metrics` once and allow
about a minute for samples. If metrics remain unavailable:

```bash
kubectl --context rpp-1 --request-timeout=10s get apiservice v1beta1.metrics.k8s.io
kubectl --context rpp-1 --request-timeout=10s -n kube-system get pods -l k8s-app=metrics-server
kubectl --context rpp-1 --request-timeout=10s -n kube-system logs deployment/metrics-server --tail=30
```

Replace the context with the cluster named in the warning. Stopped nodes are
shown without contacting their Kubernetes API; start them deliberately when
needed. See [table units and semantics](../developer-setup.md#resource-status-tables).
The metrics addon is Minikube-owned bootstrap infrastructure; do not install
a competing metrics-server Helm release.

## Mitigation

- Fix local build/test errors and rerun `make ci`, then `make dev-update`.
- After a partial Terraform apply, rerun the local command; preserve state.
  Do not delete state, manually install the same Helm releases, or change the PI.
- If Minikube itself was deleted, recreate the same profile with `make dev-up`.
  Terraform refresh will detect missing resources.
- To roll back an application edit, restore the intended source through normal
  version control, then run `make dev-update`; do not retag a release artifact.
- `make dev-down` stops the selected environment without deleting data.
  `make dev-clean` deletes the selected environment and its data; inspect the
  selected target first.

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
profile-specific state, probes, and smoke checks. Stage 2 now stores payments
on a PostgreSQL PVC. `make dev-update` preserves data; `make dev-clean` deletes
the selected environment and its database data. See
[database recovery](payment-database.md) before changing credentials, storage,
or migration SQL. Do not repair a routine outage by deleting state or a PVC.
Stage 3 also persists Kafka data and registry schema IDs; see
[event publication recovery](event-publication.md) before changing event
credentials, requeueing dead letters, or recreating a profile.
For the full-stack three-cluster variant, use the
[controlled database recovery procedure](event-publication.md#cross-cluster-postgresql-recovery).
Do not repair a fenced former primary with `minikube start` or individual
`dev-up`: its disposable node must be rebuilt from the current writer.
