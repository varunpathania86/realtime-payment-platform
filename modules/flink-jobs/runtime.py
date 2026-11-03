"""Prepare writable Flink configuration and submit one explicitly named job."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import ssl

import boto3
from botocore.exceptions import ClientError
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from ruamel.yaml import YAML

from configuration import overlay

JOB_NAME = "payment-event-time-v1"


def overview():
    with urllib.request.urlopen("http://flink-jobs:8081/jobs/overview", timeout=5) as response:
        return json.load(response)["jobs"]


def wait_for_checkpoint(previous_ids=()):
    for _ in range(120):
        jobs = [job for job in overview() if job["name"] == JOB_NAME
            and job["jid"] not in previous_ids]
        if not jobs:
            time.sleep(2)
            continue
        if len(jobs) != 1:
            raise RuntimeError("expected exactly one submitted payment job")
        job = jobs[0]
        if job["state"] in ("FINISHED", "CANCELED", "FAILED"):
            raise RuntimeError(f"submitted payment job is {job['state']}; inspect Flink exceptions before retry")
        if job["state"] == "RUNNING":
            with urllib.request.urlopen(
                f"http://flink-jobs:8081/jobs/{job['jid']}/checkpoints", timeout=5
            ) as response:
                checkpoint = json.load(response)["latest"]["completed"]
            if checkpoint is not None:
                print(f"Job {job['jid']} completed checkpoint {checkpoint['id']}", flush=True)
                return
        time.sleep(2)
    raise TimeoutError("submitted job did not complete its first durable checkpoint within four minutes")


def prepare():
    destination = Path("/runtime/conf")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree("/opt/flink/conf", destination, dirs_exist_ok=True)
    yaml = YAML(typ="safe")
    base = yaml.load(Path("/opt/flink/conf/config.yaml").read_text())
    settings = yaml.load(Path("/etc/job/config.yaml").read_text())
    settings["taskmanager.host"] = socket.gethostbyname(socket.gethostname())
    with Path("/runtime/conf/config.yaml").open("w") as stream:
        yaml.dump(overlay(base, settings), stream)


def bootstrap():
    client = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"])
    try:
        client.head_bucket(Bucket="flink-state")
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
            raise
        client.create_bucket(Bucket="flink-state")
    options = {"bootstrap_servers": os.environ["KAFKA_BROKERS"].split(",")}
    if os.environ.get("KAFKA_TLS") == "true":
        options.update({
            "security_protocol": "SASL_SSL", "sasl_mechanism": "SCRAM-SHA-512",
            "sasl_plain_username": "payments", "sasl_plain_password": os.environ["KAFKA_PASSWORD"],
            "ssl_context": ssl.create_default_context(cafile="/etc/kafka/ca.pem"),
        })
    replicas = int(os.environ.get("KAFKA_REPLICATION_FACTOR", "1"))
    admin = KafkaAdminClient(**options)
    try:
        for name in ("payment-processing-events-v1", "payment-window-results-v1", "payment-events-too-late"):
            config = {"min.insync.replicas": str(min(2, replicas))}
            if name == "payment-window-results-v1":
                config["cleanup.policy"] = "compact"
            try:
                admin.create_topics([NewTopic(name, 3, replicas, topic_configs=config)])
            except TopicAlreadyExistsError:
                description = admin.describe_topics([name])[0]
                if len(description["partitions"]) != 3 or any(
                    len(partition["replicas"]) != replicas for partition in description["partitions"]
                ):
                    raise ValueError(f"{name}: existing topology conflicts with three partitions/RF {replicas}") from None
    finally:
        admin.close()


def submit():
    for attempt in range(120):
        try:
            jobs = overview()
            break
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"Waiting for Flink REST endpoint ({attempt + 1}/120): {error}", flush=True)
            time.sleep(2)
    else:
        raise RuntimeError("Flink REST endpoint did not become ready")
    active = [job for job in jobs if job["state"] not in ("FINISHED", "CANCELED", "FAILED")]
    if active:
        if len(active) != 1 or active[0]["name"] != JOB_NAME:
            raise RuntimeError("unexpected active jobs; refusing duplicate or conflicting submission")
        print(f"Job {active[0]['jid']} is already active; no duplicate submitted", flush=True)
        wait_for_checkpoint({job["jid"] for job in jobs if job["jid"] != active[0]["jid"]})
        return
    bootstrap()
    command = ["/opt/flink/bin/flink", "run", "-d", "-m", "flink-jobs:8081"]
    if os.environ.get("RESTORE_PATH"):
        command.extend(["--fromSavepoint", os.environ["RESTORE_PATH"]])
    command.extend(["--python", "/opt/job/job.py", "--pyFiles", "/opt/job/processing.py"])
    subprocess.run(command, check=True)
    wait_for_checkpoint({job["jid"] for job in jobs})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "submit"))
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        submit()
