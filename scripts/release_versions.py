"""Pure module release calculations and Git release-line classification."""

from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

STABLE = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
NAME = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
RC_BRANCH = re.compile(rf"release/({NAME})/({STABLE})-rc([1-9][0-9]*)")
VERSION = re.compile(
    rf"{STABLE}(?:-it[1-9][0-9]*(?:-br\.[0-9a-f]{{4}}|-rv\.[1-9][0-9]*)?"
    r"|-rc[1-9][0-9]*(?:-br\.[0-9a-f]{4}|-rv\.[1-9][0-9]*)?)?"
)
IMPACTS = {"none": 0, "patch": 1, "minor": 2, "major": 3}


def parse_version(version: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError(f"invalid module release version: {version!r}")
    return version


def target_version(stable: str, impacts: list[str]) -> str:
    match = re.fullmatch(STABLE, stable)
    if not match or not impacts or any(impact not in IMPACTS for impact in impacts):
        raise ValueError("a stable version and explicit valid impacts are required")
    major, minor, patch = map(int, match.groups())
    impact = max(IMPACTS[value] for value in impacts)
    if impact == 3:
        return f"{major + 1}.0.0"
    if impact == 2:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def active_iteration(pi: dict, now: datetime) -> int:
    if now.tzinfo is None:
        raise ValueError("iteration calculation requires a timezone-aware timestamp")
    start = date.fromisoformat(pi["startDate"])
    days = (now.astimezone(timezone.utc).date() - start).days
    length, count = pi["iterationLengthDays"], pi["iterations"]
    if length <= 0 or count <= 0 or days < 0 or days >= length * count:
        raise ValueError(f"iteration publication is outside PI {pi['id']}")
    return days // length + 1


def normalized_branch(branch: str) -> str:
    name = unicodedata.normalize("NFC", branch.removeprefix("refs/heads/"))
    if not name or name.strip() != name:
        raise ValueError("invalid branch name")
    return name


def branch_hash(branch: str) -> str:
    return hashlib.sha256(normalized_branch(branch).encode("utf-8")).hexdigest()[:4]


def branch_version(target: str, line: str, iteration: int | None, branch: str) -> str:
    suffix = f"it{iteration}" if line == "main" else line
    return parse_version(f"{target}-{suffix}-br.{branch_hash(branch)}")


def revision_version(target: str, line: str, iteration: int | None, counter: int) -> str:
    suffix = f"it{iteration}" if line == "main" else line
    return parse_version(f"{target}-{suffix}-rv.{counter}")


def classify_rc_branch(
    root: Path, branch: str, head: str, pr_targets: list[str]
) -> str | None:
    """Prefer explicit PR/maintenance targets; infer only unambiguous RC ancestry."""
    if branch == "main":
        return None
    explicit = {target for target in pr_targets if RC_BRANCH.fullmatch(target)}
    if RC_BRANCH.fullmatch(branch):
        explicit.add(branch)
    if len(explicit) > 1:
        raise ValueError("ambiguous module RC targets")
    if explicit:
        return explicit.pop()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    main_base = git("merge-base", "origin/main", head)
    refs = git("for-each-ref", "--format=%(refname:strip=3)", "refs/remotes/origin/release/")
    candidates = []
    for ref in refs.splitlines():
        if not RC_BRANCH.fullmatch(ref):
            continue
        rc_base = git("merge-base", f"origin/{ref}", head)
        if rc_base == main_base:
            continue
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", main_base, rc_base], cwd=root, check=False
        )
        if result.returncode not in (0, 1):
            raise ValueError("could not determine RC ancestry")
        if result.returncode == 0:
            candidates.append(ref)
    if len(candidates) > 1:
        raise ValueError("ambiguous RC ancestry; open a PR targeting the intended RC branch")
    return candidates[0] if candidates else None
