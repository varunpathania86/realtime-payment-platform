import copy
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from scripts.topology_config import validate
from scripts.module_catalog import ROOT


class TopologyConfigurationTests(unittest.TestCase):
    def test_numbered_cluster_keys_are_canonical(self):
        sizes = {number: {"kafka_brokers": int(number)} for number in ("1", "2", "3")}
        nodes = {number: {"cpus": int(number), "memory_mib": 3072} for number in ("1", "2", "3")}
        settings = validate({"clusters": sizes, "nodes": nodes})
        self.assertEqual(settings["clusters"], sizes)
        self.assertEqual(settings["nodes"], nodes)
        for section in ("clusters", "nodes"):
            for legacy in ("a", "b", "c"):
                with self.subTest(section=section, legacy=legacy), self.assertRaises(ValueError):
                    validate({section: {legacy: {}}})

    def test_risk_requires_streaming_and_explicit_version_and_digest(self):
        risk = {"package_key": "payment-risk/1.0.0/payment_risk.zip", "package_sha256": "a" * 64}
        config = {"node": {"cpus": 4, "memory_mib": 6144}, "streaming": True, "risk": risk}
        self.assertEqual(validate(config)["risk"], risk)
        for invalid in (
            {**config, "streaming": False}, {**config, "risk": {}},
            {**config, "risk": {**risk, "package_sha256": ""}},
            {**config, "risk": {**risk, "package_key": "payment-risk/latest/payment_risk.zip"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid)

    def test_analytics_requires_streaming_and_a_sufficient_cluster_1_budget(self):
        config = json.loads((ROOT / "templates/analytics-topology.json").read_text())
        settings = validate(config)
        self.assertTrue(settings["analytics"])
        self.assertTrue(settings["streaming"])
        self.assertEqual(settings["nodes"]["1"], {"cpus": 4, "memory_mib": 8192})
        for invalid in (
            {"analytics": True},
            {"streaming": True, "analytics": True},
            {"node": {"cpus": 3, "memory_mib": 8192}, "streaming": True, "analytics": True},
            {"node": {"cpus": 4, "memory_mib": 5120}, "streaming": True, "analytics": True},
            {"nodes": {"1": {"cpus": 3, "memory_mib": 8192}}, "streaming": True, "analytics": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid)

    def test_lakehouse_requires_streaming_and_a_larger_cluster_1_budget_with_analytics(self):
        config = json.loads((ROOT / "templates/lakehouse-topology.json").read_text())
        settings = validate(config)
        self.assertTrue(settings["lakehouse"])
        self.assertEqual(settings["nodes"]["1"], {"cpus": 4, "memory_mib": 8192})
        both = {**config, "analytics": True, "nodes": {"1": {"cpus": 4, "memory_mib": 10240}}}
        self.assertTrue(validate(both)["analytics"])
        for invalid in (
            {**config, "streaming": False},
            {**config, "lakehouse": "yes"},
            {**config, "nodes": {"1": {"cpus": 4, "memory_mib": 7168}}},
            {**config, "nodes": {"1": {"cpus": 3, "memory_mib": 8192}}},
            {**both, "nodes": {"1": {"cpus": 4, "memory_mib": 8192}}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid)

    def test_bi_requires_analytics_and_extra_cluster_1_memory(self):
        config = json.loads((ROOT / "templates/bi-topology.json").read_text())
        settings = validate(config)
        self.assertTrue(settings["bi"])
        self.assertEqual(settings["nodes"]["1"], {"cpus": 4, "memory_mib": 12288})
        without_lakehouse = {**config, "lakehouse": False, "nodes": {"1": {"cpus": 4, "memory_mib": 7168}}}
        self.assertTrue(validate(without_lakehouse)["bi"])
        for invalid in (
            {**config, "analytics": False},
            {**config, "bi": "yes"},
            {**config, "nodes": {"1": {"cpus": 4, "memory_mib": 10240}}},
            {**without_lakehouse, "nodes": {"1": {"cpus": 4, "memory_mib": 6144}}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid)

    def test_lean_ai_profile_runs_analytics_without_streaming(self):
        config = json.loads((ROOT / "templates/ai-topology.json").read_text())
        settings = validate(config)
        self.assertTrue(settings["ai"])
        self.assertTrue(settings["analytics"])
        self.assertFalse(settings["streaming"])
        self.assertEqual(settings["nodes"]["1"], {"cpus": 4, "memory_mib": 10240})
        full = {**config, "streaming": True, "lakehouse": True, "bi": True, "nodes": {"1": {"cpus": 6, "memory_mib": 16384}}}
        self.assertTrue(validate(full)["ai"])
        for invalid in (
            {**config, "analytics": False},
            {**config, "ai": "yes"},
            {**config, "ai": False},
            {**config, "nodes": {"1": {"cpus": 4, "memory_mib": 7168}}},
            {**config, "nodes": {"1": {"cpus": 3, "memory_mib": 10240}}},
            {**full, "nodes": {"1": {"cpus": 6, "memory_mib": 12288}}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid)

    def test_explicit_observability_and_per_node_budgets(self):
        config = json.loads((ROOT / "templates/observability-topology.json").read_text())
        settings = validate(config)
        self.assertTrue(settings["observability"])
        self.assertEqual(settings["nodes"]["1"], {"cpus": 3, "memory_mib": 5120})
        self.assertEqual(settings["node"]["memory_mib"], 3072)
        inherited = validate({"node": {"cpus": 4}, "nodes": {"1": {"memory_mib": 5120}}})
        self.assertEqual(inherited["nodes"]["1"]["cpus"], 4)
        for invalid in (
            {"observability": "yes"}, {"nodes": {"d": {}}},
            {"nodes": {"1": {"memory_mib": 1024}}}, {"nodes": []},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid)

    def test_dbx_is_opt_in_and_adds_512_mib_to_cluster_1_budget(self):
        self.assertNotIn("dbx", validate({}))
        settings = validate(
            {"dbx": True, "node": {"cpus": 4, "memory_mib": 3584}}
        )
        self.assertTrue(settings["dbx"])
        for invalid in (
            {"dbx": "true"},
            {"dbx": True, "node": {"cpus": 4, "memory_mib": 3072}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid)

    def test_shell_selects_each_node_budget_before_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "configuration.json"
            config.write_text(json.dumps(validate({
                "nodes": {"1": {"cpus": 3, "memory_mib": 5120}},
            })))
            script = shlex.quote(str(ROOT / "scripts/multi-cluster.sh"))
            command = (
                f". {script}; STATE={shlex.quote(directory)}; "
                'node_budget rpp-1; echo "$NODE_CPUS $NODE_MEMORY"; '
                'node_budget rpp-2; echo "$NODE_CPUS $NODE_MEMORY"'
            )
            result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().splitlines(), ["3 5120", "2 3072"])

    def test_defaults_and_independent_cluster_counts(self):
        self.assertEqual(validate({}), {"node": {"cpus": 2, "memory_mib": 3072}, "clusters": {}})
        sizes = {"1": {
            "kafka_brokers": 2, "postgres_instances": 3,
            "applications": {"payment-api": 4, "web-portal": 2}, "relay": 2, "registry": 2,
        }}
        settings = validate({"node": {"cpus": 4, "memory_mib": 6144}, "clusters": sizes})
        self.assertEqual(settings["clusters"], sizes)

    def test_invalid_configuration_is_not_silently_ignored(self):
        for data in (
            {"unexpected": True}, {"node": {"cpus": 0.5}}, {"node": {"cpus": True}},
            {"node": {"memory_mib": 2048}}, {"node": {"memory_mib": 4096.5}},
            {"clusters": {"d": {}}}, {"clusters": {"1": {"kafka_brokers": 33}}},
            {"clusters": {"1": {"relay": 0}}}, {"clusters": {"1": {"postgres_instances": 1.5}}},
            {"clusters": {"1": {"applications": {"catalog-only": 2}}}},
            {"clusters": {"1": {"applications": {"../payment-api": 2}}}},
            {"clusters": {"1": {"unknown": 2}}},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate(data)

    def test_cli_persists_config_and_blocks_unsafe_broker_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            source = Path(directory) / "settings.json"
            settings = {"node": {"cpus": 3, "memory_mib": 4096}, "clusters": {"1": {"kafka_brokers": 2}}}
            source.write_text(json.dumps(settings))
            command = [str(ROOT / ".venv/bin/python"), "-m", "scripts.topology_config", str(state)]
            first = subprocess.run([*command, "--config", str(source)], capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout.strip(), "3 4096")
            persisted = state / "configuration.json"
            self.assertEqual(persisted.stat().st_mode & 0o777, 0o600)
            rerun = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(rerun.stdout, first.stdout)
            (state / "terraform.tfstate").write_text('{"resources":[{"mode":"managed"}]}')
            smaller = copy.deepcopy(settings)
            smaller["clusters"]["1"]["kafka_brokers"] = 1
            source.write_text(json.dumps(smaller))
            failure = subprocess.run([*command, "--config", str(source)], capture_output=True, text=True, cwd=ROOT)
            self.assertNotEqual(failure.returncode, 0)
            self.assertIn("partition", failure.stderr)
            self.assertEqual(json.loads(persisted.read_text()), settings)

    def test_cli_refuses_to_replace_legacy_persisted_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            persisted = state / "configuration.json"
            previous = {"node": {"cpus": 2, "memory_mib": 3072}, "clusters": {"a": {"kafka_brokers": 2}}}
            persisted.write_text(json.dumps(previous))
            (state / "terraform.tfstate").write_text('{"resources":[{"mode":"managed"}]}')
            source = Path(directory) / "settings.json"
            source.write_text(json.dumps({"clusters": {"1": {"kafka_brokers": 1}}}))
            result = subprocess.run(
                [str(ROOT / ".venv/bin/python"), "-m", "scripts.topology_config", str(state), "--config", str(source)],
                capture_output=True, text=True, cwd=ROOT,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("legacy", result.stderr.lower())
            self.assertEqual(json.loads(persisted.read_text()), previous)

    def test_stale_terraform_lock_cleanup_removes_dead_lock_files(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            stale_lock = state / "terraform.tfstate.lock.info"
            stale_lock.write_text("stale-lock-id")
            script_path = shlex.quote(str(ROOT / "scripts" / "multi-cluster.sh"))
            state_value = shlex.quote(str(state))
            lock_value = shlex.quote(str(stale_lock))
            command = [
                "/usr/bin/env",
                "bash",
                "-lc",
                f". {script_path}; STATE={state_value}; "
                f"pgrep() {{ return 1; }}; stale_lock_cleanup; test ! -e {lock_value}",
            ]
            result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr)
