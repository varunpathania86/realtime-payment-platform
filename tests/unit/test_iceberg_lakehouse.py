from datetime import datetime, timezone
import importlib.util
import io
import subprocess
import unittest
import unittest.mock
import urllib.error

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT

SPEC = importlib.util.spec_from_file_location(
    "setup_catalog", ROOT / "modules/iceberg-lakehouse/setup_catalog.py",
)
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)

ENVIRON = {
    "CATALOG_NAME": "lakehouse", "NAMESPACE": "payments",
    "WAREHOUSE_LOCATION": "s3://lakehouse/warehouse", "S3_ENDPOINT": "http://minio:9000",
    "AWS_ACCESS_KEY_ID": "rpp-streaming", "AWS_SECRET_ACCESS_KEY": "synthetic-storage",
    **{f"{prefix}_CLIENT_{part}": f"{prefix.lower()}-{part.lower()}"
        for prefix in ("WRITER", "READER", "MAINTAINER") for part in ("ID", "SECRET")},
}


class FakePolaris:
    def __init__(self, existing=(), catalog_properties=None):
        self.calls = []
        self.existing = set(existing)
        self.catalog_properties = catalog_properties or {}

    def call(self, method, path, payload=None, allowed=()):
        self.calls.append((method, path, payload))
        if method == "GET":
            return 200, {"entityVersion": 3, "properties": dict(self.catalog_properties)}
        return (409 if path in self.existing and 409 in allowed else 201), {}


class IcebergLakehouseSetupTests(unittest.TestCase):
    def test_sigv4_signing_key_matches_aws_reference_vector(self):
        key = SETUP.signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20120215", "us-east-1", "iam")
        self.assertEqual(key.hex(), "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d")

    def test_signed_headers_are_deterministic_and_scoped_to_s3(self):
        now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        headers = SETUP.signed_headers("PUT", "http://minio:9000/lakehouse", "us-east-1", "id", "secret", now)
        self.assertEqual(headers["x-amz-date"], "20261104T120000Z")
        self.assertEqual(headers["host"], "minio:9000")
        self.assertTrue(headers["Authorization"].startswith(
            "AWS4-HMAC-SHA256 Credential=id/20261104/us-east-1/s3/aws4_request, "
            "SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature="))
        self.assertEqual(headers, SETUP.signed_headers(
            "PUT", "http://minio:9000/lakehouse", "us-east-1", "id", "secret", now))

    def test_bucket_requires_s3_uri_and_existing_bucket_is_accepted(self):
        self.assertEqual(SETUP.bucket_from("s3://lakehouse/warehouse"), "lakehouse")
        with self.assertRaisesRegex(RuntimeError, "s3://"):
            SETUP.bucket_from("file:///tmp/warehouse")

        def opener(status, body):
            def open_request(request, timeout):
                self.assertEqual((request.get_method(), request.full_url), ("PUT", "http://minio:9000/lakehouse"))
                raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(body))
            return open_request

        with unittest.mock.patch("builtins.print"):
            SETUP.ensure_bucket(ENVIRON, opener(409, b"<Code>BucketAlreadyOwnedByYou</Code>"))
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            SETUP.ensure_bucket(ENVIRON, opener(403, b"<Code>AccessDenied</Code>"))

    def test_tables_use_hidden_day_partitions_on_their_own_columns(self):
        for name, definition in SETUP.TABLES.items():
            request = SETUP.table_request(name, definition)
            field = request["partition-spec"]["fields"][0]
            column = next(item for item in request["schema"]["fields"] if item["id"] == field["source-id"])
            self.assertEqual((field["transform"], column["name"]), ("day", definition["partition"][0]))
            self.assertEqual(column["type"], "timestamptz")
            self.assertEqual(request["properties"]["format-version"], "2")
            self.assertEqual(len({item["id"] for item in request["schema"]["fields"]}),
                len(request["schema"]["fields"]))

    def test_principals_have_least_privilege(self):
        writer = SETUP.PRINCIPALS["flink-lakehouse-writer"][3]
        reader = SETUP.PRINCIPALS["trino-lakehouse-reader"][3]
        self.assertFalse(any(privilege.endswith(("_CREATE", "_DROP", "WRITE_PROPERTIES")) for privilege in writer))
        self.assertNotIn("TABLE_WRITE_DATA", reader)
        self.assertFalse(any("WRITE" in privilege or "MANAGE" in privilege for privilege in reader))
        self.assertEqual(set(writer) - set(reader), {"TABLE_WRITE_DATA"})

    def test_converge_is_idempotent_and_resets_terraform_credentials(self):
        existing = {"/api/management/v1/catalogs", "/api/catalog/v1/lakehouse/namespaces",
            "/api/catalog/v1/lakehouse/namespaces/payments/tables"}
        client = FakePolaris(existing)
        with unittest.mock.patch("builtins.print"):
            SETUP.converge(client, ENVIRON)
        resets = {path: payload for method, path, payload in client.calls if path.endswith("/reset")}
        self.assertEqual(resets["/api/management/v1/principals/flink-lakehouse-writer/reset"],
            {"clientId": "writer-id", "clientSecret": "writer-secret"})
        catalog = client.calls[0][2]["catalog"]
        self.assertEqual(catalog["storageConfigInfo"]["allowedLocations"], ["s3://lakehouse/warehouse"])
        self.assertTrue(catalog["storageConfigInfo"]["pathStyleAccess"])
        grants = [payload["grant"]["privilege"] for method, path, payload in client.calls
            if path.endswith("/content_reader/grants")]
        self.assertEqual(grants, SETUP.READ)
        self.assertFalse(any(method == "DELETE" for method, _, _ in client.calls))

    def test_existing_catalog_gains_purge_on_drop_without_losing_properties(self):
        client = FakePolaris({"/api/management/v1/catalogs"}, {"default-base-location": "s3://lakehouse/warehouse",
            "polaris.managed": "kept"})
        with unittest.mock.patch("builtins.print"):
            SETUP.converge(client, ENVIRON)
        updates = [payload for method, path, payload in client.calls if method == "PUT" and path.endswith("/catalogs/lakehouse")]
        self.assertEqual(updates, [{"currentEntityVersion": 3, "properties": {
            "default-base-location": "s3://lakehouse/warehouse", "polaris.managed": "kept",
            "polaris.config.drop-with-purge.enabled": "true"}}])
        converged = FakePolaris({"/api/management/v1/catalogs"}, SETUP.catalog_properties("s3://lakehouse/warehouse"))
        with unittest.mock.patch("builtins.print"):
            SETUP.converge(converged, ENVIRON)
        self.assertFalse(any(method == "PUT" and path.endswith("/catalogs/lakehouse")
            for method, path, _ in converged.calls))

    def test_chart_bootstraps_before_serving_and_converges_in_a_hook(self):
        rendered = subprocess.run(
            ["helm", "template", "iceberg-lakehouse", str(ROOT / "modules/iceberg-lakehouse/chart"),
                "--namespace", "apps"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        resources = [item for item in yaml.safe_load_all(rendered.stdout) if item]
        validate_chart(resources)
        config = next(item for item in resources if item["kind"] == "ConfigMap")["data"]["application.properties"]
        self.assertIn("polaris.persistence.type=relational-jdbc", config)
        self.assertIn("token-broker.type=symmetric-key", config)
        pod = next(item for item in resources if item["kind"] == "Deployment")["spec"]["template"]["spec"]
        bootstrap = pod["initContainers"][0]
        self.assertIn("polaris-admin-tool.jar bootstrap", bootstrap["args"][0])
        self.assertEqual(bootstrap["image"], pod["containers"][0]["image"])
        server_env = {item["name"]: item for item in pod["containers"][0]["env"]}
        self.assertEqual(server_env["POLARIS_AUTHENTICATION_TOKEN_BROKER_SYMMETRIC_KEY_SECRET"]["valueFrom"],
            {"secretKeyRef": {"name": "iceberg-lakehouse", "key": "token-secret"}})
        job = next(item for item in resources if item["kind"] == "Job")
        self.assertEqual(job["metadata"]["annotations"]["helm.sh/hook"], "post-install,post-upgrade")
        setup = job["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(setup["command"], ["python3", "/opt/rpp/setup_catalog.py"])
        required = {"ROOT_CLIENT_ID", "WRITER_CLIENT_SECRET", "READER_CLIENT_ID", "MAINTAINER_CLIENT_SECRET",
            "WAREHOUSE_LOCATION", "S3_ENDPOINT", "AWS_SECRET_ACCESS_KEY"}
        self.assertLessEqual(required, {item["name"] for item in setup["env"]})
        self.assertFalse(any("value" in item and "SECRET" in item["name"] for item in setup["env"]))


if __name__ == "__main__":
    unittest.main()
