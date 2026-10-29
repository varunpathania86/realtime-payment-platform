#!/usr/bin/env python3
"""Check local ingress responses and the currently deployed API image version."""

import argparse
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def check(url, images, *, deadline=None):
    def open_endpoint(path):
        timeout = 10 if deadline is None else min(10, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("local ingress readiness deadline exceeded")
        return urlopen(f"{url}{path}", timeout=timeout)

    for endpoint in ("healthz", "readyz"):
        with open_endpoint(f"/api/{endpoint}") as response:
            if json.load(response).get("status") != "ok":
                raise ValueError(f"{endpoint} did not return status ok")
    with open_endpoint("/api/version") as response:
        version = json.load(response)
    expected = images["local_images"]["payment-api"].split(":")[1]
    if version.get("version") != expected or not version.get("commit"):
        raise ValueError(f"wrong API build: expected {expected}, got {version}")
    with open_endpoint("") as response:
        html = response.read().decode()
        if response.headers.get_content_type() != "text/html" or 'id="root"' not in html:
            raise ValueError("portal did not serve the React entry page")
    print(f"Smoke passed: API {expected}, readiness, liveness, and React entry page via ingress.")


def wait_check(url, images, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            check(url, images, deadline=deadline)
            return
        except (URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code not in (404, 502, 503, 504):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            print(f"Waiting for local ingress readiness: {error}", file=sys.stderr)
            time.sleep(min(1, remaining))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()
    try:
        images = json.loads(args.images.read_text())
        if args.wait_seconds > 0:
            wait_check(args.url, images, args.wait_seconds)
        else:
            check(args.url, images)
    except (OSError, ValueError, KeyError, HTTPError, URLError) as error:
        print(f"ERROR local smoke failed: {error}", file=sys.stderr)
        sys.exit(1)
