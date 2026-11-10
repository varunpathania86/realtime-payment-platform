"""Query, verify and maintain the local Iceberg lakehouse through the in-cluster Trino CLI."""

import argparse
import json
import os
import re
import subprocess
import sys
import time

NAMESPACE = "payments"
TABLES = ("payment_events_raw", "payment_events")
SCRATCH = "lakehouse_admin.payments.evolution_check"
RETENTION = re.compile(r"[1-9][0-9]*(m|h|d)")


def trino(profile, sql, catalog="lakehouse", runner=subprocess.run):
    """Run one statement; the reader catalog is the default so ad-hoc queries cannot write."""
    result = runner(["kubectl", "--context", profile, "--request-timeout=30s", "-n", "apps", "exec",
        "deploy/lakehouse-query", "--", "trino", "--catalog", catalog, "--schema", NAMESPACE,
        "--output-format", "JSON", "--execute", sql], check=False, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Trino exited {result.returncode}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def scalar(rows):
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError(f"expected one value, got {rows}")
    return next(iter(rows[0].values()))


def denied(profile, sql, runner=subprocess.run):
    try:
        trino(profile, sql, runner=runner)
    except RuntimeError as error:
        return str(error)
    raise RuntimeError(f"read-only catalog unexpectedly accepted: {sql}")


def wait_for_curated(profile, timeout, runner=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    """Iceberg commits only on Flink checkpoints, so data appears in checkpoint-sized steps."""
    deadline = clock() + timeout
    while True:
        count = scalar(trino(profile, "SELECT count(*) AS n FROM payment_events", runner=runner))
        if count > 0:
            return count
        if clock() >= deadline:
            raise RuntimeError(f"no curated rows after {timeout}s; send make streaming-fixtures and check Flink")
        sleep(10)


def verify(profile, timeout=240, runner=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    def query(sql, catalog="lakehouse"):
        return trino(profile, sql, catalog=catalog, runner=runner)

    curated = wait_for_curated(profile, timeout, runner=runner, clock=clock, sleep=sleep)
    raw = scalar(query("SELECT count(*) AS n FROM payment_events_raw"))
    if raw < curated:
        raise RuntimeError(f"bronze has {raw} rows but curated has {curated}")
    duplicates = scalar(query(
        "SELECT count(*) AS n FROM (SELECT event_id FROM payment_events GROUP BY event_id HAVING count(*) > 1)"))
    if duplicates:
        raise RuntimeError(f"curated table contains {duplicates} duplicate event_id values")
    print(f"Lakehouse: bronze={raw} curated={curated} duplicates=0", flush=True)

    first = query('SELECT snapshot_id FROM "payment_events$snapshots" ORDER BY committed_at LIMIT 1')
    if not first:
        raise RuntimeError("curated table has no snapshots")
    historical = scalar(query(
        f"SELECT count(*) AS n FROM payment_events FOR VERSION AS OF {int(first[0]['snapshot_id'])}"))
    if historical > curated:
        raise RuntimeError(f"time travel returned {historical} rows, more than current {curated}")
    print(f"Lakehouse: time travel to first snapshot returned {historical} rows", flush=True)

    message = denied(profile, "CREATE TABLE lakehouse.payments.denied_check (id integer)", runner=runner)
    print(f"Lakehouse: reader catalog denied CREATE ({message.splitlines()[0][:120]})", flush=True)

    query(f"DROP TABLE IF EXISTS {SCRATCH}", "lakehouse_admin")
    try:
        query(f"CREATE TABLE {SCRATCH} WITH (partitioning = ARRAY['day(event_time)']) AS "
            "SELECT event_id, event_time, amount_minor, currency FROM lakehouse_admin.payments.payment_events",
            "lakehouse_admin")
        query(f"ALTER TABLE {SCRATCH} ADD COLUMN review_note varchar", "lakehouse_admin")
        query(f"ALTER TABLE {SCRATCH} SET PROPERTIES partitioning = ARRAY['day(event_time)', 'currency']",
            "lakehouse_admin")
        query(f"INSERT INTO {SCRATCH} SELECT event_id, event_time, amount_minor, currency, 'evolved' "
            "FROM lakehouse_admin.payments.payment_events", "lakehouse_admin")
        notes = scalar(query(f"SELECT count(review_note) AS n FROM {SCRATCH}", "lakehouse_admin"))
        specs = scalar(query(f'SELECT count(DISTINCT spec_id) AS n FROM "{SCRATCH.rsplit(".", 1)[1]}$files"',
            "lakehouse_admin"))
        if notes != curated or specs != 2:
            raise RuntimeError(f"evolution check expected {curated} notes and 2 specs, got {notes} and {specs}")
        print("Lakehouse: ADD COLUMN and partition evolution verified on a scratch table", flush=True)
    finally:
        query(f"DROP TABLE IF EXISTS {SCRATCH}", "lakehouse_admin")


def maintain(profile, retention="7d", runner=subprocess.run):
    """Compact small checkpoint files, then expire old snapshots and orphaned files."""
    if not RETENTION.fullmatch(retention):
        raise ValueError("retention must look like 30m, 12h or 7d")
    session = ""
    if retention != "7d":
        # Trino refuses retention below 7d unless the catalog session minimum is lowered explicitly.
        session = (f"SET SESSION lakehouse_admin.expire_snapshots_min_retention = '{retention}'; "
            f"SET SESSION lakehouse_admin.remove_orphan_files_min_retention = '{retention}'; ")
    for table in TABLES:
        name = f"lakehouse_admin.{NAMESPACE}.{table}"
        before = scalar(trino(profile, f'SELECT count(*) AS n FROM "{table}$files"', "lakehouse_admin", runner))
        trino(profile, session + f"ALTER TABLE {name} EXECUTE optimize(file_size_threshold => '32MB'); "
            f"ALTER TABLE {name} EXECUTE expire_snapshots(retention_threshold => '{retention}'); "
            f"ALTER TABLE {name} EXECUTE remove_orphan_files(retention_threshold => '{retention}')",
            "lakehouse_admin", runner)
        after = scalar(trino(profile, f'SELECT count(*) AS n FROM "{table}$files"', "lakehouse_admin", runner))
        print(f"Lakehouse maintenance: {table} data files {before} -> {after} (retention {retention})", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("MINIKUBE_PROFILE") or (
        "rpp-1" if os.environ.get("MULTI_CLUSTER") == "3" else "rpp"
    ))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("query").add_argument("sql")
    commands.add_parser("verify").add_argument("--timeout", type=int, default=240)
    commands.add_parser("maintain").add_argument("--retention", default="7d")
    args = parser.parse_args(argv)
    try:
        if args.command == "query":
            for row in trino(args.profile, args.sql):
                print(json.dumps(row, sort_keys=True))
        elif args.command == "verify":
            verify(args.profile, args.timeout)
        else:
            maintain(args.profile, args.retention)
    except (RuntimeError, ValueError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
