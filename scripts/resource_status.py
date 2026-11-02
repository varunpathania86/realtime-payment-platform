#!/usr/bin/env python3
"""Read-only resource snapshots for explicitly selected local Docker/Kubernetes nodes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import re
import subprocess
import sys


NAMESPACES = {"apps", "data", "kafka", "platform", "postgres", "cnpg-system", "telemetry"}
SCALES = {
    "": Decimal(1), "n": Decimal("1e-9"), "u": Decimal("1e-6"),
    "m": Decimal("1e-3"), "k": Decimal(1000), "K": Decimal(1000),
    **{suffix: Decimal(1000) ** power for power, suffix in enumerate(("M", "G", "T", "P", "E"), 2)},
    **{suffix: Decimal(1024) ** power for power, suffix in enumerate(("Ki", "Mi", "Gi", "Ti", "Pi", "Ei"), 1)},
}


def quantity(value):
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)", str(value))
    if not match or match[2] not in SCALES:
        raise ValueError(f"invalid Kubernetes resource quantity: {value!r}")
    return Decimal(match[1]) * SCALES[match[2]]


def resource(value, kind):
    if value is None:
        return "-"
    scale = Decimal(1000) if kind == "cpu" else Decimal(1) / (1024 * 1024)
    return f"{quantity(value) * scale:.1f}"


def command_json(args):
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=20)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{' '.join(args)}: {error.stderr.strip() or f'exit {error.returncode}'}") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"{' '.join(args)}: {error}") from error
    return json.loads(result.stdout)


def workload_rows(profile, pods, metrics):
    usage = {
        (item["metadata"]["namespace"], item["metadata"]["name"], container["name"]): container["usage"]
        for item in metrics.get("items", [])
        for container in item["containers"]
    }
    rows = []
    missing = []
    for pod in sorted(pods["items"], key=lambda item: (item["metadata"]["namespace"], item["metadata"]["name"])):
        namespace, name = pod["metadata"]["namespace"], pod["metadata"]["name"]
        if namespace not in NAMESPACES:
            continue
        statuses = {item["name"]: item for item in pod.get("status", {}).get("containerStatuses", [])}
        for container in pod["spec"]["containers"]:
            status = statuses.get(container["name"], {})
            state = status.get("state", {})
            phase = pod.get("status", {}).get("phase", "Unknown")
            for key in ("waiting", "terminated"):
                if key in state:
                    phase = state[key].get("reason", key)
            sample = usage.get((namespace, name, container["name"]))
            if sample is None and "running" in state:
                missing.append(f"{namespace}/{name}/{container['name']}")
            resources = container.get("resources", {})
            requests, limits = resources.get("requests", {}), resources.get("limits", {})
            rows.append([
                profile, namespace, name, container["name"], phase,
                "yes" if status.get("ready") else "no", str(status.get("restartCount", 0)),
                resource(sample["cpu"], "cpu") if sample else "N/A",
                resource(sample["memory"], "memory") if sample else "N/A",
                f"{resource(requests.get('cpu'), 'cpu')}/{resource(limits.get('cpu'), 'cpu')}",
                f"{resource(requests.get('memory'), 'memory')}/{resource(limits.get('memory'), 'memory')}",
            ])
    return rows, missing


def snapshot(profile):
    errors = []
    try:
        node = command_json(["docker", "inspect", profile])[0]
        config, state = node["HostConfig"], node["State"]
        quota = config["NanoCpus"] / 1e9
        cpu_limit = f"{quota:g}" if quota else "unlimited"
        memory_limit = f"{config['Memory'] / (1024 * 1024):.0f}" if config["Memory"] else "unlimited"
        row = [profile, state["Status"], "N/A", cpu_limit, "N/A", memory_limit, "N/A"]
        if not state["Running"]:
            return row, [], [f"{profile}: node is {state['Status']}; Kubernetes and usage metrics unavailable"]
        stats = command_json(["docker", "stats", "--no-stream", "--format", "{{json .}}", profile])
        row[2], row[4], row[6] = stats["CPUPerc"], stats["MemUsage"].split(" / ")[0], stats["MemPerc"]
    except (RuntimeError, ValueError, KeyError) as error:
        return [profile, "ERROR", "N/A", "N/A", "N/A", "N/A", "N/A"], [], [str(error)]
    kubectl = ["kubectl", "--context", profile, "--request-timeout=10s"]
    try:
        pods = command_json([*kubectl, "get", "pods", "-A", "-o", "json"])
    except (RuntimeError, ValueError, KeyError) as error:
        return row, [], [str(error)]
    try:
        metrics = command_json([*kubectl, "get", "--raw", "/apis/metrics.k8s.io/v1beta1/pods"])
    except (RuntimeError, ValueError) as error:
        metrics = {}
        errors.append(f"{profile}: metrics unavailable: {error}. Run make dev-metrics and allow samples to warm up.")
    try:
        rows, missing = workload_rows(profile, pods, metrics)
        if missing:
            errors.append(f"{profile}: metrics not yet available for {', '.join(missing)}; retry after a sampling interval.")
        return row, rows, errors
    except (ValueError, KeyError) as error:
        return row, [], [f"{profile}: invalid pod/metrics response: {error}"]


def table(headers, rows):
    widths = [max(len(str(row[index])) for row in [headers, *rows]) for index in range(len(headers))]
    for row in [headers, *rows]:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="+")
    args = parser.parse_args(argv)
    if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", profile) for profile in args.profiles):
        parser.error("invalid Minikube profile")
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(snapshot, args.profiles))
    print("CLUSTER NODES (Docker CPU: 100% = one core; RAM usage excludes cache)")
    table(["CLUSTER", "STATE", "CPU %", "CPU LIMIT", "RAM USED", "RAM LIMIT MiB", "RAM %"], [item[0] for item in results])
    print("\nWORKLOAD CONTAINERS (CPU in millicores; RAM in MiB; REQ/LIM = requests/limits)")
    table(
        ["CLUSTER", "NAMESPACE", "POD", "CONTAINER", "STATE", "READY", "RESTARTS", "CPU USED", "RAM USED", "CPU REQ/LIM", "RAM REQ/LIM"],
        [row for _, rows, _ in results for row in rows],
    )
    print("\nSnapshot only; '-' = unset, N/A = unavailable. Init containers and kube-system pods are excluded.")
    errors = [error for _, _, errors in results for error in errors]
    for error in errors:
        print(f"WARNING: {error}", file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
