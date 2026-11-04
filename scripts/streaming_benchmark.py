"""Safety preflight and explicit-context launcher for the bounded pipeline test."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from scripts.topology_config import validate


def memory_available_mib():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise ValueError("cannot determine available host memory")


def preflight(settings, available):
    settings = validate(settings)
    if not settings.get("analytics"):
        raise ValueError("pipeline benchmark requires analytics and streaming")
    budgets = {
        key: settings.get("nodes", {}).get(key, settings["node"])
        for key in ("a", "b", "c")
    }
    required = sum(node["memory_mib"] for node in budgets.values()) + 2048
    if available < required:
        raise ValueError(
            f"unsafe three-cluster start: {available} MiB available; "
            f"{required} MiB required (node caps plus 2048 MiB host/build reserve). "
            "Increase host memory or use separate servers; no clusters were started."
        )
    return {"nodes": budgets, "available_mib": available, "required_mib": required}


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("templates/analytics-benchmark-topology.json"))
    parser.add_argument("--preflight", action="store_true", help="check memory before starting three clusters")
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,29}", args.base):
        raise ValueError("invalid explicit three-cluster base")
    if args.preflight:
        print(json.dumps(preflight(json.loads(args.config.read_text()), memory_available_mib()), indent=2))
        return
    if memory_available_mib() < 2048:
        raise ValueError("less than 2048 MiB host memory available; refusing benchmark")
    state = Path(".local") / args.base
    if (state / "owned").read_text().strip() != args.base:
        raise ValueError("benchmark requires an explicitly owned three-cluster base")
    topology = validate(json.loads((state / "configuration.json").read_text()))
    if not topology.get("analytics"):
        raise ValueError("owned topology does not enable analytics")
    workloads = {}
    node_limits = {}
    for member in ("a", "b", "c"):
        profile = args.base + "-" + member
        limits = subprocess.run(
            [
                "docker", "inspect", "--format",
                '{"memory_bytes":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}}}', profile,
            ],
            check=True, capture_output=True, text=True, timeout=10,
        )
        node_limits[profile] = json.loads(limits.stdout)
        result = subprocess.run(
            ["kubectl", "--context", profile, "--request-timeout=15s", "get", "nodes", "-o", "json"],
            check=True, capture_output=True, text=True, timeout=20,
        )
        nodes = json.loads(result.stdout)["items"]
        if not nodes or not all(
            any(condition["type"] == "Ready" and condition["status"] == "True"
                for condition in node["status"]["conditions"]) for node in nodes
        ):
            raise ValueError(f"{profile} has no nodes or has an unready node")
        result = subprocess.run(
            [
                "kubectl", "--context", profile, "--request-timeout=15s", "-n", "apps",
                "get", "deployments,statefulsets", "-o", "json",
            ],
            check=True, capture_output=True, text=True, timeout=20,
        )
        workloads[profile] = [
            {
                "name": item["metadata"]["name"],
                "replicas": item["spec"].get("replicas", 1),
                "containers": [
                    {"name": container["name"], "image": container["image"],
                        "resources": container.get("resources", {})}
                    for container in item["spec"]["template"]["spec"]["containers"]
                ],
            }
            for item in json.loads(result.stdout)["items"]
        ]
    source = Path("modules/stream-processor/benchmark.py").read_text()
    result = subprocess.run(
        [
            "kubectl", "--context", args.base + "-a", "--request-timeout=15s", "-n", "apps",
            "exec", "-i", "deployment/stream-processor-jobmanager", "--",
            "env", "PYTHONPATH=/opt/job", "python", "-",
        ],
        input=source, text=True, capture_output=True, timeout=max(1, 380 - (time.monotonic() - started)),
    )
    if result.stdout.strip():
        report = json.loads(result.stdout)
        report["deployment"] = {
            "base": args.base, "workloads": workloads, "actual_docker_node_limits": node_limits,
            "configured_topology": topology,
            "source_sha": subprocess.run(
                ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
            ).stdout.strip(),
            "source_dirty": bool(subprocess.run(
                ["git", "status", "--porcelain"], check=True, capture_output=True, text=True,
            ).stdout.strip()),
            "tool_versions": Path(".tool-versions").read_text(),
        }
        print(json.dumps(report, indent=2))
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args, stderr=result.stderr)
    if not result.stdout.strip():
        raise ValueError("benchmark returned no report")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        sys.exit(f"ERROR pipeline benchmark: {error}")
