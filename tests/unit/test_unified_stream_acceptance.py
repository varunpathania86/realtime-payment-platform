import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from scripts import unified_stream_acceptance as acceptance


class UnifiedStreamAcceptanceTests(unittest.TestCase):
    def report(self, lakehouse=True):
        return {"acceptance": "passed", "lakehouse_enabled": lakehouse,
            "payments": [{"id": "00000000-0000-4000-8000-000000000001",
                "processing_event_id": "00000000-0000-4000-8000-000000000002"}]}

    def test_checks_actual_iceberg_lineage_after_drain(self):
        report = self.report()
        rows = [{"event_id": report["payments"][0]["processing_event_id"],
            "payment_id": report["payments"][0]["id"]}]
        with patch.object(acceptance.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(report))) as execute, \
                patch.object(acceptance, "trino", side_effect=[[], rows]) as query, \
                patch.object(acceptance.time, "sleep"), contextlib.redirect_stdout(io.StringIO()) as output:
            acceptance.verify("rpp")
        self.assertEqual(json.loads(output.getvalue())["iceberg_curated_events"], 1)
        self.assertEqual(query.call_count, 2)
        self.assertIn("--context", execute.call_args.args[0])
        self.assertIn("rpp", execute.call_args.args[0])
        self.assertIn("PYTHONPATH=/opt/job", execute.call_args.args[0])
        self.assertNotIn("capture_output", execute.call_args.kwargs)
        self.assertTrue(execute.call_args.kwargs["check"])

    def test_duplicate_iceberg_rows_fail_instead_of_passing_by_dict_collapse(self):
        report = self.report()
        row = {"event_id": report["payments"][0]["processing_event_id"],
            "payment_id": report["payments"][0]["id"]}
        with patch.object(acceptance.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(report))), patch.object(acceptance, "trino", return_value=[row, row]), \
                patch.object(acceptance.time, "monotonic", side_effect=[0, 181]):
            with self.assertRaisesRegex(TimeoutError, "without duplicates"):
                acceptance.verify("rpp")

    def test_disabled_lakehouse_is_explicit_and_not_queried(self):
        with patch.object(acceptance.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(self.report(False)))), patch.object(acceptance, "trino") as query, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            acceptance.verify("rpp")
        query.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["lakehouse_enabled"])

    def test_remote_failure_is_not_success_shaped(self):
        with patch.object(acceptance.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["kubectl"])):
            with self.assertRaises(subprocess.CalledProcessError):
                acceptance.verify("rpp")
