import subprocess
import unittest

import yaml

from scripts.topology_config import validate


class FlinkMemoryProfileTests(unittest.TestCase):
    def render(self, profile):
        result = subprocess.run(
            ["helm", "template", "flink-jobs", "modules/flink-jobs/chart",
                "--set", "streaming.enabled=true", "--set", f"runtimeProfile={profile}"],
            check=True, capture_output=True, text=True,
        )
        return list(yaml.safe_load_all(result.stdout))

    def test_standard_budgets_are_preserved(self):
        documents = self.render("standard")
        configuration = next(doc["data"]["config.yaml"] for doc in documents if doc["kind"] == "ConfigMap")
        self.assertIn("jobmanager.memory.process.size: 1024m", configuration)
        self.assertIn("taskmanager.memory.process.size: 2048m", configuration)

    def test_low_memory_runtime_and_container_budgets_are_coordinated(self):
        documents = self.render("low-memory")
        configuration = next(doc["data"]["config.yaml"] for doc in documents if doc["kind"] == "ConfigMap")
        self.assertIn("jobmanager.memory.process.size: 768m", configuration)
        self.assertIn("taskmanager.memory.process.size: 1280m", configuration)
        deployments = {doc["metadata"]["name"]: doc for doc in documents if doc["kind"] == "Deployment"}
        for role, limit in (("jobmanager", "1024Mi"), ("taskmanager", "2048Mi")):
            container = deployments[f"flink-jobs-{role}"]["spec"]["template"]["spec"]["containers"][0]
            self.assertEqual(container["resources"]["limits"]["memory"], limit)

    def test_invalid_profile_fails_instead_of_defaulting(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.render("typo")
        with self.assertRaises(ValueError):
            validate({"runtime_profile": "typo"})

    def test_topology_preserves_explicit_profile(self):
        self.assertEqual(validate({"runtime_profile": "low-memory"})["runtime_profile"], "low-memory")
        self.assertNotIn("runtime_profile", validate({}))
