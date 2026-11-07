#!/usr/bin/env python3
"""Bundle checksum-pinned official artifacts for separate DBX offline imports."""

import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen
import zipfile


def tool_versions(root):
    return dict(
        line.split()
        for line in (root / ".tool-versions").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )


def artifact(cache, url, expected):
    target = cache / url.rsplit("/", 1)[1]
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return target
    target.unlink(missing_ok=True)
    try:
        with urlopen(url, timeout=120) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("DBX runtime artifact checksum mismatch")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def package(root, arch):
    if arch not in {"amd64", "arm64"}:
        raise ValueError("DBX runtime supports only amd64 and arm64")
    pins = tool_versions(root)
    cache = root / ".cache" / "dbx-runtime" / arch
    cache.mkdir(parents=True, exist_ok=True)
    base = (
        "https://github.com/t8y2/dbx/releases/download/"
        f"agents-v{pins['dbx-agents-release']}"
    )
    platform = {"amd64": "linux-x64", "arm64": "linux-aarch64"}[arch]
    registry = {"drivers": {}, "jres": {}}
    entries = []
    for key in ("kafka", "trino", "jre"):
        if key == "jre":
            name = f"dbx-jre-21-{platform}.tar.zst"
            checksum = pins[f"dbx-jre-sha256-{arch}"]
        else:
            name = f"dbx-agent-{key}-{pins[f'dbx-agent-{key}']}.tar.zst"
            checksum = pins[f"dbx-agent-{key}-sha256"]
        url = f"{base}/{name}"
        source = artifact(cache, url, checksum)
        info = {
            "url": url,
            "sha256": checksum,
            "size": source.stat().st_size,
            "format": "tar_zstd",
        }
        if key == "jre":
            registry["jres"]["21"] = {
                "version": pins["dbx-jre"],
                "platforms": {platform: info},
            }
            entries.append((source, f"jre/{name}"))
        else:
            registry["drivers"][key] = {
                "version": pins[f"dbx-agent-{key}"],
                "label": key,
                "jre": "21",
                "external_driver_required": False,
                "jar": info,
            }
            entries.append((source, f"drivers/{name}"))
    destination = cache / "runtime.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("agent-registry.json", json.dumps(registry))
        for source, name in entries:
            archive.write(source, name)
    return destination


if __name__ == "__main__":
    print(package(Path(__file__).resolve().parent.parent, sys.argv[1]))
