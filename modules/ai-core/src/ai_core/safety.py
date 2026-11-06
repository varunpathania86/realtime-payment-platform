"""Deterministic output redaction, prompt-injection signals and citation verification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Iterable

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)(\s*[:=]\s*)(\S+)"
)
_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    "[redacted-private-key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[redacted-token]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [redacted-token]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[redacted-email]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b"), "[redacted-iban]"),
]
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")

INJECTION_SIGNALS = {
    "override-instructions": re.compile(
        r"(?i)\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules|guidelines|prompt)\b"
    ),
    "reveal-system-prompt": re.compile(r"(?i)\b(reveal|print|show|repeat|leak)\b.{0,40}\bsystem\s+prompt\b"),
    "role-hijack": re.compile(r"(?i)\byou\s+are\s+now\b|\bact\s+as\s+(an?\s+)?(admin|root|developer\s+mode)\b"),
    "secret-exfiltration": re.compile(
        r"(?i)\b(reveal|print|show|send|exfiltrate|leak|output)\b.{0,50}"
        r"\b(passwords?|secrets?|credentials?|api\s*keys?|tokens?)\b"
    ),
    "tool-escalation": re.compile(r"(?i)\b(call|invoke|run|execute)\b.{0,40}\b(write|delete|drop|update)\b.{0,20}\btool"),
}
CITATION = re.compile(r"\[(S[1-9]\d?)\]")


def _luhn(digits: str) -> bool:
    total = 0
    for index, digit in enumerate(reversed(digits)):
        value = int(digit) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def redact(text: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match[1]}{match[2]}[redacted]", text)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match[0])
        return "[redacted-card]" if 13 <= len(digits) <= 19 and _luhn(digits) else match[0]

    return _CARD.sub(card, text)


def injection_signals(text: str) -> list[str]:
    return sorted(name for name, pattern in INJECTION_SIGNALS.items() if pattern.search(text))


@dataclass(frozen=True)
class CitationCheck:
    cited: list[str]
    unknown: list[str]

    @property
    def valid(self) -> bool:
        return bool(self.cited) and not self.unknown


def verify_citations(answer: str, source_ids: Iterable[str]) -> CitationCheck:
    allowed = set(source_ids)
    cited = list(dict.fromkeys(CITATION.findall(answer)))
    return CitationCheck([item for item in cited if item in allowed], [item for item in cited if item not in allowed])
