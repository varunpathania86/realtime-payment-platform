"""Validate and package the first synthetic Avro event contract."""

from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
from datetime import datetime
from pathlib import Path
from uuid import UUID

import avro.io
import avro.schema

ROOT = Path(__file__).resolve().parents[1]


def validate_event(schema: avro.schema.Schema, event: dict) -> None:
    avro.io.validate(schema, event, raise_on_error=True)
    if (
        event["event_type"] != "payment.initiated.v1"
        or type(event["schema_version"]) is not int
        or event["schema_version"] != 1
    ):
        raise ValueError("unexpected event type or schema version")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", event["event_time"]):
        raise ValueError("event_time must be RFC3339 UTC ending in Z")
    datetime.fromisoformat(event["event_time"].replace("Z", "+00:00"))
    for key in ("event_id", "correlation_id"):
        UUID(event[key])
    if event["causation_id"] is not None:
        UUID(event["causation_id"])
    UUID(event["payload"]["payment_id"])
    if type(event["payload"]["amount_minor"]) is not int or event["payload"]["amount_minor"] <= 0:
        raise ValueError("amount_minor must be positive")
    if not re.fullmatch(r"[A-Z]{3}", event["payload"]["currency"]):
        raise ValueError("currency must be a three-letter uppercase ISO 4217 code")
    buffer = io.BytesIO()
    avro.io.DatumWriter(schema).write(event, avro.io.BinaryEncoder(buffer))
    buffer.seek(0)
    decoded = avro.io.DatumReader(schema).read(avro.io.BinaryDecoder(buffer))
    if decoded != event:
        raise ValueError("Avro binary round-trip changed the event")


def package(module: Path, build: bool) -> None:
    for path in sorted((module / "schemas").glob("*.avsc")):
        schema = avro.schema.parse(path.read_text(encoding="utf-8"))
        event = json.loads((module / "examples" / f"{path.stem}.json").read_text(encoding="utf-8"))
        validate_event(schema, event)
    if build:
        output = module / "dist" / "contracts.tar"
        output.parent.mkdir(exist_ok=True)
        paths = sorted([
            module / "README.md", module / "module.yaml",
            *(module / "schemas").glob("*.avsc"),
            *(module / "examples").glob("*.json"),
        ])
        with tarfile.open(output, "w", format=tarfile.USTAR_FORMAT) as archive:
            for path in paths:
                data = path.read_bytes()
                info = tarfile.TarInfo(str(path.relative_to(module)))
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
        print(f"Built {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    package(args.module, args.build)


if __name__ == "__main__":
    main()
