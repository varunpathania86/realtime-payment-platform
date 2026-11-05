import hashlib
import tempfile
import unittest
from pathlib import Path

from operations_knowledge.chunking import chunk_document, chunk_markdown, title_of
from operations_knowledge.manifest import bundle, load_corpus

MANIFEST = """schemaVersion: 1
chunkingVersion: test-v1
tenant: platform
sources:
- {glob: "docs/*.md", docType: "runbook", classification: "internal", requiredPermission: "knowledge:read"}
- {glob: "secret/*.md", docType: "incident", classification: "restricted", requiredPermission: "knowledge:restricted"}
"""


def corpus_root(directory: str, manifest: str = MANIFEST) -> Path:
    root = Path(directory)
    (root / "modules" / "operations-knowledge").mkdir(parents=True)
    (root / "modules" / "operations-knowledge" / "corpus.yaml").write_text(manifest)
    for path, text in {"docs/b.md": "# B\n", "docs/a.md": "# A\n", "secret/x.md": "# X\n"}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return root


class ManifestTests(unittest.TestCase):
    def test_sources_are_sorted_and_classified(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = load_corpus(corpus_root(directory))
        self.assertEqual([source.path for source in corpus.sources], ["docs/a.md", "docs/b.md", "secret/x.md"])
        self.assertEqual(corpus.sources[2].required_permission, "knowledge:restricted")

    def test_rejects_weak_permission_empty_glob_and_duplicates(self):
        broken = {
            "permission": MANIFEST.replace('requiredPermission: "knowledge:restricted"', 'requiredPermission: "knowledge:read"'),
            "empty": MANIFEST.replace("secret/*.md", "missing/*.md"),
            "duplicate": MANIFEST.replace("secret/*.md", "docs/a.md").replace("restricted", "internal")
            .replace("knowledge:internal", "knowledge:read"),
        }
        for name, manifest in broken.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    load_corpus(corpus_root(directory, manifest))

    def test_bundle_is_deterministic(self):
        digests = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as directory:
                root = corpus_root(directory)
                self.assertEqual(bundle(root, root / "corpus.tar.gz"), 3)
                digests.append(hashlib.sha256((root / "corpus.tar.gz").read_bytes()).hexdigest())
        self.assertEqual(digests[0], digests[1])


class ChunkingTests(unittest.TestCase):
    def test_heading_paths_code_blocks_and_mermaid(self):
        text = (
            "# Runbook\n\nIntro.\n\n## Diagnosis\n\n```bash\n# not a heading\nkubectl get pods\n```\n\n"
            "```mermaid\nflowchart LR\n  A --> B\n```\n\n### Logs\n\nRead the logs.\n"
        )
        chunks = chunk_markdown(text)
        self.assertEqual([chunk.heading for chunk in chunks],
                        ["Runbook", "Runbook > Diagnosis", "Runbook > Diagnosis > Logs"])
        self.assertIn("# not a heading", chunks[1].content)
        self.assertFalse(any("flowchart" in chunk.content for chunk in chunks))
        self.assertEqual(title_of(text, "fallback"), "Runbook")

    def test_long_sections_are_bounded_with_overlap(self):
        text = "# Long\n\n" + " ".join(f"word{index}" for index in range(1000))
        chunks = chunk_markdown(text)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(chunk.content) <= 1200 for chunk in chunks))

    def test_contracts_use_file_name_headings(self):
        text = '{"type": "record", "name": "PaymentInitiated", "fields": []}'
        chunks = chunk_document("modules/event-contracts/schemas/payment-initiated-v1.avsc", text)
        self.assertEqual([(chunk.heading, chunk.content) for chunk in chunks],
                        [("payment-initiated-v1.avsc", text)])
        self.assertEqual(chunk_document("a/b.md", "# T\n\nBody")[0].heading, "T")


if __name__ == "__main__":
    unittest.main()
