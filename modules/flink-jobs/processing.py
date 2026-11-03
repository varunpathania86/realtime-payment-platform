"""Deterministic event-time aggregation shared by the Flink operator and tests."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from uuid import UUID

TRANSFORMATION = "payment-windows-v1"
INPUT_DATASET = "kafka:payment-processing-events-v1"
OUTPUT_DATASET = "kafka:payment-window-results-v1"
WINDOWS = (60_000, 600_000)
SLIDE_MS = 60_000
LATENESS_MS = 120_000
DEDUP_TTL_MS = 86_400_000


def decode(raw: str) -> dict:
    event = json.loads(raw)
    if (event["event_type"] != "payment.processing.v1" or type(event["schema_version"]) is not int
            or event["schema_version"] != 1):
        raise ValueError("unexpected processing event type/version")
    for field in ("event_id", "account_id", "payment_id"):
        UUID(event[field])
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", event["event_time"]):
        raise ValueError("event_time must be an RFC3339 UTC timestamp")
    if type(event["amount_minor"]) is not int or not 0 < event["amount_minor"] < 2**63:
        raise ValueError("amount_minor must be a positive signed 64-bit integer")
    if not re.fullmatch(r"[A-Z]{3}", event["currency"]):
        raise ValueError("currency must be an uppercase three-letter code")
    if not re.fullmatch(r"[A-Z]{2}", event["country"]):
        raise ValueError("country must be an uppercase two-letter code")
    if event["payment_method"] not in ("card", "transfer", "wallet"):
        raise ValueError("unknown payment method")
    if event["outcome"] not in ("authorized", "rejected"):
        raise ValueError("unknown synthetic processing outcome")
    event["timestamp_ms"] = int(datetime.fromisoformat(event["event_time"].replace("Z", "+00:00")).timestamp() * 1000)
    return event


def dimension(event: dict) -> str:
    return json.dumps([event[key] for key in ("currency", "country", "payment_method")], separators=(",", ":"))


def windows(timestamp: int):
    aligned = timestamp // SLIDE_MS * SLIDE_MS
    for duration in WINDOWS:
        for offset in range(0, duration, SLIDE_MS):
            start = aligned - offset
            yield start, start + duration


def empty_window(start: int, end: int) -> dict:
    return {
        "window_start": start, "window_end": end, "payments": 0,
        "authorized": 0, "authorized_amount_minor": 0, "rejections": 0,
        "revision": 0, "emitted": False,
    }


def add(window: dict, event: dict) -> dict:
    result = dict(window)
    result["payments"] += 1
    if event["outcome"] == "authorized":
        result["authorized"] += 1
        result["authorized_amount_minor"] += event["amount_minor"]
    else:
        result["rejections"] += 1
    return result


def output(window: dict, key: str) -> dict:
    currency, country, method = json.loads(key)
    identity = [window["window_start"], window["window_end"], currency, country, method]
    return {
        **{field: value for field, value in window.items() if field != "emitted"},
        "result_id": hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest(),
        "currency": currency, "country": country, "payment_method": method,
        "authorization_rate": window["authorized"] / window["payments"],
        "transformation_id": TRANSFORMATION, "schema_version": 1,
        "input_dataset": INPUT_DATASET, "output_dataset": OUTPUT_DATASET,
    }
