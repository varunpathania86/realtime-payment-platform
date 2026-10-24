import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_local_chart import validate
from scripts.module_catalog import ROOT, local_modules
from scripts.smoke import check


class LocalDiscoveryTests(unittest.TestCase):
    def test_only_catalog_modules_with_images_and_charts_are_runnable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "solution.yaml").write_text("modules: [runtime, contract, catalog-only]\n")
            for name in ("runtime", "contract", "catalog-only", "unlisted"):
                module = root / "modules" / name
                module.mkdir(parents=True)
                (module / "module.yaml").write_text(f"name: {name}\ntype: library\n")
                if name != "catalog-only":
                    (module / "Dockerfile").touch()
                if name in ("runtime", "unlisted"):
                    (module / "chart").mkdir()
                    (module / "chart" / "Chart.yaml").touch()
            self.assertEqual(local_modules(root), ["runtime"])


class LocalChartTests(unittest.TestCase):
    def setUp(self):
        self.deployment = {
            "kind": "Deployment",
            "spec": {"template": {"spec": {
                "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True},
                "containers": [{
                    "securityContext": {
                        "readOnlyRootFilesystem": True,
                        "allowPrivilegeEscalation": False,
                    },
                    "readinessProbe": {"httpGet": {"path": "/readyz"}},
                    "livenessProbe": {"httpGet": {"path": "/healthz"}},
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "32Mi"},
                        "limits": {"cpu": "250m", "memory": "64Mi"},
                    },
                    "imagePullPolicy": "Never",
                }],
            }}},
        }

    def test_contract_accepts_local_deployment(self):
        validate([None, self.deployment])

    def test_missing_deployment_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no Deployment"):
            validate([{"kind": "Service"}])

    def test_unsafe_or_incomplete_containers_are_rejected(self):
        for field, value in (
            ("imagePullPolicy", "Always"),
            ("readinessProbe", None),
            ("livenessProbe", None),
            ("securityContext", {"readOnlyRootFilesystem": False}),
            ("resources", {"requests": {}, "limits": {}}),
        ):
            with self.subTest(field=field):
                item = copy.deepcopy(self.deployment)
                item["spec"]["template"]["spec"]["containers"][0][field] = value
                with self.assertRaises(ValueError):
                    validate([item])


class LocalCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        for name in ("local-dev.sh", "lib.sh", "module_catalog.py"):
            shutil.copy(ROOT / "scripts" / name, self.root / "scripts" / name)
        shutil.copy(ROOT / ".tool-versions", self.root / ".tool-versions")
        (self.root / ".venv" / "bin").mkdir(parents=True)
        python = self.root / ".venv" / "bin" / "python"
        python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
        python.chmod(0o755)
        (self.root / ".gitignore").write_text(".local/\n.venv/\n__pycache__/\nbin/\n")
        module = self.root / "modules" / "demo"
        (module / "chart").mkdir(parents=True)
        (module / "module.yaml").write_text("name: demo\ntype: library\n")
        (module / "Dockerfile").write_text("FROM scratch\n")
        (module / "chart" / "Chart.yaml").write_text("name: demo\n")
        (self.root / "solution.yaml").write_text("modules: [demo]\n")
        self.source = module / "source.txt"
        self.source.write_text("first\n")
        for args in (
            ["init", "-q"], ["add", "."],
            ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"],
        ):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        fake = """#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
if name == "docker" and os.environ.get("FAIL_BUILD"):
    print("deliberate build failure", file=sys.stderr)
    sys.exit(7)
if name == "minikube" and os.environ.get("FAIL_LOAD"):
    print("deliberate image load failure", file=sys.stderr)
    sys.exit(8)
"""
        for name in ("docker", "minikube", "terraform", "kubectl"):
            path = self.bin / name
            path.write_text(fake)
            path.chmod(0o755)
        self.log = self.root / "commands.jsonl"
        self.env = {
            **os.environ,
            "BIN_DIR": str(self.bin),
            "MINIKUBE_PROFILE": "isolated",
            "COMMAND_LOG": str(self.log),
        }
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "PUBLICATION_CONTEXT", "LOCAL_PORT"):
            self.env.pop(name, None)

    def run_command(self, action, **env):
        return subprocess.run(
            ["bash", "scripts/local-dev.sh", action], cwd=self.root,
            env={**self.env, **env}, capture_output=True, text=True,
        )

    def inventory(self):
        return json.loads((self.root / ".local" / "isolated" / "images.tfvars.json").read_text())

    def test_fixture_python_preserves_test_runner_environment(self):
        result = subprocess.run(
            [
                str(self.root / ".venv" / "bin" / "python"), "-c",
                "import sys, yaml; print(sys.prefix)",
            ],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), sys.prefix)

    def test_local_images_need_no_release_credentials_and_detect_dirty_source(self):
        result = self.run_command("images")
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.inventory()
        result = self.run_command("images")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.inventory(), first)
        generated = self.source.parent / "bin"
        generated.mkdir()
        (generated / "output").write_text("ignored build output\n")
        result = self.run_command("images")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.inventory(), first)
        self.source.write_text("second\n")
        result = self.run_command("images")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.inventory(), first)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(any(call[:4] == ["minikube", "-p", "isolated", "image"] for call in calls))
        self.assertTrue(all("--push" not in call for call in calls))

    def test_untracked_source_and_tool_pin_changes_change_image_identity(self):
        self.assertEqual(self.run_command("images").returncode, 0)
        first = self.inventory()
        (self.source.parent / "new-source.txt").write_text("new untracked source\n")
        self.assertEqual(self.run_command("images").returncode, 0)
        second = self.inventory()
        self.assertNotEqual(first, second)
        with (self.root / ".tool-versions").open("a") as pins:
            pins.write("# changed tool policy\n")
        self.assertEqual(self.run_command("images").returncode, 0)
        self.assertNotEqual(second, self.inventory())

    def test_build_and_load_failures_surface_without_inventory(self):
        for failure in ("FAIL_BUILD", "FAIL_LOAD"):
            with self.subTest(failure=failure):
                result = self.run_command("images", **{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("deliberate", result.stderr)
                self.assertFalse((self.root / ".local" / "isolated" / "images.tfvars.json").exists())
                self.assertEqual(list((self.root / ".local" / "isolated").glob("images.*")), [])

    def test_plan_requires_local_images(self):
        result = self.run_command("plan")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("make dev-images", result.stderr)

    def test_invalid_profile_and_port_are_rejected_before_invoking_tools(self):
        for settings in ({"MINIKUBE_PROFILE": "../other"}, {"LOCAL_PORT": "0"}, {"LOCAL_PORT": "65536"}):
            with self.subTest(settings=settings):
                result = self.run_command("status", **settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_apply_uses_a_saved_plan_and_explicit_context(self):
        self.assertEqual(self.run_command("images").returncode, 0)
        result = self.run_command("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        terraform = [call for call in calls if call[0] == "terraform"]
        self.assertEqual(terraform[0][2], "plan")
        self.assertEqual(terraform[1][2], "apply")
        self.assertTrue(terraform[1][-1].endswith("/isolated/tfplan"))


class SmokeTests(unittest.TestCase):
    def test_stale_api_build_fails_even_when_endpoints_are_healthy(self):
        from io import BytesIO

        responses = [
            BytesIO(b'{"status":"ok"}'), BytesIO(b'{"status":"ok"}'),
            BytesIO(b'{"version":"dev-old","commit":"abc"}'),
        ]
        with patch("scripts.smoke.urlopen", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "wrong API build"):
                check("http://localhost", {"local_images": {"payment-api": "rpp-local/payment-api:dev-new"}})
