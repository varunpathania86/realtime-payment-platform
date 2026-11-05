import importlib.util
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from processing import decode


spec = importlib.util.spec_from_file_location("pipeline_benchmark", Path(__file__).resolve().parents[1] / "benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_partition_without_commit_counts_all_retained_input(self):
        self.assertEqual(benchmark.committed_lag(20, None, 5), 15)
        self.assertEqual(benchmark.committed_lag(20, 17, 5), 3)
        self.assertEqual(benchmark.committed_lag(20, 20, 5), 0)

    def test_clients_close_on_execution_failure(self):
        producer, consumer = Mock(), Mock()
        kafka = types.SimpleNamespace(
            KafkaProducer=Mock(return_value=producer), KafkaConsumer=Mock(return_value=consumer),
        )
        control = types.SimpleNamespace(connection=Mock(return_value={}))
        with patch.dict(sys.modules, {"kafka": kafka, "control": control}), \
                patch.object(benchmark, "execute", side_effect=ValueError("test failure")):
            with self.assertRaisesRegex(ValueError, "test failure"):
                benchmark.run()
        producer.close.assert_called_once_with(timeout=5)
        consumer.close.assert_called_once()

    def test_producer_closes_if_consumer_creation_fails(self):
        producer = Mock()
        kafka = types.SimpleNamespace(
            KafkaProducer=Mock(return_value=producer),
            KafkaConsumer=Mock(side_effect=ValueError("consumer failure")),
        )
        control = types.SimpleNamespace(connection=Mock(return_value={}))
        with patch.dict(sys.modules, {"kafka": kafka, "control": control}):
            with self.assertRaisesRegex(ValueError, "consumer failure"):
                benchmark.run()
        producer.close.assert_called_once_with(timeout=5)

    def test_generated_events_match_utc_contract_and_kafka_timestamp(self):
        run_id = UUID("ed537ff3-f272-4278-b9e8-37a668c3da1e")
        timestamp = datetime(2026, 11, 4, 12, 0, 0, 123456, tzinfo=timezone.utc)
        value = benchmark.make_event(run_id, "baseline", 0, timestamp)
        decoded = decode(json.dumps(value))
        self.assertEqual(decoded["timestamp_ms"], int(timestamp.timestamp() * 1000))
        self.assertEqual(value["account_id"], str(run_id))
        self.assertNotEqual(value["event_id"], benchmark.make_event(run_id, "stress", 0, timestamp)["event_id"])

    def report(self, latency=0.1, missing=False, growth=0, pacing=1, initial_backlog=5):
        sent = {str(index): ("baseline", pacing * index / 50) for index in range(6000)}
        visible = {key: timestamp + latency for key, (_, timestamp) in sent.items()}
        if missing:
            visible.pop("5999")
        samples = [("baseline", second, initial_backlog + growth * second) for second in range(120)]
        lag = [("baseline", second, 500 if second % 10 else 0) for second in range(120)]
        return benchmark.summarize(sent, visible, samples, lag, "baseline", 50, 120)

    def test_schedule_is_under_seven_minutes_and_has_both_rates(self):
        self.assertLess(sum(duration for _, _, duration in benchmark.PHASES), 7 * 60)
        self.assertIn(("baseline", 50, 120), benchmark.PHASES)
        self.assertIn(("stress", 100, 90), benchmark.PHASES)

    def test_stable_complete_baseline_passes_despite_checkpoint_lag_sawtooth(self):
        report = self.report()
        self.assertTrue(report["meets_baseline_contract"])
        self.assertEqual(report["scheduled_events_per_second"], 50)
        self.assertEqual(report["achieved_producer_events_per_second"], 50)
        self.assertEqual(report["confirmed_unique_facts"], 6000)
        self.assertAlmostEqual(report["visibility_latency_ms_upper_bound"]["p95"], 100)

    def test_missing_event_fails_even_with_good_latency(self):
        self.assertFalse(self.report(missing=True)["meets_baseline_contract"])

    def test_latency_over_target_fails(self):
        self.assertFalse(self.report(latency=2.1)["meets_baseline_contract"])

    def test_sustained_backlog_growth_fails(self):
        self.assertFalse(self.report(growth=2)["meets_baseline_contract"])

    def test_slow_actual_producer_does_not_pass_using_scheduled_rate(self):
        self.assertFalse(self.report(pacing=1.02)["meets_baseline_contract"])

    def test_flat_but_excessive_terminal_backlog_fails(self):
        self.assertFalse(self.report(initial_backlog=101)["meets_baseline_contract"])

    def test_sparse_or_empty_samples_are_errors_not_passing_defaults(self):
        with self.assertRaises(ValueError):
            benchmark.slope([(1, 0)])
        with self.assertRaises(ValueError):
            benchmark.percentile([], 95)

    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(benchmark.percentile(list(range(1, 101)), 95), 95)
        self.assertEqual(benchmark.slope([(1, 1), (2, 3), (3, 5)]), 2)


if __name__ == "__main__":
    unittest.main()
