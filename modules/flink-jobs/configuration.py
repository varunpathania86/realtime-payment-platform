"""Overlay dotted deployment settings without discarding Flink runtime defaults."""

from copy import deepcopy


def overlay(base, settings):
    result = deepcopy(base)
    for key, value in settings.items():
        parts = key.split(".")
        node = result
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            if not isinstance(node[part], dict):
                raise ValueError(f"configuration path conflicts with a scalar: {key}")
            node = node[part]
        node[parts[-1]] = value
    result["env"]["java"]["opts"]["all"] += " -Duser.home=/tmp"
    return result
