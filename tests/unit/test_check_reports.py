import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_reports import quality_report, security_report


class CheckReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_security_markdown_contains_findings_and_escapes_table_content(self):
        report = self.root / "module-api.md"
        security_report(report, "module-api", 10, {"Results": [{
            "Target": "chart/deployment.yaml", "Misconfigurations": [{
                "ID": "KSV-1", "Severity": "HIGH", "Title": "Unsafe | <image>",
                "Message": "first\nsecond", "Resolution": "Use *restricted* config",
                "CauseMetadata": {"StartLine": 24},
            }],
        }]})
        content = report.read_text()
        self.assertIn("**FINDINGS** (scanner exit 10)", content)
        self.assertIn("Findings: 1", content)
        self.assertIn("| HIGH | KSV-1 | chart/deployment.yaml | 24 |", content)
        self.assertIn("Unsafe &#124; &lt;image&gt;", content)
        self.assertIn("first<br>second", content)
        self.assertIn(r"Use \*restricted\* config", content)

    def test_clean_and_incomplete_scans_are_distinct(self):
        report = self.root / "solution.md"
        security_report(report, "solution", 0, {"Results": []})
        self.assertIn("No IaC findings", report.read_text())
        security_report(report, "solution", 1, None, "missing scanner")
        self.assertIn("**ERROR - scan incomplete**", report.read_text())
        self.assertIn("missing scanner", report.read_text())
        self.assertNotIn("No IaC findings", report.read_text())

    def test_quality_report_streams_full_log_and_preserves_failure_on_rerun(self):
        makefile = self.root / "Makefile"
        makefile.write_text("ci:\n\t@printf 'first ``` marker\\n'\n")
        self.assertEqual(quality_report(root=self.root), 0)
        report = self.root / ".local" / "quality" / "solution.md"
        self.assertIn("**PASS** (exit 0)", report.read_text())
        self.assertIn("````text", report.read_text())
        self.assertIn("first ``` marker", report.read_text())
        makefile.write_text("ci:\n\t@echo failing-check\n\t@exit 1\n\t@echo must-not-run\n")
        self.assertEqual(quality_report(root=self.root), 2)
        content = report.read_text()
        self.assertIn("**FAIL** (exit 2)", content)
        self.assertIn("failing-check", content)
        self.assertNotIn("first ``` marker", content)
        self.assertNotIn("must-not-run", content)
        self.assertNotIn("**PASS**", content)
        self.assertIn("failing-check", report.with_suffix(".log").read_text())

    def test_quality_module_scope_uses_existing_runner(self):
        module = self.root / "modules" / "api"
        module.mkdir(parents=True)
        (module / "module.yaml").write_text("name: api\ntype: api\n")
        (self.root / "Makefile").write_text("check-module:\n\t@echo checked-$(MODULE_NAME)\n")
        self.assertEqual(quality_report("api", self.root), 0)
        content = (self.root / ".local" / "quality" / "module-api.md").read_text()
        self.assertIn("make check-module MODULE_NAME=api", content)
        self.assertIn("checked-api", content)
        for name in ("../escape", "missing", ""):
            with self.subTest(module=name), self.assertRaisesRegex(ValueError, "unknown module"):
                quality_report(name, self.root)

    def test_missing_make_is_a_failed_report_not_stale_success(self):
        with patch("scripts.check_reports.subprocess.Popen", side_effect=FileNotFoundError("make missing")):
            self.assertEqual(quality_report(root=self.root), 1)
        content = (self.root / ".local" / "quality" / "solution.md").read_text()
        self.assertIn("**FAIL**", content)
        self.assertIn("make missing", content)

    def test_github_summary_is_compact_and_full_output_stays_in_artifact(self):
        summary = self.root / "step-summary"
        (self.root / "Makefile").write_text("ci:\n\t@echo full-check-output\n")
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            self.assertEqual(quality_report(root=self.root), 0)
        self.assertIn("**PASS**", summary.read_text())
        self.assertNotIn("full-check-output", summary.read_text())
        self.assertIn("report artifact", summary.read_text())
