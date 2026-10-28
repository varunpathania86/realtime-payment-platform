"""Write Markdown check reports and run quality checks without hiding failures."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import os
from pathlib import Path
import re
import subprocess
import sys

from scripts.module_catalog import ROOT, _module_manifests


def markdown_text(value: object) -> str:
    text = html.escape(str(value), quote=False)
    text = re.sub(r"([\\`*_\[\]])", r"\\\1", text)
    return text.replace("|", "&#124;").replace("\r", "").replace("\n", "<br>")


def write_report(path: Path, summary: str, details: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary + details, encoding="utf-8")
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(step_summary).open("a", encoding="utf-8") as stream:
            stream.write(summary + "\nFull Markdown report is in this job's report artifact.\n\n")


def security_report(
    path: Path, label: str, code: int, data: dict | None, error: str = "",
    exceptions: list[dict] | None = None,
) -> None:
    status = "PASS" if code == 0 else "FINDINGS" if code == 10 else "ERROR - scan incomplete"
    findings = [
        (result["Target"], finding)
        for result in (data or {}).get("Results", [])
        for finding in result.get("Misconfigurations", [])
    ]
    summary = (
        f"# Security report: {markdown_text(label)}\n\n"
        f"- Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n"
        f"- Status: **{status}** (scanner exit {code})\n"
        "- Scope: Trivy IaC misconfigurations only; advisory does not mean passed.\n"
    )
    if code in (0, 10):
        summary += f"- Findings: {len(findings)}\n"
    if error:
        summary += f"- Error: {markdown_text(error)}\n"
    summary += "\n"
    details = ""
    if findings:
        details = "| Severity | Check | Target | Line | Finding | Message | Remediation |\n"
        details += "|---|---|---|---|---|---|---|\n"
        for target, finding in findings:
            row = (
                finding.get("Severity", "UNKNOWN"), finding.get("ID", ""), target,
                finding.get("CauseMetadata", {}).get("StartLine", ""),
                finding.get("Title", ""), finding.get("Message", ""), finding.get("Resolution", ""),
            )
            details += "| " + " | ".join(markdown_text(value) for value in row) + " |\n"
        details += "\n"
    elif code == 0:
        details = "No IaC findings reported in this scope.\n"
    elif code != 10:
        details = "The scan did not complete; this is not evidence of a clean scan. Inspect the job/terminal log.\n"
    if exceptions:
        details += "\n## Configured exceptions\n\n"
        details += "These are scoped policy exceptions, not fixes or proof that a rule matched. Expired entries no longer suppress findings.\n\n"
        details += "| Check | Paths | Review by / expiry | Justification |\n|---|---|---|---|\n"
        for exception in exceptions:
            row = (
                exception["id"], ", ".join(exception["paths"]),
                exception["expired_at"], exception["statement"],
            )
            details += "| " + " | ".join(markdown_text(value) for value in row) + " |\n"
    write_report(path, summary, details)


def quality_report(module: str | None = None, root: Path = ROOT) -> int:
    if module is not None and module not in _module_manifests(root):
        raise ValueError(f"unknown module: {module!r}")
    label = f"module-{module}" if module is not None else "solution"
    command = ["make", "check-module", f"MODULE_NAME={module}"] if module is not None else ["make", "ci"]
    reports = root / ".local" / "quality"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"{label}.md"
    report.unlink(missing_ok=True)
    log = reports / f"{label}.log"
    with log.open("w", encoding="utf-8") as output:
        try:
            with subprocess.Popen(
                command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            ) as process:
                assert process.stdout is not None
                for line in process.stdout:
                    sys.stdout.write(line)
                    output.write(line)
                code = process.wait()
        except OSError as error:
            message = f"ERROR quality checks: {error}\n"
            print(message, file=sys.stderr, end="")
            output.write(message)
            code = 1
    content = log.read_text(encoding="utf-8")
    content = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", content)
    fence = "`" * max(3, 1 + max((len(match[0]) for match in re.finditer(r"`+", content)), default=0))
    summary = (
        f"# Quality report: {markdown_text(label)}\n\n"
        f"- Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n"
        f"- Command: `{' '.join(command)}`\n"
        f"- Status: **{'PASS' if code == 0 else 'FAIL'}** (exit {code})\n"
        "- This is a command-result report, not coverage or a Sonar-style quality score.\n"
        "- Checks are fail-fast; a failed command does not prove later checks ran.\n\n"
    )
    write_report(report, summary, f"## Check output\n\n{fence}text\n{content}\n{fence}\n")
    print(f"Quality report: {report}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module")
    args = parser.parse_args()
    try:
        return quality_report(args.module)
    except (OSError, ValueError) as error:
        print(f"ERROR quality report: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
