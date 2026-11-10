#!/usr/bin/env python3
"""Install pinned server-side drivers through DBX 0.6.67's offline-import API."""

import json
import hashlib
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request
import zipfile

from dbx_provision import API_CONTRACT_VERSION, DBXClient, ProvisioningError


def install(client, password, package_path):
    if not password:
        raise ProvisioningError("DBX login password is missing")
    with zipfile.ZipFile(package_path) as archive:
        registry = json.loads(archive.read("agent-registry.json"))
    expected = {key: value["version"] for key, value in registry["drivers"].items()}
    if set(expected) != {"kafka", "trino"}:
        raise ProvisioningError("DBX runtime package contains unsupported drivers")
    client.request("POST", "/auth/login", {"password": password})
    installed = client.request("GET", "/agents/installed-local")
    if ready(installed, expected):
        print("DBX pinned Kafka and Trino runtimes already installed")
        return
    with zipfile.ZipFile(package_path) as archive:
        for section in ("jres", "drivers"):
            for key, entry in registry[section].items():
                info = next(iter(entry["platforms"].values())) if section == "jres" else entry["jar"]
                filename = info["url"].rsplit("/", 1)[1]
                prefix = "jre" if section == "jres" else "drivers"
                artifact = archive.read(f"{prefix}/{filename}")
                if hashlib.sha256(artifact).hexdigest() != info["sha256"]:
                    raise ProvisioningError("DBX runtime package checksum mismatch")
                upload(client, filename, artifact)
    if not ready(client.request("GET", "/agents/installed-local"), expected):
        raise ProvisioningError("DBX runtime versions or Java runtime did not verify")
    print("DBX pinned Kafka and Trino runtimes installed and verified")


def upload(client, filename, artifact):
    boundary = "rpp-dbx-offline-runtime"
    payload = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + artifact + f"\r\n--{boundary}--\r\n".encode()
    request = Request(
        f"{client.base_url}/api/agents/import-offline",
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with client.opener.open(request, timeout=300) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        raise ProvisioningError(f"DBX runtime import returned HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ProvisioningError("DBX runtime import failed") from None
    if result.get("failures"):
        raise ProvisioningError("DBX rejected one or more pinned runtime artifacts")


def ready(installed, expected):
    if not isinstance(installed, list):
        raise ProvisioningError("DBX installed-runtime response has an unsupported shape")
    found = {item.get("db_type"): item for item in installed if isinstance(item, dict)}
    return all(
        found.get(key, {}).get("installed") is True
        and found[key].get("installed_version") == version
        and found[key].get("jre_installed") is True
        for key, version in expected.items()
    )


def verify_connections(client, password, trino_enabled):
    """Exercise server-side drivers, not just persisted profile identities."""
    client.request("POST", "/auth/login", {"password": password})
    configs = client.request("GET", "/connection/list")
    if isinstance(configs, dict):
        configs = configs.get("configs", configs.get("connections"))
    if not isinstance(configs, list):
        raise ProvisioningError("DBX connection response has an unsupported shape")
    by_id = {item.get("id"): item for item in configs if isinstance(item, dict)}
    identifiers = ["rpp-events-kafka"]
    if trino_enabled:
        identifiers.append("rpp-lakehouse-trino")
    for identifier in identifiers:
        if identifier not in by_id:
            raise ProvisioningError("DBX runtime verification profile is missing")
        client.request("POST", "/connection/connect", {"config": by_id[identifier]})
        if identifier == "rpp-events-kafka":
            result = client.request("POST", "/mq/test-connection", {"connectionId": identifier})
            if not isinstance(result, dict) or not result.get("extra", {}).get("brokers"):
                raise ProvisioningError("DBX Kafka broker metadata did not verify")
            topics = client.request("POST", "/mq/topics/list", {
                "connectionId": identifier,
                "ns": {"tenant": "", "namespace": ""},
                "opts": {},
            })
            if not isinstance(topics, list):
                raise ProvisioningError("DBX Kafka topic metadata did not verify")
        else:
            result = client.request("POST", "/query/execute", {
                "connectionId": identifier,
                "database": "lakehouse",
                "schema": "payments",
                "sql": "SELECT 1 AS dbx_runtime_check",
                "maxRows": 1,
            })
            if not isinstance(result, dict) or result.get("rows") not in ([[1]], [["1"]]):
                raise ProvisioningError("DBX Trino SELECT did not verify")
    print("DBX Kafka metadata and enabled Trino SELECT verified")


def main():
    if os.environ.get("DBX_API_CONTRACT_VERSION") != API_CONTRACT_VERSION:
        raise ProvisioningError("DBX runtime installer API contract version is unsupported")
    client = DBXClient(os.environ["DBX_API_URL"])
    if sys.argv[1:] == ["verify"]:
        verify_connections(client, os.environ.get("DBX_PASSWORD", ""), os.environ.get("TRINO_ENABLED") == "1")
    elif not sys.argv[1:]:
        install(client, os.environ.get("DBX_PASSWORD", ""), "/opt/dbx-runtime/runtime.zip")
    else:
        raise ProvisioningError("DBX runtime installer command is unsupported")


if __name__ == "__main__":
    try:
        main()
    except (ProvisioningError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        message = str(error) if isinstance(error, ProvisioningError) else "DBX runtime package is invalid"
        print(message, file=sys.stderr)
        sys.exit(1)
