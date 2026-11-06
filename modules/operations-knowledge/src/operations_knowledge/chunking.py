"""Structure-aware Markdown chunking with LangChain text splitters."""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

CHUNK_CHARACTERS = 1200
CHUNK_OVERLAP = 150
HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
_MERMAID = re.compile(r"```mermaid\n.*?```\n?", re.S)


@dataclass(frozen=True)
class TextChunk:
    heading: str
    content: str

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.content) // 4)


def title_of(text: str, fallback: str) -> str:
    match = re.search(r"^# (.+)$", text, re.M)
    return match[1].strip() if match else fallback


def chunk_document(path: str, text: str) -> list[TextChunk]:
    if path.endswith(".md"):
        return chunk_markdown(text)
    return chunk_structured(path.rsplit("/", 1)[-1], text)


def chunk_structured(name: str, text: str) -> list[TextChunk]:
    # API and event contracts have no Markdown headings; size-based splits keep fields together.
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_CHARACTERS, chunk_overlap=CHUNK_OVERLAP)
    return [TextChunk(name, part.strip()) for part in splitter.split_text(text) if part.strip()]


def chunk_markdown(text: str) -> list[TextChunk]:
    # Diagrams are rendered structure, not prose; they add noise to both search indexes.
    sections = MarkdownHeaderTextSplitter(HEADERS, strip_headers=False).split_text(_MERMAID.sub("", text))
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_CHARACTERS, chunk_overlap=CHUNK_OVERLAP)
    chunks = []
    for document in splitter.split_documents(sections):
        heading = " > ".join(document.metadata[key] for _, key in HEADERS if key in document.metadata)
        content = document.page_content.strip()
        if content:
            chunks.append(TextChunk(heading or "Document", content))
    return chunks
