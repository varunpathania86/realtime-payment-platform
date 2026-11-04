import subprocess
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT
from scripts.module_catalog import local_modules
from scripts.topology_config import validate


class StreamingTests(unittest.TestCase):
    def test_risk_requires_explicit_pinned_identity_and_streaming(self):
        command = ["helm", "template", "flink-jobs", str(ROOT / "modules/flink-jobs/chart"),
            "--set", "risk.enabled=true"]
        for settings in (
            [], ["--set", "streaming.enabled=true"],
            ["--set", "streaming.enabled=true", "--set", "risk.packageKey=payment-risk/latest/payment_risk.zip",
                "--set-string", "risk.packageSHA256=" + "a" * 64],
        ):
            result = subprocess.run([*command, *settings], text=True, capture_output=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("risk requires", result.stderr)
        result = subprocess.run([*command, "--set", "streaming.enabled=true",
            "--set", "risk.packageKey=payment-risk/1.0.0/payment_risk.zip",
            "--set-string", "risk.packageSHA256=" + "a" * 64],
            check=True, text=True, capture_output=True, timeout=30)
        resources = list(yaml.safe_load_all(result.stdout))
        for resource in resources:
            if resource and resource["kind"] in ("Deployment", "Job"):
                env = resource["spec"]["template"]["spec"]["containers"][0]["env"]
                settings = {item["name"]: item.get("value") for item in env}
                self.assertEqual(settings["RISK_ENABLED"], "true")
                self.assertEqual(settings["RISK_PACKAGE_SHA256"], "a" * 64)
                self.assertIn("S3_ENDPOINT", settings)

    def render(self, enabled):
        rendered = subprocess.run(
            ["helm", "template", "flink-jobs", str(ROOT / "modules/flink-jobs/chart"),
                "--namespace", "apps", "--set", f"streaming.enabled={str(enabled).lower()}"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return list(yaml.safe_load_all(rendered.stdout))

    def test_lakehouse_requires_streaming_and_wires_two_slots_and_writer_secret(self):
        command = ["helm", "template", "flink-jobs", str(ROOT / "modules/flink-jobs/chart"),
            "--namespace", "apps", "--set", "lakehouse.enabled=true"]
        result = subprocess.run(command, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        resources = list(yaml.safe_load_all(subprocess.run([*command, "--set", "streaming.enabled=true"],
            check=True, text=True, capture_output=True, timeout=30).stdout))
        validate_chart(resources)
        for resource in resources:
            if resource and resource["kind"] == "Deployment":
                env = {item["name"]: item.get("value") for item in
                    resource["spec"]["template"]["spec"]["containers"][0]["env"]}
                self.assertEqual(env["LAKEHOUSE_ENABLED"], "true")
        config = next(item for item in resources if item and item["kind"] == "ConfigMap")
        self.assertIn("taskmanager.numberOfTaskSlots: 2", "\n".join(config["data"].values()))
        submit = next(item for item in resources if item and item["kind"] == "Job")
        env = {item["name"]: item for item in submit["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["LAKEHOUSE_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"],
            {"name": "lakehouse-writer", "key": "client-secret"})
        self.assertEqual(submit["spec"]["activeDeadlineSeconds"], 480)

    def test_default_is_dormant_and_opt_in_has_one_cluster(self):
        dormant = self.render(False)
        validate_chart(dormant)
        deployments = [item for item in dormant if item and item["kind"] == "Deployment"]
        self.assertEqual(len(deployments), 2)
        self.assertTrue(all(item["spec"]["replicas"] == 0 for item in deployments))
        self.assertFalse(any(item and item["kind"] == "Job" for item in dormant))
        enabled = self.render(True)
        validate_chart(enabled)
        self.assertTrue(all(item["spec"]["replicas"] == 1 for item in enabled if item and item["kind"] == "Deployment"))
        jobs = [item for item in enabled if item and item["kind"] == "Job"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["spec"]["backoffLimit"], 0)

    def test_persistent_state_and_credentials_are_not_in_configuration(self):
        resources = self.render(True)
        config = next(item for item in resources if item and item["kind"] == "ConfigMap")
        settings = yaml.safe_load(config["data"]["config.yaml"])
        self.assertEqual(settings["state.checkpoints.dir"], "s3://flink-state/checkpoints/payment-event-time-v1")
        self.assertEqual(settings["restart-strategy.type"], "fixed-delay")
        self.assertEqual(settings["blob.server.port"], 6124)
        self.assertNotIn("env.java.opts.all", settings)
        self.assertNotIn("s3.secret-key", settings)
        self.assertNotIn("s3.access-key", settings)
        for item in resources:
            if item and item["kind"] in ("Deployment", "Job"):
                container = item["spec"]["template"]["spec"]["containers"][0]
                secret = next(env for env in container["env"] if env["name"] == "AWS_SECRET_ACCESS_KEY")
                self.assertIn("secretKeyRef", secret["valueFrom"])
                self.assertEqual(container["imagePullPolicy"], "Never")

    def test_streaming_configuration_is_explicit_and_preserves_defaults(self):
        self.assertNotIn("streaming", validate({}))
        self.assertTrue(validate({"streaming": True})["streaming"])
        with self.assertRaisesRegex(ValueError, "boolean"):
            validate({"streaming": "true"})

    def test_feature_discovery_keeps_normal_builds_independent(self):
        self.assertIn("flink-jobs", local_modules())
        self.assertNotIn("flink-jobs", local_modules(features=set()))
        self.assertIn("flink-jobs", local_modules(features={"streaming"}))
        self.assertIn("payment-api", local_modules(features=set()))
