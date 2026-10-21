import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from shutil import copytree

import avro.errors
import avro.schema

from scripts.event_contracts import ROOT, package, validate_event

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
