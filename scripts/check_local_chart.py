#!/usr/bin/env python3
"""Validate the rendered local deployment contract without a cluster."""

import sys

import yaml

# Run-to-completion workloads need no HTTP probes; everything else applies to every pod.
POD_PATHS = {
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}


def validate(documents):
    workloads = [item for item in documents if item and item.get("kind") in POD_PATHS]
    if not workloads:
        raise ValueError("chart has no Deployment, StatefulSet, Job or CronJob")
    for item in workloads:
        pod = item
        for key in POD_PATHS[item["kind"]]:
            pod = pod[key]
        serving = item["kind"] in ("Deployment", "StatefulSet")
        if pod.get("automountServiceAccountToken") is not False:
            raise ValueError("local application must not mount service-account tokens")
        if pod["securityContext"].get("runAsNonRoot") is not True:
            raise ValueError("local application must run as non-root")
        for container in pod["containers"] + pod.get("initContainers", []):
            security = container["securityContext"]
            if security.get("readOnlyRootFilesystem") is not True:
                raise ValueError("application filesystem must be read-only")
            if security.get("allowPrivilegeEscalation") is not False:
                raise ValueError("privilege escalation must be disabled")
            if serving and container in pod["containers"]:
                for probe in ("readinessProbe", "livenessProbe"):
                    if not container.get(probe):
                        raise ValueError(f"missing {probe}")
            for bound in ("requests", "limits"):
                if not {"cpu", "memory"} <= container["resources"][bound].keys():
                    raise ValueError(f"missing resource {bound}")
            if container["imagePullPolicy"] != "Never":
                raise ValueError("local application must use loaded images without registry pulls")


if __name__ == "__main__":
    try:
        validate(list(yaml.safe_load_all(sys.stdin)))
    except (ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"ERROR invalid local chart: {error}", file=sys.stderr)
        sys.exit(1)
