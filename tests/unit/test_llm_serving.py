import re
import subprocess
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT

MODULE = ROOT / "modules/llm-serving"
CHART = MODULE / "chart"


def render(*arguments, check=True):
    return subprocess.run(
        ["helm", "template", "llm-serving", str(CHART), "--namespace", "apps", *arguments],
        check=check, capture_output=True, text=True, timeout=30,
    )


def resources(*arguments):
    return [item for item in yaml.safe_load_all(render(*arguments).stdout) if item]


def pins():
    lines = (ROOT / ".tool-versions").read_text().splitlines()
    return dict(line.split()[:2] for line in lines if line.strip() and not line.startswith("#"))


class LlmServingChartTests(unittest.TestCase):
    def test_chart_meets_local_contract_and_exposes_both_servers(self):
        items = resources()
        validate_chart(items)
        services = {item["metadata"]["name"]: item["spec"]["ports"][0]["targetPort"]
                    for item in items if item["kind"] == "Service"}
        self.assertEqual(services, {"llm-serving-chat": "chat", "llm-serving-embed": "embed"})
        claim = next(item for item in items if item["kind"] == "PersistentVolumeClaim")
        self.assertEqual(claim["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")

    def test_models_are_fetched_with_pinned_checksums_and_sizes(self):
        values = yaml.safe_load((CHART / "values.yaml").read_text())["models"]
        deployment = next(item for item in resources() if item["kind"] == "Deployment")
        script = deployment["spec"]["template"]["spec"]["initContainers"][0]["args"][0]
        for model in values.values():
            self.assertRegex(model["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(model["url"].startswith("https://"))
            self.assertTrue(model["url"].endswith(model["sha256"]))
            self.assertIn(f'"{model["sha256"]}" "{model["size"]}"', script)
        chat = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertIn("--jinja", chat["args"])

    def test_multiple_replicas_are_rejected(self):
        result = render("--set", "replicaCount=2", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one replica", result.stderr)

    def test_cpu_default_does_not_request_gpu_or_force_cuda(self):
        deployment = next(item for item in resources() if item["kind"] == "Deployment")
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            self.assertNotIn("--device", container["args"])
            self.assertNotIn("--n-gpu-layers", container["args"])
            for kind in ("requests", "limits"):
                self.assertFalse(any(key.startswith("nvidia.com/") for key in container["resources"][kind]))

    def test_both_servers_offload_to_explicit_shared_gpu_slots(self):
        items = resources("--set", "gpu.enabled=true")
        validate_chart(items)
        deployment = next(item for item in items if item["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        self.assertEqual(len(pod["containers"]), 2)
        for container in pod["containers"]:
            args = container["args"]
            self.assertEqual(args[args.index("--device") + 1], "CUDA0")
            self.assertEqual(args[args.index("--n-gpu-layers") + 1], "999")
            self.assertIn({"name": "CUDA_CACHE_DISABLE", "value": "1"}, container["env"])
            for kind in ("requests", "limits"):
                self.assertEqual(container["resources"][kind]["nvidia.com/gpu.shared"], 1)
            self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        for container in pod["initContainers"]:
            self.assertIn({"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"}, container["env"])
            for kind in ("requests", "limits"):
                self.assertFalse(any(key.startswith("nvidia.com/") for key in container["resources"][kind]))


class LlmServingBuildTests(unittest.TestCase):
    def test_dockerfile_defaults_match_pinned_llama_cpp(self):
        dockerfile = (MODULE / "Dockerfile").read_text()
        defaults = dict(re.findall(r"^ARG (LLAMA_CPP_\w+)=(\S+)$", dockerfile, re.MULTILINE))
        self.assertEqual(defaults, {"LLAMA_CPP_VERSION": pins()["llama-cpp"],
                                    "LLAMA_CPP_DIGEST": pins()["llama-cpp-image-digest"]})
        self.assertIn("USER 65532:65532", dockerfile)

    def test_build_paths_pass_ai_pins(self):
        for script in ("scripts/local-dev.sh", "scripts/publish_modules.py"):
            text = (ROOT / script).read_text()
            for argument in ("AI_PYTHON_VERSION=", "LLAMA_CPP_VERSION=", "LLAMA_CPP_DIGEST="):
                self.assertIn(argument, text, script)


if __name__ == "__main__":
    unittest.main()
