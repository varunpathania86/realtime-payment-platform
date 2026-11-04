"""Verify and distribute the explicitly pinned model package, never a latest alias."""

import hashlib
import io
import os
from pathlib import Path
import re
import zipfile

PACKAGE_FILES = {
    "payment_risk/" + name for name in (
        "__init__.py", "features.py", "scorer.py", "feature_contract.json",
        "model.onnx", "metadata.json", "golden.json",
    )
}


def verify_package(data, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected) or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("risk package checksum mismatch or invalid expected SHA-256")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(PACKAGE_FILES) or set(names) != PACKAGE_FILES:
            raise ValueError("risk package has unexpected or missing entries")
        if sum(item.file_size for item in archive.infolist()) > 10_000_000:
            raise ValueError("risk package exceeds uncompressed size budget")


def download_package():
    import boto3

    expected = os.environ["RISK_PACKAGE_SHA256"]
    key = os.environ["RISK_PACKAGE_KEY"]
    if not re.fullmatch(r"payment-risk/[0-9]+\.[0-9]+\.[0-9]+/payment_risk\.zip", key):
        raise ValueError("risk package must use an explicit versioned registry key")
    client = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"])
    response = client.get_object(Bucket="model-registry", Key=key)
    with response["Body"] as body:
        data = body.read(10_000_001)
    if len(data) > 10_000_000:
        raise ValueError("risk package exceeds download size budget")
    verify_package(data, expected)
    destination = Path("/tmp") / expected
    destination.mkdir(exist_ok=True)
    path = destination / "payment_risk.zip"
    path.write_bytes(data)
    return str(path)
