"""Send synthetic API requests and inspect compacted output from the Flink pod."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import ssl
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from kafka import KafkaConsumer, TopicPartition
from payment_client import PaymentClient

from fixtures import dataset, event as fixture_event


def connection():
    options = {"bootstrap_servers": os.environ["KAFKA_BROKERS"].split(",")}
    if os.environ.get("KAFKA_TLS") == "true":
        options.update({
            "security_protocol": "SASL_SSL", "sasl_mechanism": "SCRAM-SHA-512",
            "sasl_plain_username": "payments", "sasl_plain_password": os.environ["KAFKA_PASSWORD"],
            "ssl_context": ssl.create_default_context(cafile="/etc/kafka/ca.pem"),
        })
    return options


def produce(run_id):
    producer = PaymentClient()
    sent = 0
    advance = None
    marker_number = 0
    base = datetime.now(timezone.utc) - timedelta(seconds=1800)
    fixture_base = datetime(2026, 11, 3, tzinfo=timezone.utc)
    account = str(uuid5(NAMESPACE_URL, run_id + "/account"))

    def send(event):
        nonlocal sent
        producer.create(event)
        sent += 1

    for record in dataset():
        if record["case"] in ("late-accepted", "too-late"):
            time.sleep(35)
            # Keep one split active after the others idle; all-idle input
            # alone cannot guarantee a newly advanced source watermark.
            marker_number += 1
            send({**advance, "currency": "USD",
                "event_id": str(uuid5(NAMESPACE_URL, run_id + "/advance/" + str(marker_number)))})
            time.sleep(2)
        event = record["event"]
        offset = datetime.fromisoformat(event["event_time"].replace("Z", "+00:00")) - fixture_base
        event["event_time"] = (base + offset).isoformat().replace("+00:00", "Z")
        event["account_id"] = account
        for key in ("event_id", "payment_id"):
            event[key] = str(uuid5(NAMESPACE_URL, run_id + "/" + event[key]))
        send(event)
        if record["case"] == "advance-watermark":
            advance = event
    sentinel = fixture_event(900000, 1800, currency="USD", account_id=account,
        event_time=(base + timedelta(seconds=1800)).isoformat().replace("+00:00", "Z"))
    for key in ("event_id", "payment_id"):
        sentinel[key] = str(uuid5(NAMESPACE_URL, run_id + "/" + sentinel[key]))
    send(sentinel)
    time.sleep(35)
    send({**sentinel, "event_id": str(uuid5(NAMESPACE_URL, run_id + "/final-advance"))})
    time.sleep(2)
    print(json.dumps({"run_id": run_id, "api_requests": sent, "partitioning": "account-keyed relay",
        "expected_eur_minute_payments": 26}))


def inspect():
    consumer = KafkaConsumer(**connection(), enable_auto_commit=False, group_id=None,
        request_timeout_ms=30000)
    results = {}
    late = []
    try:
        topics = ("payment-window-results-v1", "payment-events-too-late")
        partitions = [TopicPartition(topic, partition) for topic in topics
            for partition in consumer.partitions_for_topic(topic)]
        consumer.assign(partitions)
        for partition, start in consumer.beginning_offsets(partitions).items():
            consumer.seek(partition, start)
        ends = consumer.end_offsets(partitions)
        deadline = time.monotonic() + 60
        while any(consumer.position(partition) < end for partition, end in ends.items()):
            if time.monotonic() >= deadline:
                raise TimeoutError("could not consume the bounded inspection snapshot")
            for records in consumer.poll(timeout_ms=1000).values():
                for record in records:
                    if record.offset >= ends[TopicPartition(record.topic, record.partition)]:
                        continue
                    value = json.loads(record.value)
                    if record.topic == topics[0]:
                        result = json.loads(value["payload"])
                        results[record.key.decode()] = result
                    else:
                        late.append(value)
    finally:
        consumer.close()
    print(json.dumps({"inspected_at": datetime.now(timezone.utc).isoformat(),
        "results": results, "too_late": late}, sort_keys=True))


def load():
    producer = PaymentClient()
    run_id = str(uuid4())
    base = datetime.now(timezone.utc) - timedelta(seconds=60)
    account = str(uuid5(NAMESPACE_URL, run_id + "/account"))
    for index in range(500):
        event = fixture_event(10000 + index, currency="USD", account_id=account,
            event_id=str(uuid5(NAMESPACE_URL, run_id + "/request/" + str(index))),
            event_time=(base + timedelta(seconds=index // 10)).isoformat().replace("+00:00", "Z"))
        producer.create(event)
        if index % 50 == 0:
            producer.create(event)
        time.sleep(.1)
    sentinel = fixture_event(900001, currency="GBP", account_id=account,
        event_time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    for attempt in range(2):
        producer.create({**sentinel, "event_id": str(uuid5(NAMESPACE_URL, run_id + "/advance/" + str(attempt)))})
        if attempt == 0:
            time.sleep(35)
    print(json.dumps({"unique_input_events": 500, "duplicate_input_events": 10,
        "expected_usd_minute_payments": 500}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fixtures", "inspect", "load"))
    parser.add_argument("--run-id", default=str(uuid4()))
    args = parser.parse_args()
    if args.action == "fixtures":
        produce(args.run_id)
    elif args.action == "inspect":
        inspect()
    else:
        load()
