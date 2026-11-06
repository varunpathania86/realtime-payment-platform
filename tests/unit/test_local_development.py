import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_local_chart import validate
from scripts.module_catalog import ROOT, local_modules
from scripts.smoke import check, wait_check


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

    def test_init_containers_are_restricted_but_need_no_http_probes(self):
        pod = self.deployment["spec"]["template"]["spec"]
        migration = copy.deepcopy(pod["containers"][0])
        del migration["readinessProbe"]
        del migration["livenessProbe"]
        pod["initContainers"] = [migration]
        validate([self.deployment])
        migration["imagePullPolicy"] = "Always"
        with self.assertRaisesRegex(ValueError, "loaded images"):
            validate([self.deployment])

    def test_cronjob_pods_are_restricted_but_need_no_probes(self):
        pod = copy.deepcopy(self.deployment["spec"]["template"]["spec"])
        del pod["containers"][0]["readinessProbe"]
        del pod["containers"][0]["livenessProbe"]
        cronjob = {"kind": "CronJob", "spec": {"jobTemplate": {"spec": {"template": {"spec": pod}}}}}
        validate([cronjob])
        pod["securityContext"] = {}
        with self.assertRaisesRegex(ValueError, "non-root"):
            validate([cronjob])

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
        for name in ("local-dev.sh", "lib.sh", "module_catalog.py", "smoke.py", "local_database.py",
                    "streaming-control.py"):
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
        (module / "chart" / "values.yaml").write_text("{}\n")
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
if name == "kubectl" and "get" in sys.argv and os.environ.get("DEPLOYED_FLINK_IMAGE"):
    print(json.dumps({"spec":{"template":{"spec":{"containers":[{"image":os.environ["DEPLOYED_FLINK_IMAGE"]}]}}}}))
if name == "kubectl" and "port-forward" in sys.argv:
    if os.environ.get("FAIL_FORWARD"):
        print("deliberate forwarding failure", file=sys.stderr)
        sys.exit(9)
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if os.environ.get("FAIL_SMOKE"):
                self.send_error(503)
                return
            if self.path == "/api/version":
                body = b'{"version":"dev-test","commit":"test"}'
            elif self.path.startswith("/api/"):
                body = b'{"status":"ok"}'
            else:
                body = b'<html><div id="root"></div></html>'
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if self.path.startswith("/api/") else "text/html")
            self.end_headers()
            self.wfile.write(body)
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        pathlib.Path(os.environ["COMMAND_LOG"] + ".pid").write_text(str(os.getpid()))
        print(f"Forwarding from 127.0.0.1:{server.server_port} -> 80", flush=True)
        server.serve_forever()
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

    def test_smoke_uses_assigned_port_without_interrupting_existing_listener(self):
        self.assertEqual(self.run_command("images").returncode, 0)
        inventory = self.root / ".local" / "isolated" / "images.tfvars.json"
        inventory.write_text(json.dumps({"local_images": {"payment-api": "rpp-local/payment-api:dev-test"}}))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = str(listener.getsockname()[1])
            for settings in ({}, {"FAIL_SMOKE": "1"}, {"FAIL_FORWARD": "1"}):
                with self.subTest(settings=settings):
                    result = self.run_command("smoke", LOCAL_PORT=port, **settings)
                    if settings:
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn(
                            "deliberate forwarding failure" if "FAIL_FORWARD" in settings else "503",
                            result.stderr,
                        )
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn("Smoke passed", result.stdout)
                    with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
                        pass
                    pid = int(Path(str(self.log) + ".pid").read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        forwards = [call for call in calls if call[0] == "kubectl"]
        self.assertEqual(len(forwards), 3)
        for call in forwards:
            self.assertIn(":80", call)
            self.assertNotIn(f"{port}:80", call)
            self.assertEqual(call[call.index("--address") + 1], "127.0.0.1")

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

    def test_catalog_dependencies_are_build_contexts_and_image_identity(self):
        library = self.root / "modules" / "library"
        library.mkdir()
        (library / "module.yaml").write_text("name: library\ntype: library\n")
        (library / "code.py").write_text("VALUE = 1\n")
        (self.source.parent / "module.yaml").write_text("name: demo\ntype: library\ndependencies: [library]\n")
        self.assertEqual(self.run_command("images").returncode, 0)
        first = self.inventory()
        builds = [json.loads(line) for line in self.log.read_text().splitlines()]
        build = next(call for call in builds if call[:2] == ["docker", "build"])
        self.assertEqual(build[build.index("--build-context") + 1], "library=modules/library")
        (library / "code.py").write_text("VALUE = 2\n")
        self.assertEqual(self.run_command("images").returncode, 0)
        self.assertNotEqual(self.inventory(), first)

    def test_build_and_load_failures_surface_without_inventory(self):
        for failure in ("FAIL_BUILD", "FAIL_LOAD"):
            with self.subTest(failure=failure):
                result = self.run_command("images", **{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("deliberate", result.stderr)
                self.assertFalse((self.root / ".local" / "isolated" / "images.tfvars.json").exists())
                self.assertEqual(list((self.root / ".local" / "isolated").glob("images.*")), [])

    def test_stateful_guard_precedes_build_and_load_and_keeps_previous_inventory(self):
        flink = self.root / "modules" / "stream-processor"
        shutil.copytree(self.source.parent, flink)
        (flink / "module.yaml").write_text("name: stream-processor\ntype: library\n")
        (self.root / "solution.yaml").write_text("modules: [demo, stream-processor]\n")
        state = self.root / ".local" / "isolated"
        state.mkdir(parents=True)
        (state / "events.tfvars.json").write_text('{"streaming_enabled":true}\n')
        previous = {"local_images": {"stream-processor": "rpp-local/stream-processor:dev-old"}}
        (state / "images.tfvars.json").write_text(json.dumps(previous))
        result = self.run_command("images", DEPLOYED_FLINK_IMAGE=previous["local_images"]["stream-processor"],
            STREAMING_RESTORE_PATH="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stateful image changed", result.stderr)
        self.assertEqual(self.inventory(), previous)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertFalse(any(call[0] in ("docker", "minikube", "terraform") for call in calls))

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

    def test_external_kafka_vars_are_used_for_plan_and_destroy(self):
        self.assertEqual(self.run_command("images").returncode, 0)
        variables = self.root / ".local" / "isolated" / "events.tfvars.json"
        variables.write_text('{"external_kafka":{}}\n')
        for action in ("apply", "down"):
            result = self.run_command(action)
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        plans = [call for call in calls if call[0] == "terraform" and call[2] in ("plan", "destroy")]
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(f"-var-file={variables}" in call for call in plans))

    def test_individual_dev_commands_refuse_fenced_former_primary(self):
        state = self.root / ".local/lab"
        state.mkdir(parents=True)
        (state / "owned").write_text("lab\n")
        (state / "database-fence.json").write_text('{"old":"pg-a","phase":"complete"}')
        for action in ("up", "update", "images", "apply", "down"):
            result = self.run_command(action, MINIKUBE_PROFILE="lab-a")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is fenced", result.stderr)


class MultiClusterCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        for name in ("multi-cluster.sh", "setup-multicluster.sh", "lib.sh", "topology_config.py", "database_switchover.py"):
            shutil.copy(ROOT / "scripts" / name, self.root / "scripts" / name)
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        shutil.copy(ROOT / ".tool-versions", self.root / ".tool-versions")
        local = self.root / "scripts" / "local-dev.sh"
        local.write_text("#!/bin/sh\nexit 0\n")
        local.chmod(0o755)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        fake = """#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ["HOME"])
network = root / "network"
with open(root / "commands.jsonl", "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "cat":
    if args == ["/proc/sys/fs/inotify/max_user_instances"]:
        print(os.environ.get("TEST_INOTIFY", "1024"))
    else:
        os.execv("/bin/cat", ["/bin/cat", *args])
elif name == "docker":
    if args[0] == "inspect":
        print(args[1])
    elif args[:2] == ["network", "create"]:
        network.touch()
    elif args[:2] == ["network", "inspect"]:
        if not network.exists():
            sys.exit(1)
        print("172.18.0.1" if "Gateway" in args[-1] else os.environ.get("NETWORK_OWNER", "learning"))
    elif args[:2] == ["network", "rm"]:
        network.unlink()
elif name == "minikube" and "ip" in args:
    profile = args[args.index("-p") + 1]
    print("172.18.0." + str(2 + "abc".index(profile[-1])))
elif name == "kubectl" and "pg_control_system()" in args[-1]:
    print("42")
elif name == "kubectl" and "jsonpath={.status.currentPrimary}" in args:
    print(args[args.index("cluster") + 1] + "-2")
elif name == "terraform":
    if os.environ.get("FAIL_TF"):
        print("deliberate Terraform failure", file=sys.stderr)
        sys.exit(9)
    if "output" in args:
        print('{"a":{},"b":{},"c":{}}' if "workload_vars" in args else '{"external_kafka":{"brokers":"test"}}')
"""
        for name in ("docker", "minikube", "terraform", "kubectl", "cat", "sudo"):
            path = self.bin / name
            path.write_text(fake)
            path.chmod(0o755)
        self.env = {
            **os.environ, "HOME": str(self.root), "MINIKUBE_HOME": str(self.root),
            "BIN_DIR": str(self.bin), "DEV_PROFILE": "learning", "MULTI_FULL_STACK": "0",
        }

    def run_command(self, action, **env):
        return subprocess.run(
            ["bash", "scripts/multi-cluster.sh", action], cwd=self.root,
            env={**self.env, **env}, capture_output=True, text=True,
        )

    def calls(self):
        return [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]

    def test_default_base_needs_no_profile_override(self):
        self.env.pop("DEV_PROFILE")
        result = self.run_command("up", NETWORK_OWNER="rpp")
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertEqual(len(starts), 3)
        for suffix, call in zip("abc", starts):
            self.assertIn(f"rpp-{suffix}", call)
            self.assertIn("--network=rpp-net", call)
        state = self.root / ".local/rpp"
        self.assertEqual((state / "owned").read_text().strip(), "rpp")
        inventory = json.loads((state / "topology.tfvars.json").read_text())
        self.assertEqual(inventory["profiles"], ["rpp-a", "rpp-b", "rpp-c"])

    def test_three_fixed_budgets_and_distinct_static_ips(self):
        result = self.run_command("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertEqual(len(starts), 3)
        for i, call in enumerate(starts):
            self.assertIn(f"learning-{'abc'[i]}", call)
            for flag in ("--cpus=2", "--memory=3072", "--network=learning-net", f"--static-ip=172.18.0.{i + 2}"):
                self.assertIn(flag, call)
        quotas = [call for call in self.calls() if call[:2] == ["docker", "update"]]
        self.assertEqual(quotas, [
            ["docker", "update", "--cpus=2", "--memory=3072m", "--memory-swap=6144m", f"learning-{suffix}"]
            for suffix in "abc"
        ])
        (self.root / "commands.jsonl").write_text("")
        result = self.run_command("metrics")
        self.assertEqual(result.returncode, 0, result.stderr)
        contexts = [call[call.index("--context") + 1] for call in self.calls() if call[0] == "kubectl"]
        self.assertEqual(contexts, ["learning-a", "learning-b", "learning-c"])
        addons = [call for call in self.calls() if call[0] == "minikube"]
        self.assertEqual(addons, [
            ["minikube", "-p", f"learning-{suffix}", "addons", "enable", "metrics-server"]
            for suffix in "abc"
        ])
        variables = self.root / ".local" / "learning-a" / "events.tfvars.json"
        self.assertEqual(variables.stat().st_mode & 0o777, 0o600)

    def test_existing_unowned_profile_or_network_is_refused(self):
        existing = self.root / ".minikube" / "profiles" / "learning-b"
        existing.mkdir(parents=True)
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unowned profile", result.stderr)
        existing.rmdir()
        application = self.root / ".local" / "learning-a"
        application.mkdir(parents=True)
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unowned application state", result.stderr)
        application.rmdir()
        (self.root / "network").touch()
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unowned Docker network", result.stderr)

    def test_persisted_budget_updates_owned_nodes_without_restarting(self):
        source = self.root / "configuration.json"
        source.write_text('{"node":{"cpus":2.5,"memory_mib":4096}}')
        result = self.run_command("up", DEV_CONFIG=str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertTrue(all("--cpus=2.5" in call and "--memory=4096" in call for call in starts))
        (self.root / "commands.jsonl").write_text("")
        result = self.run_command("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[:2] == ["minikube", "start"] for call in self.calls()))
        quotas = [call for call in self.calls() if call[:2] == ["docker", "update"]]
        self.assertEqual(quotas, [
            ["docker", "update", "--cpus=2.5", "--memory=4096m", "--memory-swap=8192m", f"learning-{suffix}"]
            for suffix in "abc"
        ])

    def test_new_base_defaults_to_full_stack_and_legacy_base_is_preserved(self):
        self.env.pop("MULTI_FULL_STACK")
        result = self.run_command("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        mode = self.root / ".local/learning/full-stack"
        self.assertTrue(mode.exists())
        mode.unlink()
        result = self.run_command("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(mode.exists())
        self.assertNotIn("full_stack", json.loads(
            (self.root / ".local/learning/topology.tfvars.json").read_text(),
        ))

    def test_invalid_budget_refuses_infrastructure_changes(self):
        source = self.root / "configuration.json"
        source.write_text('{"node":{"cpus":0}}')
        result = self.run_command("up", DEV_CONFIG=str(source))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("node.cpus", result.stderr)
        self.assertEqual(
            [call for call in self.calls() if call[0] != "cat"],
            [["docker", "network", "inspect", "learning-net"]],
        )

    def test_full_stack_conversion_requires_a_separate_lab(self):
        self.assertEqual(self.run_command("up").returncode, 0)
        result = self.run_command("up", MULTI_FULL_STACK="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("in-place conversion", result.stderr)

    def test_completed_teardown_allows_owned_base_to_be_recreated_in_full_mode(self):
        for action in ("up", "down", "cluster-down"):
            result = self.run_command(action)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_command("up", MULTI_FULL_STACK="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".local/learning/full-stack").exists())

    def test_invalid_full_stack_flag_is_explicit(self):
        result = self.run_command("up", MULTI_FULL_STACK="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be 0 or 1", result.stderr)

    def test_full_stack_deploys_all_profiles_and_fence_preserves_stopped_member(self):
        result = self.run_command("up", MULTI_FULL_STACK="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.root / ".local/learning"
        for suffix in "abc":
            self.assertTrue((self.root / f".local/learning-{suffix}/events.tfvars.json").exists())
        self.assertTrue(json.loads((state / "topology.tfvars.json").read_text())["full_stack"])
        (state / "database-fence.json").write_text(json.dumps({
            "old": "pg-a", "target": "pg-b", "phase": "complete",
        }))
        (state / "database-role.json").write_text('{"database_primary":"pg-b"}')
        (self.root / "commands.jsonl").write_text("")
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Fenced member", result.stderr)
        self.assertFalse(any(call[:2] == ["minikube", "start"] for call in self.calls()))
        result = self.run_command("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        plans = [call for call in self.calls() if call[0] == "terraform" and "plan" in call]
        self.assertEqual(len(plans), 1)
        self.assertIn("-target=module.b", plans[0])
        self.assertIn("-target=module.database_b[0]", plans[0])
        self.assertIn("-target=module.c", plans[0])
        self.assertNotIn("-target=module.a", plans[0])
        self.assertFalse(any(call[0] == "minikube" and "learning-a" in call for call in self.calls()))
        self.assertEqual(json.loads((state / "topology.tfvars.json").read_text())["database_primary"], "pg-b")

    def test_pending_transition_and_rebuild_refuse_ordinary_update(self):
        self.assertEqual(self.run_command("up", MULTI_FULL_STACK="1").returncode, 0)
        state = self.root / ".local/learning"
        journal = state / "database-transition.json"
        journal.write_text("{}")
        result = self.run_command("update")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pending", result.stderr)
        journal.unlink()
        (state / "database-fence.json").write_text('{"old":"pg-a","phase":"rebuild-start"}')
        before = (state / "topology.tfvars.json").read_text()
        result = self.run_command("update")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pending database failover/rejoin", result.stderr)
        self.assertEqual((state / "topology.tfvars.json").read_text(), before)

    def test_resumed_rejoin_keeps_fenced_profile_identity_through_startup(self):
        self.assertEqual(self.run_command("up", MULTI_FULL_STACK="1").returncode, 0)
        state = self.root / ".local/learning"
        (state / "database-fence.json").write_text(json.dumps({
            "old": "pg-b", "target": "pg-c", "phase": "rebuild-start",
        }))
        (state / "database-role.json").write_text(json.dumps({
            "database_primary": "pg-c", "bootstrap_sources": {"pg-b": "pg-c"},
        }))
        (self.root / "commands.jsonl").write_text("")
        result = self.run_command("rejoin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((state / "database-fence.json").exists())
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertEqual(len(starts), 1)
        self.assertIn("learning-b", starts[0])
        addons = [call for call in self.calls() if call[0] == "minikube" and "addons" in call]
        self.assertEqual(addons, [["minikube", "-p", "learning-b", "addons", "enable", "metrics-server"]])
        queries = [call for call in self.calls() if call[0] == "kubectl" and "pg_control_system()" in call[-1]]
        self.assertEqual([call[call.index("--context") + 1] for call in queries], ["learning-c", "learning-b"])
        self.assertIn("pg-b-2", queries[-1])

    def test_low_inotify_and_terraform_failures_are_explicit(self):
        result = self.run_command("up", TEST_INOTIFY="128")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inotify", result.stderr)
        result = self.run_command("up", FAIL_TF="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deliberate Terraform failure", result.stderr)
        self.assertFalse((self.root / ".local" / "learning-a" / "events.tfvars.json").exists())

    def test_setup_only_tunes_low_limit_when_explicitly_enabled(self):
        for enabled, limit, changes in (("0", "128", False), ("1", "128", True), ("1", "2048", False)):
            with self.subTest(enabled=enabled, limit=limit):
                (self.root / "commands.jsonl").write_text("")
                result = subprocess.run(
                    ["bash", "scripts/setup-multicluster.sh"], cwd=self.root,
                    env={
                        **self.env, "PATH": f"{self.bin}:{os.environ['PATH']}",
                        "MULTI_CLUSTER": enabled, "TEST_INOTIFY": limit,
                    },
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                writes = [call for call in self.calls() if call[0] == "sudo"]
                self.assertEqual(bool(writes), changes)
                if changes:
                    self.assertEqual(writes, [
                        ["sudo", "tee", "/etc/sysctl.d/90-rpp-multicluster.conf"],
                        ["sudo", "sysctl", "-p", "/etc/sysctl.d/90-rpp-multicluster.conf"],
                    ])

    def test_cluster_deletion_refuses_changed_network_ownership_and_live_state(self):
        self.assertEqual(self.run_command("up").returncode, 0)
        result = self.run_command("cluster-down", NETWORK_OWNER="somebody-else")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ownership mismatch", result.stderr)
        state = self.root / ".local" / "learning" / "terraform.tfstate"
        state.write_text(json.dumps({"resources": [{"mode": "managed", "type": "kubernetes_namespace_v1"}]}))
        result = self.run_command("cluster-down")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dev-down", result.stderr)
        self.assertFalse(any(call[:2] == ["minikube", "delete"] for call in self.calls()))


class SmokeTests(unittest.TestCase):
    def test_ingress_propagation_is_retried_with_a_deadline(self):
        from urllib.error import HTTPError

        with (
            patch("scripts.smoke.check", side_effect=[HTTPError("local", 503, "not ready", {}, None), None]) as run,
            patch("scripts.smoke.time.sleep") as sleep,
        ):
            wait_check("http://localhost", {}, 30)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].kwargs["deadline"], run.call_args_list[1].kwargs["deadline"])
        sleep.assert_called_once()

    def test_ingress_wait_fails_at_deadline_and_does_not_retry_contract_errors(self):
        from urllib.error import HTTPError

        for error in (HTTPError("local", 503, "not ready", {}, None), ValueError("wrong API build")):
            with (
                self.subTest(error=type(error).__name__),
                patch("scripts.smoke.check", side_effect=error) as run,
                patch("scripts.smoke.time.monotonic", side_effect=[0, 31]),
                patch("scripts.smoke.time.sleep") as sleep,
            ):
                with self.assertRaises(type(error)):
                    wait_check("http://localhost", {}, 30)
                run.assert_called_once()
                sleep.assert_not_called()

    def test_stale_api_build_fails_even_when_endpoints_are_healthy(self):
        from io import BytesIO

        responses = [
            BytesIO(b'{"status":"ok"}'), BytesIO(b'{"status":"ok"}'),
            BytesIO(b'{"version":"dev-old","commit":"abc"}'),
        ]
        with patch("scripts.smoke.urlopen", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "wrong API build"):
                check("http://localhost", {"local_images": {"payment-api": "rpp-local/payment-api:dev-new"}})
