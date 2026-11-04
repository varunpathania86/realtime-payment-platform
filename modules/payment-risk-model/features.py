"""Event-time features built only from strictly preceding synthetic payments."""

from datetime import datetime
from importlib.resources import files
import json
from pathlib import Path

CONTRACT = json.loads(
    files(__package__).joinpath("feature_contract.json").read_text()
    if __package__ else Path(__file__).with_name("feature_contract.json").read_text()
)


def timestamp(event):
    return int(datetime.fromisoformat(event["event_time"].replace("Z", "+00:00")).timestamp() * 1000)


def feature_vector(event, history):
    now = timestamp(event)
    identity = (now, event["event_id"])
    previous = sorted(
        (item for item in history
            if item["account_id"] == event["account_id"] and item["currency"] == event["currency"]
            and now - 3_600_000 <= timestamp(item)
            and (timestamp(item), item["event_id"]) < identity),
        key=lambda item: (timestamp(item), item["event_id"]),
    )
    recent = [item for item in previous if timestamp(item) >= now - 600_000]
    last = previous[-1] if previous else None
    amounts = [item["amount_minor"] for item in previous]
    mean = sum(amounts) / len(amounts) if amounts else None
    created = event.get("account_created_at")
    age = (now - timestamp({"event_time": created})) / 86_400_000 if created is not None else None
    if age is not None and age < 0:
        raise ValueError("account creation cannot follow payment time")
    current_device = event.get("device_id")
    previous_device = last.get("device_id") if last else None
    result = {
        "payment_amount_minor": event["amount_minor"],
        "account_age_days": age,
        "payments_last_10_minutes": len(recent),
        "rejections_last_10_minutes": sum(item["outcome"] == "rejected" for item in recent),
        "amount_last_10_minutes": sum(item["amount_minor"] for item in recent),
        "distinct_countries_last_hour": len({item["country"] for item in previous}),
        "seconds_since_previous_payment": (now - timestamp(last)) / 1000 if last else None,
        "amount_deviation_from_account_average": event["amount_minor"] / mean - 1 if mean else None,
        "device_changed": int(current_device != previous_device)
            if current_device is not None and previous_device is not None else None,
        "country_changed": int(event["country"] != last["country"]) if last else None,
        "account_age_missing": int(age is None),
        "device_context_missing": int(current_device is None or previous_device is None),
        **{key: event[key] for key in CONTRACT["categorical"]},
    }
    if set(result) != set(CONTRACT["numeric"] + CONTRACT["categorical"]):
        raise ValueError("feature contract and generated vector disagree")
    return result


def reason_codes(vector):
    reasons = []
    if vector["device_changed"] == 1:
        reasons.append("DEVICE_CHANGED")
    if vector["country_changed"] == 1:
        reasons.append("COUNTRY_CHANGED")
    if vector["account_age_days"] is not None and vector["account_age_days"] < 7:
        reasons.append("RECENT_ACCOUNT")
    if vector["payments_last_10_minutes"] >= 10:
        reasons.append("HIGH_RECENT_ACTIVITY")
    if vector["account_age_days"] is None or vector["device_changed"] is None:
        reasons.append("INCOMPLETE_CONTEXT")
    return reasons
