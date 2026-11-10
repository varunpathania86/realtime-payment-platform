import copy
import hashlib
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
        for name in ("local-dev.sh", "gpu.sh", "lib.sh", "module_catalog.py", "smoke.py", "local_database.py",
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
if name == "docker" and sys.argv[1:] == ["info", "--format", "{{json .Runtimes}}"]:
    print('{"nvidia":{}}')
if name == "docker" and sys.argv[1:3] == ["container", "inspect"]:
    sys.exit(1)
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

    def test_gpu_build_is_opt_in_persisted_and_has_a_distinct_image_tag(self):
        module = self.root / "modules/llm-serving"
        shutil.copytree(self.source.parent, module)
        (module / "module.yaml").write_text("name: llm-serving\ntype: library\n")
        (self.root / "solution.yaml").write_text("modules: [llm-serving]\n")
        (self.root / "scripts/build-minio.sh").write_text("#!/bin/sh\nexit 0\n")
        driver = self.bin / "nvidia-smi"
        driver.write_text("#!/bin/sh\necho 'RTX A2000, 8192 MiB'\n")
        driver.chmod(0o755)
        archive = self.root / ".local/tools/nvidia-device-plugin-0.20.1.tgz"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"fixture chart")
        pins = self.root / ".tool-versions"
        old_checksum = next(line.split()[1] for line in pins.read_text().splitlines()
                            if line.startswith("nvidia-device-plugin-chart-sha256 "))
        pins.write_text(pins.read_text().replace(old_checksum, hashlib.sha256(archive.read_bytes()).hexdigest()))
        settings = self.root / ".local/isolated/events.tfvars.json"
        settings.parent.mkdir()
        settings.write_text('{"ai_enabled":true}\n')
        result = self.run_command("images", AI_GPU="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        cpu = self.inventory()["local_images"]["llm-serving"]
        result = self.run_command("images", AI_GPU="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        gpu = self.inventory()["local_images"]["llm-serving"]
        self.assertNotEqual(cpu, gpu)
        self.assertTrue(json.loads(settings.read_text())["ai_gpu_enabled"])
        commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        builds = [call for call in commands if call[:2] == ["docker", "build"]]
        self.assertIn("LLAMA_CPP_VERSION=server-b12124", builds[0])
        self.assertIn("LLAMA_CPP_VERSION=server-cuda-b12124", builds[1])
        self.assertEqual(self.run_command("images").returncode, 0)
        self.assertEqual(self.inventory()["local_images"]["llm-serving"], gpu)
        self.assertEqual(self.run_command("images", AI_GPU="0").returncode, 0)
        self.assertEqual(self.inventory()["local_images"]["llm-serving"], cpu)

    def test_gpu_requires_ai_and_boolean_settings_before_building(self):
        settings = self.root / ".local/isolated/events.tfvars.json"
        settings.parent.mkdir(parents=True)
        for data, variables in (
            ({"ai_enabled": False}, {"AI_GPU": "1"}),
            ({"ai_enabled": True, "ai_gpu_enabled": "true"}, {}),
            ({"ai_enabled": False, "ai_gpu_enabled": True}, {}),
            ({"ai_enabled": True}, {"AI_GPU": "yes"}),
        ):
            settings.write_text(json.dumps(data))
            result = self.run_command("images", **variables)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("GPU", result.stderr.upper())
            self.assertFalse(self.log.exists())

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
        state = self.root / ".local/lab-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("lab\n")
        (state / "cluster-identity").write_text("numbered-v1\n")
        (state / "database-fence.json").write_text('{"old":"pg-1","phase":"complete"}')
        for action in ("up", "update", "images", "apply", "down"):
            result = self.run_command(action, MINIKUBE_PROFILE="lab-1")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is fenced", result.stderr)

    def test_individual_dev_commands_refuse_legacy_fence_identity(self):
        state = self.root / ".local/lab-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("lab\n")
        (state / "database-fence.json").write_text('{"old":"pg-a","phase":"complete"}')
        for action in ("up", "update", "images", "apply", "down"):
            result = self.run_command(action, MINIKUBE_PROFILE="lab-1")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("legacy", result.stderr.lower())

    def test_individual_dev_commands_refuse_invalid_numbered_fence(self):
        state = self.root / ".local/lab-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("lab\n")
        (state / "cluster-identity").write_text("numbered-v1\n")
        (state / "database-fence.json").write_text('{"old":"pg-a","phase":"complete"}')
        for profile in ("lab-1", "lab-2", "lab-3"):
            result = self.run_command("update", MINIKUBE_PROFILE=profile)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid fenced database identity", result.stderr)

    def test_single_profile_refuses_legacy_shared_state(self):
        state = self.root / ".local/rpp"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        state_file = state / "terraform.tfstate"
        original = '{"resources":[{"mode":"managed"}]}'
        state_file.write_text(original)
        for action in ("up", "update", "images", "apply", "down"):
            result = self.run_command(action, MINIKUBE_PROFILE="rpp")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("legacy multi-cluster state", result.stderr)
            self.assertEqual(state_file.read_text(), original)
        self.assertFalse(self.log.exists())


class MultiClusterCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        for name in (
            "multi-cluster.sh", "setup-multicluster.sh", "gpu.sh", "lib.sh", "topology_config.py",
            "topology_identity.py", "database_switchover.py",
        ):
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
    if args[0] == "info":
        print('{"nvidia":{}}')
    elif args[:2] == ["container", "inspect"]:
        sys.exit(1)
    elif args[0] == "inspect":
        print(args[1])
    elif args[:2] == ["network", "create"]:
        network.touch()
    elif args[:2] == ["network", "inspect"]:
        if not network.exists():
            sys.exit(1)
        print("172.18.0.1" if "Gateway" in args[-1] else os.environ.get("NETWORK_OWNER", "rpp"))
    elif args[:2] == ["network", "rm"]:
        network.unlink()
elif name == "minikube" and args[:1] == ["status"] and os.environ.get("FAIL_STATUS") == args[-1]:
    sys.exit(1)
elif name == "minikube" and "ip" in args:
    profile = args[args.index("-p") + 1]
    print("172.18.0." + str(2 + "123".index(profile[-1])))
elif name == "kubectl" and "pg_control_system()" in args[-1]:
    print("42")
elif name == "kubectl" and "jsonpath={.status.currentPrimary}" in args:
    print(args[args.index("cluster") + 1] + "-2")
elif name == "terraform":
    if os.environ.get("FAIL_TF"):
        print("deliberate Terraform failure", file=sys.stderr)
        sys.exit(9)
    if "output" in args:
        print('{"1":{},"2":{},"3":{}}' if "workload_vars" in args else '{"external_kafka":{"brokers":"test"}}')
"""
        for name in ("docker", "minikube", "terraform", "kubectl", "cat", "sudo", "nvidia-smi"):
            path = self.bin / name
            path.write_text(fake)
            path.chmod(0o755)
        self.env = {
            **os.environ, "HOME": str(self.root), "MINIKUBE_HOME": str(self.root),
            "BIN_DIR": str(self.bin), "MULTI_FULL_STACK": "0",
        }

    def run_command(self, action, **env):
        return subprocess.run(
            ["bash", "scripts/multi-cluster.sh", action], cwd=self.root,
            env={**self.env, **env}, capture_output=True, text=True,
        )

    def calls(self):
        return [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]

    def test_only_cluster_1_gets_gpu_passthrough_and_builtin_plugin_is_disabled(self):
        config = json.loads((ROOT / "templates/ai-topology.json").read_text())
        config["ai_gpu"] = True
        source = self.root / "gpu-topology.json"
        source.write_text(json.dumps(config))
        archive = self.root / ".local/tools/nvidia-device-plugin-0.20.1.tgz"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"fixture chart")
        versions = self.root / ".tool-versions"
        checksum = next(line.split()[1] for line in versions.read_text().splitlines()
                        if line.startswith("nvidia-device-plugin-chart-sha256 "))
        versions.write_text(versions.read_text().replace(checksum, hashlib.sha256(archive.read_bytes()).hexdigest()))
        result = self.run_command("up", DEV_CONFIG=str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertEqual(len(starts), 3)
        self.assertIn("--gpus=all", starts[0])
        self.assertIn("--container-runtime=docker", starts[0])
        for call in starts[1:]:
            self.assertNotIn("--gpus=all", call)
        disables = [call for call in self.calls() if "addons" in call and "disable" in call]
        self.assertEqual(disables, [["minikube", "-p", "rpp-1", "addons", "disable", "nvidia-device-plugin"]])

    def test_legacy_identity_refuses_mutating_topology_commands(self):
        state = self.root / ".local/rpp-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        (state / "full-stack").touch()
        state_file = state / "terraform.tfstate"
        original = '{"resources":[{"mode":"managed"}]}'
        state_file.write_text(original)
        for action in ("up", "update", "metrics", "observability", "switchover", "failover", "rejoin", "down", "cluster-down"):
            with self.subTest(action=action):
                result = self.run_command(action, DATABASE_TARGET="pg-2")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("legacy", result.stderr.lower())
                self.assertEqual(state_file.read_text(), original)
                calls = self.calls() if (self.root / "commands.jsonl").exists() else []
                self.assertFalse(any(call[0] in ("docker", "minikube", "terraform", "kubectl") for call in calls))

    def test_old_shared_state_path_is_not_claimed_as_a_new_lab(self):
        state = self.root / ".local/rpp"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        original = '{"resources":[{"mode":"managed"}]}'
        (state / "terraform.tfstate").write_text(original)
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("legacy multi-cluster state", result.stderr)
        self.assertEqual((state / "terraform.tfstate").read_text(), original)
        self.assertFalse((self.root / ".local/rpp-multi/owned").exists())
        self.assertFalse((self.root / "commands.jsonl").exists())

    def test_unowned_topology_metadata_is_not_adopted(self):
        state = self.root / ".local/rpp-multi"
        state.mkdir(parents=True)
        for name in (
            "terraform.tfstate", "terraform.tfstate.backup", "topology.tfvars.json",
            "topology.pending.json", "configuration.json", "cluster-identity", "unknown-metadata",
            "full-stack", "database-role.json", "database-transition.json", "database-fence.json",
            "database-transition.pending", "tfplan", "database-transition.tfplan", "database-failover.tfplan",
        ):
            with self.subTest(name=name):
                metadata = state / name
                metadata.write_text("preserve")
                result = self.run_command("up")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unowned topology metadata", result.stderr)
                self.assertEqual(metadata.read_text(), "preserve")
                self.assertFalse((state / "owned").exists())
                metadata.unlink()
        self.assertFalse((self.root / "commands.jsonl").exists())

    def test_unused_terraform_cache_does_not_prevent_a_new_claim(self):
        cache = self.root / ".local/rpp-multi/terraform"
        cache.mkdir(parents=True)
        (cache / "cache-metadata").write_text("provider initialization")
        result = self.run_command("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((cache.parent / "owned").exists())

    def test_symlinked_multi_cluster_state_is_not_claimed_or_used(self):
        outside = self.root / "external-state"
        outside.mkdir()
        state = self.root / ".local/rpp-multi"
        state.parent.mkdir(parents=True)
        state.symlink_to(outside, target_is_directory=True)
        for action in ("up", "update", "down", "cluster-down"):
            with self.subTest(action=action):
                result = self.run_command(action)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlinked topology state", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.root / "commands.jsonl").exists())

    def test_unknown_identity_version_is_not_adopted(self):
        state = self.root / ".local/rpp-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        (state / "cluster-identity").write_text("numbered-v2\n")
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported cluster identity", result.stderr)
        self.assertFalse(any(call[0] in ("docker", "minikube", "terraform", "kubectl") for call in self.calls()))

    def test_stop_requires_ownership_and_preserves_state(self):
        result = self.run_command("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No owned multi-cluster topology", result.stderr)
        self.assertFalse((self.root / "commands.jsonl").exists())
        state = self.root / ".local/rpp-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        (state / "terraform.tfstate").write_text('{"resources":[{"mode":"managed"}]}')
        result = self.run_command("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["cat", str(state / "owned")],
            *[["minikube", "stop", "-p", f"rpp-{suffix}"] for suffix in "123"],
        ])
        self.assertTrue((state / "terraform.tfstate").exists())

    def test_stop_refuses_incomplete_database_recovery(self):
        state = self.root / ".local/rpp-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        for marker in ("database-fence.json", "database-transition.json"):
            (state / marker).touch()
            result = self.run_command("stop")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(call[0] == "minikube" for call in self.calls()))
            (state / marker).unlink()

    def test_destroy_checks_network_ownership_and_database_transition_first(self):
        state = self.root / ".local/rpp-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        (state / "cluster-identity").write_text("numbered-v1\n")
        (self.root / "network").touch()
        (state / "database-transition.json").touch()
        result = self.run_command("down")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database transition", result.stderr)
        (state / "database-transition.json").unlink()
        result = self.run_command("down", NETWORK_OWNER="other")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Network ownership mismatch", result.stderr)
        self.assertFalse(any(call[0] == "terraform" for call in self.calls()))
        result = self.run_command("down", FAIL_STATUS="rpp-2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rpp-2 is unavailable", result.stderr)
        self.assertFalse(any(call[0] == "terraform" for call in self.calls()))

    def test_cluster_deletion_refuses_pending_database_recovery(self):
        state = self.root / ".local/rpp-multi"
        state.mkdir(parents=True)
        (state / "owned").write_text("rpp\n")
        (state / "cluster-identity").write_text("numbered-v1\n")
        for name in ("database-fence.json", "database-transition.json"):
            with self.subTest(name=name):
                (state / name).write_text("{}")
                result = self.run_command("cluster-down")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] in ("docker", "minikube", "terraform") for call in self.calls()))
                (state / name).unlink()

    def test_default_base_needs_no_profile_override(self):
        result = self.run_command("up", NETWORK_OWNER="rpp")
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertEqual(len(starts), 3)
        for suffix, call in zip("123", starts):
            self.assertIn(f"rpp-{suffix}", call)
            self.assertIn("--network=rpp-net", call)
        state = self.root / ".local/rpp-multi"
        self.assertEqual((state / "owned").read_text().strip(), "rpp")
        self.assertEqual((state / "cluster-identity").read_text().strip(), "numbered-v1")
        inventory = json.loads((state / "topology.tfvars.json").read_text())
        self.assertEqual(inventory["profiles"], ["rpp-1", "rpp-2", "rpp-3"])

    def test_optional_features_follow_cluster_one(self):
        config = self.root / "configuration.json"
        config.write_text(json.dumps({
            "nodes": {"1": {"cpus": 4, "memory_mib": 10240}},
            "analytics": True,
            "ai": True,
            "identity": True,
        }))
        result = self.run_command("up", MULTI_FULL_STACK="1", DEV_CONFIG=str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        for number in ("1", "2", "3"):
            variables = json.loads((self.root / f".local/rpp-{number}/events.tfvars.json").read_text())
            for feature in ("analytics", "ai", "identity"):
                with self.subTest(number=number, feature=feature):
                    self.assertEqual(variables[f"{feature}_enabled"], number == "1")

    def test_three_fixed_budgets_and_distinct_static_ips(self):
        result = self.run_command("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertEqual(len(starts), 3)
        for i, call in enumerate(starts):
            self.assertIn(f"rpp-{'123'[i]}", call)
            for flag in ("--cpus=2", "--memory=3072", "--network=rpp-net", f"--static-ip=172.18.0.{i + 2}"):
                self.assertIn(flag, call)
        quotas = [call for call in self.calls() if call[:2] == ["docker", "update"]]
        self.assertEqual(quotas, [
            ["docker", "update", "--cpus=2", "--memory=3072m", "--memory-swap=6144m", f"rpp-{suffix}"]
            for suffix in "123"
        ])
        (self.root / "commands.jsonl").write_text("")
        result = self.run_command("metrics")
        self.assertEqual(result.returncode, 0, result.stderr)
        contexts = [call[call.index("--context") + 1] for call in self.calls() if call[0] == "kubectl"]
        self.assertEqual(contexts, ["rpp-1", "rpp-2", "rpp-3"])
        addons = [call for call in self.calls() if call[0] == "minikube"]
        self.assertEqual(addons, [
            ["minikube", "-p", f"rpp-{suffix}", "addons", "enable", "metrics-server"]
            for suffix in "123"
        ])
        variables = self.root / ".local" / "rpp-1" / "events.tfvars.json"
        self.assertEqual(variables.stat().st_mode & 0o777, 0o600)

    def test_existing_unowned_profile_or_network_is_refused(self):
        existing = self.root / ".minikube" / "profiles" / "rpp-2"
        existing.mkdir(parents=True)
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unowned profile", result.stderr)
        existing.rmdir()
        application = self.root / ".local" / "rpp-1"
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
            ["docker", "update", "--cpus=2.5", "--memory=4096m", "--memory-swap=8192m", f"rpp-{suffix}"]
            for suffix in "123"
        ])

    def test_new_base_defaults_to_full_stack_and_legacy_base_is_preserved(self):
        self.env.pop("MULTI_FULL_STACK")
        result = self.run_command("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        mode = self.root / ".local/rpp-multi/full-stack"
        self.assertTrue(mode.exists())
        mode.unlink()
        result = self.run_command("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(mode.exists())
        self.assertNotIn("full_stack", json.loads(
            (self.root / ".local/rpp-multi/topology.tfvars.json").read_text(),
        ))

    def test_ready_hint_selects_the_three_cluster_mode(self):
        result = self.run_command("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("make dev-access MULTI_CLUSTER=3 MINIKUBE_PROFILE=rpp-1", result.stdout)

    def test_invalid_budget_refuses_infrastructure_changes(self):
        source = self.root / "configuration.json"
        source.write_text('{"node":{"cpus":0}}')
        result = self.run_command("up", DEV_CONFIG=str(source))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("node.cpus", result.stderr)
        self.assertEqual(
            [call for call in self.calls() if call[0] != "cat"],
            [["docker", "network", "inspect", "rpp-net"]],
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
        self.assertTrue((self.root / ".local/rpp-multi/full-stack").exists())

    def test_invalid_full_stack_flag_is_explicit(self):
        result = self.run_command("up", MULTI_FULL_STACK="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be 0 or 1", result.stderr)

    def test_full_stack_deploys_all_profiles_and_fence_preserves_stopped_member(self):
        result = self.run_command("up", MULTI_FULL_STACK="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.root / ".local/rpp-multi"
        for suffix in "123":
            self.assertTrue((self.root / f".local/rpp-{suffix}/events.tfvars.json").exists())
        self.assertTrue(json.loads((state / "topology.tfvars.json").read_text())["full_stack"])
        (state / "database-fence.json").write_text(json.dumps({
            "old": "pg-1", "target": "pg-2", "phase": "complete",
        }))
        (state / "database-role.json").write_text('{"database_primary":"pg-2"}')
        (self.root / "commands.jsonl").write_text("")
        result = self.run_command("up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Fenced member", result.stderr)
        self.assertFalse(any(call[:2] == ["minikube", "start"] for call in self.calls()))
        result = self.run_command("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        plans = [call for call in self.calls() if call[0] == "terraform" and "plan" in call]
        self.assertEqual(len(plans), 1)
        self.assertIn("-target=module.cluster_2", plans[0])
        self.assertIn("-target=module.database_2[0]", plans[0])
        self.assertIn("-target=module.cluster_3", plans[0])
        self.assertNotIn("-target=module.cluster_1", plans[0])
        self.assertFalse(any(call[0] == "minikube" and "rpp-1" in call for call in self.calls()))
        self.assertEqual(json.loads((state / "topology.tfvars.json").read_text())["database_primary"], "pg-2")

    def test_pending_transition_and_rebuild_refuse_ordinary_update(self):
        self.assertEqual(self.run_command("up", MULTI_FULL_STACK="1").returncode, 0)
        state = self.root / ".local/rpp-multi"
        journal = state / "database-transition.json"
        journal.write_text("{}")
        result = self.run_command("update")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pending", result.stderr)
        journal.unlink()
        (state / "database-fence.json").write_text('{"old":"pg-1","phase":"rebuild-start"}')
        before = (state / "topology.tfvars.json").read_text()
        result = self.run_command("update")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pending database failover/rejoin", result.stderr)
        self.assertEqual((state / "topology.tfvars.json").read_text(), before)

    def test_resumed_rejoin_keeps_fenced_profile_identity_through_startup(self):
        self.assertEqual(self.run_command("up", MULTI_FULL_STACK="1").returncode, 0)
        state = self.root / ".local/rpp-multi"
        (state / "database-fence.json").write_text(json.dumps({
            "old": "pg-2", "target": "pg-3", "phase": "rebuild-start",
        }))
        (state / "database-role.json").write_text(json.dumps({
            "database_primary": "pg-3", "bootstrap_sources": {"pg-2": "pg-3"},
        }))
        (self.root / "commands.jsonl").write_text("")
        result = self.run_command("rejoin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((state / "database-fence.json").exists())
        starts = [call for call in self.calls() if call[:2] == ["minikube", "start"]]
        self.assertEqual(len(starts), 1)
        self.assertIn("rpp-2", starts[0])
        addons = [call for call in self.calls() if call[0] == "minikube" and "addons" in call]
        self.assertEqual(addons, [["minikube", "-p", "rpp-2", "addons", "enable", "metrics-server"]])
        queries = [call for call in self.calls() if call[0] == "kubectl" and "pg_control_system()" in call[-1]]
        self.assertEqual([call[call.index("--context") + 1] for call in queries], ["rpp-3", "rpp-2"])
        self.assertIn("pg-2-2", queries[-1])

    def test_rejoin_refuses_malformed_member_before_deleting_a_profile(self):
        self.assertEqual(self.run_command("up", MULTI_FULL_STACK="1").returncode, 0)
        state = self.root / ".local/rpp-multi"
        configuration = state / "configuration.json"
        original = json.dumps(json.loads(configuration.read_text()), indent=2)
        configuration.write_text(original)
        cases = (
            ({"old": "pg-101", "target": "pg-2", "phase": "rebuild-delete"}, "Invalid database member"),
            ({"old": "pg-1", "target": "pg-b", "phase": "rebuild-delete"}, "Invalid database member"),
            ({"old": "pg-2", "target": "pg-2", "phase": "rebuild-delete"}, "must differ"),
            ({"old": "pg-1", "target": "pg-2", "phase": "unknown"}, "pending failover"),
        )
        for journal, error in cases:
            with self.subTest(journal=journal):
                (state / "database-fence.json").write_text(json.dumps(journal))
                (self.root / "commands.jsonl").write_text("")
                result = self.run_command("rejoin")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertEqual(configuration.read_text(), original)
                self.assertFalse(any(call[0] in ("docker", "minikube", "terraform") for call in self.calls()))

    def test_low_inotify_and_terraform_failures_are_explicit(self):
        result = self.run_command("up", TEST_INOTIFY="128")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inotify", result.stderr)
        result = self.run_command("up", FAIL_TF="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deliberate Terraform failure", result.stderr)
        self.assertFalse((self.root / ".local" / "rpp-1" / "events.tfvars.json").exists())

    def test_setup_only_tunes_low_limit_when_explicitly_enabled(self):
        for enabled, limit, changes in (("0", "128", False), ("3", "128", True), ("3", "2048", False)):
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
        state = self.root / ".local" / "rpp-multi" / "terraform.tfstate"
        state.write_text(json.dumps({"resources": [{"mode": "managed", "type": "kubernetes_namespace_v1"}]}))
        result = self.run_command("cluster-down")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("destroy the topology", result.stderr.lower())
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
