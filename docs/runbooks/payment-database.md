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

Use the explicit profile you deployed, not an implicit current Kubernetes context:

```bash
make dev-status MINIKUBE_PROFILE=rpp
kubectl --context rpp -n data get pods,pvc,services
kubectl --context rpp -n data logs statefulset/payments-postgres
kubectl --context rpp -n apps logs deployment/payment-api -c migrate
kubectl --context rpp -n apps logs deployment/payment-api -c app
```

Kubernetes probes contact the pod directly, not through Traefik. Failed readiness
removes the pod from normal Service/ingress routing; it does not mean liveness
failed or require an API restart. Inspect readiness and restart counts with:

```bash
kubectl --context rpp -n apps get pods
kubectl --context rpp -n apps get endpointslices \
  -l kubernetes.io/service-name=payment-api -o yaml
```

To distinguish ingress availability from process health, temporarily bypass it:

```bash
kubectl --context rpp -n apps port-forward \
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
If an isolated test deliberately scaled the database to zero, restore it with:

```bash
kubectl --context rpp -n data scale statefulset/payments-postgres --replicas=1
kubectl --context rpp -n data rollout status \
  statefulset/payments-postgres --timeout=120s
kubectl --context rpp -n apps wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=payment-api --timeout=120s
```

Run scale/outage experiments only on a dedicated test profile; replace every
`rpp` context above with that profile. Terraform declares one database replica,
so `make terraform-plan` detects manual scaling and `make terraform-apply` or
`make dev-update` restores it. Use the same `MINIKUBE_PROFILE` throughout.

Do not delete the PVC or regenerate credentials to repair an ordinary outage.
PostgreSQL's initial password is set only when its data directory is first
initialized; changing the password file alone is not credential rotation.

If migration 1 has already been applied, restore its original SQL if someone
edited it. Never clear the migration ledger to hide a checksum mismatch.
Future schema changes need a new migration and runner extension.

The database and application Secret names are managed by Terraform. Do not
manually create a competing Helm release or overwrite credentials in Git.
Local plaintext cluster connections and the database-owner role are development
concessions, not production configurations.

## Verification

```bash
make smoke MINIKUBE_PROFILE=rpp
make -C modules/payment-api integration
```

Confirm readiness recovers, liveness stays independent, GET reads the original
payment, and same-key POST replays its original ID, status, amount, and timestamp.

## Prevention and follow-up

`make dev-update` preserves database state. `make dev-down` destroys Terraform
namespaces and therefore deletes local payment data/PVCs; `make cluster-down`
also destroys all data in that Minikube profile. Never use these as a routine
application restart or as a test against a shared profile.
Losing the private credential file while retaining the database requires
intentional credential recovery; do not silently generate a replacement.
Backups, TLS, rotation, and production database operations are later hardening.
