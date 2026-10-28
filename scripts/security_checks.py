"""Run separate, truthful advisory IaC checks for the solution and catalog modules."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.module_catalog import ROOT, _load_yaml, _module_manifests
from scripts.check_reports import security_report


def scan_modules(root: Path = ROOT) -> list[str]:
    catalog = _load_yaml(root / "solution.yaml")["modules"]
    return sorted(
        name for name, (manifest, _) in _module_manifests(root).items()
        if name in catalog and (
            (manifest.parent / "Dockerfile").is_file()
            or (manifest.parent / "chart" / "Chart.yaml").is_file()
            or any(manifest.parent.rglob("*.tf"))
        )
    )


def scan(scope: str, module: str | None = None, root: Path = ROOT) -> int:
    if scope == "module":
        if module not in scan_modules(root):
            raise ValueError(f"module has no cataloged IaC packaging: {module!r}")
        target = root / "modules" / module
        label = f"module-{module}"
    else:
        target = root / "platform"
        label = "solution"
    reports = root / ".local" / "security"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"{label}.json"
    markdown = report.with_suffix(".md")
    report.unlink(missing_ok=True)
    markdown.unlink(missing_ok=True)
    data = None
    exceptions = target / ".trivyignore.yaml"
    exception_entries = _load_yaml(exceptions).get("misconfigurations", []) if exceptions.is_file() else []
    ignore_args = ["--ignorefile", str(exceptions) if exceptions.is_file() else ""]
    try:
        result = subprocess.run(
            [
                "trivy", "config", "--config", str(root / "trivy.yaml"),
                *ignore_args,
                "--output", str(report), str(target),
            ],
            cwd=root, check=False,
        )
        if result.returncode in (0, 10):
            if not report.is_file():
                raise ValueError(f"{label}: scanner did not produce a valid JSON report")
            data = json.loads(report.read_text())
            if not isinstance(data, dict) or not isinstance(data.get("Results"), list):
                raise ValueError(f"{label}: scanner did not produce a valid JSON report")
    except (OSError, ValueError) as error:
        security_report(markdown, label, 1, None, str(error))
        raise
    security_report(markdown, label, result.returncode, data, exceptions=exception_entries)
    if result.returncode == 10:
        print(f"FINDINGS {label}: inspect {report}; advisory does not mean passed.", file=sys.stderr)
    elif result.returncode != 0:
        print(f"ERROR {label}: scanner failed (exit {result.returncode}); scan is not complete.", file=sys.stderr)
    else:
        print(f"PASS {label}: report {report}")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "solution", "module", "all"])
    parser.add_argument("--module")
    args = parser.parse_args()
    try:
        if args.action == "plan":
            matrix = {"include": [{"module": name} for name in scan_modules()]}
            if output := os.environ.get("GITHUB_OUTPUT"):
                with Path(output).open("a") as stream:
                    stream.write(f"matrix={json.dumps(matrix)}\n")
                    stream.write(f"has_modules={'true' if matrix['include'] else 'false'}\n")
            else:
                print(json.dumps(matrix))
            return 0
        if args.action == "all":
            results = [scan("solution")]
            results.extend(scan("module", name) for name in scan_modules())
            return next((code for code in results if code not in (0, 10)), 10 if 10 in results else 0)
        return scan(args.action, args.module)
    except (OSError, ValueError) as error:
        print(f"ERROR security checks: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
