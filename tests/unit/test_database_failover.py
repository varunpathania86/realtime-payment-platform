import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import database_failover as recovery


class DatabaseFailoverTests(unittest.TestCase):
    def evidence(self):
        return {
            "pg-b": {"system": "42", "timeline": "1", "recovery": "t", "received": "0/ABC"},
            "pg-c": {"system": "42", "timeline": "1", "recovery": "t", "received": "0/ABD"},
        }

    def test_only_greatest_durable_wal_can_be_promoted(self):
        self.assertEqual(recovery.candidate(self.evidence(), "pg-c"), 2749)
        with self.assertRaisesRegex(ValueError, "behind"):
            recovery.candidate(self.evidence(), "pg-b")

    def test_refuse_missing_conflicting_or_writable_survivor(self):
        with self.assertRaisesRegex(ValueError, "both"):
            recovery.candidate({"pg-b": self.evidence()["pg-b"]}, "pg-b")
        for field, value in (("timeline", "2"), ("system", "99"), ("recovery", "f"), ("received", "")):
            evidence = self.evidence()
            evidence["pg-b"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                recovery.candidate(evidence, "pg-c")

    def test_refuse_incomplete_local_standby_evidence(self):
        for output in ("", "t|42|1", "t|42|1|", "t||1|0/ABC"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "incomplete"):
                recovery.standby_state(output)

    def test_network_identity_failure_never_stops_a_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".local/lab"
            state.mkdir(parents=True)
            (state / "owned").write_text("lab\n")
            (state / "full-stack").touch()
            (state / "topology.tfvars.json").write_text(json.dumps({
                "profiles": ["lab-a", "lab-b", "lab-c"], "database_primary": "pg-a",
            }))
            with patch.object(recovery, "ROOT", root), patch.object(
                recovery, "run", return_value='[{"Labels":{"rpp.multi":"somebody-else"}}]',
            ) as run:
                with self.assertRaisesRegex(ValueError, "ownership mismatch"):
                    recovery.failover("lab", "pg-b")
                self.assertEqual(run.call_count, 1)
            self.assertFalse((state / "database-role.json").exists())
            self.assertEqual(json.loads((state / "database-fence.json").read_text())["phase"], "fence")

    def test_fence_then_refuse_lagging_target_and_allow_greatest_durable_survivor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".local/lab"
            state.mkdir(parents=True)
            (state / "owned").write_text("lab\n")
            (state / "full-stack").touch()
            (state / "topology.tfvars.json").write_text(json.dumps({
                "profiles": ["lab-a", "lab-b", "lab-c"], "database_primary": "pg-a",
            }))
            calls = []
            inspecting = {}
            local_override = {}

            def command(args, **kwargs):
                calls.append(args)
                if args[:3] == ["docker", "network", "inspect"]:
                    return '[{"Labels":{"rpp.multi":"lab"}}]'
                if args[:2] == ["docker", "inspect"]:
                    disconnected = any("disconnect" in call for call in calls)
                    return json.dumps([{
                        "Config": {"Labels": {"name.minikube.sigs.k8s.io": "lab-a"}},
                        "State": {"Running": not disconnected},
                        "NetworkSettings": {"Networks": {} if disconnected else {"lab-net": {}}},
                    }])
                if args[0] == "kubectl":
                    if "get" in args:
                        member = "pg-b" if "lab-b" in args else "pg-c"
                        if "pods" in args:
                            inspecting[member] = True
                            return json.dumps({"items": [{"metadata": {"name": member + "-2"}}]})
                        inspecting[member] = False
                        return json.dumps({"status": {"currentPrimary": member + "-2"}})
                    if "pg_control_system()" in args[-1]:
                        member = "pg-b" if "lab-b" in args else "pg-c"
                        if inspecting.get(member) and member in local_override:
                            return local_override[member]
                        return "t|42|1|0/ABC\n" if "lab-b" in args else "t|42|1|0/ABD\n"
                    if args[-1] == "SELECT pg_last_wal_replay_lsn()":
                        return "0/ABD\n"
                    if args[-1] == "SELECT pg_last_wal_receive_lsn()":
                        return "0/ABC\n" if "lab-b" in args else "0/ABD\n"
                    return "f\n"
                return ""

            with patch.object(recovery, "ROOT", root), patch.object(recovery, "run", side_effect=command):
                with self.assertRaisesRegex(ValueError, "behind"):
                    recovery.failover("lab", "pg-b")
                self.assertFalse(any("plan" in call for call in calls))
                self.assertEqual(json.loads((state / "database-fence.json").read_text())["phase"], "fence")
                for row, reason in (
                    ("t|42|1|0/ABE", "ahead"), ("t|99|1|0/ABC", "identity"),
                    ("t|42|2|0/ABC", "identity"), ("f|42|1|0/ABC", "identity"),
                    ("t|42|1|", "incomplete"),
                ):
                    local_override["pg-b"] = row
                    with self.subTest(local=row), self.assertRaisesRegex(ValueError, reason):
                        recovery.failover("lab", "pg-c")
                    self.assertFalse(any("plan" in call for call in calls))
                local_override.clear()
                recovery.failover("lab", "pg-c")
                recovery.failover("lab", "pg-c")
                with self.assertRaisesRegex(ValueError, "promotion is pending"):
                    recovery.failover("lab", "pg-b")
            first_sql = next(index for index, call in enumerate(calls) if call[0] == "kubectl")
            disconnect = next(index for index, call in enumerate(calls) if "disconnect" in call)
            self.assertLess(disconnect, first_sql)
            plans = [call for call in calls if "plan" in call]
            self.assertEqual([
                next(value for value in call if value.startswith("-target=")) for call in plans
            ], ["-target=module.database_c[0].helm_release.database",
                "-target=module.database_b[0].helm_release.database",
                "-target=module.database_b[0].helm_release.database"])
            fence = json.loads((state / "database-fence.json").read_text())
            self.assertEqual(fence["phase"], "complete")
            self.assertEqual(fence["old"], "pg-a")
            self.assertEqual(json.loads((state / "database-role.json").read_text())["database_primary"], "pg-c")
            self.assertEqual((state / "database-fence.json").stat().st_mode & 0o777, 0o600)
