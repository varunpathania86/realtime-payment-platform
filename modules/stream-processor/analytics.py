"""Normalized ClickHouse records and an at-least-once HTTP sink."""

import base64
import json
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pyflink.datastream.functions import MapFunction

FACTS_TABLE = "payment_facts"
WINDOWS_TABLE = "payment_window_aggregates"
LATE_TABLE = "payment_late_events"
RISK_TABLE = "payment_risk_predictions"
TABLES = {FACTS_TABLE, WINDOWS_TABLE, LATE_TABLE, RISK_TABLE}
LATE_SOURCES = {
    "all-windows-expired": ("payment-windows", "kafka:payment-events-too-late"),
    "risk-feature-lateness-expired": ("advisory-risk-features", "kafka:payment-risk-too-late-v1"),
}


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


def late_event(raw):
    record = json.loads(raw)
    event = record["event"]
    job_name, source_dataset = LATE_SOURCES[record["reason"]]
    return {
        "event_time_ms": event["timestamp_ms"],
        "event_id": event["event_id"],
        "payment_id": event["payment_id"],
        "amount_minor": event["amount_minor"],
        "currency": event["currency"],
        "country": event["country"],
        "payment_method": event["payment_method"],
        "outcome": event["outcome"],
        "reason": record["reason"],
        "watermark_ms": record["watermark_ms"],
        "job_name": job_name,
        "source_dataset": source_dataset,
        "output_dataset": f"clickhouse:analytics.{LATE_TABLE}",
    }


def risk_prediction(raw):
    prediction = json.loads(raw)
    stamp = datetime.fromisoformat(prediction["prediction_timestamp"].replace("Z", "+00:00"))
    return {
        "prediction_id": prediction["prediction_id"],
        "original_prediction_id": prediction["original_prediction_id"],
        "event_id": prediction["event_id"],
        "payment_id": prediction["payment_id"],
        "event_time_ms": prediction["window_end_ms"],
        "window_revision": prediction["window_revision"],
        "feature_version": prediction["feature_version"],
        "model_name": prediction["model_name"],
        "model_version": prediction["model_version"],
        "model_sha256": prediction["model_sha256"],
        "risk_score": prediction["risk_score"],
        "reason_codes": list(prediction["reason_codes"]),
        "advisory_only": bool(prediction["advisory_only"]),
        "prediction_time_ms": round(stamp.timestamp() * 1000),
        "schema_version": prediction["schema_version"],
        "source_dataset": "kafka:payment-risk-predictions-v1",
        "output_dataset": f"clickhouse:analytics.{RISK_TABLE}",
    }


ROW_MAPPERS = {WINDOWS_TABLE: payment_window, LATE_TABLE: late_event, RISK_TABLE: risk_prediction}


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
        row = ROW_MAPPERS.get(self.table, json.loads)(value)
        encoded = json.dumps(row, separators=(",", ":"), sort_keys=True)
        try:
            insert_batch(self.endpoint, self.user, self.password, self.table, [encoded])
        except Exception as error:
            raise RuntimeError(f"ClickHouse insert failed for analytics.{self.table}") from error
        return value
