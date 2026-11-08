# Runbook: Payment database readiness or migration failure

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-10-29

## Symptoms

Direct API payment routes or `/api/readyz` return 503 while direct `/api/healthz`
returns 200. Through ingress, all API routes can return 503 `no available server`
once the pod becomes unready. The portal may report unavailable API readiness,
or the API migration init container may fail before the application starts.

## Impact

No new payments can be confirmed while storage is unavailable. A failed response
after a committed insert is ambiguous: retry with the same idempotency key and
unchanged payload, never a new key. No unbounded API retry loop runs.

## Diagnosis

Use the explicit topology/context you deployed, not an implicit current context.
Examples use the fixed `rpp` three-cluster topology; select it with
`MULTI_CLUSTER=3`. The examples assume the global writer is in cluster 1; select its actual member after
promotion. CNPG can change the designated primary pod inside a cluster:

```bash
make dev-status MULTI_CLUSTER=3
kubectl --context rpp-1 -n postgres get clusters,pods,pvc,services
POD=$(kubectl --context rpp-1 -n postgres get cluster pg-1 -o jsonpath='{.status.currentPrimary}')
kubectl --context rpp-1 -n postgres logs "$POD" -c postgres --tail=50
kubectl --context rpp-1 -n apps logs deployment/payment-api -c migrate
kubectl --context rpp-1 -n apps logs deployment/payment-api -c app
```

Kubernetes probes contact the pod directly, not through Traefik. Failed readiness
removes the pod from normal Service/ingress routing; it does not mean liveness
failed or require an API restart. Inspect readiness and restart counts with:

```bash
kubectl --context rpp-1 -n apps get pods
kubectl --context rpp-1 -n apps get endpointslices \
  -l kubernetes.io/service-name=payment-api -o yaml
```

To distinguish ingress availability from process health, temporarily bypass it:

```bash
kubectl --context rpp-1 -n apps port-forward \
  deployment/payment-api 18081:8080
```

In another terminal, while the database is unavailable:

```bash
curl -i --max-time 5 http://127.0.0.1:18081/api/healthz
# Expected: 200, process is alive.
curl -i --max-time 5 http://127.0.0.1:18081/api/readyz
# Expected: 503, database unavailable.
```

Use the direct port for valid POST requests too when measuring the API's bounded
database failure response. An ingress 503 can be generated without contacting
the application and does not prove its database timeout behavior.
Stop this temporary forwarding with Ctrl-C when finished.

Do not print Secret contents, database URLs, or generated password files into
logs/tickets. `.local/<profile>/database.tfvars.json` and Terraform state contain
sensitive local credentials and must remain Git-ignored.

## Mitigation

Restore the database pod/storage. The API should recover readiness automatically;
an API restart or rebuild is not required for a routine database outage.
For configured instance counts, use `DEV_CONFIG` and `make dev-update
MULTI_CLUSTER=3`. Do not scale operator-managed pods as a substitute for
changing the CNPG declaration. With neither remote standby available, commits
wait in `SyncRep`; restore a remote peer rather than disabling synchronous
durability. For primary loss, follow the
[controlled promotion/rejoin procedure](event-publication.md#cross-cluster-postgresql-recovery).
Run outage experiments only on an isolated topology.

Do not delete the PVC or regenerate credentials to repair an ordinary outage.
PostgreSQL's initial password is set only when its data directory is first
initialized; changing the password file alone is not credential rotation.

If migration 1 has already been applied, restore its original SQL if someone
edited it. Never clear the migration ledger to hide a checksum mismatch.
Future schema changes need a new migration and runner extension.

The database and application Secret names are managed by Terraform. Do not
manually create a competing Helm release or overwrite credentials in Git.
Connections use verified TLS; the database-owner role remains a development
concession, not a production least-privilege configuration.

## Verification

```bash
make smoke
make -C modules/payment-api integration
```

Confirm readiness recovers, liveness stays independent, GET reads the original
payment, and same-key POST replays its original ID, status, amount, and timestamp.

## Prevention and follow-up

`make dev-update` preserves database state. `make dev-clean` destroys Terraform
namespaces and therefore deletes local payment data/PVCs; it also
destroys all data in the selected Minikube profile(s). Never use this as a routine
application restart or as a test against a shared profile.
Losing the private credential file while retaining the database requires
intentional credential recovery; do not silently generate a replacement.
Backups, certificate rotation and production database operations are later hardening.
