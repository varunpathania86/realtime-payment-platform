"""Run the lakehouse SQL in the real Flink runtime against a local Iceberg catalog.

Uses a filesystem (Hadoop) Iceberg catalog instead of Polaris/MinIO so the check is
offline, and compares curated rows with processing.decode on the same records.
"""

import json
import shutil
import tempfile

from pyflink.common import Row, Types
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.table import EnvironmentSettings, Schema, StreamTableEnvironment, TableEnvironment

from fixtures import event
import lakehouse
from processing import decode

DEVICE = "6f1c1a52-6f4b-4a8e-9d0e-6a3f7c2b9e11"
RECORDS = [
    json.dumps(event(1, 10)),
    json.dumps(event(1, 10)),
    json.dumps(event(2, 20, outcome="rejected", device_id=DEVICE, account_created_at="2026-11-02T00:00:00Z")),
    json.dumps(event(3, 30, event_time="2026-11-03T00:00:30.123456Z")),
    json.dumps(event(4, 40, schema_version="1")),
    json.dumps(event(5, 50, amount_minor=1.5)),
    json.dumps(event(6, 60, amount_minor=0)),
    json.dumps(event(7, 70, currency="eur")),
    json.dumps(event(8, 80, account_created_at="2026-11-04T00:00:00Z")),
    json.dumps(event(9, 90, device_id="not-a-uuid")),
    json.dumps(event(10, 100, event_time="2026-11-03 00:01:40")),
    json.dumps(event(11, 110, payment_method="cash")),
    json.dumps(event(12, 120, amount_minor=2 ** 63)),
    "{not json",
]


def expected_curated():
    valid = {}
    for raw in RECORDS:
        try:
            decoded = decode(raw)
        except (ValueError, KeyError, TypeError):
            continue
        valid.setdefault(decoded["event_id"], decoded)
    return valid


def create_tables(table_env, warehouse):
    table_env.execute_sql(
        f"CREATE CATALOG lakehouse WITH ('type' = 'iceberg', 'catalog-type' = 'hadoop', 'warehouse' = 'file://{warehouse}')")
    table_env.execute_sql("CREATE DATABASE IF NOT EXISTS lakehouse.payments")
    table_env.execute_sql(
        "CREATE TABLE lakehouse.payments.payment_events_raw (event_id STRING, event_type STRING, "
        "raw_payload STRING, kafka_partition INT, kafka_offset BIGINT, kafka_timestamp TIMESTAMP_LTZ(6), "
        "ingested_at TIMESTAMP_LTZ(6))")
    table_env.execute_sql(
        "CREATE TABLE lakehouse.payments.payment_events (event_id STRING, payment_id STRING, account_id STRING, "
        "event_type STRING, schema_version INT, event_time TIMESTAMP_LTZ(6), amount_minor BIGINT, currency STRING, "
        "country STRING, payment_method STRING, outcome STRING, account_created_at TIMESTAMP_LTZ(6), "
        "device_id STRING, kafka_partition INT, kafka_offset BIGINT, ingested_at TIMESTAMP_LTZ(6))")


def main():
    warehouse = tempfile.mkdtemp(prefix="lakehouse-check-")
    try:
        env = StreamExecutionEnvironment.get_execution_environment()
        env.set_parallelism(1)
        table_env = StreamTableEnvironment.create(env)
        table_env.get_config().set("table.local-time-zone", "UTC")
        create_tables(table_env, warehouse)
        rows = env.from_collection(
            [Row(raw, index % 3, index, 1_793_750_400_000 + index) for index, raw in enumerate(RECORDS)],
            type_info=Types.ROW_NAMED(["raw_payload", "kafka_partition", "kafka_offset", "epoch_ms"],
                [Types.STRING(), Types.INT(), Types.LONG(), Types.LONG()]))
        source = table_env.from_data_stream(rows, Schema.new_builder()
            .column("raw_payload", "STRING").column("kafka_partition", "INT")
            .column("kafka_offset", "BIGINT").column("epoch_ms", "BIGINT")
            .column_by_expression("kafka_timestamp", "TO_TIMESTAMP_LTZ(epoch_ms, 3)")
            .column_by_expression("processed_at", "PROCTIME()").build())
        table_env.create_temporary_view("processing_events", source)
        statements = table_env.create_statement_set()
        statements.add_insert_sql(lakehouse.bronze_insert("processing_events", "lakehouse.payments.payment_events_raw"))
        statements.add_insert_sql(lakehouse.curated_insert("processing_events", "lakehouse.payments.payment_events"))
        statements.execute().wait()

        batch = TableEnvironment.create(EnvironmentSettings.in_batch_mode())
        batch.get_config().set("table.local-time-zone", "UTC")
        batch.execute_sql(
            f"CREATE CATALOG lakehouse WITH ('type' = 'iceberg', 'catalog-type' = 'hadoop', 'warehouse' = 'file://{warehouse}')")
        with batch.execute_sql("SELECT kafka_offset, raw_payload FROM lakehouse.payments.payment_events_raw").collect() as result:
            bronze = {row[0]: row[1] for row in result}
        if bronze != dict(enumerate(RECORDS)):
            raise AssertionError(f"bronze must keep every record as received: {sorted(bronze)}")
        with batch.execute_sql(
            "SELECT event_id, payment_id, amount_minor, currency, outcome, device_id, kafka_offset, "
            "DATE_FORMAT(event_time, 'yyyy-MM-dd''T''HH:mm:ss.SSSSSS'), "
            "DATE_FORMAT(account_created_at, 'yyyy-MM-dd''T''HH:mm:ss') FROM lakehouse.payments.payment_events"
        ).collect() as result:
            curated = {row[0]: row for row in result}
        expected = expected_curated()
        if set(curated) != set(expected):
            raise AssertionError(f"curated ids differ from processing.decode: {sorted(curated)} != {sorted(expected)}")
        for event_id, decoded in expected.items():
            row = curated[event_id]
            if (row[1], row[2], row[3], row[4], row[5]) != (
                decoded["payment_id"], decoded["amount_minor"], decoded["currency"], decoded["outcome"],
                decoded.get("device_id"),
            ):
                raise AssertionError(f"curated values differ for {event_id}: {row}")
            if not decoded["event_time"].rstrip("Z").startswith(row[7].rstrip("0").rstrip(".")):
                raise AssertionError(f"event_time was not interpreted as UTC: {row[7]} vs {decoded['event_time']}")
            if decoded.get("account_created_at") and row[8] != decoded["account_created_at"].rstrip("Z"):
                raise AssertionError(f"account_created_at mismatch: {row[8]}")
        if curated[event(1)["event_id"]][6] != 0:
            raise AssertionError("deduplication must keep the first occurrence")
        print(f"lakehouse SQL: bronze {len(bronze)} records, curated {len(curated)} validated first occurrences")
    finally:
        shutil.rmtree(warehouse, ignore_errors=True)


if __name__ == "__main__":
    main()
