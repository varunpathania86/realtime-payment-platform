"""HTTP API for the assistant: deterministic identity checks, then one LangGraph run per question."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ai_core.identity import (ORCHESTRATOR_AUDIENCE, TOOLS_AUDIENCE, IdentityError, bearer_token,
                                verify_assertion, identity_key_from_env, KeycloakVerifier, payment_token_context)
from ai_orchestrator.graph import Dependencies, build_graph, response
from ai_orchestrator.llm import DisabledModel, LlamaModel, Model
from ai_orchestrator.mcp_client import McpToolClient, ToolClient

LOG = logging.getLogger(__name__)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TOOLS_ASSERTION_HEADER = "X-Assistant-Tools-Assertion"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


def _unauthenticated(request_id: str) -> JSONResponse:
    return JSONResponse({"status": "unauthenticated", "request_id": request_id}, status_code=401)


def create_app(model: Model, tools: ToolClient, public_key: Ed25519PublicKey) -> FastAPI:
    graph = build_graph(Dependencies(model=model, tools=tools))
    app = FastAPI(title="ai-orchestrator", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/assistant/ask")
    async def ask(
        body: AskRequest,
        authorization: str | None = Header(default=None),
        tools_assertion: str | None = Header(default=None, alias=TOOLS_ASSERTION_HEADER),
        payment_token: str | None = Header(default=None, alias="X-Assistant-Payment-Token"),
        x_request_id: str | None = Header(default=None),
    ) -> Any:
        started = time.monotonic()
        request_id = x_request_id if x_request_id and _REQUEST_ID.fullmatch(x_request_id) else uuid.uuid4().hex
        try:
            principal = verify_assertion(bearer_token(authorization), public_key, ORCHESTRATOR_AUDIENCE)
            relayed = verify_assertion(tools_assertion or "", public_key, TOOLS_AUDIENCE)
            if isinstance(public_key, KeycloakVerifier) and payment_token:
                payment = verify_assertion(payment_token, public_key, "payment-api")
                if payment.subject != principal.subject or not payment.has("payments:read"):
                    raise IdentityError("mismatched payment-tool identity")
        except IdentityError:
            return _unauthenticated(request_id)
        # Both assertions must describe the same user; the orchestrator never widens permissions.
        if (relayed.subject, relayed.tenant) != (principal.subject, principal.tenant):
            return _unauthenticated(request_id)
        if not principal.has("ai:invoke"):
            return JSONResponse({"status": "forbidden", "request_id": request_id}, status_code=403)
        permissions = (principal.permissions & {"ai:invoke"}) | relayed.permissions
        context_token = payment_token_context.set(payment_token if isinstance(public_key, KeycloakVerifier) else None)
        try:
            state = await graph.ainvoke(
                {"question": body.question, "request_id": request_id, "subject": principal.subject,
                "tenant": principal.tenant, "permissions": sorted(permissions), "started": started},
                config={"configurable": {"tools_token": tools_assertion}},
            )
        finally:
            payment_token_context.reset(context_token)
        result = response(state)
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result

    return app


def app_from_environment() -> FastAPI:
    public_key = identity_key_from_env()
    chat_url = os.environ.get("LLM_CHAT_URL", "")
    model: Model = LlamaModel(chat_url, os.environ.get("LLM_MODEL", "qwen2.5-1.5b-instruct-q4_k_m")) if chat_url \
        else DisabledModel()
    tools = McpToolClient(os.environ.get("ASSISTANT_TOOLS_URL", "http://assistant-tools:8080/mcp"))
    return create_app(model, tools, public_key)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(app_from_environment(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),  # nosec B104
                access_log=False, log_config=None)


if __name__ == "__main__":
    main()
