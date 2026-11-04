"""Normalized ClickHouse records and an at-least-once HTTP sink."""

import base64
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pyflink.datastream.functions import MapFunction

FACTS_TABLE = "payment_facts"
WINDOWS_TABLE = "payment_window_aggregates"
TABLES = {FACTS_TABLE, WINDOWS_TABLE}
def payment_fact(event):
    return {
        "event_time_ms": event["timestamp_ms"],
        "event_id": event["event_id"],
        "payment_id": event["payment_id"],
        "account_id": event["account_id"],
        "amount_minor": event["amount_minor"],
        "currency": event["currency"],
        "country": event["country"],
        "payment_method": event["payment_method"],
        "outcome": event["outcome"],
        "schema_version": event["schema_version"],
        "job_name": "payment-windows",
        "job_version": "payment-windows-v1",
        "transformation_id": "payment-facts-v1",
        "source_dataset": "kafka:payment-processing-events-v1",
        "output_dataset": "clickhouse:analytics.payment_facts",
    }


def payment_window(raw):
    result = json.loads(raw)
    return {
        "result_id": result["result_id"],
        "window_start_ms": result["window_start"],
        "window_end_ms": result["window_end"],
        "currency": result["currency"],
        "country": result["country"],
        "payment_method": result["payment_method"],
        "payments": result["payments"],
        "authorized": result["authorized"],
        "authorized_amount_minor": result["authorized_amount_minor"],
        "authorization_rate": result["authorization_rate"],
        "rejections": result["rejections"],
        "revision": result["revision"],
        "schema_version": result["schema_version"],
        "job_name": "payment-windows",
        "job_version": "payment-windows-v1",
        "transformation_id": result["transformation_id"],
        "input_dataset": result["input_dataset"],
        "output_dataset": result["output_dataset"],
        "analytics_dataset": "clickhouse:analytics.payment_window_aggregates",
    }


def insert_batch(endpoint, user, password, table, rows):
    if table not in TABLES:
        raise ValueError(f"unsupported ClickHouse table: {table}")
    query = f"INSERT INTO analytics.{table} FORMAT JSONEachRow"
    credential = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = Request(
        f"{endpoint}/?{urlencode({'query': query})}",
        data=("\n".join(rows) + "\n").encode(),
        headers={"Authorization": f"Basic {credential}", "Content-Type": "application/x-ndjson"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        response.read()


def write_payment_fact(event, endpoint, user, password):
    row = json.dumps(payment_fact(event), separators=(",", ":"), sort_keys=True)
    try:
        insert_batch(endpoint, user, password, FACTS_TABLE, [row])
    except Exception as error:
        raise RuntimeError(f"ClickHouse insert failed for analytics.{FACTS_TABLE}") from error


class ClickHouseMap(MapFunction):
    def __init__(self, table):
        if table not in TABLES:
            raise ValueError(f"unsupported ClickHouse table: {table}")
        self.table = table

    def open(self, runtime_context):
        import os

        self.endpoint = os.environ["CLICKHOUSE_URL"].rstrip("/")
        self.user = os.environ["CLICKHOUSE_USER"]
        self.password = os.environ["CLICKHOUSE_PASSWORD"]

    def map(self, value):
        row = payment_window(value) if self.table == WINDOWS_TABLE else json.loads(value)
        encoded = json.dumps(row, separators=(",", ":"), sort_keys=True)
        try:
            insert_batch(self.endpoint, self.user, self.password, self.table, [encoded])
        except Exception as error:
            raise RuntimeError(f"ClickHouse insert failed for analytics.{self.table}") from error
        return value
