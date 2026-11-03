# Runbook: ClickHouse analytics

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-11-03

## Symptoms

ClickHouse is not ready, the Flink job repeatedly restarts, inserts fail, or
analytical queries return duplicate fact/revision rows.

## Impact

The optional analytics path may lag or stop checkpoint progress. PostgreSQL
payment processing remains the transactional source of truth.

## Diagnosis

1. Select the profile explicitly: `PROFILE=rpp-a` (replace with your owned A
    profile) and use `kubectl --context "$PROFILE"`.
2. Check workload, service and PVC status:

    ```bash
    kubectl --context "$PROFILE" -n apps get statefulset,pod,service,pvc
    kubectl --context "$PROFILE" -n apps logs statefulset/clickhouse-analytics
    ```

3. Check Flink task logs for `ClickHouse insert failed` and inspect Flink's
    completed-checkpoint age at the JobManager REST API.
4. If the PVC is `Pending`, inspect the profile's default StorageClass and
    available node disk. Do not remove the PVC to make the symptom disappear.

## Mitigation

Restore ClickHouse readiness, credentials or profile storage first. Sink
inserts are authenticated with a profile-generated Kubernetes Secret. Do not
copy its password into source control, logs or issue reports. If credentials
must be replaced, use a reviewed Terraform plan and keep the secret and server
configuration synchronized.

The StatefulSet owns one local PVC. It is not replicated or backed up. Do not
run `make dev-down` or disable analytics if its local data must be retained.

## Verification

1. Confirm the pod and service are ready and `/ping` succeeds.
2. Confirm Flink completes a new checkpoint and the synthetic input resumes.
3. Query with replacement semantics while background merges may be pending:

    ```sql
    SELECT event_id, count()
    FROM analytics.payment_facts FINAL
    GROUP BY event_id
    HAVING count() > 1;
    ```

    The query should return no duplicate logical event IDs.
4. Run `make -C modules/clickhouse-analytics benchmark` through the documented
    loopback port-forward. Retain the report with the hardware and configuration
    if using its p95 as capacity evidence.

## Prevention and follow-up

Monitor ClickHouse disk use, active parts, insert errors and Flink checkpoint
age. Detailed payment facts expire after 90 days. Window revisions have no TTL
in this local stage. Export data before deleting a profile or its PVC.
