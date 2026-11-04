"""Run synthetic fixtures and inspect compacted output from inside the Flink pod."""

import argparse
from datetime import datetime, timezone
import json
import os
import ssl
import time
from uuid import NAMESPACE_URL, uuid5

from kafka import KafkaConsumer, KafkaProducer, TopicPartition

from fixtures import dataset, event as fixture_event
from processing import decode


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
    producer = KafkaProducer(**connection(), acks="all")
    sent = 0
    advance = None

    def send(event, partition):
        nonlocal sent
        raw = json.dumps(event)
        producer.send("payment-processing-events-v1", partition=partition,
            timestamp_ms=decode(raw)["timestamp_ms"], value=raw.encode()).get(timeout=30)
        sent += 1

    try:
        for record in dataset():
            if record["case"] in ("late-accepted", "too-late"):
                time.sleep(35)
                # Keep one split active after the others idle; all-idle input
                # alone cannot guarantee a newly advanced source watermark.
                send(*advance)
                time.sleep(2)
            event = record["event"]
            for key in ("event_id", "payment_id"):
                event[key] = str(uuid5(NAMESPACE_URL, run_id + "/" + event[key]))
            send(event, record["partition"])
            if record["case"] == "advance-watermark":
                advance = (event, record["partition"])
        sentinel = fixture_event(900000, 1800, currency="XXX")
        for key in ("event_id", "payment_id"):
            sentinel[key] = str(uuid5(NAMESPACE_URL, run_id + "/" + sentinel[key]))
        send(sentinel, 1)
        time.sleep(35)
        send(sentinel, 1)
        time.sleep(2)
        producer.flush(timeout=30)
    finally:
        producer.close(timeout=10)
    print(json.dumps({"run_id": run_id, "records": sent, "idle_partition": 2,
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
    producer = KafkaProducer(**connection(), acks="all")
    try:
        for index in range(500):
            event = fixture_event(10000 + index, 3600 + index // 10, currency="USD")
            raw = json.dumps(event)
            producer.send("payment-processing-events-v1", partition=index % 2,
                timestamp_ms=decode(raw)["timestamp_ms"], value=raw.encode()).get(timeout=30)
            if index % 50 == 0:
                producer.send("payment-processing-events-v1", partition=index % 2,
                    timestamp_ms=decode(raw)["timestamp_ms"], value=raw.encode()).get(timeout=30)
            time.sleep(.1)
        sentinel = fixture_event(900001, 5400, currency="XXX")
        for attempt in range(2):
            raw = json.dumps(sentinel)
            producer.send("payment-processing-events-v1", partition=1,
                timestamp_ms=decode(raw)["timestamp_ms"], value=raw.encode()).get(timeout=30)
            if attempt == 0:
                time.sleep(35)
        producer.flush(timeout=30)
    finally:
        producer.close(timeout=10)
    print(json.dumps({"unique_input_events": 500, "duplicate_input_events": 10,
        "expected_usd_minute_payments": 500}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fixtures", "inspect", "load"))
    parser.add_argument("--run-id", default="stage5-acceptance")
    args = parser.parse_args()
    if args.action == "fixtures":
        produce(args.run_id)
    elif args.action == "inspect":
        inspect()
    else:
        load()
