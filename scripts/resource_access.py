"""Loopback-only desktop access to the data services deployed in one explicit profile."""

import argparse
from dataclasses import dataclass
import json
import re
import selectors
import signal
import subprocess
import sys
import time


@dataclass(frozen=True)
class Endpoint:
    label: str
    namespace: str
    service: str
    remote: int
    local: int
    protocol: str


ENDPOINTS = (
    Endpoint("Payment / BI / Polaris PostgreSQL", "data", "payments-postgres", 5432, 15432, "PostgreSQL"),
    Endpoint("Knowledge PostgreSQL", "data", "knowledge-postgres", 5432, 15433, "PostgreSQL"),
    Endpoint("Identity PostgreSQL", "data", "identity-postgres", 5432, 15434, "PostgreSQL"),
    Endpoint("ClickHouse HTTP", "apps", "realtime-analytics", 8123, 18123, "HTTP"),
    Endpoint("ClickHouse native", "apps", "realtime-analytics", 9000, 19000, "ClickHouse"),
    Endpoint("Trino", "apps", "lakehouse-query", 8080, 18080, "HTTP/JDBC"),
    Endpoint("MinIO S3", "data", "minio", 9000, 19090, "HTTP/S3"),
    Endpoint("MinIO console", "data", "minio", 9001, 19091, "Browser"),
    Endpoint("Polaris catalog", "apps", "lakehouse-catalog", 8181, 18181, "HTTP/OAuth"),
    Endpoint("Schema registry", "data", "apicurio", 8080, 18081, "HTTP"),
)


def command(profile, namespace, *args):
    return ["kubectl", "--context", profile, "-n", namespace, *args]


def discover(profile, runner=subprocess.check_output):
    raw = runner(["kubectl", "--context", profile, "get", "services", "-A", "-o", "json"], text=True, timeout=30)
    services = {
        (item["metadata"]["namespace"], item["metadata"]["name"]):
        {port["port"] for port in item["spec"].get("ports", [])}
        for item in json.loads(raw)["items"]
    }
    endpoints = []
    for endpoint in ENDPOINTS:
        if (endpoint.namespace, endpoint.service) not in services:
            print(f"skip {endpoint.label}: service not deployed", flush=True)
        elif endpoint.remote not in services[(endpoint.namespace, endpoint.service)]:
            raise RuntimeError(f"{endpoint.namespace}/{endpoint.service} does not expose port {endpoint.remote}")
        else:
            endpoints.append(endpoint)
    if 19092 in services.get(("data", "payments-kafka"), set()):
        raw = runner(command(profile, "data", "get", "secret", "payments-kafka", "-o", "json"),
            text=True, timeout=30)
        import base64
        properties = base64.b64decode(json.loads(raw)["data"]["server.properties"]).decode()
        match = re.search(r"HOST://localhost:(\d+)", properties)
        if not match:
            raise RuntimeError("Kafka desktop Service exists but the broker does not advertise localhost")
        endpoints.append(Endpoint("Kafka", "data", "payments-kafka", 19092, int(match[1]), "SASL_SSL/SCRAM-SHA-512"))
    elif ("data", "payments-kafka") in services:
        print("skip Kafka desktop: set kafka_host_port=19092 in the profile's events.tfvars.json and rerun make run-local",
            flush=True)
    if not endpoints:
        raise RuntimeError("no supported data services found in the selected profile")
    ports = [endpoint.local for endpoint in endpoints]
    if len(ports) != len(set(ports)):
        raise RuntimeError("Kafka advertised port conflicts with another desktop forward; choose a different kafka_host_port")
    return endpoints


def start_forward(profile, endpoint, timeout=30):
    process = subprocess.Popen(
        command(profile, endpoint.namespace, "port-forward", "--address", "127.0.0.1",
                f"service/{endpoint.service}", f"{endpoint.local}:{endpoint.remote}"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            lines = []
            while time.monotonic() < deadline:
                if selector.select(timeout=max(0, deadline - time.monotonic())):
                    line = process.stdout.readline()
                    if not line:
                        raise RuntimeError(f"{endpoint.label} port-forward exited: {''.join(lines).strip()}")
                    lines.append(line)
                    if f"Forwarding from 127.0.0.1:{endpoint.local} " in line:
                        return process
            raise RuntimeError(f"{endpoint.label} port-forward timed out: {''.join(lines).strip()}")
    except BaseException:
        stop_forward(process)
        raise


def stop_forward(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


def serve(profile, endpoints):
    processes = []
    try:
        for endpoint in endpoints:
            process = start_forward(profile, endpoint)
            processes.append((endpoint, process))
            print(f"ready {endpoint.label}: 127.0.0.1:{endpoint.local} ({endpoint.protocol})", flush=True)
        print("Keep this command running. Ctrl-C closes only these forwards. Passwords: developer-setup.md, "
            "'Local accounts and credentials'. Do not enable extension cloud sync for local credentials.", flush=True)
        # Drain kubectl output so active clients cannot fill a pipe and block their own forwarding.
        with selectors.DefaultSelector() as selector:
            for endpoint, process in processes:
                selector.register(process.stdout, selectors.EVENT_READ, endpoint)
            while True:
                for endpoint, process in processes:
                    if process.poll() is not None:
                        raise RuntimeError(f"{endpoint.label} port-forward stopped; rerun make access-local")
                for key, _ in selector.select(timeout=1):
                    line = key.fileobj.readline()
                    if not line:
                        raise RuntimeError(f"{key.data.label} port-forward closed; rerun make access-local")
                    if "error" in line.lower():
                        print(f"{key.data.label}: {line.strip()}", file=sys.stderr, flush=True)
    finally:
        for _, process in reversed(processes):
            stop_forward(process)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--list", action="store_true", help="show deployed endpoints without opening forwards")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        parser.error("invalid profile")
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        endpoints = discover(args.profile)
        if args.list:
            for endpoint in endpoints:
                print(f"{endpoint.label}: 127.0.0.1:{endpoint.local} ({endpoint.protocol})")
        else:
            serve(args.profile, endpoints)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"ERROR local resource access: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
