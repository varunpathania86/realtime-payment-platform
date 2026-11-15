"""Verify authenticated API-originated payments across the enabled streaming sinks."""

import argparse
import json
import re
import subprocess
import time

from scripts.lakehouse import trino


PROGRAM = r'''
import base64, json, os, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4
from payment_client import PaymentClient

client = PaymentClient()
account = str(uuid4())
records = []
for outcome in ("authorized", "rejected"):
    event = {"event_id": str(uuid4()), "account_id": account, "amount_minor": 1250,
        "currency": "EUR", "country": "DE", "payment_method": "transfer",
        "event_time": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        "outcome": outcome}
    first = client.create(event)
    assert client.create(event) == first, "API idempotent replay changed the payment"
    records.append({**first, "expected_outcome": outcome})
    changed = {**event, "amount_minor": 1251}
    try:
        client.create(changed)
        raise AssertionError("API accepted conflicting idempotency payload")
    except RuntimeError as error:
        assert "HTTP 409" in str(error), str(error)

def denied(headers, payload, expected):
    request = Request(client.api + "/payments", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid4()), **headers})
    try:
        urlopen(request, timeout=10).close()
        raise AssertionError("API accepted invalid or unauthorized request")
    except HTTPError as error:
        assert error.code == expected, (error.code, expected)
        error.close()

payload = {"account_id": account, "amount_minor": 100, "currency": "EUR", "country": "DE", "payment_method": "card"}
denied({}, payload, 401)
denied({"Authorization": "Bearer invalid"}, payload, 401)
denied({"Authorization": "Bearer " + client.access_token()},
    {key: value for key, value in payload.items() if key != "country"}, 400)

auth = base64.b64encode((os.environ["CLICKHOUSE_USER"] + ":" + os.environ["CLICKHOUSE_PASSWORD"]).encode()).decode()
def query(sql):
    request = Request(os.environ["CLICKHOUSE_URL"] + "/?" + urlencode({"query": sql}),
        data=b"", headers={"Authorization": "Basic " + auth})
    with urlopen(request, timeout=10) as response:
        return [json.loads(line) for line in response.read().decode().splitlines()]

expected = {record["processing_event_id"]: record for record in records}
event_ids = ",".join("'" + identifier + "'" for identifier in expected)
deadline = time.monotonic() + 180
while True:
    facts = query("SELECT event_id, payment_id, country, payment_method, outcome FROM analytics.payment_facts FINAL "
        "WHERE account_id = '" + account + "' FORMAT JSONEachRow")
    risk = query("SELECT event_id FROM analytics.payment_risk_predictions FINAL "
        "WHERE event_id IN (" + event_ids + ") FORMAT JSONEachRow") if os.environ["RISK_ENABLED"] == "true" else []
    if len(facts) == len(expected) and (os.environ["RISK_ENABLED"] != "true" or set(expected) <= {r["event_id"] for r in risk}):
        break
    if time.monotonic() >= deadline:
        raise TimeoutError("API-originated facts/predictions did not reach ClickHouse")
    time.sleep(2)
for fact in facts:
    source = expected[fact["event_id"]]
    assert fact["payment_id"] == source["id"]
    assert fact["country"] == source["country"] and fact["payment_method"] == source["payment_method"]
    assert fact["outcome"] == source["expected_outcome"]
print(json.dumps({"acceptance": "passed", "api_idempotency": "passed", "api_rejections": "passed",
    "payments": records, "clickhouse_facts": len(facts), "prediction_events": len({r["event_id"] for r in risk}),
    "lakehouse_enabled": os.environ["LAKEHOUSE_ENABLED"] == "true"}))
'''


def verify(profile):
    result = subprocess.run(["kubectl", "--context", profile, "--request-timeout=30s", "-n", "apps",
        "exec", "-i", "deployment/stream-processor-jobmanager", "--", "env", "PYTHONPATH=/opt/job", "python", "-"],
        input=PROGRAM, text=True, stdout=subprocess.PIPE, timeout=240, check=True)
    report = json.loads(result.stdout)
    if report["lakehouse_enabled"]:
        identifiers = ",".join("'" + record["id"] + "'" for record in report["payments"])
        expected = {record["processing_event_id"]: record["id"] for record in report["payments"]}
        deadline = time.monotonic() + 180
        while True:
            rows = trino(profile, "SELECT event_id, payment_id FROM payment_events WHERE payment_id IN (" + identifiers + ")")
            if {row["event_id"]: row["payment_id"] for row in rows} == expected and len(rows) == len(expected):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("API-originated payments did not reach curated Iceberg without duplicates")
            time.sleep(10)
        report["iceberg_curated_events"] = len(rows)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"rpp(?:-[123])?", args.profile):
        raise ValueError("use an explicit supported profile")
    verify(args.profile)


if __name__ == "__main__":
    main()
