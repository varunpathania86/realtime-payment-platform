#!/usr/bin/env python3
"""Check local ingress responses and the currently deployed API image version."""

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def check(url, images):
    for endpoint in ("healthz", "readyz"):
        with urlopen(f"{url}/api/{endpoint}", timeout=10) as response:
            if json.load(response).get("status") != "ok":
                raise ValueError(f"{endpoint} did not return status ok")
    with urlopen(f"{url}/api/version", timeout=10) as response:
        version = json.load(response)
    expected = images["local_images"]["payment-api"].split(":")[1]
    if version.get("version") != expected or not version.get("commit"):
        raise ValueError(f"wrong API build: expected {expected}, got {version}")
    with urlopen(url, timeout=10) as response:
        html = response.read().decode()
        if response.headers.get_content_type() != "text/html" or 'id="root"' not in html:
            raise ValueError("portal did not serve the React entry page")
    print(f"Smoke passed: API {expected}, readiness, liveness, and React entry page via ingress.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--images", type=Path, required=True)
    args = parser.parse_args()
    try:
        check(args.url, json.loads(args.images.read_text()))
    except (OSError, ValueError, KeyError, HTTPError, URLError) as error:
        print(f"ERROR local smoke failed: {error}", file=sys.stderr)
        sys.exit(1)
