import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
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
        for name in ("install-tools.sh", "lib.sh", "github-release.sh", "build-minio.sh"):
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
asset_api = api and "/releases/assets/" in args[-1]
if (api and not asset_api and os.environ.get("API_FAIL") == "1") or (
    not api and os.environ.get("ASSET_FAIL") == "1"
) or (asset_api and os.environ.get("ASSET_API_FAIL") == "1"):
    print("curl: (22) HTTP 403", file=sys.stderr)
    sys.exit(22)
source = os.environ["METADATA"] if api and not asset_api else os.environ["ASSET"]
if asset_api:
    source = os.environ.get("API_ASSET", source)
shutil.copyfile(source, destination)
""")
        curl.chmod(0o755)
        self.calls = self.root / "calls"
        self.environment = {
            **os.environ,
            "PATH": f"{self.mock_bin}:{os.environ['PATH']}",
            "BIN_DIR": str(self.bin), "GH_TOKEN": "", "GITHUB_TOKEN": "",
            "CURL_CALLS": str(self.calls), "ASSET": str(self.asset), "METADATA": str(self.metadata),
        }

    def set_metadata(self, digest, asset_id=123, name=None):
        architecture = "amd64" if os.uname().machine == "x86_64" else "arm64"
        self.metadata.write_text(json.dumps({"assets": [{
            "name": name or f"jq-linux-{architecture}", "digest": digest, "id": asset_id,
        }]}))

    def install(self, tool="jq", **environment):
        return subprocess.run(
            ["bash", "scripts/install-tools.sh", tool], cwd=self.root,
            env={**self.environment, **environment}, capture_output=True, text=True, check=False,
        )

    def test_api_auth_is_scoped_to_api_and_install_remains_idempotent(self):
        result = self.install(GH_TOKEN="test-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertIn("Authorization: Bearer test-token", calls[0])
        self.assertNotIn("Authorization: Bearer test-token", calls[1])
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
                api = json.loads(self.calls.read_text().splitlines()[0])
                self.assertEqual(any("Authorization:" in arg for arg in api), bool(token))

    def test_release_failure_uses_verified_official_api_asset(self):
        for token in ("", "test-token"):
            with self.subTest(token=token):
                (self.bin / "jq").unlink(missing_ok=True)
                self.calls.unlink(missing_ok=True)
                result = self.install(ASSET_FAIL="1", GH_TOKEN=token)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("retrying through the official GitHub asset API", result.stderr)
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                self.assertEqual(len(calls), 3)
                self.assertFalse(any("Authorization:" in arg for arg in calls[1]))
                self.assertEqual(calls[2][-1], "https://api.github.com/repos/jqlang/jq/releases/assets/123")
                self.assertIn("Accept: application/octet-stream", calls[2])
                self.assertEqual(any("Authorization:" in arg for arg in calls[2]), bool(token))
                self.assertEqual((self.bin / "jq").read_bytes(), self.asset.read_bytes())

    def test_both_download_endpoints_failing_is_an_error(self):
        result = self.install(ASSET_FAIL="1", ASSET_API_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("through either official endpoint", result.stderr)
        self.assertFalse((self.bin / "jq").exists())

    def test_api_asset_checksum_mismatch_cannot_install(self):
        wrong_asset = self.root / "wrong-asset"
        wrong_asset.write_text("not the approved bytes")
        result = self.install(ASSET_FAIL="1", API_ASSET=str(wrong_asset))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Checksum mismatch", result.stderr)
        self.assertFalse((self.bin / "jq").exists())

    def test_invalid_asset_ids_fail_before_download(self):
        for asset_id in (None, True, 0, "123", "https://untrusted.example/asset"):
            with self.subTest(asset_id=asset_id):
                self.set_metadata("sha256:" + self.digest, asset_id=asset_id)
                self.calls.unlink(missing_ok=True)
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing or invalid asset ID", result.stderr)
                self.assertEqual(len(self.calls.read_text().splitlines()), 1)
                self.assertFalse((self.bin / "jq").exists())

    def test_editorconfig_hook_uses_setup_managed_verified_binary(self):
        architecture = "amd64" if os.uname().machine == "x86_64" else "arm64"
        asset_name = f"ec-linux-{architecture}"
        binary = self.root / "ec"
        binary.write_text("#!/bin/sh\necho 3.11.1\n")
        archive = self.root / "ec.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(binary, arcname=f"bin/{asset_name}")
        self.set_metadata(
            "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            name=f"{asset_name}.tar.gz",
        )
        (self.root / ".tool-versions").write_text("editorconfig-checker 3.11.1\n")
        stub = self.bin / "editorconfig-checker"
        stub.write_text("#!/bin/sh\necho 0.0.0\n")
        stub.chmod(0o755)
        result = self.install(tool="editorconfig-checker", ASSET=str(archive), ASSET_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stub.read_bytes(), binary.read_bytes())
        result = self.install(tool="editorconfig-checker", ASSET=str(archive))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok       editorconfig-checker 3.11.1", result.stdout)
        hook = next(
            hook for repo in yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]
            for hook in repo["hooks"] if hook["id"] == "editorconfig-checker"
        )
        self.assertEqual(hook["entry"], "editorconfig-checker")
        self.assertEqual(hook["language"], "system")

    def test_minio_build_checks_pinned_bytes_before_docker(self):
        architecture = "amd64" if os.uname().machine == "x86_64" else "arm64"
        version = "RELEASE.2025-04-22T22-12-26Z"
        self.set_metadata("sha256:" + self.digest, name=f"minio.linux-{architecture}.{version}")
        (self.root / ".tool-versions").write_text(
            f"go 1.27.1\nminio {version}\nminio-sha256-{architecture} {self.digest}\n"
        )
        runtime = self.root / "platform/runtime/minio"
        runtime.mkdir(parents=True)
        shutil.copy(ROOT / "platform/runtime/minio/Dockerfile", runtime / "Dockerfile")
        docker = self.mock_bin / "docker"
        docker.write_text("""#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import sys

context = Path(sys.argv[-1])
Path(os.environ["DOCKER_CALLS"]).write_text(json.dumps({
    "args": sys.argv[1:],
    "sha256": hashlib.sha256((context / "minio").read_bytes()).hexdigest(),
    "dockerfile": (context / "Dockerfile").read_text(),
}))
""")
        docker.chmod(0o755)
        record = self.root / "docker-calls"
        for pinned_digest in (self.digest, "a" * 64):
            with self.subTest(pinned_digest=pinned_digest):
                record.unlink(missing_ok=True)
                (self.root / ".tool-versions").write_text(
                    f"go 1.27.1\nminio {version}\nminio-sha256-{architecture} {pinned_digest}\n"
                )
                result = subprocess.run(
                    ["bash", "scripts/build-minio.sh"], cwd=self.root,
                    env={**self.environment, "ASSET_FAIL": "1", "DOCKER_CALLS": str(record)},
                    capture_output=True, text=True, check=False,
                )
                if pinned_digest != self.digest:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Checksum mismatch", result.stderr)
                    self.assertFalse(record.exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    built = json.loads(record.read_text())
                    self.assertEqual(built["sha256"], self.digest)
                    self.assertIn("COPY minio /minio", built["dockerfile"])
                    self.assertIn("sha256sum -c -", built["dockerfile"])

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
