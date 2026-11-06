"""Intent routing and deterministic tool planning.

The model proposes an intent and live tools through a JSON schema; deterministic code then
restricts the proposal to the MCP allowlist and derives every tool argument from the question.
When the model is unavailable, keyword rules produce the same route shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

INTENTS = ("direct", "rag", "tools", "rag_tools")
LIVE_TOOLS = ("payment_metrics", "get_payment", "payment_history", "risk_model_summary")
KNOWLEDGE_TOOL = "search_knowledge"
UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

ROUTE_SCHEMA: dict[str, Any] = {
    "title": "route",
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "tools": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": list(LIVE_TOOLS)}},
    },
    "required": ["intent", "tools"],
    "additionalProperties": False,
}

ROUTER_PROMPT = """You route questions for a payment-platform operations assistant.
Choose:
- "rag": how-to, runbook, architecture, ADR, API, event-contract, support or vendor documentation questions.
- "tools": questions needing live data (counts, rates, amounts, a specific payment ID, risk scores, history).
- "rag_tools": needs both documentation and live data.
- "direct": greetings or questions about the assistant itself.
Tools (only for tools/rag_tools):
- payment_metrics: recent payment counts, authorization or rejection rates by country, currency or method.
- get_payment: one payment by its UUID.
- payment_history: daily payment history over days or weeks.
- risk_model_summary: risk model version usage and score distribution.
Examples:
Q: How do I restore the stream processor from a savepoint? -> {"intent":"rag","tools":[]}
Q: What is the failure rate by country in the last hour? -> {"intent":"tools","tools":["payment_metrics"]}
Q: Show payment 0b0c8f8e-4a59-4f6b-9a39-3f1f2c1d9e01 -> {"intent":"tools","tools":["get_payment"]}
Q: How did daily payment volume change over the last two weeks? -> {"intent":"tools","tools":["payment_history"]}
Q: Which risk model version is scoring payments and is that model documented? -> \
{"intent":"rag_tools","tools":["risk_model_summary"]}
Q: Hello! -> {"intent":"direct","tools":[]}"""

_GREETING = re.compile(r"(?i)^\s*(hi|hello|hey|thanks|thank you)\b|\bwhat can you do\b|\bwho are you\b")
_DOCS = re.compile(r"(?i)\b(how (do|should|does|can) (i|we|support|the)|why|explain|runbook|adr|documented|"
                    r"procedure|what should|when is|what (does|is) the .*(notice|policy|contract|schema))\b")
_DOCS_LEAD = re.compile(r"(?i)^\s*(why|how (do|should|does|can)|what should|explain|when is)\b")
_RISK = re.compile(r"(?i)\brisk\b.*\b(score|model|distribution|version)s?\b|\b(score|model)s?\b.*\brisk\b")
_HISTORY = re.compile(r"(?i)\b(daily|history|historical|trend|per day|over the (last|past) \d+ days?|"
                        r"(last|past) (\d+|two|three|seven) (days|weeks)|weeks?)\b")
_METRICS = re.compile(r"(?i)\b(rate|how many|count|volume|total|number of)\b.*\b(payments?|authori[sz]\w*|"
                        r"reject\w*|declin\w*)\b|\b(authori[sz]ation|rejection|failure) rate\b")
_GROUPS = (
    ("payment_method", re.compile(r"(?i)\b(payment )?method\b")),
    ("country", re.compile(r"(?i)\bcountr(y|ies)\b")),
    ("currency", re.compile(r"(?i)\bcurrenc(y|ies)\b")),
    ("outcome", re.compile(r"(?i)\boutcomes?\b")),
)
_DURATION = re.compile(r"(?i)\b(\d{1,5}|an?|one|two|three|seven)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?)\b")
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "seven": 7}


@dataclass
class Route:
    intent: str
    tools: list[str] = field(default_factory=list)
    source: str = "rules"


def keyword_route(question: str) -> Route:
    tools: list[str] = []
    if UUID.search(question):
        tools.append("get_payment")
    if _RISK.search(question):
        tools.append("risk_model_summary")
    if _HISTORY.search(question) and re.search(r"(?i)\bpayments?|volume\b", question):
        tools.append("payment_history")
    elif _METRICS.search(question):
        tools.append("payment_metrics")
    tools = tools[:2]
    docs = bool(_DOCS.search(question))
    # "Why/how do I ..." questions are documentation unless they name a payment or a time window.
    if _DOCS_LEAD.search(question) and not UUID.search(question) and _window_minutes(question) is None:
        tools = []
    if tools:
        return Route("rag_tools" if docs and re.search(r"(?i)\b(documented|runbook|adr)\b", question) else "tools", tools)
    if _GREETING.search(question) and not docs:
        return Route("direct")
    return Route("rag")


def sanitize_route(proposal: Any, question: str) -> Route:
    """Restrict a model proposal to the schema and allowlist; fill gaps from keyword rules."""
    rules = keyword_route(question)
    if not isinstance(proposal, dict) or proposal.get("intent") not in INTENTS:
        return rules
    intent = proposal["intent"]
    proposed = proposal.get("tools") if isinstance(proposal.get("tools"), list) else []
    tools = list(dict.fromkeys(tool for tool in proposed if tool in LIVE_TOOLS))
    if not UUID.search(question):
        tools = [tool for tool in tools if tool != "get_payment"]
    elif "get_payment" not in tools:
        tools.insert(0, "get_payment")
    if intent in ("direct", "rag"):
        # A small model often misses obvious live-data questions; add the rule-based tools.
        if rules.tools:
            return Route("rag_tools" if intent == "rag" else rules.intent, rules.tools, "llm+rules")
        return Route(intent, [], "llm")
    if not tools:
        tools = rules.tools
    if not tools:
        return Route("rag", [], "llm+rules")
    return Route(intent, tools[:2], "llm")


def _window_minutes(question: str) -> int | None:
    match = _DURATION.search(question)
    if match:
        amount = int(match[1]) if match[1].isdigit() else _NUMBERS[match[1].lower()]
        unit = match[2].lower()
        per = 1 if unit.startswith("min") else 60 if unit.startswith(("hour", "hr")) else \
            1440 if unit.startswith("day") else 10080
        return amount * per
    if re.search(r"(?i)\b(last|past) hour\b", question):
        return 60
    if re.search(r"(?i)\b(today|last day|past day)\b", question):
        return 1440
    if re.search(r"(?i)\b(last|past) week\b", question):
        return 10080
    return None


def _group(question: str, default: str) -> str:
    by = re.search(r"(?i)\b(by|per|for each)\s+([a-z ]{3,20})", question)
    for scope in ([by[2]] if by else []) + [question]:
        for name, pattern in _GROUPS:
            if pattern.search(scope):
                return name
    return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def tool_arguments(tool: str, question: str) -> dict[str, Any]:
    """Derive bounded arguments deterministically; the model never writes tool arguments."""
    minutes = _window_minutes(question)
    if tool == "get_payment":
        match = UUID.search(question)
        return {"payment_id": match[0].lower()} if match else {}
    if tool == "payment_metrics":
        return {"window_minutes": _clamp(minutes or 60, 5, 1440), "group_by": _group(question, "none")}
    if tool == "payment_history":
        days = _clamp(round((minutes or 7 * 1440) / 1440), 1, 30)
        return {"days": days, "group_by": _group(question, "outcome")}
    if tool == "risk_model_summary":
        return {"window_minutes": _clamp(minutes or 1440, 5, 10080)}
    raise ValueError(f"unknown tool {tool}")
