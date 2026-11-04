"""Publish verified experimental artifacts using an explicitly selected local pod."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess


def bundle(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    required = {"model.onnx", "metadata.json", "metrics.json", "golden.json", "model-card.md", "payment_risk.zip"}
    if not required <= manifest.keys():
        raise ValueError("model manifest is missing required artifacts")
    result = {}
    for name, expected in manifest.items():
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name):
            raise ValueError("invalid model artifact name")
        data = (directory / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"artifact checksum mismatch: {name}")
        result[name] = base64.b64encode(data).decode()
    result["manifest.json"] = base64.b64encode((directory / "manifest.json").read_bytes()).decode()
    return result, manifest["payment_risk.zip"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--directory", type=Path, default=Path(".local/models/payment-risk/1.0.0"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        raise ValueError("invalid explicit profile")
    metadata = json.loads((args.directory / "metadata.json").read_text())
    version = metadata["model_version"]
    if metadata["model_name"] != "payment-risk" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("unexpected model identity")
    artifacts, checksum = bundle(args.directory)
    program = """
import base64, hashlib, json, os
import boto3
from botocore.exceptions import ClientError
client = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"])
try:
    client.head_bucket(Bucket="model-registry")
except ClientError as error:
    if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
        raise
    client.create_bucket(Bucket="model-registry")
for name, encoded in ARTIFACTS.items():
    data = base64.b64decode(encoded)
    key = PREFIX + name
    try:
        client.put_object(Bucket="model-registry", Key=key, Body=data, IfNoneMatch="*")
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
            raise
        existing = client.get_object(Bucket="model-registry", Key=key)
        with existing["Body"] as body:
            actual = body.read()
        if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
            raise ValueError("immutable model version already contains different artifact: " + name)
print("Verified versioned model registry artifacts")
"""
    source = f"ARTIFACTS = {artifacts!r}\nPREFIX = {'payment-risk/' + version + '/'!r}\n" + program
    subprocess.run(
        ["kubectl", "--context", args.profile, "--request-timeout=30s", "-n", "apps",
            "exec", "-i", "deployment/flink-jobs-jobmanager", "--", "python", "-"],
        input=source, text=True, check=True, timeout=120,
    )
    print(json.dumps({
        "risk_enabled": True, "risk_package_key": f"payment-risk/{version}/payment_risk.zip",
        "risk_package_sha256": checksum,
    }, indent=2))


if __name__ == "__main__":
    main()
