import json
import subprocess
import unittest

from scripts import lakehouse


class FakeTrino:
    """Answers Trino CLI invocations from (substring, rows-or-error) rules, recording each SQL."""

    def __init__(self, rules):
        self.rules = rules
        self.calls = []

    def __call__(self, command, **_):
        catalog = command[command.index("--catalog") + 1]
        sql = command[-1]
        self.calls.append((catalog, sql))
        for needle, answer in self.rules:
            if needle in sql:
                if isinstance(answer, Exception):
                    return subprocess.CompletedProcess(command, 1, "", str(answer))
                return subprocess.CompletedProcess(command, 0, "\n".join(json.dumps(row) for row in answer), "")
        return subprocess.CompletedProcess(command, 0, "", "")


def healthy(curated=3, raw=14, specs=2, created=True):
    return FakeTrino([
        ("denied_check", Exception("Access Denied: Cannot create table" if created else "")),
        ("count(*) AS n FROM payment_events_raw", [{"n": raw}]),
        ("HAVING count(*) > 1", [{"n": 0}]),
        ("$snapshots", [{"snapshot_id": 42}]),
        ("FOR VERSION AS OF 42", [{"n": 1}]),
        ("count(review_note)", [{"n": curated}]),
        ("count(DISTINCT spec_id)", [{"n": specs}]),
        ("count(*) AS n FROM payment_events", [{"n": curated}]),
    ])


class LakehouseControlTests(unittest.TestCase):
    def test_trino_uses_an_explicit_context_and_read_only_catalog_by_default(self):
        fake = FakeTrino([("SELECT 1", [{"x": 1}])])
        self.assertEqual(lakehouse.trino("rpp-1", "SELECT 1", runner=fake), [{"x": 1}])
        self.assertEqual(fake.calls, [("lakehouse", "SELECT 1")])

    def test_verify_checks_reader_denial_time_travel_and_scratch_evolution(self):
        fake = healthy()
        lakehouse.verify("rpp-1", runner=fake, clock=lambda: 0, sleep=lambda _: None)
        admin = [sql for catalog, sql in fake.calls if catalog == "lakehouse_admin"]
        self.assertTrue(all(lakehouse.SCRATCH in sql or "$files" in sql or "lakehouse_admin.payments" in sql
            for sql in admin))
        self.assertIn("DROP TABLE IF EXISTS", admin[-1])
        readers = [sql for catalog, sql in fake.calls if catalog == "lakehouse"]
        self.assertFalse(any(word in sql for sql in readers for word in ("ALTER", "INSERT", "DROP")))

    def test_verify_fails_on_bad_invariants_and_always_drops_the_scratch_table(self):
        for fake in (healthy(raw=2), healthy(specs=1)):
            with self.subTest(), self.assertRaises(RuntimeError):
                lakehouse.verify("rpp-1", runner=fake, clock=lambda: 0, sleep=lambda _: None)
        fake = healthy(specs=1)
        with self.assertRaises(RuntimeError):
            lakehouse.verify("rpp-1", runner=fake, clock=lambda: 0, sleep=lambda _: None)
        self.assertIn("DROP TABLE IF EXISTS", fake.calls[-1][1])

    def test_verify_rejects_a_reader_catalog_that_accepts_writes(self):
        fake = healthy()
        fake.rules[0] = ("denied_check", [])
        with self.assertRaisesRegex(RuntimeError, "unexpectedly accepted"):
            lakehouse.verify("rpp-1", runner=fake, clock=lambda: 0, sleep=lambda _: None)

    def test_waiting_for_curated_rows_is_bounded(self):
        times = iter([0, 5, 300])
        fake = FakeTrino([("count(*) AS n FROM payment_events", [{"n": 0}])])
        with self.assertRaisesRegex(RuntimeError, "no curated rows"):
            lakehouse.wait_for_curated("rpp-1", 240, runner=fake, clock=lambda: next(times), sleep=lambda _: None)

    def test_maintenance_uses_admin_catalog_and_only_lowers_minimums_when_asked(self):
        fake = FakeTrino([("$files", [{"n": 4}])])
        lakehouse.maintain("rpp-1", runner=fake)
        procedures = [sql for _, sql in fake.calls if "EXECUTE" in sql]
        self.assertEqual(len(procedures), 2)
        self.assertTrue(all(catalog == "lakehouse_admin" for catalog, _ in fake.calls))
        self.assertFalse(any("SET SESSION" in sql for sql in procedures))
        for name in ("optimize", "expire_snapshots", "remove_orphan_files"):
            self.assertIn(name, procedures[0])
        fake = FakeTrino([("$files", [{"n": 4}])])
        lakehouse.maintain("rpp-1", retention="1h", runner=fake)
        self.assertIn("expire_snapshots_min_retention = '1h'", [sql for _, sql in fake.calls if "EXECUTE" in sql][0])
        for invalid in ("0s", "7", "1d; DROP TABLE x"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                lakehouse.maintain("rpp-1", retention=invalid, runner=fake)


if __name__ == "__main__":
    unittest.main()
