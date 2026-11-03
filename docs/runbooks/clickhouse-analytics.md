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
    `ModuleNotFoundError: No module named 'analytics'` means the submission
    package is incomplete: both `processing.py` and `analytics.py` must be
    included in the comma-separated `--pyFiles` argument. A healthy pod or an
    initial empty checkpoint alone does not verify the record-processing path.
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

## Reduced-resource verification evidence

On 2026-11-03, the owned `rpp-stage6-single` diagnostic profile used one
4-CPU, 6144-MiB Minikube node instead of the full three-cluster topology.
ClickHouse was limited to 2 CPUs and 2 GiB with an 8-GiB PVC. This is diagnostic
evidence, not certification of the cross-cluster deployment or production capacity.

- A dirty-source image rebuild restored the Flink job from a retained checkpoint.
  The profile was then deleted and recreated from clean local state.
- Timed synthetic fixtures on that clean deployment produced 28 distinct facts,
  26 EUR minute-window payments, a maximum revision of 2 and one too-late Kafka
  record. Flink was RUNNING with 13 completed checkpoints and none failed.
- Deleting only the ClickHouse pod and waiting for StatefulSet readiness
  preserved all 28 logical facts on the existing PVC.
- The benchmark ran inside the JobManager pod against ClickHouse 25.8.16.34:
  100,000 rows, 1,000-row insert batches, four monthly partitions
  (`202608` through `202611`), four active parts and 2,922,038 bytes on disk.
  Thirty warmed samples of the seven-day grouped query measured client HTTP
  round-trip p50 **5.53 ms**, p95 **6.65 ms**, and p99 **24.15 ms**;
  the p95 target of 500 ms passed.

The benchmark report's 20 logical CPUs and 1,342,177,280-byte memory limit
describe the client process's host-visible CPUs and JobManager cgroup, not
ClickHouse's CPU/memory allocation. The WSL2 host had about 8.8 GiB available
after verification. Earlier three-cluster attempts exhausted practical host
resources; full three-cluster acceptance remains outstanding.
