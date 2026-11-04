import base64
import importlib
import json
import sys
import types
import unittest
from unittest.mock import patch


class MapFunction:
    pass


pyflink = types.ModuleType("pyflink")
datastream = types.ModuleType("pyflink.datastream")
functions = types.ModuleType("pyflink.datastream.functions")
functions.MapFunction = MapFunction
datastream.functions = functions
pyflink.datastream = datastream
with patch.dict(sys.modules, {
    "pyflink": pyflink,
    "pyflink.datastream": datastream,
    "pyflink.datastream.functions": functions,
}):
    analytics = importlib.import_module("analytics")


class ClickHouseMapTests(unittest.TestCase):
    def test_payment_fact_preserves_contract_and_lineage_fields(self):
        fact = analytics.payment_fact({
            "timestamp_ms": 1_800_000_000_000,
            "event_id": "event", "payment_id": "payment", "account_id": "account",
            "amount_minor": 1250, "currency": "EUR", "country": "DE",
            "payment_method": "card", "outcome": "authorized", "schema_version": 1,
        })
        self.assertEqual(fact["event_time_ms"], 1_800_000_000_000)
        self.assertEqual(fact["amount_minor"], 1250)
        self.assertEqual(fact["source_dataset"], "kafka:payment-processing-events-v1")

    def test_window_mapping_matches_clickhouse_schema(self):
        raw = json.dumps({
            "result_id": "a" * 64, "window_start": 1000, "window_end": 61000,
            "currency": "EUR", "country": "DE", "payment_method": "card",
            "payments": 2, "authorized": 1, "authorized_amount_minor": 1250,
            "authorization_rate": 0.5, "rejections": 1, "revision": 3,
            "schema_version": 1, "job_name": "payment-windows",
            "job_version": "payment-windows-v1", "transformation_id": "payment-windows-v1",
            "input_dataset": "kafka:payment-processing-events-v1",
            "output_dataset": "kafka:payment-window-results-v1",
            "analytics_dataset": "clickhouse:analytics.payment_window_aggregates",
        })
        self.assertEqual(analytics.payment_window(raw), {
            "result_id": "a" * 64, "window_start_ms": 1000, "window_end_ms": 61000,
            "currency": "EUR", "country": "DE", "payment_method": "card",
            "payments": 2, "authorized": 1, "authorized_amount_minor": 1250,
            "authorization_rate": 0.5, "rejections": 1, "revision": 3,
            "schema_version": 1, "job_name": "payment-windows",
            "job_version": "payment-windows-v1", "transformation_id": "payment-windows-v1",
            "input_dataset": "kafka:payment-processing-events-v1",
            "output_dataset": "kafka:payment-window-results-v1",
            "analytics_dataset": "clickhouse:analytics.payment_window_aggregates",
        })

    def test_insert_uses_allowlisted_table_and_authenticated_json_each_row(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b""

        with patch.object(analytics, "urlopen", return_value=Response()) as urlopen:
            analytics.insert_batch(
                "http://clickhouse:8123/", "user", "secret",
                analytics.FACTS_TABLE, ['{"amount_minor":4}'],
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b'{"amount_minor":4}\n')
        self.assertEqual(request.get_header("Authorization"), f"Basic {base64.b64encode(b'user:secret').decode()}")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            analytics.insert_batch("http://clickhouse:8123", "user", "secret", "unsafe", [])
        self.assertEqual(urlopen.call_count, 1)

    def test_map_writes_before_return_and_propagates_insert_failure(self):
        writer = analytics.ClickHouseMap(analytics.FACTS_TABLE)
        writer.endpoint, writer.user, writer.password = "http://clickhouse", "user", "secret"
        value = '{"event_id":"e1"}'
        with patch.object(analytics, "insert_batch") as insert:
            self.assertEqual(writer.map(value), value)
        self.assertEqual(insert.call_args.args[3], analytics.FACTS_TABLE)
        self.assertEqual(json.loads(insert.call_args.args[4][0]), {"event_id": "e1"})
        with patch.object(analytics, "insert_batch", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "ClickHouse insert failed"):
                writer.map(value)

    def test_map_transforms_window_rows_for_clickhouse_and_returns_kafka_value(self):
        writer = analytics.ClickHouseMap(analytics.WINDOWS_TABLE)
        writer.endpoint, writer.user, writer.password = "http://clickhouse", "user", "secret"
        value = json.dumps({
            "result_id": "a" * 64, "window_start": 1000, "window_end": 61000,
            "currency": "EUR", "country": "DE", "payment_method": "card",
            "payments": 1, "authorized": 1, "authorized_amount_minor": 1250,
            "authorization_rate": 1.0, "rejections": 0, "revision": 1,
            "schema_version": 1, "transformation_id": "payment-windows-v1",
            "input_dataset": "kafka:payment-processing-events-v1",
            "output_dataset": "kafka:payment-window-results-v1",
        })
        with patch.object(analytics, "insert_batch") as insert:
            self.assertEqual(writer.map(value), value)
        row = json.loads(insert.call_args.args[4][0])
        self.assertEqual(row["window_start_ms"], 1000)
        self.assertEqual(row["analytics_dataset"], "clickhouse:analytics.payment_window_aggregates")

    def test_map_rejects_unknown_table(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            analytics.ClickHouseMap("arbitrary")

    def test_fact_write_is_synchronous_and_has_explicit_errors(self):
        event = {
            "timestamp_ms": 1, "event_id": "e1", "payment_id": "p1",
            "account_id": "a1", "amount_minor": 100, "currency": "EUR",
            "country": "DE", "payment_method": "card", "outcome": "authorized",
            "schema_version": 1,
        }
        with patch.object(analytics, "insert_batch") as insert:
            analytics.write_payment_fact(event, "http://clickhouse", "user", "secret")
        self.assertEqual(insert.call_args.args[3], analytics.FACTS_TABLE)
        with patch.object(analytics, "insert_batch", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "payment_facts"):
                analytics.write_payment_fact(event, "http://clickhouse", "user", "secret")


if __name__ == "__main__":
    unittest.main()
