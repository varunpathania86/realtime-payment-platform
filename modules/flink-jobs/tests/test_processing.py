import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixtures import dataset, event
from configuration import overlay
from processing import LATENESS_MS, add, decode, dimension, empty_window, output, windows


class ProcessingTests(unittest.TestCase):
    def test_runtime_overlay_preserves_java17_defaults(self):
        base = {"env": {"java": {"opts": {"all": "--add-opens=java.base/java.util=ALL-UNNAMED"}}},
            "jobmanager": {"rpc": {"address": "localhost"}}}
        merged = overlay(base, {"jobmanager.rpc.address": "flink-jobs", "blob.server.port": 6124})
        self.assertIn("--add-opens", merged["env"]["java"]["opts"]["all"])
        self.assertIn("-Duser.home=/tmp", merged["env"]["java"]["opts"]["all"])
        self.assertEqual(merged["jobmanager"]["rpc"]["address"], "flink-jobs")
        self.assertEqual(base["jobmanager"]["rpc"]["address"], "localhost")
        with self.assertRaisesRegex(ValueError, "scalar"):
            overlay(base, {"jobmanager.rpc.address.port": 1})

    def test_tumbling_and_sliding_boundaries(self):
        assigned = list(windows(60_000))
        self.assertEqual(len(assigned), 11)
        self.assertIn((60_000, 120_000), assigned)
        self.assertIn((-480_000, 120_000), assigned)
        self.assertNotIn((0, 60_000), assigned)
        self.assertEqual(sum(end - start == 600_000 for start, end in assigned), 10)

    def test_minor_units_and_outcome_metrics(self):
        accepted = decode(json.dumps(event(1)))
        rejected = decode(json.dumps(event(2, outcome="rejected")))
        original = empty_window(0, 60_000)
        window = add(add(original, accepted), rejected)
        result = output(window, dimension(accepted))
        self.assertEqual(original["payments"], 0)
        self.assertEqual(result["payments"], 2)
        self.assertEqual(result["authorized_amount_minor"], 100)
        self.assertEqual(result["authorization_rate"], .5)
        self.assertEqual(result["rejections"], 1)
        self.assertIn("input_dataset", result)

    def test_late_revision_identity_is_stable(self):
        value = decode(json.dumps(event(1)))
        window = add(empty_window(0, 60_000), value)
        first = output(window, dimension(value))
        revised = output(add(window, value), dimension(value))
        self.assertEqual(first["result_id"], revised["result_id"])
        self.assertNotEqual(first["payments"], revised["payments"])
        self.assertNotEqual(first["result_id"], output(window, '["USD","DE","transfer"]')["result_id"])
        self.assertEqual(LATENESS_MS, 120_000)

    def test_fixture_is_deterministic_with_missing_idle_and_skewed_cases(self):
        records = dataset()
        self.assertEqual(records, dataset())
        self.assertEqual({record["partition"] for record in records}, {0, 1})
        self.assertIn("too-late", {record["case"] for record in records})
        self.assertIn("late-accepted", {record["case"] for record in records})
        self.assertEqual(len({record["event"]["account_id"] for record in records}), 1)
        identifiers = [record["event"]["event_id"] for record in records]
        self.assertLess(len(set(identifiers)), len(identifiers))
        self.assertNotIn(event(8)["event_id"], identifiers)
        for record in records:
            decode(json.dumps(record["event"]))

    def test_invalid_input_fails_instead_of_changing_payment_semantics(self):
        for changes in ({"amount_minor": True}, {"amount_minor": -1},
                        {"outcome": "unknown"}, {"country": "de"},
                        {"schema_version": True},
                        {"event_type": "payment.initiated.v1"}, {"event_time": "yesterday"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                decode(json.dumps(event(1, **changes)))


if __name__ == "__main__":
    unittest.main()
