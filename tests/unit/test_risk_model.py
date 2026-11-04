import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.risk_model import bundle
from scripts import risk_acceptance


class ArtifactBundleTests(unittest.TestCase):
    def test_live_acceptance_is_explicit_bounded_and_syntax_checked(self):
        compile(risk_acceptance.PROGRAM, "risk_acceptance_remote", "exec")
        with patch("sys.argv", ["risk_acceptance", "--profile", "owned-a"]), \
                patch.object(risk_acceptance.subprocess, "run") as run:
            risk_acceptance.main()
        self.assertIn("owned-a", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 300)
        self.assertTrue(run.call_args.kwargs["check"])
        with patch("sys.argv", ["risk_acceptance", "--profile", "../other"]), \
                patch.object(risk_acceptance.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "profile"):
                risk_acceptance.main()
        run.assert_not_called()

    def test_local_checksums_and_safe_artifact_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = {
                name: hashlib.sha256(b"artifact").hexdigest() for name in (
                    "model.onnx", "metadata.json", "metrics.json", "golden.json",
                    "model-card.md", "payment_risk.zip",
                )
            }
            for name in manifest:
                (directory / name).write_bytes(b"artifact")
            (directory / "manifest.json").write_text(json.dumps(manifest))
            artifacts, checksum = bundle(directory)
            self.assertEqual(checksum, manifest["payment_risk.zip"])
            self.assertIn("manifest.json", artifacts)
            (directory / "model.onnx").write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "checksum"):
                bundle(directory)
            (directory / "model.onnx").write_bytes(b"artifact")
            manifest["../outside"] = "0" * 64
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "name"):
                bundle(directory)

    def test_missing_required_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "manifest.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "missing"):
                bundle(directory)
