#!/usr/bin/env python3
"""Use explicit local contexts for Flink fixtures, inspection and savepoints."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def command(profile, *args):
    return subprocess.run(["kubectl", "--context", profile, "--request-timeout=30s", "-n", "apps", *args],
        check=True, text=True, capture_output=True, timeout=180).stdout


def rest(profile, path, body=None):
    program = (
        "import urllib.request; "
        f"r=urllib.request.Request('http://flink-jobs:8081{path}', "
        f"data={None if body is None else json.dumps(body).encode()!r}, "
        "headers={'Content-Type':'application/json'}); "
        "print(urllib.request.urlopen(r,timeout=30).read().decode())"
    )
    return json.loads(command(profile, "exec", "deployment/flink-jobs-jobmanager", "--", "python", "-c", program))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fixtures", "inspect", "load", "savepoint", "stop", "guard"))
    parser.add_argument("--profile", default=os.environ.get("MINIKUBE_PROFILE", os.environ.get("DEV_PROFILE", "rpp") + "-a"))
    parser.add_argument("--images", type=Path, help="candidate image inventory for the pre-build guard")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        raise ValueError("invalid explicit Minikube profile")
    if args.action == "guard":
        deployment = command(args.profile, "get", "deployment", "flink-jobs-jobmanager",
            "--ignore-not-found", "-o", "json")
        if not deployment.strip():
            return
        image = json.loads(deployment)["spec"]["template"]["spec"]["containers"][0]["image"]
        root = Path(__file__).resolve().parents[1]
        inventory = args.images or root / ".local" / args.profile / "images.tfvars.json"
        expected = json.loads(inventory.read_text())["local_images"]["flink-jobs"]
        if image == expected:
            return
        if not os.environ.get("STREAMING_RESTORE_PATH"):
            raise ValueError("stateful image changed: run make streaming-stop, then set STREAMING_RESTORE_PATH to its URI before dev-update")
        jobs = rest(args.profile, "/jobs/overview")["jobs"]
        if any(job["state"] not in ("FINISHED", "CANCELED", "FAILED") for job in jobs):
            raise ValueError("stop the existing Flink job with a savepoint before replacing its image")
        return
    if args.action in ("fixtures", "inspect", "load"):
        print(command(args.profile, "exec", "deployment/flink-jobs-jobmanager", "--", "python",
            "/opt/job/control.py", args.action), end="")
        return
    jobs = rest(args.profile, "/jobs/overview")["jobs"]
    active = [job for job in jobs if job["state"] == "RUNNING" and job["name"] == "payment-event-time-v1"]
    if len(active) != 1:
        raise ValueError("expected exactly one running payment event-time job")
    job = active[0]["jid"]
    endpoint = "stop" if args.action == "stop" else "savepoints"
    body = {"targetDirectory": "s3://flink-state/savepoints", "drain": False} if endpoint == "stop" else {
        "target-directory": "s3://flink-state/savepoints", "cancel-job": False,
    }
    request = rest(args.profile, f"/jobs/{job}/{endpoint}", body)
    for _ in range(120):
        result = rest(args.profile, f"/jobs/{job}/savepoints/{request['request-id']}")
        if result["status"]["id"] == "COMPLETED":
            operation = result["operation"]
            if "failure-cause" in operation:
                raise ValueError(f"savepoint failed: {operation['failure-cause']}")
            print(operation["location"])
            return
        time.sleep(2)
    raise TimeoutError("savepoint did not complete within four minutes")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        sys.exit(f"ERROR streaming operation: {error}")
