"""Offline runtime packaging and version verification do not need a cluster."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

from scripts import dbx_runtime_package


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("dbx_runtime_install", ROOT / "scripts/dbx_runtime_install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class RuntimeTests(unittest.TestCase):
    def test_requires_exact_versions_and_java(self):
        expected = {"kafka": "0.1.24", "trino": "0.1.92"}
        records = [
            {"db_type": key, "installed": True, "installed_version": version, "jre_installed": True}
            for key, version in expected.items()
        ]
        self.assertTrue(installer.ready(records, expected))
        records[0]["installed_version"] = "newer-unapproved"
        self.assertFalse(installer.ready(records, expected))
        records[0]["installed_version"] = expected["kafka"]
        records[1]["jre_installed"] = False
        self.assertFalse(installer.ready(records, expected))
        self.assertFalse(installer.ready([], expected))
        with self.assertRaises(installer.ProvisioningError):
            installer.ready({}, expected)

    def test_unsupported_architecture_fails_before_download(self):
        with patch.object(dbx_runtime_package, "artifact") as download:
            with self.assertRaisesRegex(ValueError, "only amd64 and arm64"):
                dbx_runtime_package.package(ROOT, "ppc64le")
            download.assert_not_called()

    def test_cache_checksum_mismatch_is_rejected_and_removed(self):
        target = unittest.mock.MagicMock()
        target.exists.return_value = False
        target.open.return_value.__enter__.return_value = io.BytesIO()
        target.read_bytes.return_value = b"wrong"
        cache = unittest.mock.MagicMock()
        cache.__truediv__.return_value = target
        response = io.BytesIO(b"wrong")
        with patch.object(dbx_runtime_package, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "checksum"):
                dbx_runtime_package.artifact(cache, "https://example.invalid/agent", hashlib.sha256(b"right").hexdigest())
        self.assertEqual(target.unlink.call_count, 2)

    def test_install_is_idempotent_and_uses_only_local_list(self):
        registry = {"drivers": {"kafka": {"version": "0.1.24"}, "trino": {"version": "0.1.92"}}}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("agent-registry.json", json.dumps(registry))
        archive.seek(0)
        client = unittest.mock.MagicMock()
        client.request.return_value = [
            {"db_type": key, "installed": True, "installed_version": value["version"], "jre_installed": True}
            for key, value in registry["drivers"].items()
        ]
        installer.install(client, "synthetic-login", archive)
        client.opener.open.assert_not_called()
        self.assertEqual(client.request.call_args_list[-1].args, ("GET", "/agents/installed-local"))

    def test_local_runtime_is_loaded_and_imported_before_connection_sync(self):
        terraform = (ROOT / "platform/terraform/environments/local/dbx.tf").read_text()
        self.assertIn('image_pull_policy = "Never"', terraform)
        self.assertIn("dbx_runtime_install.py && python /opt/dbx/dbx_provision.py", terraform)
        self.assertIn('filesha256("${local.root}/scripts/dbx_runtime_install.py")', terraform)
        self.assertIn('"ANALYTICS_READER_PASSWORD"', terraform)
        self.assertIn('"KNOWLEDGE_READER_PASSWORD"', terraform)
        self.assertIn('"BI_DATABASE_PASSWORD"', terraform)
        self.assertIn('"POLARIS_DATABASE_PASSWORD"', terraform)

    def test_imports_standalone_release_artifacts_java_first(self):
        archive = io.BytesIO()
        registry = {"drivers": {}, "jres": {}}
        with zipfile.ZipFile(archive, "w") as bundle:
            for key in ("kafka", "trino", "jre"):
                filename = f"dbx-{key}.tar.zst"
                content = key.encode()
                info = {"url": f"https://example.invalid/{filename}", "sha256": hashlib.sha256(content).hexdigest()}
                entry = {"version": "test", "jar": info}
                if key == "jre":
                    registry["jres"]["21"] = {"platforms": {"linux-x64": info}}
                else:
                    registry["drivers"][key] = entry
                bundle.writestr(f"{'jre' if key == 'jre' else 'drivers'}/{filename}", content)
            bundle.writestr("agent-registry.json", json.dumps(registry))
        archive.seek(0)
        client = unittest.mock.MagicMock()
        verified = [
            {"db_type": key, "installed": True, "installed_version": "test", "jre_installed": True}
            for key in ("kafka", "trino")
        ]
        client.request.side_effect = [None, [], verified]
        with patch.object(installer, "upload") as upload:
            installer.install(client, "synthetic-login", archive)
        self.assertEqual([call.args[1] for call in upload.call_args_list], ["dbx-jre.tar.zst", "dbx-kafka.tar.zst", "dbx-trino.tar.zst"])

    def test_import_errors_never_echo_response_body(self):
        client = unittest.mock.MagicMock()
        client.base_url = "http://dbx-workbench.apps.svc.cluster.local:4224"
        client.opener.open.side_effect = HTTPError(client.base_url, 500, "sensitive upstream message", {}, io.BytesIO(b"sensitive"))
        with self.assertRaisesRegex(installer.ProvisioningError, "^DBX runtime import returned HTTP 500$"):
            installer.upload(client, "dbx-kafka.tar.zst", b"artifact")

    def test_verification_exercises_metadata_and_select_after_connect(self):
        client = unittest.mock.MagicMock()
        client.request.side_effect = [
            None,
            [{"id": "rpp-events-kafka"}, {"id": "rpp-lakehouse-trino"}],
            "rpp-events-kafka",
            {"extra": {"brokers": [{"id": 1}]}},
            [],
            "rpp-lakehouse-trino",
            {"rows": [["1"]]},
        ]
        installer.verify_connections(client, "synthetic-login", True)
        paths = [call.args[1] for call in client.request.call_args_list]
        self.assertEqual(paths, ["/auth/login", "/connection/list", "/connection/connect", "/mq/test-connection", "/mq/topics/list", "/connection/connect", "/query/execute"])

    def test_verification_rejects_missing_runtime_metadata(self):
        client = unittest.mock.MagicMock()
        client.request.side_effect = [
            None, [{"id": "rpp-events-kafka"}], "rpp-events-kafka", {"extra": {}},
        ]
        with self.assertRaisesRegex(installer.ProvisioningError, "Kafka broker metadata"):
            installer.verify_connections(client, "synthetic-login", False)


if __name__ == "__main__":
    unittest.main()
