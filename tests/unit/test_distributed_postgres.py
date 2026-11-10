import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from scripts.module_catalog import ROOT


class DistributedPostgresChartTests(unittest.TestCase):
    def render(self, name, primary="pg-1", bootstrap=None):
        if bootstrap is None:
            bootstrap = "" if name == "pg-1" else "pg-1"
        result = subprocess.run([
            "helm", "template", "database", str(ROOT / "platform/terraform/modules/cnpg/chart"),
            "--set", f"name={name}", "--set", f"primary={primary}",
            "--set", f"bootstrapSource={bootstrap}",
            "--set", "image=ghcr.io/cloudnative-pg/postgresql:test",
            "--set", "addresses.pg-1=172.19.0.2",
            "--set", "addresses.pg-2=172.19.0.3",
            "--set", "addresses.pg-3=172.19.0.4",
        ], check=True, capture_output=True, text=True)
        return yaml.safe_load(result.stdout)["spec"]

    def test_numbered_database_members_are_used_in_replication(self):
        spec = self.render("pg-1", primary="pg-1", bootstrap="")
        self.assertEqual(set(spec["postgresql"]["synchronous"]["standbyNamesPre"]), {"pg-2", "pg-3"})
        self.assertEqual(spec["replica"], {"primary": "pg-1", "source": "pg-2"})

    def test_primary_and_standbys_share_tls_and_require_remote_acknowledgement(self):
        for name in ("pg-1", "pg-2", "pg-3"):
            with self.subTest(name=name):
                spec = self.render(name)
                self.assertEqual(spec["instances"], 1)
                synchronous = spec["postgresql"]["synchronous"]
                self.assertEqual(synchronous["number"], 1)
                self.assertEqual(synchronous["dataDurability"], "required")
                self.assertEqual(synchronous["maxStandbyNamesFromCluster"], 0)
                self.assertEqual(set(synchronous["standbyNamesPre"]), {"pg-1", "pg-2", "pg-3"} - {name})
                self.assertEqual(spec["certificates"]["replicationTLSSecret"], "database-replication")
                self.assertEqual(spec["replica"]["primary"], "pg-1")
                for external in spec["externalClusters"]:
                    self.assertEqual(external["connectionParameters"]["sslmode"], "verify-full")
                    self.assertEqual(external["connectionParameters"]["application_name"], name)
                if name == "pg-1":
                    self.assertEqual(spec["bootstrap"]["initdb"]["database"], "payments")
                else:
                    self.assertEqual(spec["bootstrap"], {"pg_basebackup": {"source": "pg-1"}})

    def test_declared_primary_changes_without_changing_bootstrap_identity(self):
        promoted = self.render("pg-2", "pg-2")
        self.assertEqual(promoted["replica"], {"primary": "pg-2", "source": "pg-1"})
        self.assertEqual(promoted["bootstrap"], {"pg_basebackup": {"source": "pg-1"}})

    def test_each_member_tracks_the_current_writer_without_self_replication(self):
        for primary in ("pg-1", "pg-2", "pg-3"):
            for name in ("pg-1", "pg-2", "pg-3"):
                with self.subTest(primary=primary, name=name):
                    spec = self.render(name, primary)
                    source = primary if name != primary else ("pg-2" if name == "pg-1" else "pg-1")
                    self.assertEqual(spec["replica"], {"primary": primary, "source": source})

    def test_former_primary_rebuild_clones_current_writer(self):
        rebuilt = self.render("pg-1", "pg-2", bootstrap="pg-2")
        self.assertEqual(rebuilt["bootstrap"], {"pg_basebackup": {"source": "pg-2"}})
        self.assertEqual(rebuilt["replica"], {"primary": "pg-2", "source": "pg-2"})


class CNPGSetupTests(unittest.TestCase):
    def test_chart_download_is_checksum_verified_idempotent_and_repairable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("setup-cnpg.sh", "lib.sh"):
                shutil.copy(ROOT / "scripts" / name, scripts / name)
            binary = root / "bin"
            binary.mkdir()
            curl = binary / "curl"
            curl.write_text(f"""#!{sys.executable}
from pathlib import Path
import sys
Path(sys.argv[-1]).write_bytes(b"test-chart")
with Path(__file__).with_name("calls").open("a") as stream:
    stream.write("download\\n")
""")
            curl.chmod(0o755)
            checksum = hashlib.sha256(b"test-chart").hexdigest()
            pins = root / ".tool-versions"
            pins.write_text(f"cnpg-chart 0.29.1\ncnpg-chart-sha256 {checksum}\n")
            environment = {**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"}

            def setup():
                return subprocess.run(
                    ["bash", str(scripts / "setup-cnpg.sh")],
                    env=environment, capture_output=True, text=True,
                )

            for _ in range(2):
                result = setup()
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((binary / "calls").read_text(), "download\n")
            archive = root / ".local/tools/cloudnative-pg-0.29.1.tgz"
            archive.write_bytes(b"corrupt")
            self.assertEqual(setup().returncode, 0)
            self.assertEqual(archive.read_bytes(), b"test-chart")
            archive.unlink()
            pins.write_text(f"cnpg-chart 0.29.1\ncnpg-chart-sha256 {'0' * 64}\n")
            result = setup()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum mismatch", result.stderr)
            self.assertFalse(archive.exists())
            self.assertFalse(list(archive.parent.glob("cnpg.*")))
