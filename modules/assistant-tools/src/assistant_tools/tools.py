"""The allowlisted, read-only platform tools exposed over MCP.

Each tool checks the caller's permission itself, validates and bounds its arguments, runs a fixed
parameterized query (no model-written SQL) and redacts string fields before returning. Results are
plain dictionaries with a `status` of ok, denied, invalid, not_found or unavailable so the
orchestrator can report tool outcomes without parsing errors.
"""

from __future__ import annotations

import re
from typing import Any

from ai_core.identity import Principal
from ai_core.safety import redact
from assistant_tools.backends import ClickHouse, KnowledgeIndex, PaymentApi, Trino

TOOL_PERMISSIONS = {
    "search_knowledge": "knowledge:read",
    "get_payment": "payments:read",
    "payment_metrics": "analytics:read",
    "payment_history": "analytics:read",
    "risk_model_summary": "analytics:read",
}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Allowlisted grouping expressions; callers choose a key, never SQL text.
METRIC_GROUPS = {
    "none": "'all'", "currency": "toString(currency)", "country": "toString(country)",
    "payment_method": "payment_method", "outcome": "toString(outcome)",
}
HISTORY_GROUPS = {"currency": "currency", "country": "country", "payment_method": "payment_method",
                    "outcome": "outcome"}

METRICS_SQL = """
SELECT {group} AS dimension, count() AS payments,
        countIf(outcome = 'authorized') AS authorized, countIf(outcome = 'rejected') AS rejected,
        round(authorized / payments, 4) AS authorization_rate,
        formatDateTime(min(event_time), '%FT%TZ', 'UTC') AS first_event,
        formatDateTime(max(event_time), '%FT%TZ', 'UTC') AS last_event
FROM analytics.payment_facts FINAL
WHERE event_time >= now64(3) - toIntervalMinute({{window_minutes:UInt32}})
GROUP BY dimension ORDER BY payments DESC LIMIT 20
"""

RISK_SQL = """
SELECT model_name, model_version, count() AS predictions,
        round(avg(risk_score), 4) AS mean_score, round(quantile(0.95)(risk_score), 4) AS p95_score,
        countIf(risk_score >= 0.8) AS high_risk_predictions
FROM analytics.payment_risk_predictions FINAL
WHERE event_time >= now64(3) - toIntervalMinute({window_minutes:UInt32})
GROUP BY model_name, model_version ORDER BY predictions DESC LIMIT 20
"""

HISTORY_SQL = """
SELECT CAST(CAST(event_time AS date) AS varchar) AS day, {group} AS dimension,
        count(*) AS payments, count_if(outcome = 'authorized') AS authorized
FROM lakehouse.payments.payment_events
WHERE event_time >= current_timestamp - INTERVAL '{days}' DAY
GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC LIMIT 100
"""


def _status(status: str, tool: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, "tool": tool, **fields}


def _bounded_int(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


class Toolset:
    def __init__(self, knowledge: KnowledgeIndex, payments: PaymentApi, clickhouse: ClickHouse,
                trino: Trino | None) -> None:
        self._knowledge, self._payments, self._clickhouse, self._trino = knowledge, payments, clickhouse, trino

    @staticmethod
    def _denied(principal: Principal, tool: str) -> dict[str, Any] | None:
        permission = TOOL_PERMISSIONS[tool]
        if principal.has(permission):
            return None
        return _status("denied", tool, required_permission=permission)

    async def search_knowledge(self, principal: Principal, query: str, limit: int = 5) -> dict[str, Any]:
        tool = "search_knowledge"
        if denied := self._denied(principal, tool):
            return denied
        query = query.strip() if isinstance(query, str) else ""
        bounded = _bounded_int(limit, 1, 8)
        if not 3 <= len(query) <= 500 or bounded is None:
            return _status("invalid", tool, message="query must be 3-500 characters and limit 1-8")
        chunks = await self._knowledge.search(principal, query, bounded)
        return _status("ok", tool, results=[_clean({
            "chunk_id": chunk.chunk_id, "document_id": chunk.document_id,
            "document_version": chunk.document_version, "heading": chunk.heading, "content": chunk.content,
            "source_uri": chunk.source_uri, "classification": chunk.classification,
            "score": round(float(chunk.score), 6),
        }) for chunk in chunks])

    async def get_payment(self, principal: Principal, payment_id: str) -> dict[str, Any]:
        tool = "get_payment"
        if denied := self._denied(principal, tool):
            return denied
        payment_id = payment_id.strip().lower() if isinstance(payment_id, str) else ""
        if not _UUID.fullmatch(payment_id):
            return _status("invalid", tool, message="payment_id must be a UUID")
        payment = await self._payments.get(payment_id)
        if payment is None:
            return _status("not_found", tool, payment_id=payment_id)
        account = str(payment.get("account_id", ""))
        return _status("ok", tool, payment=_clean({
            "id": payment.get("id"), "status": payment.get("status"), "amount_minor": payment.get("amount_minor"),
            "currency": payment.get("currency"), "created_at": payment.get("created_at"),
            # Accounts are synthetic, but tools still return only what an operator needs.
            "account_hint": f"…{account[-4:]}" if account else None,
        }))

    async def payment_metrics(self, principal: Principal, window_minutes: int = 60,
                                group_by: str = "none") -> dict[str, Any]:
        tool = "payment_metrics"
        if denied := self._denied(principal, tool):
            return denied
        window = _bounded_int(window_minutes, 5, 1440)
        if window is None or group_by not in METRIC_GROUPS:
            return _status("invalid", tool, message=f"window_minutes 5-1440; group_by one of {sorted(METRIC_GROUPS)}")
        rows = await self._clickhouse.query(METRICS_SQL.format(group=METRIC_GROUPS[group_by]),
                                            {"window_minutes": window})
        return _status("ok", tool, window_minutes=window, group_by=group_by, rows=_clean(rows))

    async def payment_history(self, principal: Principal, days: int = 7,
                                group_by: str = "currency") -> dict[str, Any]:
        tool = "payment_history"
        if denied := self._denied(principal, tool):
            return denied
        bounded = _bounded_int(days, 1, 30)
        if bounded is None or group_by not in HISTORY_GROUPS:
            return _status("invalid", tool, message=f"days 1-30; group_by one of {sorted(HISTORY_GROUPS)}")
        if self._trino is None:
            return _status("unavailable", tool, message="lakehouse history is not enabled in this environment")
        # Both substitutions are validated: an int in range and an allowlisted column.
        rows = await self._trino.query(HISTORY_SQL.format(group=HISTORY_GROUPS[group_by], days=bounded))
        return _status("ok", tool, days=bounded, group_by=group_by, rows=_clean(rows))

    async def risk_model_summary(self, principal: Principal, window_minutes: int = 1440) -> dict[str, Any]:
        tool = "risk_model_summary"
        if denied := self._denied(principal, tool):
            return denied
        window = _bounded_int(window_minutes, 5, 10080)
        if window is None:
            return _status("invalid", tool, message="window_minutes must be 5-10080")
        rows = await self._clickhouse.query(RISK_SQL, {"window_minutes": window})
        return _status("ok", tool, window_minutes=window, models=_clean(rows),
                        note="Risk scores are advisory only and never authorize or reject payments.")
