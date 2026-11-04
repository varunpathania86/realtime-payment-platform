"""Run bounded advisory prediction/revision acceptance on an explicit local profile."""

import argparse
import json
import re
import subprocess


PROGRAM = r'''
from datetime import datetime, timedelta, timezone
import json, signal, sys, time, urllib.request
from uuid import NAMESPACE_URL, uuid4, uuid5
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from control import connection
from fixtures import event
from model_registry import download_package
from processing import decode

def deadline(_signal, _frame):
    raise SystemExit("ERROR risk acceptance exceeded its 280-second remote deadline")

signal.signal(signal.SIGALRM, deadline)
signal.alarm(280)
sys.path.insert(0, download_package())
from payment_risk.scorer import Scorer
scorer = Scorer()
print("Risk acceptance: verified model loaded", file=sys.stderr, flush=True)
run = str(uuid4())
base = datetime.now(timezone.utc) - timedelta(seconds=90)
account = str(uuid5(NAMESPACE_URL, run + "/account"))

def fixture(index, seconds, **changes):
    return event(index, seconds,
        event_id=str(uuid5(NAMESPACE_URL, run + "/event/" + str(index))),
        payment_id=str(uuid5(NAMESPACE_URL, run + "/payment/" + str(index))),
        account_id=account,
        event_time=(base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
        account_created_at=(base - timedelta(days=90)).isoformat().replace("+00:00", "Z"),
        device_id=str(uuid5(NAMESPACE_URL, run + "/device")), **changes)

topics = ["payment-risk-predictions-v1", "payment-risk-too-late-v1"]
consumer = KafkaConsumer(**connection(), enable_auto_commit=False, group_id=None,
    request_timeout_ms=30000)
partitions = [TopicPartition(topic, partition) for topic in topics
    for partition in consumer.partitions_for_topic(topic)]
consumer.assign(partitions)
for partition, end in consumer.end_offsets(partitions).items():
    consumer.seek(partition, end)
print("Risk acceptance: audit starting offsets initialized", file=sys.stderr, flush=True)
producer = KafkaProducer(**connection(), acks="all")
audit, expired = [], []

def send(record):
    producer.send("payment-processing-events-v1", json.dumps(record).encode(),
        partition=0, timestamp_ms=decode(json.dumps(record))["timestamp_ms"]).get(timeout=20)
    producer.flush(timeout=20)

def collect(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for records in consumer.poll(timeout_ms=1000).values():
            for record in records:
                value = json.loads(record.value)
                event_id = value.get("event_id", value.get("event", {}).get("event_id"))
                if event_id in identifiers:
                    (audit if record.topic == topics[0] else expired).append(value)
        if predicate():
            return
    raise TimeoutError("risk acceptance timed out waiting for expected audit records")

def rest(path):
    with urllib.request.urlopen("http://stream-processor:8081" + path, timeout=10) as response:
        return json.load(response)

first, later, late = fixture(1, 0), fixture(2, 60), fixture(3, 30, outcome="rejected")
marker, too_late = fixture(4, 90), fixture(5, -300)
identifiers = {record["event_id"] for record in (first, later, late, marker, too_late)}
try:
    initial_job = next(item for item in rest("/jobs/overview")["jobs"]
        if item["name"] == "payment-event-time-v1" and item["state"] == "RUNNING")
    initial_checkpoints = rest("/jobs/" + initial_job["jid"] + "/checkpoints")["counts"]
    send(first)
    send(later)
    collect(lambda: any(value["event_id"] == later["event_id"] for value in audit))
    original = next(value for value in audit if value["event_id"] == later["event_id"])
    send(late)
    collect(lambda: any(value["event_id"] == later["event_id"] and value["window_revision"] == 2
        for value in audit))
    revised = next(value for value in audit if value["event_id"] == later["event_id"]
        and value["window_revision"] == 2)
    assert revised["original_prediction_id"] == original["prediction_id"]
    assert revised["prediction_id"] != original["prediction_id"]
    expected, _, _ = scorer.predict(decode(json.dumps(later)),
        [decode(json.dumps(value)) for value in (first, late)], 2, original["prediction_id"])
    assert revised["feature_hash"] == expected["feature_hash"]
    assert abs(revised["risk_score"] - expected["risk_score"]) <= 1e-5
    assert all(value["advisory_only"] for value in audit)
    send(marker)
    collect(lambda: any(value["event_id"] == marker["event_id"] for value in audit))
    job = next(item for item in rest("/jobs/overview")["jobs"]
        if item["name"] == "payment-event-time-v1" and item["state"] == "RUNNING")
    details = rest("/jobs/" + job["jid"])
    risk = next(item for item in details["vertices"] if "advisory-risk-predictions" in item["name"])
    metrics_path = "/jobs/" + job["jid"] + "/vertices/" + risk["id"] + "/metrics"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        metric_ids = [item["id"] for item in rest(metrics_path)]
        watermark_ids = [name for name in metric_ids
            if name.endswith(".advisory-risk-predictions.currentInputWatermark")]
        if watermark_ids:
            break
        time.sleep(2)
    else:
        raise TimeoutError("risk input watermark metrics were not registered")
    deadline = time.monotonic() + 90
    minimum = decode(json.dumps(too_late))["timestamp_ms"] + 120000
    while time.monotonic() < deadline:
        values = rest(metrics_path + "?get=" + ",".join(watermark_ids))
        if values and min(int(item["value"]) for item in values) >= minimum:
            break
        time.sleep(2)
    else:
        raise TimeoutError("risk watermark did not advance")
    send(too_late)
    collect(lambda: bool(expired))
    assert expired[0]["reason"] == "risk-feature-lateness-expired"
    send(late)
    end = time.monotonic() + 12
    while time.monotonic() < end:
        for records in consumer.poll(timeout_ms=1000).values():
            for record in records:
                value = json.loads(record.value)
                event_id = value.get("event_id", value.get("event", {}).get("event_id"))
                assert event_id not in identifiers, "duplicate input produced a new output"
    version = scorer.metadata["model_version"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        selected = [item["id"] for item in rest(metrics_path) if "risk_model" in item["id"] and
            any(item["id"].endswith(suffix) for suffix in (
                "prediction_count", "prediction_revision_count",
                "inference_duration_total_us", "risk_too_late_event_count"))]
        if len(selected) == 4:
            break
        time.sleep(2)
    else:
        raise TimeoutError("four version-labelled risk counters were not registered")
    telemetry = rest(metrics_path + "?get=" + ",".join(selected))
    assert len(telemetry) == 4 and all(version.replace(".", "_") in item["id"] for item in telemetry)
    for metric in telemetry:
        minimum = 5 if metric["id"].endswith(".prediction_count") else 1
        assert int(metric["value"]) >= minimum, "risk metric did not reflect the exercised behavior"
    checkpoints = rest("/jobs/" + job["jid"] + "/checkpoints")
    assert checkpoints["counts"]["completed"] > initial_checkpoints["completed"]
    assert checkpoints["counts"]["failed"] == initial_checkpoints["failed"]
    print(json.dumps({"acceptance": "passed", "run_id": run,
        "model_version": revised["model_version"], "model_sha256": revised["model_sha256"],
        "original_prediction_id": original["prediction_id"], "revised_prediction_id": revised["prediction_id"],
        "revision": revised["window_revision"], "score": revised["risk_score"],
        "feature_hash": revised["feature_hash"], "audit_records": len(audit),
        "too_late_records": len(expired), "checkpoints": checkpoints["counts"],
        "initial_checkpoints": initial_checkpoints,
        "model_telemetry": telemetry}, indent=2))
finally:
    producer.close(timeout=10)
    consumer.close()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        raise ValueError("invalid explicit profile")
    subprocess.run(
        ["kubectl", "--context", args.profile, "--request-timeout=30s", "-n", "apps",
            "exec", "-i", "deployment/stream-processor-jobmanager", "--",
            "env", "PYTHONPATH=/opt/job", "python", "-"],
        input=PROGRAM, text=True, check=True, timeout=300,
    )


if __name__ == "__main__":
    main()
