"""Model-provider adapter: llama.cpp's OpenAI-compatible server through LangChain."""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from ai_orchestrator.routing import ROUTE_SCHEMA, ROUTER_PROMPT


class LlmUnavailable(Exception):
    """Raised when the model cannot route or answer; the graph then uses deterministic fallbacks."""


class Model(Protocol):
    name: str

    async def route(self, question: str) -> dict[str, Any]: ...

    async def answer(self, messages: list[BaseMessage], source_ids: list[str]) -> dict[str, Any]: ...


def answer_schema(source_ids: list[str]) -> dict[str, Any]:
    """Grammar-constrained answer: the model can only name labels of sources it was given."""
    return {
        "title": "answer", "type": "object", "additionalProperties": False, "required": ["answer", "sources"],
        "properties": {
            "answer": {"type": "string"},
            "sources": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "enum": source_ids}},
        },
    }


class LlamaModel:
    def __init__(self, base_url: str, name: str, *, route_timeout: float = 20, answer_timeout: float = 90,
                max_answer_tokens: int = 350) -> None:
        self.name = name
        common: dict[str, Any] = {"base_url": base_url, "api_key": "unused", "model": name, "temperature": 0,
                                    "max_retries": 0}
        self._router = ChatOpenAI(**common, max_tokens=80, timeout=route_timeout).with_structured_output(
            ROUTE_SCHEMA, method="json_schema")
        self._writer = ChatOpenAI(**common, max_tokens=max_answer_tokens, timeout=answer_timeout)

    async def route(self, question: str) -> dict[str, Any]:
        try:
            result = await self._router.ainvoke([("system", ROUTER_PROMPT), ("user", question)])
        except Exception as error:  # Provider errors vary (connection, timeout, HTTP, parsing).
            raise LlmUnavailable(type(error).__name__) from error
        if not isinstance(result, dict):
            raise LlmUnavailable("route was not an object")
        return result

    async def answer(self, messages: list[BaseMessage], source_ids: list[str]) -> dict[str, Any]:
        try:
            if not source_ids:
                result = await self._writer.ainvoke(messages)
                return {"answer": result.content if isinstance(result.content, str) else "", "sources": []}
            structured = await self._writer.with_structured_output(
                answer_schema(source_ids), method="json_schema").ainvoke(messages)
        except Exception as error:
            raise LlmUnavailable(type(error).__name__) from error
        if not isinstance(structured, dict):
            raise LlmUnavailable("answer was not an object")
        return structured


class DisabledModel:
    """Used when no model URL is configured; every request takes the fallback path."""

    name = "disabled"

    async def route(self, question: str) -> dict[str, Any]:
        raise LlmUnavailable("model disabled")

    async def answer(self, messages: list[BaseMessage], source_ids: list[str]) -> dict[str, Any]:
        raise LlmUnavailable("model disabled")
