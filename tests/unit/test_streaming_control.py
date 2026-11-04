import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from scripts.module_catalog import ROOT

SPEC = importlib.util.spec_from_file_location("streaming_control", ROOT / "scripts/streaming-control.py")
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class StreamingControlTests(unittest.TestCase):
    def test_remote_execution_has_a_real_process_deadline(self):
        error = CONTROL.subprocess.TimeoutExpired("kubectl", 180)
        with patch.object(CONTROL.subprocess, "run", side_effect=error) as run:
            with self.assertRaises(CONTROL.subprocess.TimeoutExpired):
                CONTROL.command("isolated-a", "exec", "deployment/flink-jobs-jobmanager", "--", "python")
        self.assertEqual(run.call_args.kwargs["timeout"], 180)
        self.assertIn("isolated-a", run.call_args.args[0])

    def invoke_guard(self, deployment, restore="", jobs=None, settings=None):
        argv = ["streaming-control", "guard", "--profile", "isolated-a"]
        inventory = json.dumps({"local_images": {"flink-jobs": "rpp-local/flink-jobs:dev-new"}})
        with patch.object(sys, "argv", argv), \
            patch.dict(os.environ, {"STREAMING_RESTORE_PATH": restore}), \
            patch.object(CONTROL, "command", return_value=deployment), \
            patch.object(CONTROL, "rest", return_value={"jobs": jobs or []}), \
            patch.object(Path, "exists", return_value=settings is not None), \
            patch.object(Path, "read_text", side_effect=[inventory, json.dumps(settings)] if settings is not None else [inventory]):
            CONTROL.main()

    def deployment(self, image):
        return json.dumps({"spec": {"template": {"spec": {"containers": [{"image": image}]}}}})

    def test_initial_install_and_unchanged_image_are_idempotent(self):
        self.invoke_guard("")
        self.invoke_guard(self.deployment("rpp-local/flink-jobs:dev-new"))

    def test_changed_stateful_image_needs_explicit_restore_and_stopped_job(self):
        changed = self.deployment("rpp-local/flink-jobs:dev-old")
        with self.assertRaisesRegex(ValueError, "STREAMING_RESTORE_PATH"):
            self.invoke_guard(changed)
        with self.assertRaisesRegex(ValueError, "stop the existing"):
            self.invoke_guard(changed, "s3://flink-state/savepoints/approved", [{"state": "RUNNING"}])
        self.invoke_guard(changed, "s3://flink-state/savepoints/approved", [{"state": "CANCELED"}])

    def test_profile_is_validated_before_any_command(self):
        with patch.object(sys, "argv", ["streaming-control", "fixtures", "--profile", "../main"]), \
            patch.object(CONTROL, "command") as command:
            with self.assertRaisesRegex(ValueError, "invalid explicit"):
                CONTROL.main()
            command.assert_not_called()

    def test_risk_configuration_change_needs_a_stopped_job_and_restore_even_with_same_image(self):
        deployment = self.deployment("rpp-local/flink-jobs:dev-new")
        settings = {"risk_enabled": True, "risk_package_key": "payment-risk/1.0.0/payment_risk.zip",
            "risk_package_sha256": "a" * 64}
        with self.assertRaisesRegex(ValueError, "risk configuration changed"):
            self.invoke_guard(deployment, settings=settings)
        with self.assertRaisesRegex(ValueError, "stop the existing"):
            self.invoke_guard(deployment, "s3://flink-state/savepoints/approved",
                [{"state": "RUNNING"}], settings=settings)
        self.invoke_guard(deployment, "s3://flink-state/savepoints/approved",
            [{"state": "CANCELED"}], settings=settings)
