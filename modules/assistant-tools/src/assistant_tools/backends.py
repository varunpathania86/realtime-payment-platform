"""Read-only backend clients owned by the MCP server. The graph never receives these credentials."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
from langchain_core.embeddings import Embeddings

from ai_core.identity import Principal
from ai_core.retrieval import RetrievedChunk, hybrid_search

QUERY_TIMEOUT_SECONDS = 5
MAX_ROWS = 200
LOG = logging.getLogger("assistant_tools.backends")


class BackendError(Exception):
    """A backend is unreachable, slow or returned an unexpected response."""


class ClickHouse:
    def __init__(self, client: httpx.AsyncClient, url: str, user: str, password: str) -> None:
        self._client, self._url = client, url.rstrip("/")
        self._headers = {"X-ClickHouse-User": user, "X-ClickHouse-Key": password}

    async def query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        # Values travel as typed ClickHouse query parameters ({name:Type}), never inside the SQL text.
        params = {
            "database": "analytics", "default_format": "JSON", "max_execution_time": QUERY_TIMEOUT_SECONDS,
            "max_result_rows": MAX_ROWS, "result_overflow_mode": "throw",
            "output_format_json_quote_64bit_integers": 0,
            **{f"param_{name}": value for name, value in parameters.items()},
        }
        try:
            response = await self._client.post(self._url, params=params, content=sql, headers=self._headers,
                                                timeout=QUERY_TIMEOUT_SECONDS + 2)
            response.raise_for_status()
            return list(response.json()["data"])
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise BackendError("analytics query failed") from error


class Trino:
    def __init__(self, client: httpx.AsyncClient, url: str, user: str = "assistant-tools",
                timeout: float = 15.0) -> None:
        self._client, self._url, self._timeout = client, url.rstrip("/"), timeout
        self._headers = {"X-Trino-User": user, "X-Trino-Source": "assistant-tools"}

    async def query(self, sql: str) -> list[dict[str, Any]]:
        deadline = time.monotonic() + self._timeout
        columns: list[str] = []
        rows: list[dict[str, Any]] = []
        try:
            response = await self._client.post(f"{self._url}/v1/statement", content=sql, headers=self._headers)
            while True:
                response.raise_for_status()
                body = response.json()
                if "error" in body:
                    raise BackendError("history query failed")
                columns = columns or [column["name"] for column in body.get("columns", [])]
                rows.extend(dict(zip(columns, values, strict=True)) for values in body.get("data", []))
                if len(rows) > MAX_ROWS:
                    raise BackendError("history query exceeded the row limit")
                next_uri = body.get("nextUri")
                if not next_uri:
                    return rows
                if time.monotonic() > deadline:
                    await self._client.delete(next_uri, headers=self._headers)
                    raise BackendError("history query timed out")
                response = await self._client.get(next_uri, headers=self._headers)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            raise BackendError("history query failed") from error


class PaymentApi:
    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client, self._url = client, url.rstrip("/")

    async def get(self, payment_id: str) -> dict[str, Any] | None:
        try:
            from ai_core.identity import payment_token_context
            token = payment_token_context.get()
            kwargs = {"headers": {"Authorization": "Bearer " + token}} if token else {}
            response = await self._client.get(f"{self._url}/payments/{payment_id}", timeout=QUERY_TIMEOUT_SECONDS, **kwargs)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return dict(response.json())
        except (httpx.HTTPError, ValueError) as error:
            raise BackendError("payment lookup failed") from error


class KnowledgeIndex:
    def __init__(self, dsn: str, embeddings: Embeddings,
                connect: Callable[..., Any] = psycopg.connect) -> None:
        self._dsn, self._embeddings, self._connect = dsn, embeddings, connect

    def _search(self, principal: Principal, query: str, limit: int) -> list[RetrievedChunk]:
        try:
            embedding: list[float] | None = self._embeddings.embed_query(query)
        except Exception as error:  # an unavailable embedding server degrades to full-text ranking
            LOG.warning("embedding unavailable, using full-text ranking only: %s", type(error).__name__)
            embedding = None
        options = f"-c statement_timeout={QUERY_TIMEOUT_SECONDS * 1000} -c default_transaction_read_only=on"
        with self._connect(self._dsn, options=options, connect_timeout=QUERY_TIMEOUT_SECONDS) as connection:
            with connection.cursor() as cursor:
                return hybrid_search(cursor, principal, query, embedding, limit=limit)

    async def search(self, principal: Principal, query: str, limit: int) -> list[RetrievedChunk]:
        try:
            return await asyncio.to_thread(self._search, principal, query, limit)
        except Exception as error:  # connection and SQL failures mean "unavailable"
            raise BackendError("knowledge search failed") from error
