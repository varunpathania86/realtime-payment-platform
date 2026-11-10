import os
import subprocess
import unittest

import yaml

from scripts.module_catalog import ROOT


class ReplicaScalingTests(unittest.TestCase):
    def render(self, chart, *settings, check=True):
        args = ["helm", "template", "scaling", str(ROOT / chart)]
        for setting in settings:
            args.extend(["--set", setting])
        return subprocess.run(args, check=check, capture_output=True, text=True)

    def test_api_and_worker_replicas_are_independent(self):
        result = self.render(
            "modules/payment-api/chart", "replicaCount=3", "events.enabled=true",
            "events.relayReplicas=2", "events.consumerReplicas=4",
            "events.brokers=kafka:9092", "events.registryURL=http://registry",
        )
        deployments = {
            doc["metadata"]["name"]: doc["spec"]["replicas"]
            for doc in yaml.safe_load_all(result.stdout) if doc and doc["kind"] == "Deployment"
        }
        self.assertEqual(deployments, {"scaling": 3, "scaling-relay": 2, "scaling-consumer": 4})

    def test_portal_and_cnpg_counts(self):
        portal = self.render("modules/web-portal/chart", "replicaCount=2")
        deployment = next(doc for doc in yaml.safe_load_all(portal.stdout) if doc["kind"] == "Deployment")
        self.assertEqual(deployment["spec"]["replicas"], 2)
        database = self.render("platform/terraform/modules/cnpg/chart", "instances=3", "maxConnections=256", "image=test")
        spec = yaml.safe_load(database.stdout)["spec"]
        self.assertEqual(spec["instances"], 3)
        self.assertEqual(spec["postgresql"]["parameters"]["max_connections"], "256")
        self.assertEqual(spec["postgresql"]["synchronous"]["maxStandbyNamesFromCluster"], 0)

    def test_invalid_chart_counts_fail_instead_of_defaulting(self):
        for chart, setting in (
            ("modules/payment-api/chart", "replicaCount=0"),
            ("modules/payment-api/chart", "events.relayReplicas=1.5"),
            ("modules/web-portal/chart", "replicaCount=-1"),
            ("platform/terraform/modules/cnpg/chart", "instances=0"),
        ):
            with self.subTest(chart=chart, setting=setting):
                result = self.render(chart, setting, "image=test" if "cnpg" in chart else "image.tag=test", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("schema", result.stderr)

    def test_public_make_targets_use_only_one_topology_workflow(self):
        result = subprocess.run(["make", "help"], cwd=ROOT, check=True, capture_output=True, text=True)
        targets = {line.strip().split()[0] for line in result.stdout.splitlines() if line.strip()}
        self.assertTrue({"dev-up", "dev-update", "dev-status", "dev-down", "dev-clean", "dev-access", "smoke", "db-switchover"} <= targets)
        self.assertFalse(any(name.startswith("multi-") or name.startswith("terraform-") for name in targets))
        self.assertNotIn("cluster-up", targets)
        self.assertNotIn("dev-images", targets)
        self.assertTrue({"run-local", "stop-local", "clean-local", "cluster-down"}.isdisjoint(targets))

    def test_access_and_smoke_select_default_base_or_explicit_overrides(self):
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {"DEV_PROFILE", "MINIKUBE_PROFILE", "MULTI_CLUSTER", "MAKEFLAGS", "MAKEOVERRIDES"}
        }
        for settings, expected in (
            ({}, "rpp"),
            ({"MULTI_CLUSTER": "3"}, "rpp-1"),
            ({"MULTI_CLUSTER": "3", "MINIKUBE_PROFILE": "rpp-3"}, "rpp-3"),
        ):
            for target, action in (("dev-access", "access"), ("smoke", "smoke")):
                with self.subTest(settings=settings, target=target):
                    recipe = subprocess.run(
                        ["make", "--no-print-directory", "-n", target], cwd=ROOT,
                        env={**environment, **settings}, check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    selection = recipe.removesuffix(f" scripts/local-dev.sh {action}")
                    self.assertNotEqual(selection, recipe)
                    result = subprocess.run(
                        ["bash", "-c", f'{selection} bash -c \'printf "%s" "$MINIKUBE_PROFILE"\''],
                        env={**environment, **settings}, check=True, capture_output=True, text=True,
                    )
                    self.assertEqual(result.stdout, expected)
