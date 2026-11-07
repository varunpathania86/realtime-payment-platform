import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.module_catalog import ROOT


class RunLocalTests(unittest.TestCase):
    def run_local(self, root, action="up", **overrides):
        scripts = root / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "lib.sh").write_text(
            f'REPO_ROOT="{root}"\nrefuse_fenced_profile() {{ return 0; }}\n'
        )
        minikube = scripts / "minikube"
        minikube.write_text('#!/bin/sh\nprintf "minikube %s\\n" "$*"\nexit "${TEST_EXIT:-0}"\n')
        minikube.chmod(0o755)
        (scripts / "run-local.sh").write_text((ROOT / "scripts/run-local.sh").read_text())
        (scripts / "local-dev.sh").write_text(
            'printf "%s %s %s %s" "$1" "$MINIKUBE_PROFILE" "$MINIKUBE_CPUS" "$MINIKUBE_MEMORY"\n'
            'exit "${TEST_EXIT:-0}"\n'
        )
        env = {key: value for key, value in os.environ.items()
                if key not in {"MINIKUBE_PROFILE", "MINIKUBE_CPUS", "MINIKUBE_MEMORY"}}
        return subprocess.run(
            ["bash", str(scripts / "run-local.sh"), action],
            env={**env, "PATH": str(scripts) + os.pathsep + env["PATH"], **overrides},
            text=True, capture_output=True, timeout=10,
        )

    def test_defaults_enable_full_single_cluster_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_local(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("up rpp-dev-single 6 16384", result.stdout)
            settings = json.loads((root / ".local/rpp-dev-single/events.tfvars.json").read_text())
            self.assertEqual(settings, {
                **{f"{feature}_enabled": True for feature in ("streaming", "analytics", "lakehouse", "bi", "ai")},
                "kafka_host_port": 19092,
            })

    def test_existing_settings_and_explicit_budget_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".local/my-dev"
            state.mkdir(parents=True)
            config = state / "events.tfvars.json"
            original = '{"ai_enabled": true, "analytics_enabled": true}\n'
            config.write_text(original)
            result = self.run_local(root, MINIKUBE_PROFILE="my-dev", MINIKUBE_CPUS="4", MINIKUBE_MEMORY="10240")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("up my-dev 4 10240", result.stdout)
            self.assertEqual(config.read_text(), original)

    def test_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_local(Path(directory), TEST_EXIT="7")
            self.assertEqual(result.returncode, 7)
            self.assertNotIn("Browser access:", result.stdout)

    def test_invalid_profile_cannot_write_outside_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_local(root, MINIKUBE_PROFILE="../outside")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / ".local").exists())

    def test_stop_preserves_profile_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_local(root)
            result = self.run_local(root, "stop")
            self.assertEqual(result.returncode, 0)
            self.assertIn("minikube stop -p rpp-dev-single", result.stdout)
            self.assertTrue((root / ".local/rpp-dev-single/events.tfvars.json").exists())

    def test_clean_removes_only_selected_profile_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_local(root)
            other = root / ".local/other-profile"
            other.mkdir()
            result = self.run_local(root, "clean")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("minikube delete -p rpp-dev-single", result.stdout)
            self.assertFalse((root / ".local/rpp-dev-single").exists())
            self.assertTrue(other.exists())

    def test_failed_cluster_deletion_preserves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_local(root)
            result = self.run_local(root, "clean", TEST_EXIT="7")
            self.assertEqual(result.returncode, 7)
            self.assertTrue((root / ".local/rpp-dev-single").exists())

    def test_clean_refuses_symlinked_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".local").mkdir()
            target = root / "preserve"
            target.mkdir()
            (root / ".local/rpp-dev-single").symlink_to(target, target_is_directory=True)
            result = self.run_local(root, "clean")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("minikube delete", result.stdout)
            self.assertTrue(target.exists())
