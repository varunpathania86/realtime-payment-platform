#!/usr/bin/env python3
"""Validate the rendered local deployment contract without a cluster."""

import sys

import yaml


def validate(documents):
    deployments = [item for item in documents if item and item.get("kind") == "Deployment"]
    if not deployments:
        raise ValueError("chart has no Deployment")
    for item in deployments:
        pod = item["spec"]["template"]["spec"]
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
            if container in pod["containers"]:
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
