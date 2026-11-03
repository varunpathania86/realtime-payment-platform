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
    if not isinstance(data, dict) or set(data) - {"node", "clusters", "nodes", "observability", "streaming"}:
        raise ValueError("configuration accepts only node, nodes, clusters, observability and streaming")
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
    if "nodes" in data:
        nodes = data["nodes"]
        if not isinstance(nodes, dict) or set(nodes) - {"a", "b", "c"}:
            raise ValueError("nodes accepts only a, b and c budget overrides")
        result["nodes"] = {}
        for key, value in nodes.items():
            if not isinstance(value, dict):
                raise ValueError(f"nodes.{key} must be a node budget object")
            result["nodes"][key] = validate({"node": {**result["node"], **value}})["node"]
    for feature in ("observability", "streaming"):
        if feature in data:
            if not isinstance(data[feature], bool):
                raise ValueError(f"{feature} must be a boolean")
            result[feature] = data[feature]
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
