"""Corpus manifest loading and deterministic bundling.

Uses only the standard library and PyYAML so the host can bundle the corpus with the
repository virtualenv; the ingestion job reads the same manifest inside the bundle.
"""

from __future__ import annotations

import argparse
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path

import yaml

MANIFEST = "modules/operations-knowledge/corpus.yaml"
PERMISSION_FOR = {"public": "knowledge:read", "internal": "knowledge:read", "restricted": "knowledge:restricted"}


@dataclass(frozen=True)
class Source:
    path: str
    doc_type: str
    classification: str
    required_permission: str


@dataclass(frozen=True)
class Corpus:
    chunking_version: str
    tenant: str
    sources: tuple[Source, ...]


def load_corpus(root: Path, manifest: str = MANIFEST) -> Corpus:
    document = yaml.safe_load((root / manifest).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise ValueError("corpus manifest must have schemaVersion 1")
    sources: dict[str, Source] = {}
    for rule in document.get("sources", []):
        classification, permission = rule["classification"], rule["requiredPermission"]
        if PERMISSION_FOR.get(classification) != permission:
            raise ValueError(f"{rule['glob']}: {classification} documents require {PERMISSION_FOR.get(classification)}")
        matches = sorted(path for path in root.glob(rule["glob"]) if path.is_file())
        if not matches:
            raise ValueError(f"corpus glob matched no files: {rule['glob']}")
        for path in matches:
            relative = path.relative_to(root).as_posix()
            if relative in sources:
                raise ValueError(f"{relative} matches more than one corpus rule")
            sources[relative] = Source(relative, rule["docType"], classification, permission)
    if not sources:
        raise ValueError("corpus manifest has no sources")
    return Corpus(str(document["chunkingVersion"]), str(document["tenant"]), tuple(sources.values()))


def bundle(root: Path, output: Path) -> int:
    corpus = load_corpus(root)
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in [MANIFEST, *(source.path for source in corpus.sources)]:
            data = (root / path).read_bytes()
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    return len(corpus.sources)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Bundled {bundle(args.root.resolve(), args.output)} corpus documents into {args.output}")


if __name__ == "__main__":
    main()
