# Payment event-time processing

Checkpointed PyFlink processing of synthetic payment outcomes. Part of
[realtime-payment-platform](../../README.md).

## Responsibilities

- Native per-Kafka-partition event-time watermarks and idleness.
- One-minute tumbling and ten-minute sliding windows, with one-minute slide.
- Two-minute allowed lateness, revision-aware results and too-late side output.
- Keyed event-ID deduplication with 24-hour processing-time TTL, event-time
  emission/cleanup timers and persistent MinIO checkpoints/savepoints.
- Transformation/schema/dataset identities; Stage 6 adds the ClickHouse sink
  and durable per-record lineage.

The processing dataset is separate from API initiation events. Synthetic
`authorized`/`rejected` values are labels, not payment decisions.

## Layout

See the [Helm chart guide](chart/README.md) for the session cluster, submission
hook and external storage/service relationships.

```text
.
├── chart/          # Opt-in session cluster and job submission
├── configuration.py # Preserve native Flink/Java defaults during configuration
├── control.py      # Kafka fixtures and materialized result inspection
├── fixtures.py     # Deterministic disorder/late/duplicate/idle/skewed cases
├── job.py          # Real PyFlink keyed state and event-time timers
├── processing.py   # Pure event validation and aggregate calculations
├── runtime.py      # Writable configuration, bucket/topic setup, submission
└── tests/
```

## Prerequisites

Run `STREAMING=1 make setup` at the repository root. Flink, its JVM and Python
are containerized and pinned in the root tool-version file; no host Java,
PyFlink or Maven installation is needed. First-use downloads require internet.

## Quick start

From the repository root:

```bash
make -C modules/flink-jobs build test lint
make -C modules/flink-jobs integration
make dev-up DEV_CONFIG=templates/streaming-topology.json
make streaming-fixtures
make streaming-inspect
make streaming-savepoint
```

Use an explicitly owned, unused `DEV_PROFILE` for destructive acceptance.
The example raises cluster A to 4 CPUs/8192 MiB; B/C remain 2 CPUs/3072 MiB.
This is a low-load starting budget, not capacity certification.

## Make targets

| Target | Description |
|---|---|
| `build` | Compile Python sources |
| `test` | Offline deterministic event/window tests |
| `integration` | Exercise actual PyFlink operator APIs in the setup runtime |
| `lint` | Validate Python syntax |
| `fmt` | Check shared whitespace rules |
| `clean` | Remove module bytecode caches |

## Configuration

The default Helm values scale both Flink roles to zero for standalone chart
inspection. Ordinary local orchestration excludes this optional chart entirely.
The opt-in topology
enables them only in A and provisions a 5 GiB MinIO PVC. Kafka uses the existing
TLS/SCRAM credentials, private cluster services and three partitions. Input
records are UTF-8 JSON conforming to the Avro logical contract; this topic does
not use the initiation topic's Confluent-wire encoding.

Deployment preserves the image's Java 17 module-access flags and pins the
BlobServer service port. The submission hook waits for a completed durable
checkpoint, not just a successful detached submission, before reporting success.
Submission ships both processing and analytics Python modules to distributed
workers; merely including them in the image does not put them on worker import paths.

The module's scoped, expiring scanner exceptions follow the existing module
policy: Kubernetes owns health probes and namespace selection, development
images use `Never`, and the official mapped Flink UID/GID is retained. The
solution exception covers only the scratch MinIO image's Kubernetes-managed
health probes. These are not global scanner suppressions.

Results contain counts, integer authorized minor-unit sums, authorization rate,
rejections and deterministic result identity/revision. Runtime counters include
processing-delay total/processed count, late, too-late and duplicate events.
These are Flink operator metrics, not invented HTTP payment metrics.

### Bounded pipeline benchmark

The existing `make streaming-load` is a small recovery workload, not a capacity
benchmark. Use the separate three-cluster benchmark for throughput, lag and fact
visibility latency:

```bash
make streaming-benchmark-preflight DEV_PROFILE=rpp-stage6-perf
make dev-up DEV_PROFILE=rpp-stage6-perf DEV_CONFIG=templates/analytics-benchmark-topology.json
make streaming-benchmark DEV_PROFILE=rpp-stage6-perf
```

Do not continue to `dev-up` if preflight fails. The benchmark template budgets
A at 4 CPUs/6144 MiB and B/C at 2 CPUs/3072 MiB each, without observability
backends. A fresh start requires 14336 MiB **available** host memory, including
a 2048-MiB safety reserve; installed RAM alone is not the check.

The measured schedule lasts 285 seconds: 20-second warmup, 120 seconds at
50 events/s, 10-second baseline drain, 90 seconds at 100 events/s, and a
45-second final drain. Startup/build/deployment are separate. The launcher
uses explicit owned contexts and bounds remote execution within its remaining
380-second command budget. The 100 events/s phase is a stress measurement,
not a requirement to keep lag flat.

The JSON report includes actual producer rate, confirmed throughput including
the visibility tail, p50/p95/p99 send-to-visible latency, backlog samples,
checkpoint-committed Kafka lag, checkpoint progress, source revision/dirty
status, deployed image references and container allocations. Baseline acceptance
requires all 6000 facts, actual producer pacing within 1% of 50 events/s,
p95 at most 2 seconds, backlog slope at most 1 event/s (sampling-noise
tolerance), and terminal baseline backlog at most 100 events. Overall success
also requires all warmup/stress facts after drain, checkpoint progress, no
additional failed checkpoints and no job recovery during measurement.
A failed target returns nonzero and preserves the report when available.
Unconfirmed facts at the deadline indicate an undrained backlog, not proof of
permanent data loss; the report keeps baseline and stress outcomes separate.

Latency is a conservative upper bound from the producer send attempt to the
first successful `FINAL` fact query, including the nominal one-second polling
interval and query overhead. It is not broker-append latency, API latency or
event-time window completion latency. Kafka lag uses checkpoint-committed
offsets and naturally has a checkpoint sawtooth; the non-growing criterion
uses this run's sent-minus-visible backlog instead. Each run uses a distinct
account/event identity and current UTC timestamps; synthetic facts remain
subject to the normal 90-day TTL. The short run does not certify long-term
capacity or cross-cluster failover.

For partitions without a committed offset yet, the report explicitly counts
lag from the earliest retained offset and lists those partitions. This is
expected on fresh topics; it does not pretend an absent commit means zero lag.

An opt-in `runtime_profile: low-memory` topology setting reduces Flink process
and container budgets together. See the [chart profile table](chart/README.md#opt-in-low-memory-runtime)
and [actual test results](../../tests/benchmarks/pipeline/2026-11-04-low-memory.md).
The measured single-cluster run drained all facts but failed the 2-second p95
target; do not lower node minimums or claim capacity from reduced limits.

See the [analytics runbook](../../docs/runbooks/clickhouse-analytics.md#three-cluster-resources-and-failure-boundaries)
for resource ownership and separate-server failure boundaries.

Kafka result records use a compacted key and JSON `{result_id, payload}`;
`payload` contains the aggregate JSON. Read the latest value per key, not the
raw append count. Too-late records wrap the original event, watermark and reason.
Both external sinks are at-least-once; there is no cross-topic transaction.

## Documentation

- [Event-time decision](../../docs/adr/ADR-022-pyflink-event-time-semantics.md)
- [Recovery runbook](../../docs/runbooks/flink-recovery.md)
- [Authoritative scope](../../docs/master-plan.md#stage-5-flink-event-time-processing-fundamentals)
