#!/usr/bin/env python3
"""Fence an owned Minikube primary and promote only a verified durable standby."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from scripts.database_switchover import MEMBERS, run, save


ROOT = Path(__file__).resolve().parents[1]


def lsn(value):
    if not re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", value):
        raise ValueError(f"invalid durable WAL position: {value!r}")
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def candidate(evidence, target):
    if len(evidence) != 2 or target not in evidence:
        raise ValueError("both surviving standbys must be inspected")
    if len({(row["system"], row["timeline"]) for row in evidence.values()}) != 1:
        raise ValueError("standby system identifiers or timelines differ; refuse promotion")
    if any(row["recovery"] != "t" for row in evidence.values()):
        raise ValueError("a surviving member is already writable; refuse promotion")
    maximum = max(lsn(row["received"]) for row in evidence.values())
    if lsn(evidence[target]["received"]) != maximum:
        raise ValueError("requested target is behind another survivor; choose the standby with greatest durable WAL")
    return maximum


def standby_state(output):
    values = output.split("|")
    if len(values) != 4 or any(not value for value in values):
        raise ValueError("standby evidence is incomplete; refuse promotion")
    return dict(zip(("recovery", "system", "timeline", "received"), values))


def failover(base, target):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,29}", base) or target not in MEMBERS:
        raise ValueError("use DEV_PROFILE=<owned-base> DATABASE_TARGET=pg-a|pg-b|pg-c")
    state = ROOT / ".local" / base
    if (state / "owned").read_text().strip() != base or not (state / "full-stack").is_file():
        raise ValueError("refusing topology without matching full-stack ownership")
    if (state / "database-transition.json").exists():
        raise ValueError("a planned switchover is pending; resume it before attempting failover")
    topology_path = state / "topology.tfvars.json"
    topology = json.loads(topology_path.read_text())
    profiles = dict(zip(MEMBERS, topology["profiles"]))
    fence_path = state / "database-fence.json"
    if fence_path.exists():
        journal = json.loads(fence_path.read_text())
        if journal["target"] != target:
            if journal["phase"] != "fence" or target == journal["old"]:
                raise ValueError("a promotion is pending; resume the same target or rebuild the fenced member")
            journal["target"] = target
            save(fence_path, journal)
    else:
        old = topology.get("database_primary", "pg-a")
        if old == target:
            raise ValueError("target is already the declared primary")
        journal = {"old": old, "target": target, "phase": "fence"}
        save(fence_path, journal)
    old_profile = profiles[journal["old"]]
    network = f"{base}-net"
    metadata = json.loads(run(["docker", "network", "inspect", network], capture=True))[0]
    if metadata.get("Labels", {}).get("rpp.multi") != base:
        raise ValueError("Docker network ownership mismatch; refuse fencing")
    container = json.loads(run(["docker", "inspect", old_profile], capture=True))[0]
    if container.get("Config", {}).get("Labels", {}).get("name.minikube.sigs.k8s.io") != old_profile:
        raise ValueError("Docker container identity mismatch; refuse fencing")
    run(["docker", "update", "--restart=no", old_profile])
    if container["State"]["Running"]:
        run(["docker", "stop", "--time=30", old_profile])
    if network in container["NetworkSettings"]["Networks"]:
        run(["docker", "network", "disconnect", network, old_profile])
    fenced = json.loads(run(["docker", "inspect", old_profile], capture=True))[0]
    if fenced["State"]["Running"] or network in fenced["NetworkSettings"]["Networks"]:
        raise ValueError("old primary is not stopped AND disconnected; refuse promotion")
    survivors = [member for member in MEMBERS if member != journal["old"]]

    def sql(member, query, pod=None):
        if pod is None:
            cluster = json.loads(run([
                "kubectl", "--context", profiles[member], "--request-timeout=15s",
                "-n", "postgres", "get", "cluster", member, "-o", "json",
            ], capture=True))
            pod = cluster.get("status", {}).get("currentPrimary", "")
            if not re.fullmatch(re.escape(member) + r"-[1-9][0-9]*", pod):
                raise ValueError("CNPG has no valid designated primary; refuse promotion")
        return run([
            "kubectl", "--context", profiles[member], "--request-timeout=15s", "-n", "postgres",
            "exec", pod, "-c", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1",
            "-U", "postgres", "-d", "postgres", "-Atqc", query,
        ], capture=True).strip()

    if journal["phase"] == "complete" and sql(target, "SELECT pg_is_in_recovery()") != "f":
        raise ValueError("declared failover target is not writable; inspect CNPG before changing roles")

    if journal["phase"] == "fence":
        query = """SELECT pg_is_in_recovery(), system_identifier,
            timeline_id, pg_last_wal_receive_lsn() FROM pg_control_system(), pg_control_checkpoint()"""
        evidence = {}
        for member in survivors:
            evidence[member] = standby_state(sql(member, query))
        maximum = candidate(evidence, target)
        for member in survivors:
            pods = json.loads(run([
                "kubectl", "--context", profiles[member], "--request-timeout=15s", "-n", "postgres",
                "get", "pods", "-l", f"cnpg.io/cluster={member}", "-o", "json",
            ], capture=True))
            expected = topology.get("cluster_sizes", {}).get(member[-1], {}).get("postgres_instances", 1)
            if len(pods["items"]) != expected:
                raise ValueError("not all surviving CNPG instances can be inspected; refuse promotion")
            for pod in pods["items"]:
                name = pod["metadata"]["name"]
                if not re.fullmatch(re.escape(member) + r"-[1-9][0-9]*", name):
                    raise ValueError("unexpected CNPG pod identity; refuse promotion")
                local = standby_state(sql(member, query, pod=name))
                if local["recovery"] != "t" or any(
                    local[key] != evidence[member][key] for key in ("system", "timeline")
                ):
                    raise ValueError("a local CNPG instance has conflicting recovery identity; refuse promotion")
                if lsn(local["received"]) > maximum:
                    raise ValueError("a local CNPG replica is ahead of the designated promotion candidate; reconcile it before global promotion")
        deadline = time.monotonic() + 120
        while lsn(sql(target, "SELECT pg_last_wal_replay_lsn()")) < maximum:
            if time.monotonic() >= deadline:
                raise TimeoutError("target has not replayed the greatest durable WAL; refuse promotion")
            time.sleep(2)
        journal.update(phase="promote", evidence=evidence)
        save(fence_path, journal)
    topology["database_primary"] = target
    topology.setdefault("promotion_tokens", {}).pop(target, None)
    environment = {**os.environ, "TF_DATA_DIR": str(state / "terraform")}
    terraform = ["terraform", f"-chdir={ROOT / 'platform/terraform/environments/kafka-multicluster'}"]

    def apply_member(member):
        save(topology_path, topology)
        plan = state / "database-failover.tfplan"
        run([*terraform, "plan", "-input=false", f"-var-file={topology_path}",
            f"-target=module.database_{member[-1]}[0].helm_release.database", f"-out={plan}"], env=environment)
        run([*terraform, "apply", "-input=false", str(plan)], env=environment)

    if journal["phase"] == "promote":
        apply_member(target)
        deadline = time.monotonic() + 300
        while sql(target, "SELECT pg_is_in_recovery()") != "f":
            if time.monotonic() >= deadline:
                raise TimeoutError("target remains in recovery; preserve the fence and retry")
            time.sleep(2)
        journal["phase"] = "reconcile"
        save(fence_path, journal)
    for member in survivors:
        if member != target:
            apply_member(member)
    role = {key: topology[key] for key in ("database_primary", "promotion_tokens", "bootstrap_sources") if key in topology}
    save(state / "database-role.json", role)
    save(topology_path, topology)
    journal["phase"] = "complete"
    save(fence_path, journal)
    print(f"Verified promotion to {target}; {old_profile} remains stopped and network-fenced. Reconcile surviving applications next.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        failover(args.base, args.target)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit(f"ERROR database failover: {error}")


if __name__ == "__main__":
    main()
