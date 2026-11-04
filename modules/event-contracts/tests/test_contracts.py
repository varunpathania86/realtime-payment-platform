import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from shutil import copytree

import avro.errors
import avro.schema

from scripts.event_contracts import ROOT, package, validate_event, validate_compatibility

MODULE = ROOT / "modules" / "event-contracts"


class PaymentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = avro.schema.parse(
            (MODULE / "schemas" / "payment-initiated-v1.avsc").read_text(encoding="utf-8")
        )
        self.event = json.loads(
            (MODULE / "examples" / "payment-initiated-v1.json").read_text(encoding="utf-8")
        )

    def test_example_round_trips(self) -> None:
        validate_event(self.schema, self.event)

    def test_processing_contract_is_separate_and_compatible(self) -> None:
        schema = avro.schema.parse((MODULE / "schemas/payment-processing-v1.avsc").read_text())
        event = json.loads((MODULE / "examples/payment-processing-v1.json").read_text())
        validate_event(schema, event)
        validate_compatibility(schema, sorted((MODULE / "compatibility/payment-processing-v1").glob("*.avsc")))
        for field, value in (("country", "de"), ("outcome", "pending"), ("amount_minor", True)):
            invalid = {**event, field: value}
            with self.subTest(field=field), self.assertRaises((ValueError, avro.errors.AvroTypeException)):
                validate_event(schema, invalid)

    def test_backward_compatibility_rejects_breaking_changes(self) -> None:
        baselines = sorted((MODULE / "compatibility" / "payment-initiated-v1").glob("*.avsc"))
        validate_compatibility(self.schema, baselines)
        changed = json.loads(str(self.schema))
        changed["fields"].append({"name": "new_required_field", "type": "string"})
        with self.assertRaisesRegex(ValueError, "not backward compatible"):
            validate_compatibility(avro.schema.parse(json.dumps(changed)), baselines)
        changed["fields"][-1]["default"] = ""
        validate_compatibility(avro.schema.parse(json.dumps(changed)), baselines)
        with self.assertRaisesRegex(ValueError, "baseline"):
            validate_compatibility(self.schema, [])

    def test_optional_synthetic_account_device_fields_preserve_old_events(self) -> None:
        schema = avro.schema.parse((MODULE / "schemas/payment-processing-v1.avsc").read_text())
        event = json.loads((MODULE / "examples/payment-processing-v1.json").read_text())
        validate_event(schema, event)
        enriched = {**event, "account_created_at": "2026-01-01T00:00:00Z",
            "device_id": "d41d8cd9-8f00-4204-a980-0998ecf8427e"}
        validate_event(schema, enriched)
        validate_event(schema, {**event, "account_created_at": None, "device_id": None})
        for changes in (
            {"account_created_at": "2099-01-01T00:00:00Z"},
            {"account_created_at": "2026-01-01"},
            {"device_id": "real-device-fingerprint"},
        ):
            with self.subTest(changes=changes), self.assertRaises((ValueError, avro.errors.AvroTypeException)):
                validate_event(schema, {**event, **changes})

    def test_rejects_invalid_events(self) -> None:
        for field, value in [
            ("amount_minor", 12.50), ("amount_minor", -1),
            ("amount_minor", True),
            ("amount_minor", 2**63), ("currency", "eur"), ("payment_id", "invalid"),
        ]:
            with self.subTest(field=field, value=value):
                event = copy.deepcopy(self.event)
                event["payload"][field] = value
                with self.assertRaises((ValueError, avro.errors.AvroTypeException)):
                    validate_event(self.schema, event)
        self.event["event_time"] = "2026-10-26T00:00:00+02:00"
        with self.assertRaisesRegex(ValueError, "UTC"):
            validate_event(self.schema, self.event)

    def test_archive_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "module"
            copytree(MODULE, module, ignore=lambda _path, names: {"dist", "__pycache__"} & set(names))
            package(module, True)
            first = hashlib.sha256((module / "dist" / "contracts.tar").read_bytes()).hexdigest()
            package(module, True)
            self.assertEqual(first, hashlib.sha256((module / "dist" / "contracts.tar").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
