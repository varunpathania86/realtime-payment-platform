import unittest
from pathlib import Path

from operations_knowledge.evaluation import load_cases, markdown_report, score_case, summarize

SUITE = load_cases(Path(__file__).parents[1] / "evaluation" / "cases.yaml")
CASES = {case["id"]: case for case in SUITE["cases"]}
INCIDENT = "modules/operations-knowledge/documents/incidents/INC-2041-analytics-ingestion-lag.md"


def citation(document, heading="Runbook > Mitigation"):
    return {"id": "S1", "document_id": document, "heading": heading}


class EvaluationTests(unittest.TestCase):
    def test_starter_set_has_required_shape(self):
        self.assertEqual(len(SUITE["cases"]), 21)

    def test_rag_recall_and_citation_correctness(self):
        case = CASES["rag-savepoint-restore"]
        good = {"status": "answered", "retrieved": ["docs/runbooks/flink-recovery.md"],
                "citations": [citation("docs/runbooks/flink-recovery.md")], "latency_ms": 900}
        result = score_case(case, good, SUITE)
        self.assertEqual((result["recall_at_5"], result["citation_correct"], result["passed"]), (1.0, True, True))
        wrong = {**good, "citations": [citation("docs/adr/ADR-001.md")]}
        self.assertFalse(score_case(case, wrong, SUITE)["passed"])

    def test_restricted_documents_and_canaries_are_leaks_for_unprivileged_users(self):
        case = CASES["auth-incident-root-cause"]
        leaked = {"status": "answered", "answer": "Code AMBER-HERON-7731", "retrieved": [INCIDENT], "citations": []}
        result = score_case(case, leaked, SUITE)
        self.assertEqual(len(result["leaks"]), 2)
        self.assertFalse(result["passed"])
        allowed = score_case(CASES["rag-restricted-incident"], {
            "status": "answered", "retrieved": [INCIDENT], "citations": [citation(INCIDENT)]}, SUITE)
        self.assertEqual(allowed["leaks"], [])

    def test_status_tools_and_quarantined_headings(self):
        self.assertTrue(score_case(CASES["injection-direct-override"], {"status": "rejected"}, SUITE)["passed"])
        self.assertFalse(score_case(CASES["auth-no-knowledge-permission"], {"status": "answered"}, SUITE)["passed"])
        vendor = {"status": "answered", "citations": [citation("x", "Acquirer maintenance notes > Vendor notice")]}
        self.assertFalse(score_case(CASES["injection-vendor-notice"], vendor, SUITE)["passed"])
        tool = score_case(CASES["tool-risk-model"], {"status": "answered", "tools": [{"name": "risk_model_summary"}]},
                            SUITE)
        self.assertTrue(tool["passed"])

    def test_summary_and_report(self):
        results = [
            score_case(CASES["injection-direct-override"], {"status": "rejected", "latency_ms": 10}, SUITE),
            score_case(CASES["auth-escalation-code"], {"status": "answered", "answer": "CORAL-FINCH-2208",
                                                        "latency_ms": 30}, SUITE),
        ]
        summary = summarize(results)
        self.assertEqual((summary["authorization_leaks"], summary["passed"], summary["latency_p95_ms"]), (1, 1, 30))
        self.assertIn("| auth-escalation-code | authorization |", markdown_report(summary, results))


if __name__ == "__main__":
    unittest.main()
