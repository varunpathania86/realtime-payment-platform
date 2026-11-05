#!/usr/bin/env python3
"""Verify ClickHouse schema writes and replacement semantics using synthetic rows."""

import argparse
import base64
from datetime import datetime, timezone
import json
import os
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def request(endpoint, password, statement, body=None):
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"rpp_analytics:{password}".encode()).decode(),
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


if __name__ == "__main__":
    main()
