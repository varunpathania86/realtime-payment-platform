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

    def test_late_event_rows_identify_the_rejecting_job(self):
        event = {
            "timestamp_ms": 5000, "event_id": "e1", "payment_id": "p1", "account_id": "a1",
            "amount_minor": 700, "currency": "EUR", "country": "DE",
            "payment_method": "card", "outcome": "rejected", "schema_version": 1,
        }
        windows = analytics.late_event(json.dumps({
            "event": event, "reason": "all-windows-expired", "watermark_ms": 900000,
            "transformation_id": "payment-windows-v1",
        }))
        risk = analytics.late_event(json.dumps({
            "event": event, "reason": "risk-feature-lateness-expired", "watermark_ms": 950000,
        }))
        self.assertEqual(windows["job_name"], "payment-windows")
        self.assertEqual(windows["source_dataset"], "kafka:payment-events-too-late")
        self.assertEqual(risk["job_name"], "advisory-risk-features")
        self.assertEqual(risk["watermark_ms"], 950000)
        self.assertEqual(risk["output_dataset"], "clickhouse:analytics.payment_late_events")
        self.assertNotIn("account_id", risk)
        with self.assertRaises(KeyError):
            analytics.late_event(json.dumps({"event": event, "reason": "unknown", "watermark_ms": 1}))

    def test_risk_prediction_rows_keep_model_lineage_and_advisory_flag(self):
        row = analytics.risk_prediction(json.dumps({
            "schema_version": 1, "prediction_id": "b" * 64, "original_prediction_id": "c" * 64,
            "event_id": "e1", "payment_id": "p1", "account_id": "a1",
            "feature_timestamp": "2027-01-15T08:00:00Z", "feature_version": "risk-features-v1",
            "feature_hash": "d" * 64, "window_version": "account-currency-prior-history-v1",
            "window_revision": 2, "window_start_ms": 1000, "window_end_ms": 3601000,
            "model_name": "payment-risk", "model_version": "1.0.0", "model_sha256": "e" * 64,
            "risk_score": 0.73, "reason_codes": ["velocity"], "advisory_only": True,
            "prediction_timestamp": "2027-01-15T08:00:01.250000Z",
        }))
        self.assertEqual(row["event_time_ms"], 3601000)
        self.assertEqual(row["prediction_time_ms"], 1800000001250)
        self.assertEqual(row["window_revision"], 2)
        self.assertTrue(row["advisory_only"])
        self.assertEqual(row["reason_codes"], ["velocity"])
        self.assertNotIn("account_id", row)

    def test_map_uses_table_specific_mapper(self):
        writer = analytics.ClickHouseMap(analytics.LATE_TABLE)
        writer.endpoint, writer.user, writer.password = "http://clickhouse", "user", "secret"
        value = json.dumps({"event": {
            "timestamp_ms": 1, "event_id": "e1", "payment_id": "p1", "amount_minor": 1,
            "currency": "EUR", "country": "DE", "payment_method": "card", "outcome": "authorized",
        }, "reason": "all-windows-expired", "watermark_ms": 2})
        with patch.object(analytics, "insert_batch") as insert:
            self.assertEqual(writer.map(value), value)
        self.assertEqual(insert.call_args.args[3], analytics.LATE_TABLE)
        self.assertEqual(json.loads(insert.call_args.args[4][0])["reason"], "all-windows-expired")

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
