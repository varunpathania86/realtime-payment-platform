#!/usr/bin/env python3
"""Load synthetic rows into a disposable table and report local query latency."""

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def percentile(samples, percent):
    if (not samples or isinstance(percent, bool) or not isinstance(percent, (int, float))
            or not 0 < percent <= 100):
        raise ValueError("percentile requires samples and a value in (0, 100]")
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * percent + 99) // 100 - 1)]


def memory_limit():
    for path in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            value = path.read_text().strip()
            return None if value == "max" else int(value)
        except (OSError, ValueError):
            continue
    return None


def host_facts():
    return {
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "memory_limit_bytes": memory_limit(),
    }


def query(endpoint, user, password, statement, body=None):
    request = Request(
        f"{endpoint}/?{urlencode({'query': statement})}",
        data=body,
        headers={
            "Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
            "Content-Type": "application/x-ndjson",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode()


def create_statement(table):
    return f"""CREATE TABLE analytics.{table}
(
    event_time_ms Int64,
    event_time DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(event_time_ms),
    event_day Date MATERIALIZED toDate(event_time),
    event_id UUID,
    amount_minor UInt64,
    currency FixedString(3),
    country FixedString(2),
    payment_method LowCardinality(String),
    outcome Enum8('authorized' = 1, 'rejected' = 2)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_day)
ORDER BY (event_day, currency, country, payment_method, event_time, event_id)"""


def load_rows(endpoint, user, password, table, count, batch_size, run_id):
    base_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for start in range(0, count, batch_size):
        rows = []
        for index in range(start, min(start + batch_size, count)):
            day_offset = index % 90
            timestamp_ms = base_ms - day_offset * 86_400_000 - (index % 86_400_000)
            rows.append(json.dumps({
                "event_time_ms": timestamp_ms,
                "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"rpp-stage6-{run_id}-{index}")),
                "amount_minor": 500 + index % 500_000,
                "currency": ("EUR", "USD", "GBP")[index % 3],
                "country": ("DE", "FR", "ES", "NL")[index % 4],
                "payment_method": ("card", "transfer", "wallet")[index % 3],
                "outcome": "authorized" if index % 4 else "rejected",
            }, separators=(",", ":")))
        query(
            endpoint, user, password, f"INSERT INTO analytics.{table} FORMAT JSONEachRow",
            ("\n".join(rows) + "\n").encode(),
        )


def run(endpoint, user, password, rows, samples, batch_size, p95_target_ms):
    table = f"benchmark_{uuid.uuid4().hex}"
    created = False
    try:
        query(endpoint, user, password, create_statement(table))
        created = True
        run_id = uuid.uuid4().hex
        load_rows(endpoint, user, password, table, rows, batch_size, run_id)
        query(endpoint, user, password, f"OPTIMIZE TABLE analytics.{table} FINAL")
        query_text = f"""SELECT event_day, currency, country, payment_method,
count() AS payments, countIf(outcome = 'authorized') AS authorized,
sumIf(amount_minor, outcome = 'authorized') AS authorized_amount_minor
FROM analytics.{table}
WHERE event_day >= today() - 7
GROUP BY event_day, currency, country, payment_method
ORDER BY event_day, currency, country, payment_method"""
        query(endpoint, user, password, query_text)
        durations = []
        for _ in range(samples):
            started = time.perf_counter()
            query(endpoint, user, password, query_text)
            durations.append((time.perf_counter() - started) * 1000)
        details = json.loads(query(
            endpoint, user, password,
            f"SELECT version() AS version, count() AS active_parts, sum(rows) AS stored_rows, "
            f"sum(bytes_on_disk) AS bytes_on_disk, groupArray(DISTINCT partition) AS partitions "
            f"FROM system.parts WHERE database = 'analytics' AND table = '{table}' AND active FORMAT JSONEachRow",
        ))
        report = {
            "query": "7-day grouped payments by event day/currency/country/payment method",
            "latency_ms_client_round_trip": {
                "samples": len(durations),
                "p50": percentile(durations, 50),
                "p95": percentile(durations, 95),
                "p99": percentile(durations, 99),
                "mean": statistics.fmean(durations),
            },
            "target": {"p95_ms": p95_target_ms, "met": percentile(durations, 95) <= p95_target_ms},
            "data": {
                "generated_rows": rows,
                "stored_rows": int(details["stored_rows"]),
                "active_parts": int(details["active_parts"]),
                "partitions": sorted(details["partitions"]),
                "bytes_on_disk": int(details["bytes_on_disk"]),
                "partition_by": "toYYYYMM(event_day)",
                "order_by": "(event_day, currency, country, payment_method, event_time, event_id)",
            },
            "clickhouse_version": details["version"],
            "host": host_facts(),
            "configuration": {
                "insert_batch_rows": batch_size,
                "query_samples_after_warmup": samples,
                "event_day_filter": "today() - 7 days",
                "query_output": "small grouped result (client round-trip includes HTTP response)",
            },
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return report["target"]["met"]
    finally:
        if created:
            query(endpoint, user, password, f"DROP TABLE analytics.{table}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123"))
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--p95-target-ms", type=float, default=500)
    args = parser.parse_args()
    if args.rows < 1 or args.samples < 1 or args.batch_size < 1 or args.p95_target_ms <= 0:
        parser.error("rows, samples, batch size, and p95 target must be positive")
    try:
        met = run(
            args.url.rstrip("/"), os.environ.get("CLICKHOUSE_USER", "rpp_analytics"),
            os.environ["CLICKHOUSE_PASSWORD"], args.rows, args.samples,
            args.batch_size, args.p95_target_ms,
        )
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"ERROR ClickHouse benchmark: {error}")
    if not met:
        sys.exit("ERROR ClickHouse benchmark: selected query exceeded the p95 target")


if __name__ == "__main__":
    main()
