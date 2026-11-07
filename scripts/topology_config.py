#!/usr/bin/env python3
"""Validate and persist local node budgets and distribution-neutral workload counts."""

import argparse
import json
from pathlib import Path
import re
import sys

from scripts.database_switchover import save


COUNTS = {"kafka_brokers", "postgres_instances", "relay", "consumer", "registry", "ingress"}


def positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def validate(data):
    if not isinstance(data, dict) or set(data) - {
        "node", "clusters", "nodes", "observability", "streaming", "analytics", "lakehouse", "bi", "ai",
        "runtime_profile", "risk", "identity", "dbx",
    }:
        raise ValueError(
            "configuration accepts only node, nodes, clusters, observability, streaming, analytics, lakehouse, "
            "bi, ai, identity, dbx, runtime_profile and risk"
        )
    node = data.get("node", {})
    if not isinstance(node, dict) or set(node) - {"cpus", "memory_mib"}:
        raise ValueError("node accepts only cpus and memory_mib")
    cpus = node.get("cpus", 2)
    if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or not 1 <= cpus <= 256:
        raise ValueError("node.cpus must be a number from 1 to 256")
    memory = positive_integer(node.get("memory_mib", 3072), "node.memory_mib")
    if memory < 3072:
        raise ValueError("node.memory_mib must be at least 3072 for the full-stack lab")
    clusters = data.get("clusters", {})
    if not isinstance(clusters, dict) or set(clusters) - {"a", "b", "c"}:
        raise ValueError("clusters accepts only a, b and c")
    for cluster, size in clusters.items():
        if not isinstance(size, dict) or set(size) - (COUNTS | {"applications"}):
            raise ValueError(f"unknown cluster sizing field in {cluster}")
        for key in COUNTS & size.keys():
            positive_integer(size[key], f"{cluster}.{key}")
        if size.get("kafka_brokers", 1) > 32:
            raise ValueError("at most 32 Kafka brokers per cluster are supported")
        applications = size.get("applications", {})
        if not isinstance(applications, dict):
            raise ValueError("applications must map implemented module names to replica counts")
        implemented = []
        if applications:
            from scripts.module_catalog import local_modules

            implemented = local_modules()
        for module, count in applications.items():
            if not re.fullmatch(r"[a-z][a-z0-9-]*", module):
                raise ValueError("invalid application module name")
            if module not in implemented:
                raise ValueError(f"application {module} is not a runnable catalog module")
            positive_integer(count, f"{cluster}.applications.{module}")
    result = {"node": {"cpus": cpus, "memory_mib": memory}, "clusters": clusters}
    required_memory = 3072
    if "runtime_profile" in data:
        if data["runtime_profile"] not in ("standard", "low-memory"):
            raise ValueError("runtime_profile must be standard or low-memory")
        result["runtime_profile"] = data["runtime_profile"]
    if "nodes" in data:
        nodes = data["nodes"]
        if not isinstance(nodes, dict) or set(nodes) - {"a", "b", "c"}:
            raise ValueError("nodes accepts only a, b and c budget overrides")
        result["nodes"] = {}
        for key, value in nodes.items():
            if not isinstance(value, dict):
                raise ValueError(f"nodes.{key} must be a node budget object")
            result["nodes"][key] = validate({"node": {**result["node"], **value}})["node"]
    for feature in ("observability", "streaming", "analytics", "lakehouse", "bi", "ai", "identity", "dbx"):
        if feature in data:
            if not isinstance(data[feature], bool):
                raise ValueError(f"{feature} must be a boolean")
            result[feature] = data[feature]
    if result.get("analytics", False) and not (result.get("streaming", False) or result.get("ai", False)):
        raise ValueError("analytics requires streaming, or ai for the lean AI profile")
    if result.get("analytics", False):
        required_memory = 6144
        cluster_a = result.get("nodes", {}).get("a", result["node"])
        if cluster_a["cpus"] < 4 or cluster_a["memory_mib"] < 6144:
            raise ValueError("analytics requires cluster A to have at least 4 CPUs and 6144 MiB")
    if result.get("lakehouse", False):
        if not result.get("streaming", False):
            raise ValueError("lakehouse requires streaming to be enabled")
        cluster_a = result.get("nodes", {}).get("a", result["node"])
        # Polaris, Trino and the second Flink slot add about 2.5 GiB of limits on cluster A.
        minimum = 10240 if result.get("analytics", False) else 8192
        required_memory = max(required_memory, minimum)
        if cluster_a["cpus"] < 4 or cluster_a["memory_mib"] < minimum:
            raise ValueError(f"lakehouse requires cluster A to have at least 4 CPUs and {minimum} MiB")
    if result.get("bi", False):
        if not result.get("analytics", False):
            raise ValueError("bi requires analytics to be enabled")
        cluster_a = result.get("nodes", {}).get("a", result["node"])
        # Superset adds about 1 GiB of limits on cluster A on top of analytics (and the lakehouse).
        minimum = 11264 if result.get("lakehouse", False) else 7168
        required_memory = max(required_memory, minimum)
        if cluster_a["memory_mib"] < minimum:
            raise ValueError(f"bi requires cluster A to have at least {minimum} MiB")
    if result.get("ai", False):
        if not result.get("analytics", False):
            raise ValueError("ai requires analytics to be enabled")
        cluster_a = result.get("nodes", {}).get("a", result["node"])
        # llama.cpp chat and embeddings, knowledge PostgreSQL, MinIO and three Python services add
        # about 4 GiB of limits on cluster A; Flink, the lakehouse and Superset add their own.
        minimum = 8192
        if result.get("streaming", False):
            minimum += 4096
        if result.get("lakehouse", False):
            minimum += 2048
        if result.get("bi", False):
            minimum += 1024
        required_memory = max(required_memory, minimum)
        if cluster_a["cpus"] < 4 or cluster_a["memory_mib"] < minimum:
            raise ValueError(f"ai requires cluster A to have at least 4 CPUs and {minimum} MiB")
    if result.get("identity", False):
        cluster_a = result.get("nodes", {}).get("a", result["node"])
        minimum = required_memory + 2048
        required_memory = max(required_memory, minimum)
        if cluster_a["memory_mib"] < minimum:
            raise ValueError(f"identity requires cluster A to have at least {minimum} MiB")
    if result.get("dbx", False):
        cluster_a = result.get("nodes", {}).get("a", result["node"])
        minimum = required_memory + 512
        if cluster_a["memory_mib"] < minimum:
            raise ValueError(f"dbx requires cluster A to have at least {minimum} MiB")
    if "risk" in data:
        risk = data["risk"]
        if not isinstance(risk, dict) or set(risk) != {"package_key", "package_sha256"}:
            raise ValueError("risk requires only package_key and package_sha256")
        if not result.get("streaming"):
            raise ValueError("risk requires streaming")
        if not isinstance(risk["package_key"], str) or not re.fullmatch(
            r"payment-risk/[0-9]+\.[0-9]+\.[0-9]+/payment_risk\.zip", risk["package_key"]
        ):
            raise ValueError("risk requires a versioned package_key")
        if not isinstance(risk["package_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", risk["package_sha256"]):
            raise ValueError("risk requires an explicit package_sha256")
        result["risk"] = risk
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        destination = args.state / "configuration.json"
        source = args.config or (destination if destination.exists() else None)
        settings = validate(json.loads(source.read_text()) if source else {})
        previous = json.loads(destination.read_text()) if destination.exists() else {"clusters": {}}
        for cluster in ("a", "b", "c"):
            old_count = previous.get("clusters", {}).get(cluster, {}).get("kafka_brokers", 1)
            new_count = settings["clusters"].get(cluster, {}).get("kafka_brokers", 1)
            if new_count < old_count and (args.state / "terraform.tfstate").exists():
                state = json.loads((args.state / "terraform.tfstate").read_text())
                if any(resource.get("mode") == "managed" for resource in state.get("resources", [])):
                    raise ValueError("refusing Kafka broker removal: reassign partitions and use a reviewed Terraform plan; adding brokers does not rebalance existing partitions")
        args.state.mkdir(parents=True, exist_ok=True)
        save(destination, settings)
        print(f'{settings["node"]["cpus"]} {settings["node"]["memory_mib"]}')
    except (OSError, ValueError, TypeError) as error:
        sys.exit(f"ERROR topology configuration: {error}")


if __name__ == "__main__":
    main()
