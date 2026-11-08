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

PAYMENT_JOB = "payment-event-time-v1"
LAKEHOUSE_JOB = "payment-lakehouse-v1"
INACTIVE = ("FINISHED", "CANCELED", "FAILED")
RELEASE = "stream-processor"
# ADR-027 renamed flink-jobs; still find it so its jobs are savepointed before Terraform replaces it.
LEGACY_RELEASES = ("flink-jobs",)


def command(profile, *args):
    return subprocess.run(["kubectl", "--context", profile, "--request-timeout=30s", "-n", "apps", *args],
        check=True, text=True, capture_output=True, timeout=180).stdout


def current_release(profile):
    """Return the deployed Flink release name and its JobManager Deployment, or (None, None)."""
    for name in (RELEASE, *LEGACY_RELEASES):
        deployment = command(profile, "get", "deployment", f"{name}-jobmanager", "--ignore-not-found", "-o", "json")
        if deployment.strip():
            return name, json.loads(deployment)
    return None, None


def rest(profile, path, body=None, release=RELEASE):
    program = (
        "import urllib.request; "
        f"r=urllib.request.Request('http://{release}:8081{path}', "
        f"data={None if body is None else json.dumps(body).encode()!r}, "
        "headers={'Content-Type':'application/json'}); "
        "print(urllib.request.urlopen(r,timeout=30).read().decode())"
    )
    return json.loads(command(profile, "exec", f"deployment/{release}-jobmanager", "--", "python", "-c", program))


def desired_runtime(settings):
    """Flink JobManager environment that Terraform will render from the profile settings."""
    return {
        "RISK_ENABLED": str(settings.get("risk_enabled", False)).lower(),
        "RISK_PACKAGE_KEY": settings.get("risk_package_key", ""),
        "RISK_PACKAGE_SHA256": settings.get("risk_package_sha256", ""),
        "LAKEHOUSE_ENABLED": str(settings.get("lakehouse_enabled", False)).lower(),
    }


def runtime_changed(current, settings):
    # Any of these restarts the session cluster, which discards running jobs without HA.
    return any(current.get(key, "false" if key.endswith("_ENABLED") else "") != value
        for key, value in desired_runtime(settings).items())


def restore_failure(current, settings, environ):
    """Return why a replacing update would lose Flink state, or None when restore paths are set."""
    if not environ.get("STREAMING_RESTORE_PATH"):
        return ("stateful image changed (or the lakehouse or risk configuration changed): run make streaming-stop, "
            "then set STREAMING_RESTORE_PATH to its URI before dev-update")
    if current.get("LAKEHOUSE_ENABLED") == "true" and desired_runtime(settings)["LAKEHOUSE_ENABLED"] == "true" \
            and not environ.get("LAKEHOUSE_RESTORE_PATH"):
        return "the lakehouse job was running: also set LAKEHOUSE_RESTORE_PATH from make streaming-stop output"
    return None


def savepoint(profile, job, stop, release=RELEASE):
    endpoint = "stop" if stop else "savepoints"
    body = {"targetDirectory": "s3://flink-state/savepoints", "drain": False} if stop else {
        "target-directory": "s3://flink-state/savepoints", "cancel-job": False,
    }
    request = rest(profile, f"/jobs/{job}/{endpoint}", body, release)
    for _ in range(120):
        result = rest(profile, f"/jobs/{job}/savepoints/{request['request-id']}", release=release)
        if result["status"]["id"] == "COMPLETED":
            operation = result["operation"]
            if "failure-cause" in operation:
                raise ValueError(f"savepoint failed: {operation['failure-cause']}")
            return operation["location"]
        time.sleep(2)
    raise TimeoutError("savepoint did not complete within four minutes")


def running(jobs, name):
    return [job["jid"] for job in jobs if job["state"] == "RUNNING" and job["name"] == name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fixtures", "inspect", "load", "savepoint", "stop", "guard"))
    parser.add_argument("--profile", default=os.environ.get("MINIKUBE_PROFILE") or (
        "rpp-1" if os.environ.get("MULTI_CLUSTER") == "3" else "rpp"
    ))
    parser.add_argument("--images", type=Path, help="candidate image inventory for the pre-build guard")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        raise ValueError("invalid explicit Minikube profile")
    if args.action == "guard":
        release, deployment = current_release(args.profile)
        if deployment is None:
            return
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        image = container["image"]
        root = Path(__file__).resolve().parents[1]
        inventory = args.images or root / ".local" / args.profile / "images.tfvars.json"
        expected = json.loads(inventory.read_text())["local_images"][RELEASE]
        settings_path = root / ".local" / args.profile / "events.tfvars.json"
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        current = {item["name"]: item.get("value", "") for item in container.get("env", [])}
        if release == RELEASE and image == expected and not runtime_changed(current, settings):
            return
        failure = restore_failure(current, settings, os.environ)
        if failure:
            raise ValueError(failure)
        jobs = rest(args.profile, "/jobs/overview", release=release)["jobs"]
        if any(job["state"] not in INACTIVE for job in jobs):
            raise ValueError("stop the existing Flink jobs with make streaming-stop before replacing them")
        return
    if args.action in ("fixtures", "inspect", "load"):
        print(command(args.profile, "exec", f"deployment/{RELEASE}-jobmanager", "--", "python",
            "/opt/job/control.py", args.action), end="")
        return
    release = current_release(args.profile)[0] or RELEASE
    jobs = rest(args.profile, "/jobs/overview", release=release)["jobs"]
    payment, lakehouse = running(jobs, PAYMENT_JOB), running(jobs, LAKEHOUSE_JOB)
    if len(payment) != 1 or len(lakehouse) > 1:
        raise ValueError("expected exactly one running payment event-time job and at most one lakehouse job")
    # Stop the lakehouse writer first so the payment job keeps producing until the writer has a savepoint.
    lakehouse_location = savepoint(args.profile, lakehouse[0], args.action == "stop", release) if lakehouse else None
    # The first line stays the bare payment savepoint URI for existing STREAMING_RESTORE_PATH usage.
    print(savepoint(args.profile, payment[0], args.action == "stop", release))
    if lakehouse_location:
        print(f"LAKEHOUSE_RESTORE_PATH={lakehouse_location}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        sys.exit(f"ERROR streaming operation: {error}")
