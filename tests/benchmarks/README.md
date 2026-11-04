# Integrated benchmarks

Reproducible procedures and measured evidence for cross-module workloads.
Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Keep performance targets, achieved results and raw evidence together. Runbooks
own operational diagnosis and recovery, not historical benchmark tables.
Never describe a planned test, an offered rate or a failed threshold as
demonstrated sustainable capacity.

## Layout

```text
tests/benchmarks/
├── README.md
└── pipeline/
    ├── 2026-11-03-clickhouse-query.md
    ├── 2026-11-03-clickhouse-query.json
    ├── 2026-11-04-low-memory.md
    └── 2026-11-04-low-memory.json
```

| Report | Scope | Outcome |
|---|---|---|
| [ClickHouse query](pipeline/2026-11-03-clickhouse-query.md) | Warmed grouped query over 100000 synthetic rows | p95 6.65 ms; 500-ms query target passed |
| [Low-memory pipeline](pipeline/2026-11-04-low-memory.md) | Publisher to Kafka to Flink to visible ClickHouse fact | 50 events/s baseline p95 4.73 seconds; 2-second target failed; stress backlog grew |

These are different metrics. A fast query over already stored data is not a
measurement of pipeline ingestion latency.

## Prerequisites

- Follow [developer setup](../../docs/developer-setup.md); pinned dependencies
  are installed through `make setup`, not manual package installation.
- Use synthetic data, an explicitly owned profile/base, and sufficient memory.
- Read the report's topology and limitations before reproducing it. The
  recorded pipeline run used one cluster; the public launcher requires three.
- Review raw JSON for credentials, personal data and private infrastructure
  details before adding it. Never retain generated Secrets or Terraform state.

## Quick start

Run commands from the repository root, in order:

1. Inventory profiles and reclaim memory safely using the
    [cleanup procedure](pipeline/2026-11-04-low-memory.md#reclaim-memory-before-starting).
2. Validate local tooling and source:

    ```bash
    make doctor
    make ci
    ```

3. Check memory **before** starting the owned three-cluster base:

    ```bash
    make streaming-benchmark-preflight DEV_PROFILE=rpp-stage6-perf DEV_CONFIG=templates/analytics-low-memory-topology.json
    ```

    **Stop if preflight fails.** This laptop's recorded preflight failed;
    no three-cluster performance result exists.
4. Only after a passing preflight:

    ```bash
    make dev-up DEV_PROFILE=rpp-stage6-perf DEV_CONFIG=templates/analytics-low-memory-topology.json
    make smoke MINIKUBE_PROFILE=rpp-stage6-perf-a
    make dev-status DEV_PROFILE=rpp-stage6-perf
    make streaming-benchmark DEV_PROFILE=rpp-stage6-perf
    ```

    The benchmark prints JSON and returns nonzero on failed acceptance.
    To retain output, redirect the command's stdout to an ignored local report;
    Make also echoes the recipe, so extract the JSON object before committing
    it. Preserve stderr and the exit status separately. Do not overwrite
    historical reports with reruns.

5. After evidence collection, stop only your test profiles to preserve data:

    ```bash
    minikube stop -p rpp-stage6-perf-a
    minikube stop -p rpp-stage6-perf-b
    minikube stop -p rpp-stage6-perf-c
    ```

    For permanent, explicitly approved disposal, follow the destructive cleanup
    sequence in the report instead.

## Make targets

| Target | Purpose |
|---|---|
| `make doctor` | Verify pinned tools |
| `make ci` | Validate source, tests and packaging; not a performance test |
| `make streaming-benchmark-preflight` | Refuse an unsafe fresh three-cluster memory allocation |
| `make dev-up` | Create the explicitly selected topology |
| `make smoke` | Verify application readiness, not throughput |
| `make dev-status` | Inspect resource allocations and usage |
| `make streaming-benchmark` | Run the bounded 50/100 events/s pipeline test |
| `make -C modules/realtime-analytics benchmark` | Separate disposable-table query benchmark |
| `make dev-down` then `make cluster-down` | Destructively dispose of an owned three-cluster test base |

## Configuration

Executable benchmark logic stays with its module and launcher:
[pipeline](../../modules/stream-processor/benchmark.py),
[launcher/preflight](../../scripts/streaming_benchmark.py), and
[query benchmark](../../modules/realtime-analytics/benchmark.py).
This directory holds procedures and evidence, not duplicated implementations.

For new reports, record goal, targets, achieved results, workload, hardware,
source/image identity when captured, exact command outcomes, cleanup, metric
definitions and limitations. Explicitly identify missing metadata rather than
reconstructing it as measured evidence.

## Documentation

- [Pipeline metric definitions](../../modules/stream-processor/README.md#bounded-pipeline-benchmark)
- [Low-memory chart settings](../../modules/stream-processor/chart/README.md#opt-in-low-memory-runtime)
- [Operational recovery and failure boundaries](../../docs/runbooks/realtime-analytics.md)
- [Master plan](../../docs/master-plan.md)
