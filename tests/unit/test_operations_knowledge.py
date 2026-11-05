import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "modules" / "operations-knowledge"
sys.path.insert(0, str(MODULE / "src"))

from operations_knowledge.evaluation import load_cases  # noqa: E402
from operations_knowledge.manifest import load_corpus  # noqa: E402


class OperationsKnowledgeRepositoryTests(unittest.TestCase):
    def test_repository_corpus_covers_docs_and_restricts_incidents(self):
        corpus = load_corpus(ROOT)
        paths = {source.path: source for source in corpus.sources}
        self.assertTrue(any(path.startswith("docs/runbooks/") for path in paths))
        self.assertTrue(any(path.startswith("docs/adr/ADR-") for path in paths))
        self.assertEqual(paths["modules/payment-api/openapi.yaml"].doc_type, "openapi")
        self.assertIn("modules/event-contracts/schemas/payment-initiated-v1.avsc", paths)
        incidents = [source for path, source in paths.items() if "/documents/incidents/" in path]
        self.assertGreaterEqual(len(incidents), 2)
        for source in incidents:
            self.assertEqual((source.classification, source.required_permission),
                            ("restricted", "knowledge:restricted"))

    def test_evaluation_set_loads(self):
        self.assertEqual(len(load_cases(MODULE / "evaluation" / "cases.yaml")["cases"]), 21)

    def test_dockerfile_default_matches_pinned_ai_runtime(self):
        pins = dict(line.split()[:2] for line in (ROOT / ".tool-versions").read_text().splitlines()
                    if line.strip() and not line.startswith("#"))
        dockerfile = (MODULE / "Dockerfile").read_text()
        self.assertEqual(re.search(r"^ARG AI_PYTHON_VERSION=(\S+)$", dockerfile, re.MULTILINE)[1],
                        pins["ai-python"])
        self.assertIn("COPY --from=ai-core", dockerfile)


if __name__ == "__main__":
    unittest.main()
