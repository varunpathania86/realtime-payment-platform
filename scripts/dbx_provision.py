#!/usr/bin/env python3
"""Provision DBX connections through the pinned Web API contract."""

import http.cookiejar
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    Request,
    build_opener,
)


API_CONTRACT_VERSION = "0.6.67"
API_BASE_PATH = "/api"
KAFKA_CA_PATH = "/etc/dbx/kafka/ca.pem"
EXPECTED_HOST = "dbx-workbench.apps.svc.cluster.local"


class ProvisioningError(Exception):
    """A safe-to-display provisioning failure without request details."""


def build_connections(environ):
    """Build deterministic DBX wire-format configs from injected Secret values."""
    database_url = environ.get("PAYMENT_DATABASE_URL", "")
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("payment database URL is invalid")
    if parsed.username is None or parsed.password is None or not parsed.path.strip("/"):
        raise ValueError("payment database URL is incomplete")

    pg_ssl = parsed.query.lower().find("sslmode=verify-full") >= 0
    connections = [
        {
            "id": "rpp-payments-postgres",
            "name": "RPP Payments PostgreSQL",
            "db_type": "postgres",
            "driver_profile": "postgres",
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "username": unquote(parsed.username),
            "password": unquote(parsed.password),
            "database": unquote(parsed.path.lstrip("/")),
            "ssl": pg_ssl,
            "ca_cert_path": "/etc/dbx/postgres/ca.crt" if pg_ssl else "",
            "read_only": True,
            "save_password": True,
        }
    ]

    for identifier, name, username, database, host, password_key in (
        ("superset", "Superset metadata", "superset", "superset", parsed.hostname, "BI_DATABASE_PASSWORD"),
        ("polaris", "Polaris metadata", "polaris", "polaris", parsed.hostname, "POLARIS_DATABASE_PASSWORD"),
        ("knowledge", "Knowledge (reader)", "rpp_knowledge_reader", "ai_knowledge",
            "knowledge-postgres.data.svc.cluster.local", "KNOWLEDGE_READER_PASSWORD"),
    ):
        credential = environ.get(password_key, "")
        if credential:
            connections.append({
                "id": f"rpp-{identifier}-postgres",
                "name": f"RPP {name} PostgreSQL",
                "db_type": "postgres",
                "driver_profile": "postgres",
                "host": host,
                "port": (parsed.port or 5432) if identifier != "knowledge" else 5432,
                "username": username,
                "password": credential,
                "database": database,
                "ssl": pg_ssl if identifier != "knowledge" else False,
                "ca_cert_path": "/etc/dbx/postgres/ca.crt" if pg_ssl and identifier != "knowledge" else "",
                "read_only": True,
                "save_password": True,
            })

    analytics_password = environ.get("ANALYTICS_READER_PASSWORD") or environ.get("ANALYTICS_PASSWORD", "")
    if environ.get("ANALYTICS_READER_PASSWORD") and not environ.get("ANALYTICS_READER_USERNAME"):
        raise ValueError("analytics reader username is missing")
    if analytics_password:
        connections.append(
            {
                "id": "rpp-analytics-clickhouse",
                "name": "RPP Analytics ClickHouse",
                "db_type": "clickhouse",
                "driver_profile": "clickhouse",
                "host": "realtime-analytics.apps.svc.cluster.local",
                "port": 8123,
                "username": (
                    environ.get("ANALYTICS_READER_USERNAME", "")
                    if environ.get("ANALYTICS_READER_PASSWORD") else "rpp_analytics"
                ),
                "password": analytics_password,
                "database": "analytics",
                "read_only": True,
                "save_password": True,
            }
        )

    if environ.get("TRINO_ENABLED") == "1":
        connections.append(
            {
                "id": "rpp-lakehouse-trino",
                "name": "RPP Lakehouse Trino",
                "db_type": "trino",
                "driver_profile": "trino",
                "host": "lakehouse-query.apps.svc.cluster.local",
                "port": 8080,
                "username": "developer",
                "password": "",
                "database": "lakehouse",
                "default_schema": "payments",
                "read_only": True,
                "save_password": True,
            }
        )

    kafka_password = environ.get("KAFKA_PASSWORD", "")
    kafka_username = environ.get("KAFKA_USERNAME", "")
    if kafka_password and kafka_username:
        bootstrap_servers = environ.get(
            "KAFKA_BOOTSTRAP_SERVERS",
            "payments-kafka.data.svc.cluster.local:9092",
        )
        first_broker = bootstrap_servers.split(",", maxsplit=1)[0].strip()
        match = re.fullmatch(r"(\[[^\]]+\]|[^:\s]+):([0-9]+)", first_broker)
        if not match or not 1 <= int(match.group(2)) <= 65535:
            raise ValueError("Kafka bootstrap server address is invalid")
        kafka_host = match.group(1).strip("[]")
        kafka_port = int(match.group(2))
        kafka_address = f"{kafka_host}:{kafka_port}"
        connections.append(
            {
                "id": "rpp-events-kafka",
                "name": "RPP Events Kafka",
                "db_type": "mq",
                "driver_profile": "kafka",
                "driver_label": "Apache Kafka",
                "host": kafka_host,
                "port": kafka_port,
                "username": "",
                "password": "",
                "database": None,
                "ssl": True,
                "ca_cert_path": KAFKA_CA_PATH,
                "read_only": True,
                "save_password": True,
                "external_config": {
                    "systemKind": "kafka",
                    "adminUrl": f"http://{kafka_address}",
                    "auth": {"kind": "none"},
                    "tls": {"ca_cert_path": KAFKA_CA_PATH},
                    "extra": {
                        "bootstrapServers": bootstrap_servers,
                        "securityProtocol": "SASL_SSL",
                        "saslMechanism": "SCRAM-SHA-512",
                        "saslUsername": kafka_username,
                        "saslPassword": kafka_password,
                    },
                },
            }
        )

    return connections


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class DBXClient:
    def __init__(self, base_url, opener=None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != EXPECTED_HOST
            or parsed.port not in (None, 4224)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ProvisioningError("DBX API URL must be the in-cluster workbench Service")
        self.base_url = f"http://{EXPECTED_HOST}:4224"
        self.opener = opener or build_opener(
            HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect()
        )

    def request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{API_BASE_PATH}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                status = response.status
                content = response.read()
        except HTTPError as error:
            raise ProvisioningError(
                f"DBX API returned HTTP {error.code} for {method} {path}"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise ProvisioningError("DBX API request failed") from None
        if not 200 <= status < 300:
            raise ProvisioningError(
                f"DBX API returned HTTP {status} for {method} {path}"
            )
        if not content:
            return None
        try:
            return json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProvisioningError("DBX API returned an invalid response") from None

    def provision(self, password, connections):
        if not password:
            raise ProvisioningError("DBX login password is missing")
        self.request("POST", "/auth/login", {"password": password})
        self.request("POST", "/connection/save", {"configs": connections})
        saved = self.request("GET", "/connection/list")
        if isinstance(saved, dict):
            saved = saved.get("configs", saved.get("connections"))
        if not isinstance(saved, list):
            raise ProvisioningError("DBX connection list response has an unsupported shape")
        found = {
            item.get("id"): (item.get("db_type"), item.get("driver_profile"))
            for item in saved
            if isinstance(item, dict)
        }
        expected = {
            item["id"]: (item["db_type"], item.get("driver_profile"))
            for item in connections
        }
        if any(found.get(key) != value for key, value in expected.items()):
            raise ProvisioningError("DBX did not persist every requested connection")


def main():
    if os.environ.get("DBX_API_CONTRACT_VERSION") != API_CONTRACT_VERSION:
        raise ProvisioningError("DBX API contract version does not match the pinned adapter")
    try:
        connections = build_connections(os.environ)
    except (ValueError, KeyError):
        raise ProvisioningError("DBX connection source configuration is invalid") from None
    client = DBXClient(os.environ.get("DBX_API_URL", ""))
    client.provision(os.environ.get("DBX_PASSWORD", ""), connections)
    print(f"DBX provisioned {len(connections)} connection profiles.")


if __name__ == "__main__":
    try:
        main()
    except ProvisioningError as error:
        sys.exit(f"ERROR DBX provisioning: {error}")
