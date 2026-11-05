"""Converge the Polaris lakehouse catalog, namespace, tables, principals and grants.

Runs inside the Polaris image (Python 3.9, standard library only). Every step is
safe to repeat: existing objects are kept, grants are re-applied and principal
credentials are reset to the values supplied by Terraform-managed Secrets.
Existing tables are never altered here; schema changes are explicit maintainer
operations (see the module README).
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

READ = [
    "NAMESPACE_LIST", "NAMESPACE_READ_PROPERTIES",
    "TABLE_LIST", "TABLE_READ_PROPERTIES", "TABLE_READ_DATA",
]
# The writer appends snapshots only; this setup owns table creation and evolution is a maintainer task.
WRITE = READ + ["TABLE_WRITE_DATA"]
MAINTAIN = ["CATALOG_MANAGE_CONTENT"]

# principal -> (credential environment prefix, principal role, catalog role, privileges)
PRINCIPALS = {
    "flink-lakehouse-writer": ("WRITER", "lakehouse_writer", "content_writer", WRITE),
    "trino-lakehouse-reader": ("READER", "lakehouse_reader", "content_reader", READ),
    "lakehouse-maintainer": ("MAINTAINER", "lakehouse_maintainer", "content_maintainer", MAINTAIN),
}


TABLE_PROPERTIES = {
    "format-version": "2",
    "write.parquet.compression-codec": "zstd",
    "write.target-file-size-bytes": str(64 * 1024 * 1024),
    "write.parquet.row-group-size-bytes": str(8 * 1024 * 1024),
    "write.metadata.delete-after-commit.enabled": "true",
    "write.metadata.previous-versions-max": "50",
}


def _fields(*columns):
    return [{"id": index, "name": name, "required": False, "type": kind}
        for index, (name, kind) in enumerate(columns, start=1)]


# Iceberg REST definitions; hidden day() partitions are not expressible in Flink DDL.
TABLES = {
    "payment_events_raw": {
        "doc": "Bronze: every record exactly as received from Kafka, including invalid payloads.",
        "columns": _fields(
            ("event_id", "string"), ("event_type", "string"), ("raw_payload", "string"),
            ("kafka_partition", "int"), ("kafka_offset", "long"), ("kafka_timestamp", "timestamptz"),
            ("ingested_at", "timestamptz"),
        ),
        "partition": ("kafka_timestamp", "kafka_day"),
    },
    "payment_events": {
        "doc": "Curated: validated payment.processing.v1 facts, first occurrence per event_id within 24 hours.",
        "columns": _fields(
            ("event_id", "string"), ("payment_id", "string"), ("account_id", "string"),
            ("event_type", "string"), ("schema_version", "int"), ("event_time", "timestamptz"),
            ("amount_minor", "long"), ("currency", "string"), ("country", "string"),
            ("payment_method", "string"), ("outcome", "string"), ("account_created_at", "timestamptz"),
            ("device_id", "string"), ("kafka_partition", "int"), ("kafka_offset", "long"),
            ("ingested_at", "timestamptz"),
        ),
        "partition": ("event_time", "event_day"),
    },
}


def table_request(name, definition):
    columns = definition["columns"]
    source, partition = definition["partition"]
    source_id = next(column["id"] for column in columns if column["name"] == source)
    return {
        "name": name,
        "schema": {"type": "struct", "schema-id": 0, "fields": columns},
        "partition-spec": {"spec-id": 0, "fields": [
            {"name": partition, "transform": "day", "source-id": source_id, "field-id": 1000},
        ]},
        "properties": {**TABLE_PROPERTIES, "comment": definition["doc"]},
    }


def signing_key(secret, date, region, service):
    key = ("AWS4" + secret).encode()
    for part in (date, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def signed_headers(method, url, region, access_key, secret_key, now, payload=b""):
    """AWS Signature Version 4 headers for one path-style S3 request (standard library only)."""
    parsed = urllib.parse.urlsplit(url)
    stamp, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"host": parsed.netloc, "x-amz-content-sha256": digest, "x-amz-date": stamp}
    names = ";".join(sorted(headers))
    canonical = "\n".join((
        method, urllib.parse.quote(parsed.path or "/"), parsed.query,
        "".join(f"{name}:{headers[name]}\n" for name in sorted(headers)), names, digest,
    ))
    scope = f"{date}/{region}/s3/aws4_request"
    text = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    signature = hmac.new(signing_key(secret_key, date, region, "s3"), text.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={names}, Signature={signature}")
    return headers


def bucket_from(location):
    parsed = urllib.parse.urlsplit(location)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise RuntimeError("WAREHOUSE_LOCATION must be an s3://bucket/prefix URI")
    return parsed.netloc


def ensure_bucket(environ, opener=urllib.request.urlopen):
    """Polaris writes table metadata itself, so the warehouse bucket must exist before tables."""
    bucket = bucket_from(environ["WAREHOUSE_LOCATION"])
    url = environ["S3_ENDPOINT"].rstrip("/") + "/" + bucket
    headers = signed_headers("PUT", url, environ.get("S3_REGION", "us-east-1"), environ["AWS_ACCESS_KEY_ID"],
        environ["AWS_SECRET_ACCESS_KEY"], datetime.now(timezone.utc))
    try:
        with opener(urllib.request.Request(url, method="PUT", headers=headers), timeout=10):
            print(f"bucket {bucket}: created", flush=True)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        if error.code == 409 and "BucketAlreadyOwnedByYou" in body:
            print(f"bucket {bucket}: exists", flush=True)
            return
        raise RuntimeError(f"bucket {bucket} creation failed with HTTP {error.code}: {body[:300]}") from None


class Polaris:
    def __init__(self, base, realm, client_id, client_secret):
        self.base = base.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None

    def authenticate(self):
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": self.client_id,
            "client_secret": self.client_secret, "scope": "PRINCIPAL_ROLE:ALL",
        }).encode()
        request = urllib.request.Request(
            self.base + "/api/catalog/v1/oauth/tokens", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Polaris-Realm": self.realm})
        with urllib.request.urlopen(request, timeout=10) as response:
            self.token = json.load(response)["access_token"]

    def call(self, method, path, payload=None, allowed=()):
        """Return (status, body); statuses in `allowed` are expected non-2xx outcomes."""
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "Polaris-Realm": self.realm,
        })
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                text = response.read().decode()
                return response.status, json.loads(text) if text else {}
        except urllib.error.HTTPError as error:
            if error.code in allowed:
                return error.code, {}
            detail = error.read().decode()[:500]
            raise RuntimeError(f"{method} {path} failed with HTTP {error.code}: {detail}") from None


def catalog_properties(location):
    # Trino always purges on DROP TABLE; without this a maintainer drop is refused (or would orphan files).
    return {"default-base-location": location, "polaris.config.drop-with-purge.enabled": "true"}


def catalog_request(name, location, endpoint, region):
    return {"catalog": {
        "type": "INTERNAL", "name": name,
        "properties": catalog_properties(location),
        "storageConfigInfo": {
            "storageType": "S3", "allowedLocations": [location], "endpoint": endpoint,
            "region": region, "pathStyleAccess": True, "stsUnavailable": True,
        },
    }}


def converge(client, environ):
    catalog = environ["CATALOG_NAME"]
    namespace = environ["NAMESPACE"]
    management = "/api/management/v1"
    status, _ = client.call("POST", management + "/catalogs", catalog_request(
        catalog, environ["WAREHOUSE_LOCATION"], environ["S3_ENDPOINT"], environ.get("S3_REGION", "us-east-1"),
    ), allowed=(409,))
    print(f"catalog {catalog}: {'exists' if status == 409 else 'created'}", flush=True)
    if status == 409:
        _, current = client.call("GET", f"{management}/catalogs/{catalog}")
        properties = current.get("properties", {})
        desired = catalog_properties(environ["WAREHOUSE_LOCATION"])
        if any(properties.get(key) != value for key, value in desired.items()):
            # Updates replace the whole map, so merge to keep Polaris-managed properties.
            client.call("PUT", f"{management}/catalogs/{catalog}", {
                "currentEntityVersion": current["entityVersion"], "properties": {**properties, **desired}})
            print(f"catalog {catalog}: properties updated", flush=True)
    status, _ = client.call("POST", f"/api/catalog/v1/{catalog}/namespaces",
        {"namespace": [namespace], "properties": {}}, allowed=(409,))
    print(f"namespace {namespace}: {'exists' if status == 409 else 'created'}", flush=True)
    for name, definition in TABLES.items():
        status, _ = client.call("POST", f"/api/catalog/v1/{catalog}/namespaces/{namespace}/tables",
            table_request(name, definition), allowed=(409,))
        print(f"table {namespace}.{name}: {'exists' if status == 409 else 'created'}", flush=True)
    for principal, (prefix, principal_role, catalog_role, privileges) in PRINCIPALS.items():
        client.call("POST", management + "/principals",
            {"principal": {"name": principal, "properties": {}}, "credentialRotationRequired": False},
            allowed=(409,))
        client.call("POST", f"{management}/principals/{principal}/reset", {
            "clientId": environ[prefix + "_CLIENT_ID"], "clientSecret": environ[prefix + "_CLIENT_SECRET"],
        })
        client.call("POST", management + "/principal-roles",
            {"principalRole": {"name": principal_role, "properties": {}}}, allowed=(409,))
        client.call("PUT", f"{management}/principals/{principal}/principal-roles",
            {"principalRole": {"name": principal_role}})
        client.call("POST", f"{management}/catalogs/{catalog}/catalog-roles",
            {"catalogRole": {"name": catalog_role, "properties": {}}}, allowed=(409,))
        client.call("PUT", f"{management}/principal-roles/{principal_role}/catalog-roles/{catalog}",
            {"catalogRole": {"name": catalog_role}})
        for privilege in privileges:
            client.call("PUT", f"{management}/catalogs/{catalog}/catalog-roles/{catalog_role}/grants",
                {"grant": {"type": "catalog", "privilege": privilege}})
        print(f"principal {principal}: role {principal_role} -> {catalog_role} {privileges}", flush=True)


def retry(description, action, attempts=60, sleep=time.sleep):
    """Retry only connection failures; HTTP errors from a reachable service fail immediately."""
    for attempt in range(attempts):
        try:
            return action()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError) as error:
            print(f"waiting for {description} ({attempt + 1}/{attempts}): {error}", flush=True)
            sleep(2)
    raise RuntimeError(f"{description} was not reachable within {attempts * 2} seconds")


def main():
    environ = os.environ
    client = Polaris(environ["POLARIS_URL"], environ.get("POLARIS_REALM", "POLARIS"),
        environ["ROOT_CLIENT_ID"], environ["ROOT_CLIENT_SECRET"])
    retry("Polaris", client.authenticate)
    retry("object storage", lambda: ensure_bucket(environ))
    converge(client, environ)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError) as error:
        sys.exit(f"ERROR lakehouse catalog setup: {error}")
