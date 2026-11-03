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

The module's scoped, expiring scanner exceptions follow the existing module
policy: Kubernetes owns health probes and namespace selection, development
images use `Never`, and the official mapped Flink UID/GID is retained. The
solution exception covers only the scratch MinIO image's Kubernetes-managed
health probes. These are not global scanner suppressions.

Results contain counts, integer authorized minor-unit sums, authorization rate,
rejections and deterministic result identity/revision. Runtime counters include
processing-delay total/processed count, late, too-late and duplicate events.
These are Flink operator metrics, not invented HTTP payment metrics.

Kafka result records use a compacted key and JSON `{result_id, payload}`;
`payload` contains the aggregate JSON. Read the latest value per key, not the
raw append count. Too-late records wrap the original event, watermark and reason.
Both external sinks are at-least-once; there is no cross-topic transaction.

## Documentation

- [Event-time decision](../../docs/adr/ADR-022-pyflink-event-time-semantics.md)
- [Recovery runbook](../../docs/runbooks/flink-recovery.md)
- [Authoritative scope](../../docs/master-plan.md#stage-5-flink-event-time-processing-fundamentals)
