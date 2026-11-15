import json
from pathlib import Path
import re
import stat
import tempfile
import unittest

from scripts.local_database import ensure_credentials
from scripts.module_catalog import ROOT


class LocalDatabaseTests(unittest.TestCase):
    def test_payment_and_identity_database_probes_allow_bounded_loaded_startup(self):
        for path in ("platform/terraform/environments/local/postgres.tf",
                "platform/terraform/modules/identity/main.tf"):
            with self.subTest(path=path):
                source = (ROOT / path).read_text()
                for name in ("startup", "readiness", "liveness"):
                    probe = re.search(rf"{name}_probe\s*\{{(.*?)\n\s{{10}}\}}", source, re.S)
                    self.assertIsNotNone(probe)
                    self.assertRegex(probe.group(1), r"timeout_seconds\s*=\s*5")
                self.assertRegex(source, r"failure_threshold\s*=\s*30")

    def test_existing_database_state_requires_credential_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.tfvars.json"
            path.with_name("terraform.tfstate").write_text(json.dumps({
                "resources": [{"type": "kubernetes_secret_v1", "name": "postgres"}],
            }))
            with self.assertRaisesRegex(ValueError, "recover"):
                ensure_credentials(path)
            self.assertFalse(path.exists())

    def test_credentials_are_private_stable_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile/database.tfvars.json"
            ensure_credentials(path)
            original = path.read_bytes()
            self.assertEqual(len(json.loads(original)["postgres_password"]), 64)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            path.chmod(0o644)
            ensure_credentials(path)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            path.write_text('{"postgres_password": "bad"}')
            with self.assertRaisesRegex(ValueError, "invalid local database credential"):
                ensure_credentials(path)
            self.assertEqual(path.read_text(), '{"postgres_password": "bad"}')
