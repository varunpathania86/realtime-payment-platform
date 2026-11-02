#!/usr/bin/env python3
"""Coordinate a resumable CNPG planned switchover through Terraform-owned resources."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
MEMBERS = ("pg-a", "pg-b", "pg-c")


def save(path, data):
    temporary = path.with_suffix(".pending")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream)
        stream.write("\n")
    temporary.replace(path)


def run(args, *, capture=False, env=None, timeout=360):
    result = subprocess.run(args, capture_output=capture, text=True, env=env, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}; preserve the recovery journal and retry the same target")
    return result.stdout if capture else None


def primary_pod(profile, member):
    cluster = json.loads(run([
        "kubectl", "--context", profile, "--request-timeout=15s", "-n", "postgres",
        "get", "cluster", member, "-o", "json",
    ], capture=True))
    pod = cluster.get("status", {}).get("currentPrimary", "")
    if not re.fullmatch(re.escape(member) + r"-[1-9][0-9]*", pod):
        raise ValueError(f"{member} has no valid designated primary; wait for CNPG reconciliation")
    return pod


def recovery_state(profile, member):
    return run([
        "kubectl", "--context", profile, "--request-timeout=15s", "-n", "postgres",
        "exec", primary_pod(profile, member), "-c", "postgres", "--", "psql", "-U", "postgres", "-d", "postgres",
        "-Atqc", "SELECT pg_is_in_recovery()",
    ], capture=True).strip()


def transition(base, target):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,29}", base) or target not in MEMBERS:
        raise ValueError("use DEV_PROFILE=<owned-base> DATABASE_TARGET=pg-a|pg-b|pg-c")
    state = ROOT / ".local" / base
    if (state / "owned").read_text().strip() != base or not (state / "full-stack").is_file():
        raise ValueError("refusing topology without matching full-stack ownership")
    if (state / "database-fence.json").exists():
        raise ValueError("rebuild the fenced member before a planned switchover")
    journal_path = state / "database-transition.json"
    topology_path = state / "topology.tfvars.json"
    topology = json.loads(topology_path.read_text())
    profiles = dict(zip(MEMBERS, topology["profiles"]))
    if journal_path.exists():
        journal = json.loads(journal_path.read_text())
        if journal["target"] != target:
            raise ValueError("a different database transition is pending; resume its original target first")
    else:
        old = topology.get("database_primary", "pg-a")
        if old == target:
            if recovery_state(profiles[target], target) != "f":
                raise RuntimeError("declared primary is not writable; inspect CNPG before changing roles")
            print(f"{target} is already the verified writer. Reconciling application connections.")
            return
        for member in MEMBERS:
            run([
                "kubectl", "--context", profiles[member], "--request-timeout=15s", "-n", "postgres",
                "wait", "--for=condition=Ready", f"cluster.postgresql.cnpg.io/{member}", "--timeout=60s",
            ])
        journal = {"old": old, "target": target, "phase": "demote"}
        save(journal_path, journal)
    topology["database_primary"] = target
    tokens = topology.setdefault("promotion_tokens", {})
    environment = {**os.environ, "TF_DATA_DIR": str(state / "terraform")}
    terraform = ["terraform", f"-chdir={ROOT / 'platform/terraform/environments/kafka-multicluster'}"]

    def apply_member(member):
        save(topology_path, topology)
        module = f"module.database_{member[-1]}[0].helm_release.database"
        plan = state / "database-transition.tfplan"
        run([*terraform, "plan", "-input=false", f"-var-file={topology_path}", f"-target={module}", f"-out={plan}"], env=environment)
        run([*terraform, "apply", "-input=false", str(plan)], env=environment)

    if journal["phase"] == "demote":
        apply_member(journal["old"])
        deadline = time.monotonic() + 300
        while True:
            cluster = json.loads(run([
                "kubectl", "--context", profiles[journal["old"]], "--request-timeout=15s",
                "-n", "postgres", "get", "cluster", journal["old"], "-o", "json",
            ], capture=True))
            token = cluster.get("status", {}).get("demotionToken")
            if token:
                journal.update(phase="promote", token=token)
                save(journal_path, journal)
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("CNPG did not produce a demotion token; retry the same target, never force promotion")
            time.sleep(2)
    if journal["phase"] == "promote":
        tokens[target] = journal["token"]
        apply_member(target)
        run([
            "kubectl", "--context", profiles[target], "--request-timeout=15s", "-n", "postgres",
            "wait", "--for=condition=Ready", f"cluster.postgresql.cnpg.io/{target}", "--timeout=300s",
        ])
        deadline = time.monotonic() + 300
        while True:
            if recovery_state(profiles[target], target) == "f":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("target is still in recovery; preserve journal and retry")
            time.sleep(2)
        journal["phase"] = "reconcile"
        save(journal_path, journal)
    for member in MEMBERS:
        if member not in (journal["old"], target):
            apply_member(member)
    role = {key: topology[key] for key in ("database_primary", "promotion_tokens", "bootstrap_sources") if key in topology}
    save(state / "database-role.json", role)
    save(topology_path, topology)
    journal_path.unlink()
    print(f"CNPG planned switchover declared {target} as writer. Reconciling all application connections next.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        transition(args.base, args.target)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit(f"ERROR database switchover: {error}")


if __name__ == "__main__":
    main()
