import unittest

from scripts.streaming_benchmark import preflight


SETTINGS = {
    "node": {"cpus": 2, "memory_mib": 3072},
    "nodes": {"a": {"cpus": 4, "memory_mib": 6144}},
    "analytics": True,
    "streaming": True,
}


class BenchmarkPreflightTests(unittest.TestCase):
    def test_budget_includes_all_three_nodes_and_host_reserve(self):
        result = preflight(SETTINGS, 14336)
        self.assertEqual(result["required_mib"], 14336)
        self.assertEqual(result["nodes"]["b"]["memory_mib"], 3072)
        self.assertEqual(result["nodes"]["a"]["memory_mib"], 6144)

    def test_insufficient_memory_refuses_start(self):
        with self.assertRaisesRegex(ValueError, "no clusters were started"):
            preflight(SETTINGS, 12000)

    def test_analytics_is_required(self):
        with self.assertRaisesRegex(ValueError, "requires analytics"):
            preflight({}, 20000)

    def test_invalid_budget_cannot_bypass_topology_validation(self):
        with self.assertRaises(ValueError):
            preflight({**SETTINGS, "node": {"memory_mib": 1024}}, 20000)


if __name__ == "__main__":
    unittest.main()
