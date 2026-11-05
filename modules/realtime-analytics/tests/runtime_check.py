#!/usr/bin/env python3
"""Verify ClickHouse schema writes, replacement semantics and reader grants using synthetic rows."""

import argparse
import base64
from datetime import datetime, timezone
import json
import os
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def request(endpoint, password, statement, body=None, user="rpp_analytics"):
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
        "Content-Type": "application/x-ndjson",
    }
    with urlopen(Request(
        f"{endpoint}/?{urlencode({'query': statement})}",
        data=body,
        headers=headers,
        method="POST",
    ), timeout=15) as response:
        return response.read().decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    password = os.environ["CLICKHOUSE_PASSWORD"]
    event_id = str(uuid.uuid4())
    payment_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    fact = {
        "event_time_ms": timestamp, "event_id": event_id, "payment_id": payment_id,
        "account_id": account_id, "amount_minor": 1250, "currency": "EUR",
        "country": "DE", "payment_method": "card", "outcome": "authorized",
        "schema_version": 1, "job_name": "payment-windows",
        "job_version": "payment-windows-v1", "transformation_id": "payment-facts-v1",
        "source_dataset": "kafka:payment-processing-events-v1",
        "output_dataset": "clickhouse:analytics.payment_facts",
    }
    fact_body = (json.dumps(fact) + "\n" + json.dumps(fact) + "\n").encode()
    request(args.url, password, "INSERT INTO analytics.payment_facts FORMAT JSONEachRow", fact_body)
    fact_count = int(request(
        args.url, password,
        f"SELECT count() FROM analytics.payment_facts FINAL WHERE event_id = '{event_id}' FORMAT TabSeparated",
    ).strip())
    if fact_count != 1:
        raise AssertionError(f"ReplacingMergeTree FINAL returned {fact_count} copies of one fact")

    result_id = "b" * 64
    aggregate = {
        "result_id": result_id, "window_start_ms": timestamp - 60_000,
        "window_end_ms": timestamp, "currency": "EUR", "country": "DE",
        "payment_method": "card", "payments": 1, "authorized": 1,
        "authorized_amount_minor": 1250, "authorization_rate": 1.0,
        "rejections": 0, "revision": 1, "schema_version": 1,
        "job_name": "payment-windows", "job_version": "payment-windows-v1",
        "transformation_id": "payment-windows-v1",
        "input_dataset": "kafka:payment-processing-events-v1",
        "output_dataset": "kafka:payment-window-results-v1",
        "analytics_dataset": "clickhouse:analytics.payment_window_aggregates",
    }
    revised = {
        **aggregate, "payments": 2, "authorized": 2,
        "authorized_amount_minor": 2500, "revision": 2,
    }
    request(
        args.url, password, "INSERT INTO analytics.payment_window_aggregates FORMAT JSONEachRow",
        (json.dumps(aggregate) + "\n" + json.dumps(revised) + "\n").encode(),
    )
    latest = json.loads(request(
        args.url, password,
        f"SELECT revision, payments, authorized_amount_minor "
        f"FROM analytics.payment_window_aggregates FINAL WHERE result_id = '{result_id}' "
        "FORMAT JSONEachRow",
    ))
    if latest != {"revision": 2, "payments": 2, "authorized_amount_minor": 2500}:
        raise AssertionError(f"FINAL did not select the newest aggregate revision: {latest}")
    check_late_events(args.url, password, timestamp)
    check_risk_predictions(args.url, password, timestamp)
    check_bi_reader(args.url, os.environ["BI_READER_PASSWORD"])


def count_final(endpoint, password, table, condition):
    return int(request(
        endpoint, password, f"SELECT count() FROM analytics.{table} FINAL WHERE {condition} FORMAT TabSeparated",
    ).strip())


def check_late_events(endpoint, password, timestamp):
    event_id = str(uuid.uuid4())
    row = {
        "event_time_ms": timestamp - 900_000, "event_id": event_id, "payment_id": str(uuid.uuid4()),
        "amount_minor": 700, "currency": "EUR", "country": "DE", "payment_method": "card",
        "outcome": "rejected", "reason": "all-windows-expired", "watermark_ms": timestamp,
        "job_name": "payment-windows", "source_dataset": "kafka:payment-events-too-late",
        "output_dataset": "clickhouse:analytics.payment_late_events",
    }
    risk = {**row, "reason": "risk-feature-lateness-expired", "job_name": "advisory-risk-features"}
    body = "\n".join(json.dumps(item) for item in (row, row, risk)) + "\n"
    request(endpoint, password, "INSERT INTO analytics.payment_late_events FORMAT JSONEachRow", body.encode())
    found = count_final(endpoint, password, "payment_late_events", f"event_id = '{event_id}'")
    if found != 2:
        raise AssertionError(f"late events must deduplicate per event and reason, found {found}")


def check_risk_predictions(endpoint, password, timestamp):
    event_id = str(uuid.uuid4())
    first = {
        "prediction_id": "c" * 64, "original_prediction_id": "c" * 64, "event_id": event_id,
        "payment_id": str(uuid.uuid4()), "event_time_ms": timestamp, "window_revision": 1,
        "feature_version": "risk-features-v1", "model_name": "payment-risk", "model_version": "1.0.0",
        "model_sha256": "d" * 64, "risk_score": 0.2, "reason_codes": ["velocity"],
        "advisory_only": True, "prediction_time_ms": timestamp, "schema_version": 1,
        "source_dataset": "kafka:payment-risk-predictions-v1",
        "output_dataset": "clickhouse:analytics.payment_risk_predictions",
    }
    revised = {**first, "prediction_id": "e" * 64, "window_revision": 2, "risk_score": 0.8}
    request(
        endpoint, password, "INSERT INTO analytics.payment_risk_predictions FORMAT JSONEachRow",
        (json.dumps(first) + "\n" + json.dumps(revised) + "\n").encode(),
    )
    latest = json.loads(request(
        endpoint, password,
        "SELECT window_revision, risk_score FROM analytics.payment_risk_predictions FINAL "
        f"WHERE event_id = '{event_id}' FORMAT JSONEachRow",
    ))
    if latest != {"window_revision": 2, "risk_score": 0.8}:
        raise AssertionError(f"FINAL did not select the newest prediction revision: {latest}")


def check_bi_reader(endpoint, password):
    rows = int(request(
        endpoint, password, "SELECT count() FROM analytics.payment_facts FORMAT TabSeparated",
        user="rpp_bi_reader",
    ).strip())
    if rows < 1:
        raise AssertionError("BI reader could not read analytics.payment_facts")
    for statement in (
        "INSERT INTO analytics.payment_late_events (event_id) VALUES (generateUUIDv4())",
        "DROP TABLE analytics.payment_facts",
        "CREATE TABLE analytics.reader_probe (id UInt8) ENGINE = Memory",
    ):
        try:
            request(endpoint, password, statement, user="rpp_bi_reader")
        except HTTPError as error:
            if error.code not in (403, 500):
                raise
            continue
        raise AssertionError(f"BI reader unexpectedly ran: {statement}")


if __name__ == "__main__":
    main()
