"""Bounded synthetic Kafka-to-ClickHouse fact visibility benchmark."""

import base64
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import os
import statistics
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4, uuid5


PHASES = (
    ("warmup", 50, 20),
    ("baseline", 50, 120),
    ("baseline_drain", 0, 10),
    ("stress", 100, 90),
    ("drain", 0, 45),
)


def percentile(values, percent):
    if not values:
        raise ValueError("latency samples must not be empty")
    return sorted(values)[math.ceil(len(values) * percent / 100) - 1]


def slope(samples):
    if len(samples) < 2:
        raise ValueError("at least two backlog samples are required")
    origin = samples[0][0]
    xs = [sample[0] - origin for sample in samples]
    ys = [sample[1] for sample in samples]
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        raise ValueError("backlog samples need distinct timestamps")
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator


def committed_lag(end, committed, beginning):
    return max(0, end - (beginning if committed is None else committed))


def summarize(sent, visible, samples, lag_samples, phase, rate, duration):
    selected = {key: timestamp for key, (name, timestamp) in sent.items() if name == phase}
    confirmed = [key for key in selected if key in visible]
    latencies = [1000 * (visible[key] - selected[key]) for key in confirmed]
    expected = rate * duration
    phase_samples = [(stamp, backlog) for name, stamp, backlog in samples if name == phase]
    phase_lag = [lag for name, _, lag in lag_samples if name == phase]
    trend = slope(phase_samples)
    result = {
        "target_events_per_second": rate,
        "duration_seconds": duration,
        "expected_events": expected,
        "sent_events": len(selected),
        "confirmed_unique_facts": len(confirmed),
        "unconfirmed_facts_at_deadline": len(selected) - len(confirmed),
        "scheduled_events_per_second": len(selected) / duration,
        "achieved_producer_events_per_second": (
            (len(selected) - 1) / (max(selected.values()) - min(selected.values()))
            if len(selected) > 1 else 0
        ),
        "visibility_latency_ms_upper_bound": {
            "p50": percentile(latencies, 50) if latencies else None,
            "p95": percentile(latencies, 95) if latencies else None,
            "p99": percentile(latencies, 99) if latencies else None,
        },
        "visibility_backlog_slope_events_per_second": trend,
        "max_visibility_backlog": max(value for _, value in phase_samples),
        "kafka_checkpoint_committed_lag": {
            "first": phase_lag[0], "last": phase_lag[-1], "max": max(phase_lag),
        },
    }
    if confirmed:
        elapsed = max(visible[key] for key in confirmed) - min(selected.values())
        result["confirmed_events_per_second_including_visibility_tail"] = len(confirmed) / elapsed
    result["meets_baseline_contract"] = (
        len(selected) == expected
        and len(confirmed) == expected
        and result["achieved_producer_events_per_second"] >= rate * 0.99
        and percentile(latencies, 95) <= 2000
        and trend <= 1
        and phase_samples[-1][1] <= rate * 2
    )
    return result


def clickhouse_ids(run_id):
    sql = (
        "SELECT event_id FROM analytics.payment_facts FINAL "
        f"WHERE account_id = '{run_id}' FORMAT JSONEachRow"
    )
    credential = base64.b64encode(
        (os.environ["CLICKHOUSE_USER"] + ":" + os.environ["CLICKHOUSE_PASSWORD"]).encode()
    ).decode()
    request = Request(
        os.environ["CLICKHOUSE_URL"].rstrip("/") + "/?" + urlencode({"query": sql}),
        data=b"", headers={"Authorization": "Basic " + credential},
    )
    with urlopen(request, timeout=5) as response:
        return {json.loads(line)["event_id"] for line in response.read().decode().splitlines()}


def make_event(run_id, phase, index, timestamp):
    from fixtures import event

    identifier = str(uuid5(run_id, phase + "/" + str(index)))
    return event(
        index, event_id=identifier, payment_id=identifier,
        account_id=str(run_id),
        event_time=timestamp.isoformat().replace("+00:00", "Z"),
        currency="GBP",
    )


def job_health():
    root = "http://flink-jobs:8081"
    with urlopen(root + "/jobs/overview", timeout=5) as response:
        jobs = [job for job in json.load(response)["jobs"]
                if job["name"] == "payment-event-time-v1" and job["state"] == "RUNNING"]
    if len(jobs) != 1:
        raise ValueError("benchmark requires exactly one RUNNING payment event-time job")
    with urlopen(root + "/jobs/" + jobs[0]["jid"] + "/checkpoints", timeout=5) as response:
        checkpoints = json.load(response)
    completed = checkpoints["latest"].get("completed")
    if not completed:
        raise ValueError("benchmark requires a completed Flink checkpoint")
    return {"job_id": jobs[0]["jid"], "counts": checkpoints["counts"],
            "checkpoint_id": completed["id"]}


def run():
    from kafka import KafkaConsumer, KafkaProducer
    from control import connection

    with ExitStack() as cleanup:
        producer = KafkaProducer(
            **connection(), acks="all", max_block_ms=5000,
            request_timeout_ms=5000, api_version_auto_timeout_ms=5000,
        )
        cleanup.callback(producer.close, timeout=5)
        consumer = KafkaConsumer(
            **connection(), enable_auto_commit=False,
            group_id="payment-windows-v1", request_timeout_ms=5000,
            session_timeout_ms=4000, api_version_auto_timeout_ms=5000,
        )
        cleanup.callback(consumer.close)
        return execute(producer, consumer)


def execute(producer, consumer):
    from kafka import TopicPartition

    run_id = uuid4()
    sent, visible, samples, lag_samples = {}, {}, [], []
    lock, stop = threading.Lock(), threading.Event()
    errors = []
    partitions = [
        TopicPartition("payment-processing-events-v1", partition)
        for partition in sorted(consumer.partitions_for_topic("payment-processing-events-v1"))
    ]
    if not partitions:
        raise ValueError("processing topic has no partitions")
    before = job_health()
    beginnings = consumer.beginning_offsets(partitions)
    uncommitted_partitions = set()
    started = time.monotonic()
    boundaries, offset = [], 0
    for phase, rate, duration in PHASES:
        boundaries.append((phase, rate, started + offset, started + offset + duration))
        offset += duration

    def produce():
        try:
            for phase, rate, begin, end in boundaries:
                if not rate:
                    if stop.wait(max(0, end - time.monotonic())):
                        return
                    continue
                for index in range(round((end - begin) * rate)):
                    deadline = begin + index / rate
                    if stop.wait(max(0, deadline - time.monotonic())):
                        return
                    if time.monotonic() - deadline > 1:
                        raise TimeoutError("producer fell over one second behind its pacing schedule")
                    timestamp = datetime.now(timezone.utc)
                    value = make_event(run_id, phase, index, timestamp)
                    with lock:
                        sent[value["event_id"]] = (phase, time.monotonic())
                    producer.send(
                        "payment-processing-events-v1",
                        partition=partitions[index % len(partitions)].partition,
                        timestamp_ms=int(timestamp.timestamp() * 1000),
                        value=json.dumps(value).encode(),
                    ).get(timeout=5)
        except Exception as error:
            with lock:
                errors.append(error)
            stop.set()

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()
    try:
        while time.monotonic() < boundaries[-1][3] and not stop.is_set():
            observed = clickhouse_ids(run_id)
            now = time.monotonic()
            phase = next((name for name, _, begin, end in boundaries if begin <= now < end), "drain")
            with lock:
                for identifier in observed & sent.keys():
                    visible.setdefault(identifier, now)
                samples.append((phase, now, len(sent) - len(visible)))
            ends = consumer.end_offsets(partitions)
            lag = 0
            for partition in partitions:
                committed = consumer.committed(partition)
                if committed is None:
                    uncommitted_partitions.add(partition.partition)
                lag += committed_lag(ends[partition], committed, beginnings[partition])
            lag_samples.append((phase, now, lag))
            stop.wait(1)
        with lock:
            if errors:
                raise RuntimeError("benchmark producer failed") from errors[0]
        after = job_health()
        if after["job_id"] != before["job_id"] or after["checkpoint_id"] <= before["checkpoint_id"]:
            raise ValueError("Flink job changed or did not complete a new checkpoint")
        with lock:
            baseline = summarize(sent, visible, samples, lag_samples, "baseline", 50, 120)
            stress = summarize(sent, visible, samples, lag_samples, "stress", 100, 90)
            missing = len(sent) - len(visible)
        report = {
            "run_id": str(run_id), "elapsed_seconds": time.monotonic() - started,
            "baseline": baseline, "stress": stress, "unconfirmed_facts_all_phases_at_deadline": missing,
            "checkpoints_before": before, "checkpoints_after": after,
            "samples": [
                {"phase": phase, "elapsed_seconds": stamp - started, "visibility_backlog": backlog}
                for phase, stamp, backlog in samples
            ],
            "kafka_lag_samples": [
                {"phase": phase, "elapsed_seconds": stamp - started, "committed_lag": lag}
                for phase, stamp, lag in lag_samples
            ],
            "passed": (
                baseline["meets_baseline_contract"] and missing == 0
                and after["counts"]["failed"] == before["counts"]["failed"]
                and after["counts"]["restored"] == before["counts"]["restored"]
            ),
            "measurement": {
                "latency": "producer send attempt to first query observing FINAL fact; includes polling",
                "poll_interval_seconds": 1,
                "lag": "Kafka end offsets minus Flink checkpoint-committed offsets, not processing backlog",
                "partitions_observed_without_commit": sorted(uncommitted_partitions),
                "no_commit_policy": "count from earliest retained offset until a commit exists",
                "backlog": "this run's sent unique events minus query-visible unique facts",
                "non_growing_tolerance_events_per_second": 1,
                "producer_rate_scheduling_tolerance_percent": 1,
                "terminal_baseline_backlog_limit_events": 100,
                "scope": "facts only; not event-time aggregate completion or API latency",
            },
        }
        print(json.dumps(report, indent=2))
        return report["passed"]
    finally:
        stop.set()
        worker.join(timeout=6)


if __name__ == "__main__":
    import sys

    try:
        sys.exit(0 if run() else 1)
    except Exception as error:
        raise RuntimeError("pipeline benchmark failed; no passing result is available") from error
