"""MCP client for the platform tools; the graph's only path to data."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from langchain_mcp_adapters.client import MultiServerMCPClient
from ai_core.identity import payment_token_context

LOG = logging.getLogger(__name__)


class ToolClient(Protocol):
    async def call(self, name: str, arguments: dict[str, Any], token: str, request_id: str) -> dict[str, Any]: ...


class McpToolClient:
    def __init__(self, url: str, timeout_seconds: float = 30) -> None:
        self._url, self._timeout = url, timeout_seconds

    async def call(self, name: str, arguments: dict[str, Any], token: str, request_id: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "X-Request-ID": request_id}
        if (payment := payment_token_context.get()) and name == "get_payment":
            headers["X-Assistant-Payment-Token"] = payment
        # A per-call client keeps the user's relayed assertion out of any shared connection state.
        client = MultiServerMCPClient({"tools": {
            "transport": "streamable_http", "url": self._url, "timeout": self._timeout,
            "headers": headers,
        }})
        try:
            async with client.session("tools") as session:
                result = await session.call_tool(name, arguments)
        except Exception as error:  # Transport failures surface as one tool status.
            LOG.warning("MCP call %s failed: %s", name, type(error).__name__)
            return {"status": "unavailable", "tool": name}
        content = result.structuredContent
        if result.isError or not isinstance(content, dict) or not isinstance(content.get("status"), str):
            return {"status": "unavailable", "tool": name}
        return content
