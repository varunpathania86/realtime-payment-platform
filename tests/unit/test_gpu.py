import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from scripts.module_catalog import ROOT


class GpuPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls"
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": str(self.log)}
        self.stub("nvidia-smi", 'echo "RTX A2000, 8192 MiB"')
        self.stub("docker", '''
case "$1" in
    info) printf '%s\\n' "${RUNTIMES:-{\\"nvidia\\":{}}}" ;;
    container) [ "${EXISTING:-0}" = 1 ] ;;
    inspect) printf '%s\\n' "${REQUESTS:-null}" ;;
esac
''')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(f'#!/bin/bash\nprintf "%s %s\\n" {shlex.quote(name)} "$*" >> "$CALLS"\n{body}\n')
        path.chmod(0o755)

    def run_helper(self, command, **env):
        script = f'set -euo pipefail\n. {shlex.quote(str(ROOT / "scripts/lib.sh"))}\n'
        script += f'. {shlex.quote(str(ROOT / "scripts/gpu.sh"))}\n{command}\n'
        return subprocess.run(
            ["bash", "-c", script], env={**self.env, **env},
            capture_output=True, text=True, timeout=15,
        )

    def test_new_gpu_node_passes_preflight_without_starting_or_deleting_containers(self):
        result = self.run_helper("gpu_preflight rpp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("delete", self.log.read_text())
        self.assertNotIn("run ", self.log.read_text())

    def test_existing_cpu_node_is_refused_without_deletion(self):
        result = self.run_helper("gpu_preflight rpp", EXISTING="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no GPU passthrough", result.stderr)
        self.assertNotIn("delete", self.log.read_text())
        self.assertNotIn("rm ", self.log.read_text())

    def test_existing_gpu_node_is_reusable(self):
        requests = [{"Capabilities": [["gpu"]], "Count": -1, "Driver": "nvidia"}]
        result = self.run_helper("gpu_preflight rpp", EXISTING="1", REQUESTS=json.dumps(requests))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_docker_runtime_is_an_explicit_failure(self):
        result = self.run_helper("gpu_preflight rpp", RUNTIMES='{"runc":{}}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NVIDIA runtime is missing", result.stderr)

    def test_unresponsive_driver_is_bounded_and_stops_before_docker(self):
        self.stub("timeout", '[ "$*" = "-k 2 10 nvidia-smi --query-gpu=name,memory.total --format=csv,noheader" ]\nexit 124')
        result = self.run_helper("gpu_preflight rpp")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("driver did not respond", result.stderr)
        self.assertNotIn("docker", self.log.read_text())


class GpuSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        for name in ("setup-gpu.sh", "gpu.sh", "lib.sh"):
            shutil.copy(ROOT / "scripts" / name, self.root / "scripts" / name)
        self.payload = b"verified chart"
        self.checksum = hashlib.sha256(self.payload).hexdigest()
        (self.root / ".tool-versions").write_text(
            f"nvidia-device-plugin 0.20.1\nnvidia-device-plugin-chart-sha256 {self.checksum}\n"
        )
        (self.root / "scripts/github-release.sh").write_text('''
fetch_github_asset() {
    printf '%s %s %s\\n' "$1" "$2" "$3" >> "$REPO_ROOT/downloads"
    printf '%s' "${PAYLOAD:-verified chart}" > "$4"
}
verify() {
    printf '%s  %s\\n' "$2" "$1" | sha256sum --check --status ||
    { echo "Checksum mismatch" >&2; exit 1; }
}
''')
        self.env = {**os.environ, "AI_GPU": "1"}
        self.archive = self.root / ".local/tools/nvidia-device-plugin-0.20.1.tgz"

    def setup_gpu(self, **env):
        return subprocess.run(
            ["bash", "scripts/setup-gpu.sh"], cwd=self.root, env={**self.env, **env},
            capture_output=True, text=True, timeout=10,
        )

    def test_verified_setup_is_idempotent_and_repairs_corrupt_cache(self):
        for _ in range(2):
            result = self.setup_gpu()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.archive.read_bytes(), self.payload)
        self.assertEqual(len((self.root / "downloads").read_text().splitlines()), 1)
        self.archive.write_text("corrupt")
        self.assertEqual(self.setup_gpu().returncode, 0)
        self.assertEqual(self.archive.read_bytes(), self.payload)
        self.assertEqual(len((self.root / "downloads").read_text().splitlines()), 2)

    def test_checksum_failure_never_installs_archive(self):
        result = self.setup_gpu(PAYLOAD="wrong")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Checksum mismatch", result.stderr)
        self.assertFalse(self.archive.exists())

    def test_disabled_setup_never_downloads_and_invalid_selector_fails(self):
        self.assertEqual(self.setup_gpu(AI_GPU="0").returncode, 0)
        self.assertFalse((self.root / "downloads").exists())
        result = self.setup_gpu(AI_GPU="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AI_GPU must be 0 or 1", result.stderr)

    def test_runtime_requires_an_intact_setup_managed_chart(self):
        self.assertEqual(self.setup_gpu().returncode, 0)
        command = '. scripts/lib.sh; . scripts/gpu.sh; gpu_prepare_chart'
        self.archive.write_text("corrupt")
        result = subprocess.run(
            ["bash", "-c", command], cwd=self.root, env=self.env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AI_GPU=1 make setup", result.stderr)
