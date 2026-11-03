"""Deterministic synthetic input cases; no real authorization decision is made."""

from datetime import datetime, timedelta, timezone
import json
from uuid import NAMESPACE_URL, uuid5


def event(index, seconds=0, **changes):
    result = {
        "event_id": str(uuid5(NAMESPACE_URL, f"rpp-stage5/event/{index}")),
        "payment_id": str(uuid5(NAMESPACE_URL, f"rpp-stage5/payment/{index}")),
        "account_id": str(uuid5(NAMESPACE_URL, "rpp-stage5/account/skewed")),
        "event_type": "payment.processing.v1", "schema_version": 1,
        "event_time": (datetime(2026, 11, 3, tzinfo=timezone.utc) + timedelta(seconds=seconds))
        .isoformat().replace("+00:00", "Z"),
        "amount_minor": 100, "currency": "EUR", "country": "DE",
        "payment_method": "transfer", "outcome": "authorized",
    }
    result.update(changes)
    return result


def dataset():
    # Partition 2 intentionally receives nothing; the connector must mark it idle.
    return [
        {"partition": 0, "case": "in-order", "event": event(1, 10)},
        {"partition": 1, "case": "in-order", "event": event(2, 20, outcome="rejected")},
        {"partition": 0, "case": "out-of-order", "event": event(3, 15)},
        {"partition": 0, "case": "duplicate", "event": event(1, 10)},
        {"partition": 1, "case": "advance-watermark", "event": event(4, 90)},
        {"partition": 0, "case": "late-accepted", "event": event(5, 25)},
        {"partition": 1, "case": "advance-watermark", "event": event(6, 900)},
        {"partition": 0, "case": "too-late", "event": event(7, 5)},
        *[{"partition": index % 2, "case": "skewed-account", "event": event(index, 910 + index)}
            for index in range(10, 30)],
    ]


if __name__ == "__main__":
    for record in dataset():
        print(json.dumps(record, sort_keys=True))
