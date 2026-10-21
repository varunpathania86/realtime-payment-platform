import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.release_versions import (
    active_iteration, branch_hash, branch_version, classify_rc_branch,
    parse_version, revision_version, target_version,
)


class ReleaseVersionTests(unittest.TestCase):
    def test_iteration_utc_and_boundaries(self) -> None:
        pi = {"id": "PI-test", "startDate": "2026-10-26", "iterations": 6, "iterationLengthDays": 14}
        for timestamp, expected in [
            ("2026-10-26T00:00:00+00:00", 1),
            ("2026-11-08T23:59:59+00:00", 1),
            ("2026-11-09T00:00:00+00:00", 2),
            ("2027-01-17T23:59:59+00:00", 6),
            ("2026-10-26T01:00:00+02:00", None),
            ("2027-01-18T00:00:00+00:00", None),
        ]:
            with self.subTest(timestamp=timestamp):
                now = datetime.fromisoformat(timestamp)
                if expected is None:
                    with self.assertRaisesRegex(ValueError, "outside PI"):
                        active_iteration(pi, now)
                else:
                    self.assertEqual(active_iteration(pi, now), expected)
        with self.assertRaisesRegex(ValueError, "timezone"):
            active_iteration(pi, datetime(2026, 10, 26))

    def test_approved_impact_aggregation(self) -> None:
        for impacts, expected in [
            (["none"], "0.0.1"), (["patch"], "0.0.1"),
            (["none", "minor"], "0.1.0"), (["minor", "major", "patch"], "1.0.0"),
        ]:
            self.assertEqual(target_version("0.0.0", impacts), expected)
        for stable, impacts in [("0.1.0-rc1", ["patch"]), ("0.1.0", ["guess"]), ("0.1.0", [])]:
            with self.assertRaises(ValueError):
                target_version(stable, impacts)

    def test_hash_uses_normalized_branch_not_commit(self) -> None:
        self.assertEqual(branch_hash("refs/heads/feature/contracts"), branch_hash("feature/contracts"))
        self.assertEqual(len(branch_hash("feature/contracts")), 4)
        self.assertNotEqual(branch_hash("Feature/contracts"), branch_hash("feature/contracts"))
        self.assertEqual(branch_hash("feature/caf\u00e9"), branch_hash("feature/cafe\u0301"))
        self.assertEqual(branch_hash("feature/contracts"), "ebcb")

    def test_locked_versions(self) -> None:
        for version in [
            "0.2.1", "0.2.2-it1-br.0ac2", "0.2.2-it1-rv.1", "0.2.2-it1",
            "0.2.2-rc1", "0.2.2-rc1-br.c84d", "0.2.2-rc1-rv.1", "0.2.2-rc2",
        ]:
            self.assertEqual(parse_version(version), version)
        for version in ["v0.2.1", "0.02.1", "0.2.2-it0", "0.2.2-it1-rv.0", "0.2.2-rc1-br.abcdef"]:
            with self.assertRaises(ValueError):
                parse_version(version)
        self.assertEqual(revision_version("0.2.2", "main", 2, 3), "0.2.2-it2-rv.3")
        self.assertRegex(branch_version("0.2.2", "rc2", None, "fix/a"), r"^0.2.2-rc2-br\.[a-f0-9]{4}$")

    def test_rc_requires_actual_release_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", *args], cwd=root, check=True, capture_output=True, text=True
                ).stdout.strip()

            git("init", "-b", "main")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            git("commit", "--allow-empty", "-m", "base")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            rc = "release/event-contracts/0.0.1-rc1"
            git("commit", "--allow-empty", "-m", "rc divergence")
            git("update-ref", f"refs/remotes/origin/{rc}", "HEAD")
            rc_commit = git("rev-parse", "HEAD")
            self.assertEqual(classify_rc_branch(root, "hotfix/fix", rc_commit, []), rc)
            base = git("rev-parse", "origin/main")
            self.assertIsNone(classify_rc_branch(root, "feature/ordinary", base, []))
            self.assertIsNone(classify_rc_branch(root, "main", rc_commit, []))
            self.assertEqual(classify_rc_branch(root, "hotfix/fix", base, [rc]), rc)
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                classify_rc_branch(root, "hotfix/fix", base, [rc, "release/payment-api/0.0.1-rc1"])


if __name__ == "__main__":
    unittest.main()
