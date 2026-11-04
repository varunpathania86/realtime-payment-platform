from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest
import sys

spec = importlib.util.spec_from_file_location("risk_features", Path(__file__).resolve().parents[1] / "features.py")
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import train


def event(index, seconds=0, **changes):
    return {
        "event_id": f"{index:04}", "account_id": "account", "currency": "EUR",
        "event_time": (datetime(2026, 11, 4, tzinfo=timezone.utc) + timedelta(seconds=seconds))
            .isoformat().replace("+00:00", "Z"),
        "amount_minor": 100, "outcome": "authorized", "country": "DE",
        "payment_method": "card", **changes,
    }


class FeatureTests(unittest.TestCase):
    def test_synthetic_dataset_is_reproducible_and_accounts_are_identified(self):
        first = train.dataset(accounts=8, payments=12)
        self.assertEqual(first, train.dataset(accounts=8, payments=12))
        rows, labels, groups = first
        self.assertEqual(len(rows), 96)
        self.assertEqual(set(labels), {0, 1})
        self.assertEqual(len(set(groups)), 8)
        self.assertNotIn("outcome", rows[0])

    def test_missing_context_is_unknown_not_safe(self):
        vector = features.feature_vector(event(1), [])
        self.assertIsNone(vector["account_age_days"])
        self.assertIsNone(vector["device_changed"])
        self.assertIsNone(vector["country_changed"])
        self.assertIn("INCOMPLETE_CONTEXT", features.reason_codes(vector))

    def test_account_age_and_device_change_use_event_time(self):
        prior = event(1, device_id="first")
        current = event(2, 60, device_id="second", account_created_at="2026-11-03T00:00:00Z")
        vector = features.feature_vector(current, [prior])
        self.assertAlmostEqual(vector["account_age_days"], 1 + 1 / 1440)
        self.assertEqual(vector["device_changed"], 1)
        self.assertEqual(vector["seconds_since_previous_payment"], 60)

    def test_features_never_use_current_outcome_future_or_other_accounts_currency(self):
        current = event(3, 60, outcome="rejected")
        history = [event(1, 0), event(2, 10, currency="USD"), event(4, 20, account_id="other"),
            current, event(5, 120, outcome="rejected")]
        vector = features.feature_vector(current, history)
        self.assertEqual(vector["payments_last_10_minutes"], 1)
        self.assertEqual(vector["rejections_last_10_minutes"], 0)
        self.assertEqual(vector["amount_last_10_minutes"], 100)

    def test_rolling_bounds_and_deterministic_equal_time_ordering(self):
        vector = features.feature_vector(event(5, 3600), [
            event(1, -1), event(2, 0, country="FR"), event(3, 2999),
            event(4, 3000), event(1, 3600), event(6, 3600),
        ])
        self.assertEqual(vector["payments_last_10_minutes"], 2)
        self.assertEqual(vector["distinct_countries_last_hour"], 2)
        self.assertEqual(vector["seconds_since_previous_payment"], 0)

    def test_accepted_late_history_changes_later_vector_without_future_leak(self):
        later = event(3, 100, amount_minor=300)
        history = [event(1, 0)]
        original = features.feature_vector(later, history)
        revised = features.feature_vector(later, [*history, event(2, 50, outcome="rejected")])
        self.assertEqual(original["payments_last_10_minutes"], 1)
        self.assertEqual(revised["payments_last_10_minutes"], 2)
        self.assertEqual(revised["rejections_last_10_minutes"], 1)
        self.assertEqual(original["rejections_last_10_minutes"], 0)

    def test_creation_after_event_is_invalid(self):
        with self.assertRaises(ValueError):
            features.feature_vector(event(1, account_created_at="2099-01-01T00:00:00Z"), [])


if __name__ == "__main__":
    unittest.main()
