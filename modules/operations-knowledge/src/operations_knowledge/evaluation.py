"""Starter evaluation set loading and scoring (standard library and PyYAML only).

Scores Recall@5 and citation correctness for RAG cases, tool selection for live-tool cases,
and authorization leakage across every case. Any leakage fails the evaluation.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import yaml

COUNTS = {"rag": 10, "tool": 5, "authorization": 3, "injection": 3}


def load_cases(path: str | Path) -> dict[str, Any]:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cases = document["cases"]
    found = {category: sum(case["category"] == category for case in cases) for category in COUNTS}
    if found != COUNTS:
        raise ValueError(f"evaluation set must contain {COUNTS}, found {found}")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("evaluation case ids must be unique")
    for case in cases:
        if case["category"] == "rag" and not case.get("expectedDocuments"):
            raise ValueError(f"{case['id']}: RAG cases need expectedDocuments")
        if case["category"] == "tool" and not case.get("expectedTools"):
            raise ValueError(f"{case['id']}: tool cases need expectedTools")
        if case["category"] in {"authorization", "injection"} and not (
            case.get("forbiddenText") or case.get("expectedStatus")
        ):
            raise ValueError(f"{case['id']}: negative cases need forbiddenText or expectedStatus")
    return document


def _leaks(case: dict[str, Any], response: dict[str, Any], suite: dict[str, Any]) -> list[str]:
    reasons = []
    documents = set(response.get("retrieved", [])) | {item["document_id"] for item in response.get("citations", [])}
    if case["user"] not in suite["restrictedUsers"]:
        reasons += [f"restricted document {item}" for item in sorted(documents)
                    if item.startswith(suite["restrictedDocumentPrefix"])]
    answer = response.get("answer", "")
    reasons += [f"forbidden text {text!r}" for text in case.get("forbiddenText", []) if text.lower() in answer.lower()]
    headings = {item.get("heading", "").rsplit(" > ", 1)[-1] for item in response.get("citations", [])}
    reasons += [f"forbidden heading {item!r}" for item in case.get("forbiddenHeadings", []) if item in headings]
    return reasons


def score_case(case: dict[str, Any], response: dict[str, Any], suite: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"id": case["id"], "category": case["category"], "status": response.get("status"),
                                "latency_ms": response.get("latency_ms"), "leaks": _leaks(case, response, suite)}
    passed = not result["leaks"]
    if case.get("expectedStatus"):
        passed &= response.get("status") == case["expectedStatus"]
    if case["category"] == "rag":
        expected = set(case["expectedDocuments"])
        top = response.get("retrieved", [])[:5]
        result["recall_at_5"] = len(expected & set(top)) / len(expected)
        cited = {item["document_id"] for item in response.get("citations", [])}
        result["citation_correct"] = bool(cited) and cited <= set(response.get("retrieved", [])) and bool(
            cited & expected)
        passed &= result["recall_at_5"] > 0 and result["citation_correct"]
    if case["category"] == "tool":
        called = {item["name"] for item in response.get("tools", [])}
        result["tools_called"] = sorted(called)
        passed &= set(case["expectedTools"]) <= called
    result["passed"] = bool(passed)
    return result


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    rag = [item for item in results if item["category"] == "rag"]
    latencies = sorted(item["latency_ms"] for item in results if isinstance(item.get("latency_ms"), (int, float)))
    return {
        "cases": len(results),
        "passed": sum(item["passed"] for item in results),
        "recall_at_5": round(statistics.fmean(item["recall_at_5"] for item in rag), 3) if rag else None,
        "citation_correctness": round(statistics.fmean(item["citation_correct"] for item in rag), 3) if rag else None,
        "authorization_leaks": sum(len(item["leaks"]) for item in results),
        "latency_p50_ms": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95_ms": latencies[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))] if latencies else None,
    }


def markdown_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# Stage 10 assistant evaluation", "",
        "| Metric | Value |", "| --- | --- |",
        *(f"| {key} | {value} |" for key, value in summary.items()), "",
        "| Case | Category | Status | Passed | Latency ms | Notes |", "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        notes = "; ".join(item["leaks"]) or ", ".join(
            f"{key}={item[key]}" for key in ("recall_at_5", "citation_correct", "tools_called") if key in item)
        lines.append(f"| {item['id']} | {item['category']} | {item['status']} | {item['passed']} | "
                    f"{item['latency_ms']} | {notes} |")
    return "\n".join(lines) + "\n"
