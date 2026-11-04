"""Prepare writable Flink configuration and submit the explicitly named jobs."""

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
LAKEHOUSE_JOB_NAME = "payment-lakehouse-v1"
INACTIVE = ("FINISHED", "CANCELED", "FAILED")


def overview():
    with urllib.request.urlopen("http://flink-jobs:8081/jobs/overview", timeout=5) as response:
        return json.load(response)["jobs"]


def wait_for_checkpoint(previous_ids=(), name=JOB_NAME):
    for _ in range(120):
        jobs = [job for job in overview() if job["name"] == name
            and job["jid"] not in previous_ids]
        if not jobs:
            time.sleep(2)
            continue
        if len(jobs) != 1:
            raise RuntimeError(f"expected exactly one submitted {name} job")
        job = jobs[0]
        if job["state"] in INACTIVE:
            raise RuntimeError(f"submitted {name} job is {job['state']}; inspect Flink exceptions before retry")
        if job["state"] == "RUNNING":
            with urllib.request.urlopen(
                f"http://flink-jobs:8081/jobs/{job['jid']}/checkpoints", timeout=5
            ) as response:
                checkpoint = json.load(response)["latest"]["completed"]
            if checkpoint is not None:
                print(f"Job {name} {job['jid']} completed checkpoint {checkpoint['id']}", flush=True)
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
        topics = ["payment-processing-events-v1", "payment-window-results-v1", "payment-events-too-late"]
        if os.environ.get("RISK_ENABLED") == "true":
            topics.extend(["payment-risk-predictions-v1", "payment-risk-too-late-v1"])
        for name in topics:
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


def expected_jobs(environ=os.environ):
    return [JOB_NAME] + ([LAKEHOUSE_JOB_NAME] if environ.get("LAKEHOUSE_ENABLED") == "true" else [])


def missing_jobs(jobs, expected):
    """Return expected job names to submit; refuse duplicate or unexpected active jobs."""
    active = [job["name"] for job in jobs if job["state"] not in INACTIVE]
    if len(active) != len(set(active)) or set(active) - set(expected):
        raise RuntimeError(f"unexpected active jobs {sorted(active)}; refusing duplicate or conflicting submission")
    for name in active:
        print(f"Job {name} is already active; no duplicate submitted", flush=True)
    return [name for name in expected if name not in active]


def submit_command(name, environ=os.environ):
    command = ["/opt/flink/bin/flink", "run", "-d", "-m", "flink-jobs:8081"]
    restore = environ.get("RESTORE_PATH" if name == JOB_NAME else "LAKEHOUSE_RESTORE_PATH")
    if restore:
        command.extend(["--fromSavepoint", restore])
    if name == LAKEHOUSE_JOB_NAME:
        # Pure SQL: TaskManagers run no Python, so no --pyFiles are distributed.
        return command + ["--python", "/opt/job/lakehouse.py"]
    python_files = ["/opt/job/processing.py", "/opt/job/analytics.py"]
    if environ.get("RISK_ENABLED") == "true":
        from model_registry import download_package

        python_files.extend(["/opt/job/risk.py", download_package()])
    return command + ["--python", "/opt/job/job.py", "--pyFiles", ",".join(python_files)]


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
    expected = expected_jobs()
    missing = missing_jobs(jobs, expected)
    if missing:
        bootstrap()
    previous = {job["jid"] for job in jobs if job["state"] in INACTIVE}
    for name in expected:
        if name in missing:
            subprocess.run(submit_command(name), check=True)
        wait_for_checkpoint(previous, name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "submit"))
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        submit()
