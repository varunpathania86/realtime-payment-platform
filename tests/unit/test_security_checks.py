import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.security_checks import scan, scan_modules


class SecurityChecksTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""})
        environment.start()
        self.addCleanup(environment.stop)
        (self.root / "platform").mkdir()
        (self.root / "trivy.yaml").write_text("exit-code: 10\n")
        (self.root / "solution.yaml").write_text("modules: [api, contract, catalog]\n")
        for name in ("api", "contract", "catalog", "unlisted"):
            module = self.root / "modules" / name
            module.mkdir(parents=True)
            (module / "module.yaml").write_text(f"name: {name}\ntype: library\n")
            if name != "catalog":
                (module / "Dockerfile").write_text("FROM scratch\n")

    def test_scan_scopes_follow_packaging_and_catalog_not_type(self):
        self.assertEqual(scan_modules(self.root), ["api", "contract"])

    def test_unknown_or_unimplemented_modules_are_rejected(self):
        for name in ("../platform", "unlisted", "catalog", None):
            with self.subTest(name=name), patch("scripts.security_checks.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "cataloged"):
                    scan("module", name, self.root)
                run.assert_not_called()

    def test_scan_reports_findings_and_execution_errors_without_masking_status(self):
        for code in (0, 10, 1, 2):
            with self.subTest(code=code):
                def execute(args, **kwargs):
                    output = Path(args[args.index("--output") + 1])
                    output.write_text(json.dumps({"Results": []}))
                    self.assertEqual(args[-1], str(self.root / "platform"))
                    self.assertNotIn("--exit-code", args)
                    return subprocess.CompletedProcess(args, code)

                with patch("scripts.security_checks.subprocess.run", side_effect=execute):
                    self.assertEqual(scan("solution", root=self.root), code)
                content = (self.root / ".local" / "security" / "solution.md").read_text()
                status = "PASS" if code == 0 else "FINDINGS" if code == 10 else "ERROR - scan incomplete"
                self.assertIn(f"**{status}**", content)

    def test_stale_report_is_removed_before_failed_scanner(self):
        report = self.root / ".local" / "security" / "module-api.json"
        report.parent.mkdir(parents=True)
        report.write_text("stale success")
        report.with_suffix(".md").write_text("stale pass")
        with patch("scripts.security_checks.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(scan("module", "api", self.root), 1)
        self.assertFalse(report.exists())
        self.assertIn("scan incomplete", report.with_suffix(".md").read_text())
        self.assertNotIn("stale pass", report.with_suffix(".md").read_text())

    def test_success_without_a_report_is_not_accepted(self):
        with patch("scripts.security_checks.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(ValueError, "valid JSON report"):
                scan("solution", root=self.root)
        self.assertIn("scan incomplete", (self.root / ".local/security/solution.md").read_text())

    def test_invalid_json_produces_an_error_markdown_report(self):
        def execute(args, **kwargs):
            Path(args[args.index("--output") + 1]).write_text("not-json")
            return subprocess.CompletedProcess(args, 0)

        with patch("scripts.security_checks.subprocess.run", side_effect=execute):
            with self.assertRaises(ValueError):
                scan("solution", root=self.root)
        self.assertIn("scan incomplete", (self.root / ".local/security/solution.md").read_text())
