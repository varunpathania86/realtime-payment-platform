# Runbook: Flink checkpoints and savepoint recovery

- **Severity:** Medium
- **Owner:** varunpathania86
- **Last reviewed:** 2026-11-03

## Symptoms

The payment window job restarts, checkpoints fail, partitions stop advancing
watermarks, or an intentional application update needs state-preserving restore.

## Impact

The optional analytics path may lag; the transactional payment API is unchanged.
MinIO is persistent local development storage, not a replicated backup.

## Diagnosis

1. Run `make dev-status` and check both Flink roles, MinIO and Kafka readiness.
2. Inspect `kubectl --context rpp-a -n apps logs deployment/flink-jobs-taskmanager`.
3. Use a loopback REST forward:

    ```bash
    kubectl --context rpp-a -n apps port-forward --address 127.0.0.1 service/flink-jobs 8081:8081
    ```

4. Inspect the running job and completed checkpoint at `/jobs/overview` and
    `/jobs/<job-id>/checkpoints`. A checkpoint must be completed, with its external
    path in `s3://flink-state/checkpoints`, before testing failure recovery.
5. A silent Kafka partition becomes idle after 30 seconds; active partitions
    still need newer event timestamps to advance watermarks. All-idle input does
    not synthesize future event time.
6. Discover custom counters at
    `/jobs/<job-id>/vertices/<vertex-id>/subtasks/0/metrics` for the
    `payment-windows` vertex. REST discovery is asynchronous; allow a fetch
    interval after the first request before concluding a counter is missing.

## Mitigation

Before updating stateful processing, create a compatible savepoint:

```bash
make streaming-savepoint DEV_PROFILE=rpp
make streaming-stop DEV_PROFILE=rpp
```

Save the returned URI. Do not remove the MinIO volume or use `dev-down` when
you intend to retain state. Restore after stopping/replacing the session
cluster using the returned URI:

```bash
STREAMING_RESTORE_PATH=s3://flink-state/savepoints/<returned-savepoint> make dev-update DEV_PROFILE=rpp
```

The local update guard refuses a changed stateful image without an explicit
restore path and a stopped prior job. Restore must keep compatible operator
UIDs and state serializers.

With Stage 6 analytics enabled, ClickHouse writes occur synchronously inside
Flink operators before those records are forwarded. A failed insert fails the
record path before a later checkpoint can acknowledge it; after recovery,
Kafka replay can create physical duplicates, so query replacement tables with
`FINAL`. The ClickHouse tables do not participate in a transaction with Kafka
or each other. An unavailable ClickHouse service must be recovered before
checkpoint progress can resume.

The guard runs before building/loading candidate images and keeps the previous
image inventory on rejection. Unchanged immutable application tags are reused.
The path is explicit: never silently start from empty state after failed restore.

The Helm submit hook refuses duplicate/conflicting active jobs. A running
session job is not automatically replaced just because a Helm upgrade happens.
Stop with a savepoint first for application-code changes. For an incompatible
change, design an explicit migration or a reviewed reset.

## Verification

1. Run `make streaming-fixtures` and `make streaming-inspect`.
2. Check revised one-minute results use the same result ID, duplicates do not
    increase counts, and fully expired events appear in the too-late topic.
3. Record a completed checkpoint, delete only the dedicated verification lab's
    TaskManager pod during `make streaming-load`, wait for the job to recover,
    and compare the final one-minute USD result: 500 payments, 500 authorized,
    50000 authorized minor units. Ten deliberately duplicated input records
    must not increase those totals.
4. Create/stop/restore from a savepoint and verify state continues; raw Kafka
    sink record counts may grow on replay. Logical results are replacements by
    key; too-late outputs are at-least-once and require event-ID deduplication.

## Prevention and follow-up

Keep stable operator UIDs, monitor checkpoint failures/age and disk capacity,
and test serializer compatibility before upgrades. Deduplication is bounded by
24-hour processing-time TTL. MinIO PVC deletion destroys checkpoints/savepoints.
Disabling the streaming feature also removes its owned MinIO PVC; export any
state you need before removing the feature or running `dev-down`.
Cluster-A loss is not covered by local HA. Stage 6 adds the persistent analytics
sink and durable lineage; it must preserve result-revision semantics.
