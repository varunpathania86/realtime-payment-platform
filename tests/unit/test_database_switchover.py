import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import database_switchover as switchover


class DatabaseSwitchoverTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / ".local" / "lab"
        self.state.mkdir(parents=True)
        (self.state / "owned").write_text("lab\n")
        (self.state / "full-stack").touch()
        (self.state / "topology.tfvars.json").write_text(json.dumps({
            "profiles": ["lab-a", "lab-b", "lab-c"], "full_stack": True,
            "addresses": ["172.19.0.2", "172.19.0.3", "172.19.0.4"],
        }))

    def command(self, args, **kwargs):
        if "get" in args:
            return json.dumps({"status": {"demotionToken": "test-token", "currentPrimary": args[args.index("cluster") + 1] + "-2"}})
        if "exec" in args:
            return "f\n"
        return ""

    def test_demote_before_promote_and_persist_new_primary(self):
        with patch.object(switchover, "ROOT", self.root), patch.object(switchover, "run", side_effect=self.command) as run:
            switchover.transition("lab", "pg-b")
        plans = [call.args[0] for call in run.call_args_list if "plan" in call.args[0]]
        targets = [next(value for value in args if value.startswith("-target=")) for args in plans]
        self.assertEqual(targets, [
            "-target=module.database_a[0].helm_release.database",
            "-target=module.database_b[0].helm_release.database",
            "-target=module.database_c[0].helm_release.database",
        ])
        role = json.loads((self.state / "database-role.json").read_text())
        self.assertEqual(role, {"database_primary": "pg-b", "promotion_tokens": {"pg-b": "test-token"}})
        self.assertEqual((self.state / "database-role.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.state / "database-transition.json").exists())

    def test_failure_retains_journal_and_refuses_different_target(self):
        def fail_plan(args, **kwargs):
            if "plan" in args:
                raise RuntimeError("deliberate Terraform failure")
            return ""
        with patch.object(switchover, "ROOT", self.root), patch.object(switchover, "run", side_effect=fail_plan):
            with self.assertRaisesRegex(RuntimeError, "deliberate"):
                switchover.transition("lab", "pg-b")
            self.assertTrue((self.state / "database-transition.json").exists())
            with self.assertRaisesRegex(ValueError, "different database transition"):
                switchover.transition("lab", "pg-c")
        with patch.object(switchover, "ROOT", self.root), patch.object(switchover, "run", side_effect=self.command):
            switchover.transition("lab", "pg-b")
        self.assertFalse((self.state / "database-transition.json").exists())

    def test_invalid_identity_is_rejected_before_commands(self):
        with patch.object(switchover, "ROOT", self.root), patch.object(switchover, "run") as run:
            for base, target in (("../lab", "pg-b"), ("lab", "pg-x")):
                with self.assertRaises(ValueError):
                    switchover.transition(base, target)
            (self.state / "full-stack").unlink()
            with self.assertRaises(ValueError):
                switchover.transition("lab", "pg-b")
            run.assert_not_called()

    def test_planned_switchover_refuses_an_existing_fence(self):
        (self.state / "database-fence.json").write_text("{}")
        with patch.object(switchover, "ROOT", self.root), patch.object(switchover, "run") as run:
            with self.assertRaisesRegex(ValueError, "fenced member"):
                switchover.transition("lab", "pg-b")
            run.assert_not_called()

    def test_completed_target_is_idempotent_only_when_verified_writable(self):
        with patch.object(switchover, "ROOT", self.root), patch.object(switchover, "run", side_effect=self.command) as run:
            switchover.transition("lab", "pg-a")
            self.assertEqual(run.call_count, 2)
            self.assertIn("exec", run.call_args.args[0])
            self.assertIn("pg-a-2", run.call_args.args[0])
        def standby(args, **kwargs):
            return self.command(args, **kwargs) if "get" in args else "t\n"
        with patch.object(switchover, "ROOT", self.root), patch.object(switchover, "run", side_effect=standby):
            with self.assertRaisesRegex(RuntimeError, "not writable"):
                switchover.transition("lab", "pg-a")
