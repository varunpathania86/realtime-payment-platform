import json
import subprocess
import unittest
from unittest import mock

from scripts import ai_platform

ORIGIN = "http://localhost:8080"
PAYMENT = "11111111-2222-4333-8444-555555555555"
USERS = {name: {"password": f"{name}-password"} for name in ("ops-viewer", "payments-clerk", "ops-engineer")}
SUITE = {
    "restrictedDocumentPrefix": "modules/operations-knowledge/documents/incidents/",
    "restrictedUsers": ["ops-engineer"],
    "cases": [
        {"id": "rag", "category": "rag", "user": "ops-viewer", "question": "How do I restore?",
        "expectedDocuments": ["docs/runbooks/flink-recovery.md"]},
        {"id": "tool", "category": "tool", "user": "payments-clerk", "question": "Show payment {payment_id}",
        "expectedTools": ["get_payment"]},
        {"id": "denied", "category": "authorization", "user": "payments-clerk", "question": "How do I restore?",
        "expectedStatus": "denied"},
        {"id": "secret", "category": "authorization", "user": "ops-viewer", "question": "Root cause?",
        "forbiddenText": ["AMBER-HERON-7731"]},
    ],
}


class FakePortal:
    def __init__(self, leak=False):
        self.leak, self.questions, self.logins = leak, [], []

    def __call__(self, method, path, host, headers, body):
        if headers.get("Origin") != ORIGIN:
            return 403, {}, ""
        if path == "/auth/login":
            self.logins.append(body["username"])
            if USERS[body["username"]]["password"] != body["password"]:
                return 401, {}, ""
            return 200, {"Set-Cookie": f"rpp_portal_session={body['username']}; HttpOnly; SameSite=Strict"}, "{}"
        user = headers["Cookie"].split("=", 1)[1]
        self.questions.append((user, body["question"]))
        if user == "payments-clerk" and "restore" in body["question"]:
            return 200, {}, json.dumps({"status": "denied", "answer": "", "citations": [], "retrieved": []})
        if body["question"].startswith("Show payment"):
            return 200, {}, json.dumps({"status": "answered", "answer": "found", "citations": [], "retrieved": [],
                                        "tools": [{"name": "get_payment", "status": "ok"}], "latency_ms": 900})
        answer = "AMBER-HERON-7731" if self.leak and "Root" in body["question"] else "Use a savepoint."
        document = "docs/runbooks/flink-recovery.md"
        return 200, {}, json.dumps({"status": "answered", "answer": answer, "retrieved": [document],
                                    "citations": [{"id": "1", "document_id": document, "heading": "Restore"}]})


class EvaluationTests(unittest.TestCase):
    def test_cases_run_as_their_user_with_one_sign_in_each(self):
        portal = FakePortal()
        results = ai_platform.evaluate(portal, ORIGIN, USERS, SUITE, PAYMENT, clock=iter(range(100)).__next__)
        self.assertEqual(sorted(portal.logins), ["ops-viewer", "payments-clerk"])
        self.assertIn(("payments-clerk", f"Show payment {PAYMENT}"), portal.questions)
        self.assertTrue(all(item["passed"] for item in results), results)
        self.assertEqual(results[1]["latency_ms"], 900)
        self.assertEqual(results[0]["latency_ms"], 1000)
        self.assertEqual(ai_platform.failures(results), [])

    def test_leaks_and_failed_negative_cases_fail_but_quality_misses_do_not(self):
        results = ai_platform.evaluate(FakePortal(leak=True), ORIGIN, USERS, SUITE, PAYMENT)
        self.assertEqual(ai_platform.failures(results), ["secret"])
        quality_miss = [{"id": "rag", "category": "rag", "leaks": [], "passed": False},
                        {"id": "inj", "category": "injection", "leaks": [], "passed": False}]
        self.assertEqual(ai_platform.failures(quality_miss), ["inj"])

    def test_http_errors_are_scored_as_failures_not_crashes(self):
        results = ai_platform.evaluate(lambda *request: (200, {"Set-Cookie": "rpp_portal_session=x; HttpOnly; "
                                                                "SameSite=Strict"}, "{}")
                                        if request[1] == "/auth/login" else (504, {}, ""),
                                        ORIGIN, USERS, SUITE, PAYMENT)
        self.assertEqual({item["status"] for item in results}, {"http-504"})
        self.assertEqual(ai_platform.failures(results), ["denied"])

    def test_wrong_password_stops_the_run(self):
        users = {**USERS, "ops-viewer": {"password": "wrong"}}
        with self.assertRaisesRegex(RuntimeError, "ops-viewer cannot sign in"):
            ai_platform.evaluate(FakePortal(), ORIGIN, users, SUITE, PAYMENT)

    def test_starter_cases_load_and_reference_configured_users(self):
        import sys
        sys.path.insert(0, str(ai_platform.KNOWLEDGE_SRC))
        from operations_knowledge.evaluation import load_cases

        suite = load_cases(ai_platform.CASES)
        bi = (ai_platform.ROOT / "platform/terraform/environments/local/bi.tf").read_text()
        for user in {case["user"] for case in suite["cases"]}:
            self.assertIn(f"    {user} ", bi)


class FixtureTests(unittest.TestCase):
    def test_rows_are_deterministic_synthetic_and_recent(self):
        now = 1_762_300_000_000
        facts, predictions = ai_platform.fixture_rows(now)
        self.assertEqual((facts, predictions), ai_platform.fixture_rows(now))
        self.assertEqual(len(facts), 600)
        self.assertEqual(len(predictions), 300)
        self.assertTrue(all(now - 86_400_000 < row["event_time_ms"] < now for row in facts))
        self.assertGreaterEqual(sum(now - row["event_time_ms"] < 3_600_000 for row in facts), 200)
        self.assertEqual({row["source_dataset"] for row in facts + predictions}, {ai_platform.FIXTURE_DATASET})
        self.assertEqual({row["outcome"] for row in facts}, {"authorized", "rejected"})
        self.assertTrue(all(row["advisory_only"] and 0 <= row["risk_score"] < 1 for row in predictions))
        self.assertTrue(all(len(row["prediction_id"]) == 64 for row in predictions))
        self.assertEqual(len({row["event_id"] for row in facts}), 600)

    def test_load_replaces_only_fixture_rows(self):
        calls = []

        def run(command, **options):
            calls.append((command, options.get("input")))
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch("subprocess.run", run):
            self.assertEqual(ai_platform.load_fixtures("rpp-a"), (600, 300))
        statements = [command[-1] for command, _ in calls]
        self.assertEqual(statements[0], "ALTER TABLE payment_facts DELETE WHERE source_dataset = "
                        "'synthetic-ai-fixtures' SETTINGS mutations_sync = 2")
        self.assertEqual(statements[1], "INSERT INTO payment_facts FORMAT JSONEachRow")
        self.assertEqual(len(calls[1][1].splitlines()), 600)
        self.assertIn("payment_risk_predictions", statements[2])
        self.assertTrue(all(command[:3] == ["kubectl", "--context", "rpp-a"] for command, _ in calls))


class CommandTests(unittest.TestCase):
    def test_profile_must_be_explicit(self):
        with self.assertRaises(SystemExit) as raised:
            ai_platform.main(["eval", "--profile", ""])
        self.assertIn("MINIKUBE_PROFILE", str(raised.exception))

    def test_targets_are_wired_through_make(self):
        makefile = (ai_platform.ROOT / "Makefile").read_text()
        for command in ("ingest", "fixtures", "eval"):
            self.assertIn(f'-m scripts.ai_platform {command} --profile "$(MINIKUBE_PROFILE)"', makefile)


if __name__ == "__main__":
    unittest.main()
