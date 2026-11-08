import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import streaming_benchmark
from scripts.streaming_benchmark import preflight


SETTINGS = {
    "node": {"cpus": 2, "memory_mib": 3072},
    "nodes": {"1": {"cpus": 4, "memory_mib": 6144}},
    "analytics": True,
    "streaming": True,
}


class BenchmarkPreflightTests(unittest.TestCase):
    def test_budget_includes_all_three_nodes_and_host_reserve(self):
        result = preflight(SETTINGS, 14336)
        self.assertEqual(result["required_mib"], 14336)
        self.assertEqual(result["nodes"]["2"]["memory_mib"], 3072)
        self.assertEqual(result["nodes"]["1"]["memory_mib"], 6144)

    def test_insufficient_memory_refuses_start(self):
        with self.assertRaisesRegex(ValueError, "no clusters were started"):
            preflight(SETTINGS, 12000)

    def test_analytics_is_required(self):
        with self.assertRaisesRegex(ValueError, "requires analytics"):
            preflight({}, 20000)

    def test_invalid_budget_cannot_bypass_topology_validation(self):
        with self.assertRaises(ValueError):
            preflight({**SETTINGS, "node": {"memory_mib": 1024}}, 20000)

    def test_benchmark_refuses_legacy_topology_before_running_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".local/rpp-multi"
            state.mkdir(parents=True)
            (state / "owned").write_text("rpp\n")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with (
                    patch.object(sys, "argv", ["streaming_benchmark", "--base", "rpp"]),
                    patch.object(streaming_benchmark, "memory_available_mib", return_value=20000),
                    patch.object(streaming_benchmark.subprocess, "run") as run,
                ):
                    with self.assertRaisesRegex(ValueError, "legacy"):
                        streaming_benchmark.main()
                    run.assert_not_called()
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
