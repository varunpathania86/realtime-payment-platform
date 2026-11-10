import os
import subprocess
import unittest

from scripts.module_catalog import ROOT


class LocalMakeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"MINIKUBE_PROFILE", "DEV_PROFILE", "MULTI_CLUSTER"}}

    def recipe(self, target, **variables):
        return subprocess.run(
            ["make", "--no-print-directory", "--dry-run", target,
             *(f"{key}={value}" for key, value in variables.items())],
            cwd=ROOT, env=self.env, check=True, capture_output=True, text=True, timeout=10,
        ).stdout

    def test_lifecycle_routes_without_touching_a_cluster(self):
        cases = (
            ("dev-up", {}, "scripts/run-local.sh up|rpp"),
            ("dev-up", {"MINIKUBE_PROFILE": "rpp"}, "scripts/run-local.sh up|rpp"),
            ("dev-up", {"MULTI_CLUSTER": "3"}, "scripts/multi-cluster.sh up|"),
            ("dev-update", {}, "scripts/local-dev.sh update|rpp"),
            ("dev-update", {"MINIKUBE_PROFILE": "rpp"}, "scripts/local-dev.sh update|rpp"),
            ("dev-update", {"MULTI_CLUSTER": "3"}, "scripts/multi-cluster.sh update|"),
            ("dev-status", {}, "scripts/cluster.sh status|rpp"),
            ("dev-status", {"MULTI_CLUSTER": "3"}, "scripts/multi-cluster.sh status|"),
            ("dev-down", {}, "scripts/run-local.sh stop|rpp"),
            ("dev-down", {"MINIKUBE_PROFILE": "rpp"}, "scripts/run-local.sh stop|rpp"),
            ("dev-down", {"MULTI_CLUSTER": "3"}, "scripts/multi-cluster.sh stop|"),
            ("dev-clean", {}, "scripts/run-local.sh clean|rpp"),
            ("dev-clean", {"MINIKUBE_PROFILE": "rpp"}, "scripts/run-local.sh clean|rpp"),
            ("dev-clean", {"MULTI_CLUSTER": "3"}, "scripts/multi-cluster.sh down|"),
        )
        for target, variables, expected in cases:
            with self.subTest(target=target, variables=variables):
                command = (
                    'bash() { printf "%s|%s\\n" "$*" "${MINIKUBE_PROFILE:-}"; }\n'
                    'kubectl() { printf "kubectl %s\\n" "$*"; }\n'
                    + self.recipe(target, **variables)
                )
                result = subprocess.run(
                    ["bash", "-c", command], cwd=ROOT, env=self.env, check=True,
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.stdout.splitlines()[0], expected)
                if target == "dev-clean" and variables == {"MULTI_CLUSTER": "3"}:
                    self.assertEqual(result.stdout.splitlines()[1], "scripts/multi-cluster.sh cluster-down|")

    def test_single_profile_override_is_rejected_for_multi_lifecycle(self):
        for target in ("dev-status", "dev-down", "dev-clean"):
            command = 'bash() { echo "unexpected cluster call"; }\n' + self.recipe(
                target, MULTI_CLUSTER="3", MINIKUBE_PROFILE="rpp-2",
            )
            result = subprocess.run(
                ["bash", "-c", command], cwd=ROOT, env=self.env,
                capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Do not select a single profile", result.stderr)
            self.assertNotIn("unexpected cluster call", result.stdout)

    def test_only_the_two_fixed_profile_families_are_accepted(self):
        for variables in (
            {"MINIKUBE_PROFILE": "custom"},
            {"MULTI_CLUSTER": "3", "MINIKUBE_PROFILE": "%"},
            {"MULTI_CLUSTER": "3", "MINIKUBE_PROFILE": "rpp-1 rpp-2"},
            {"MULTI_CLUSTER": "0", "MINIKUBE_PROFILE": "rpp-1"},
            {"MULTI_CLUSTER": "3", "MINIKUBE_PROFILE": "rpp"},
            {"MULTI_CLUSTER": ""},
            {"MULTI_CLUSTER": "0 3"},
            {"MULTI_CLUSTER": "3 "},
            {"MULTI_CLUSTER": "%"},
            {"MULTI_CLUSTER": "1"},
            {"MULTI_CLUSTER": "2"},
        ):
            with self.subTest(variables=variables):
                result = subprocess.run(
                    ["make", "--no-print-directory", "-n", "dev-access",
                     *(f"{key}={value}" for key, value in variables.items())],
                    cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)

    def test_topology_only_targets_require_explicit_multi_mode(self):
        targets = (
            "dev-metrics", "dev-observability", "db-switchover", "db-failover", "db-rejoin",
            "streaming-benchmark-preflight", "streaming-benchmark",
        )
        for target in targets:
            with self.subTest(target=target):
                result = subprocess.run(
                    ["make", "--no-print-directory", target],
                    cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MULTI_CLUSTER=3", result.stderr)

    def test_multi_cleanup_does_not_delete_nodes_if_destroy_fails(self):
        command = (
            'bash() { printf "%s\\n" "$*"; return 1; }\n'
            + self.recipe("dev-clean", MULTI_CLUSTER="3")
        )
        result = subprocess.run(
            ["bash", "-c", command], cwd=ROOT, env=self.env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["scripts/multi-cluster.sh down"])

    def test_everyday_commands_select_single_or_explicit_profile(self):
        targets = (
            "dev-access", "smoke", "access-local", "dbx-access", "dbx-status", "minio-access",
            "identity-access", "identity-sync", "identity-verify", "risk-publish", "risk-verify",
            "bi-verify", "knowledge-ingest", "ai-fixtures", "ai-eval", "streaming-fixtures",
            "streaming-inspect", "streaming-load", "streaming-savepoint", "streaming-stop",
            "lakehouse-query", "lakehouse-verify", "lakehouse-maintain",
        )
        for target in targets:
            for variables, expected in (
                ({}, "rpp"),
                ({"MULTI_CLUSTER": "3"}, "rpp-1"),
                ({"MULTI_CLUSTER": "3", "MINIKUBE_PROFILE": "rpp-2"}, "rpp-2"),
            ):
                with self.subTest(target=target, variables=variables):
                    recipe = self.recipe(target, SQL="SELECT 1", **variables)
                    self.assertIn(expected, recipe)

    def test_lab_configuration_cannot_be_silently_ignored(self):
        for target in ("dev-up", "dev-update"):
            for variables in (
                {"DEV_CONFIG": "lab.json"},
                {"DEV_CONFIG": "lab.json", "MULTI_CLUSTER": "0"},
            ):
                with self.subTest(target=target, variables=variables):
                    command = 'bash() { echo "unexpected cluster call"; }\n' + self.recipe(target, **variables)
                    result = subprocess.run(
                        ["bash", "-c", command], cwd=ROOT, env=self.env,
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("DEV_CONFIG requires MULTI_CLUSTER=3", result.stderr)
                    self.assertNotIn("unexpected cluster call", result.stdout)
