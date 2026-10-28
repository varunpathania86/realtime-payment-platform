import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from scripts.module_catalog import ROOT


class ToolInstallationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("install-tools.sh", "lib.sh"):
            shutil.copy(ROOT / "scripts" / name, scripts / name)
        (self.root / ".tool-versions").write_text("jq 1.8.2\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.mock_bin = self.root / "mock-bin"
        self.mock_bin.mkdir()
        (self.mock_bin / "jq").write_text("#!/bin/sh\necho jq-0.0.0\n")
        (self.mock_bin / "jq").chmod(0o755)
        self.asset = self.root / "asset"
        self.asset.write_text("#!/bin/sh\necho jq-1.8.2\n")
        self.metadata = self.root / "metadata.json"
        self.digest = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.set_metadata("sha256:" + self.digest)
        curl = self.mock_bin / "curl"
        curl.write_text("""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
with Path(os.environ["CURL_CALLS"]).open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
destination = args[args.index("-o") + 1]
api = args[-1].startswith("https://api.github.com/")
if api and os.environ.get("API_FAIL") == "1":
    print("curl: (22) HTTP 403", file=sys.stderr)
    sys.exit(22)
shutil.copyfile(os.environ["METADATA"] if api else os.environ["ASSET"], destination)
""")
        curl.chmod(0o755)
        self.calls = self.root / "calls"
        self.environment = {
            **os.environ,
            "PATH": f"{self.mock_bin}:{os.environ['PATH']}",
            "BIN_DIR": str(self.bin), "GH_TOKEN": "", "GITHUB_TOKEN": "",
            "CURL_CALLS": str(self.calls), "ASSET": str(self.asset), "METADATA": str(self.metadata),
        }

    def set_metadata(self, digest):
        architecture = "amd64" if os.uname().machine == "x86_64" else "arm64"
        self.metadata.write_text(json.dumps({"assets": [{"name": f"jq-linux-{architecture}", "digest": digest}]}))

    def install(self, **environment):
        return subprocess.run(
            ["bash", "scripts/install-tools.sh", "jq"], cwd=self.root,
            env={**self.environment, **environment}, capture_output=True, text=True, check=False,
        )

    def test_api_auth_is_scoped_to_metadata_and_install_remains_idempotent(self):
        result = self.install(GH_TOKEN="test-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("Authorization: Bearer test-token", calls[0])
        self.assertIn("Authorization: Bearer test-token", calls[1])
        self.assertEqual((self.bin / "jq").read_bytes(), self.asset.read_bytes())
        self.assertEqual(self.install(GH_TOKEN="test-token").returncode, 0)
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_local_anonymous_and_github_token_fallback_are_supported(self):
        for token in ("", "fallback-token"):
            with self.subTest(token=token):
                (self.bin / "jq").unlink(missing_ok=True)
                self.calls.unlink(missing_ok=True)
                result = self.install(GITHUB_TOKEN=token)
                self.assertEqual(result.returncode, 0, result.stderr)
                api = json.loads(self.calls.read_text().splitlines()[-1])
                self.assertEqual(any("Authorization:" in arg for arg in api), bool(token))

    def test_api_failure_stops_before_parsing_or_installing(self):
        result = self.install(API_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot fetch checksum metadata", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Checksum mismatch", result.stderr)
        self.assertFalse((self.bin / "jq").exists())

    def test_invalid_missing_and_mismatched_digests_fail_closed(self):
        for digest in ("", "sha256:invalid", "sha256:" + "a" * 64):
            with self.subTest(digest=digest):
                self.set_metadata(digest)
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.bin / "jq").exists())
                self.assertNotIn("Traceback", result.stderr)
        self.assertIn("Checksum mismatch", result.stderr)
        self.metadata.write_text("invalid json")
        result = self.install()
        self.assertIn("Invalid GitHub checksum metadata", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_github_release_tool_install_steps_receive_job_token(self):
        for name in ("build.yml", "security.yml", "security-module.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
            steps = [
                step for job in workflow["jobs"].values() for step in job.get("steps", [])
                if step.get("run", "").startswith("make tools")
            ]
            self.assertTrue(steps)
            for step in steps:
                self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")
