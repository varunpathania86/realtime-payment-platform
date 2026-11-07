"""FastMCP streamable-HTTP server exposing the platform tools inside the cluster.

Every call carries the portal-signed identity assertion for the `assistant-tools` audience, relayed
by the orchestrator. The server verifies it per call, so the graph cannot widen its own
permissions, then writes one JSON audit line per tool call without arguments or results.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from ai_core.embeddings import LlamaEmbeddings
from ai_core.identity import (TOOLS_AUDIENCE, IdentityError, Principal, bearer_token, verify_assertion,
                            identity_key_from_env, KeycloakVerifier, payment_token_context)
from assistant_tools.backends import BackendError, ClickHouse, KnowledgeIndex, PaymentApi, Trino
from assistant_tools.tools import Toolset

AUDIT = logging.getLogger("assistant_tools.audit")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ToolCall = Callable[[Principal], Awaitable[dict[str, Any]]]


def _headers(ctx: Context) -> Any:
    request = ctx.request_context.request
    return request.headers if request is not None else {}


def create_server(toolset: Toolset, public_key: Ed25519PublicKey, allowed_hosts: list[str]) -> FastMCP:
    mcp = FastMCP(
        "rpp-platform-tools", stateless_http=True, json_response=True, log_level="WARNING",
        instructions="Read-only payment platform tools. Every call is authorized for the end user.",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                    allowed_hosts=allowed_hosts),
    )

    async def run(ctx: Context, tool: str, call: ToolCall) -> dict[str, Any]:
        started = time.monotonic()
        headers = _headers(ctx)
        request_id = headers.get("x-request-id", "")
        record: dict[str, Any] = {"event": "tool_call", "tool": tool,
                                    "request_id": request_id if _REQUEST_ID.fullmatch(request_id) else None}
        try:
            principal = verify_assertion(bearer_token(headers.get("authorization")), public_key, TOOLS_AUDIENCE)
            payment_token = None
            if isinstance(public_key, KeycloakVerifier) and tool == "get_payment":
                payment_token = headers.get("x-assistant-payment-token")
                payment = verify_assertion(payment_token or "", public_key, "payment-api")
                if payment.subject != principal.subject or not payment.has("payments:read"):
                    raise IdentityError("mismatched payment-tool identity")
        except IdentityError:
            result = {"status": "unauthenticated", "tool": tool}
        else:
            record.update(subject=principal.subject, tenant=principal.tenant,
                            assertion_hash=hashlib.sha256(principal.assertion_id.encode()).hexdigest()[:16])
            try:
                context_token = payment_token_context.set(payment_token)
                try:
                    result = await call(principal)
                finally:
                    payment_token_context.reset(context_token)
            except BackendError:
                result = {"status": "unavailable", "tool": tool, "message": "backend unavailable"}
        record.update(status=result["status"], duration_ms=round((time.monotonic() - started) * 1000))
        AUDIT.info(json.dumps(record, sort_keys=True))
        return result

    @mcp.tool(description="Search platform runbooks, ADRs, contracts and support notes the user may read. "
                            "Returns cited chunks.")
    async def search_knowledge(query: str, ctx: Context, limit: int = 5) -> dict[str, Any]:
        return await run(ctx, "search_knowledge", lambda p: toolset.search_knowledge(p, query, limit))

    @mcp.tool(description="Read one synthetic payment by its UUID.")
    async def get_payment(payment_id: str, ctx: Context) -> dict[str, Any]:
        return await run(ctx, "get_payment", lambda p: toolset.get_payment(p, payment_id))

    @mcp.tool(description="Recent payment counts and authorization rate from ClickHouse. window_minutes 5-1440; "
                            "group_by none, currency, country, payment_method or outcome.")
    async def payment_metrics(ctx: Context, window_minutes: int = 60, group_by: str = "none") -> dict[str, Any]:
        return await run(ctx, "payment_metrics", lambda p: toolset.payment_metrics(p, window_minutes, group_by))

    @mcp.tool(description="Daily payment history from the Iceberg lakehouse via Trino. days 1-30; "
                            "group_by currency, country, payment_method or outcome.")
    async def payment_history(ctx: Context, days: int = 7, group_by: str = "currency") -> dict[str, Any]:
        return await run(ctx, "payment_history", lambda p: toolset.payment_history(p, days, group_by))

    @mcp.tool(description="Risk model versions in use and their advisory score distribution. "
                            "window_minutes 5-10080.")
    async def risk_model_summary(ctx: Context, window_minutes: int = 1440) -> dict[str, Any]:
        return await run(ctx, "risk_model_summary", lambda p: toolset.risk_model_summary(p, window_minutes))

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp


def create_app(toolset: Toolset, public_key: Ed25519PublicKey, allowed_hosts: list[str]) -> Starlette:
    return create_server(toolset, public_key, allowed_hosts).streamable_http_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    client = httpx.AsyncClient(timeout=10.0)
    trino_url = os.environ.get("TRINO_URL", "")
    toolset = Toolset(
        KnowledgeIndex(os.environ["KNOWLEDGE_DSN"], LlamaEmbeddings(os.environ["LLM_EMBEDDINGS_URL"], timeout=15)),
        PaymentApi(client, os.environ["PAYMENT_API_URL"]),
        ClickHouse(client, os.environ["CLICKHOUSE_URL"], os.environ["CLICKHOUSE_USER"],
                    os.environ["CLICKHOUSE_PASSWORD"]),
        Trino(client, trino_url) if trino_url else None,
    )
    public_key = identity_key_from_env()
    allowed_hosts = os.environ.get("ALLOWED_HOSTS", "127.0.0.1:*,localhost:*").split(",")
    app = create_app(toolset, public_key, allowed_hosts)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), access_log=False,
                log_level="warning")


if __name__ == "__main__":
    main()
