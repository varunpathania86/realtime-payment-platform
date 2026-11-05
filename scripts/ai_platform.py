"""Stage 10 assistant operations on an explicit local profile: knowledge ingestion, synthetic
ClickHouse fixtures and the starter evaluation run through the portal BFF.

All data is synthetic. Evaluation reports go to .local/<profile>/ai-eval/ and contain scores,
statuses and document identifiers, never answers or user passwords.
"""

import argparse
import datetime
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
import uuid

from scripts.bi_acceptance import PORTAL_HOST, forward, portal_env, secret, session_cookie
from scripts.module_catalog import ROOT

KNOWLEDGE_SRC = ROOT / "modules/operations-knowledge/src"
CASES = ROOT / "modules/operations-knowledge/evaluation/cases.yaml"
CORPUS_CONFIGMAP = "operations-knowledge-corpus"
INGEST_CRONJOB = "operations-knowledge-ingest"
FIXTURE_DATASET = "synthetic-ai-fixtures"
FIXTURE_NAMESPACE = uuid.UUID("3f0b8d2e-6a59-4c1e-9d7a-2b5f4e8c1a10")
# A fixed synthetic account and idempotency key make the lookup payment replay on every run.
LOOKUP_ACCOUNT = "7d1e0c4a-5b8f-4e2d-9a6c-3f1b2e4d5c6a"
LOOKUP_KEY = "rpp-ai-eval-lookup-payment"
CURRENCIES = (("USD", "US"), ("EUR", "DE"), ("GBP", "GB"), ("INR", "IN"), ("SGD", "SG"))
METHODS = ("card", "bank_transfer", "wallet")
MODEL = ("payment-risk", "1.0.0", "0" * 64)
PROFILE = re.compile(r"[a-z][a-z0-9-]{0,39}")
ANSWER_TIMEOUT = 240


def kubectl(profile, *arguments, namespace="apps", stdin=None, timeout=120):
    return subprocess.run(
        ["kubectl", "--context", profile, "--request-timeout=60s", "-n", namespace, *arguments],
        input=stdin, check=True, capture_output=True, text=True, timeout=timeout).stdout


def fixture_rows(now_ms, seed=20251105, count=600):
    """Deterministic synthetic payment facts and advisory predictions spread over the last 24 hours."""
    generator = random.Random(seed)
    facts, predictions = [], []
    for index in range(count):
        # The first third lands in the last hour so hourly tool questions have data.
        horizon = 3_600_000 if index < count // 3 else 86_400_000
        event_ms = now_ms - generator.randrange(60_000, horizon)
        currency, country = CURRENCIES[generator.randrange(len(CURRENCIES))]
        method = METHODS[generator.randrange(len(METHODS))]
        outcome = "rejected" if generator.random() < (0.18 if method == "wallet" else 0.08) else "authorized"
        event_id = str(uuid.uuid5(FIXTURE_NAMESPACE, f"event-{index}"))
        payment_id = str(uuid.uuid5(FIXTURE_NAMESPACE, f"payment-{index}"))
        facts.append({
            "event_time_ms": event_ms, "event_id": event_id, "payment_id": payment_id,
            "account_id": str(uuid.uuid5(FIXTURE_NAMESPACE, f"account-{index % 40}")),
            "amount_minor": generator.randrange(100, 250_000), "currency": currency, "country": country,
            "payment_method": method, "outcome": outcome, "schema_version": 1, "job_name": "ai-fixtures",
            "job_version": "1", "transformation_id": "ai-fixtures-v1", "source_dataset": FIXTURE_DATASET,
            "output_dataset": "analytics.payment_facts",
        })
        if index % 2 == 0:
            score = round(min(0.99, generator.betavariate(2, 8) + (0.35 if outcome == "rejected" else 0)), 4)
            prediction_id = uuid.uuid5(FIXTURE_NAMESPACE, f"prediction-{index}").hex * 2
            predictions.append({
                "prediction_id": prediction_id, "original_prediction_id": prediction_id, "event_id": event_id,
                "payment_id": payment_id, "event_time_ms": event_ms, "window_revision": 0,
                "feature_version": "payment-risk-features-v1", "model_name": MODEL[0], "model_version": MODEL[1],
                "model_sha256": MODEL[2], "risk_score": score,
                "reason_codes": ["high_amount"] if score >= 0.8 else [], "advisory_only": True,
                "prediction_time_ms": event_ms + 250, "schema_version": 1, "source_dataset": FIXTURE_DATASET,
                "output_dataset": "analytics.payment_risk_predictions",
            })
    return facts, predictions


def load_fixtures(profile):
    facts, predictions = fixture_rows(int(time.time() * 1000))
    client = ('clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" '
                '--database analytics --query "$0"')
    for table, rows in (("payment_facts", facts), ("payment_risk_predictions", predictions)):
        # Replace earlier fixture rows; real Flink output in the same table is left untouched.
        kubectl(profile, "exec", "statefulset/realtime-analytics", "--", "sh", "-c", client,
                f"ALTER TABLE {table} DELETE WHERE source_dataset = '{FIXTURE_DATASET}' SETTINGS mutations_sync = 2")
        kubectl(profile, "exec", "-i", "statefulset/realtime-analytics", "--", "sh", "-c", client,
                f"INSERT INTO {table} FORMAT JSONEachRow",
                stdin="".join(json.dumps(row) + "\n" for row in rows))
    return len(facts), len(predictions)


def ingest(profile, wait_seconds=1800):
    sys.path.insert(0, str(KNOWLEDGE_SRC))
    from operations_knowledge.manifest import bundle

    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "corpus.tar.gz"
        documents = bundle(ROOT, archive)
        manifest = kubectl(profile, "create", "configmap", CORPUS_CONFIGMAP,
                            f"--from-file=corpus.tar.gz={archive}", "--dry-run=client", "-o", "yaml")
        kubectl(profile, "apply", "-f", "-", stdin=manifest)
    name = "knowledge-ingest-" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    kubectl(profile, "create", "job", f"--from=cronjob/{INGEST_CRONJOB}", name)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        status = json.loads(kubectl(profile, "get", "job", name, "-o", "jsonpath={.status}") or "{}")
        if status.get("succeeded"):
            print(kubectl(profile, "logs", f"job/{name}", "--tail=20"), end="")
            return documents, name
        if status.get("failed", 0) > 2 or any(
                item.get("type") == "Failed" and item.get("status") == "True" for item in status.get("conditions", [])):
            raise RuntimeError(f"ingestion job {name} failed; inspect kubectl logs job/{name}")
        time.sleep(5)
    raise RuntimeError(f"ingestion job {name} did not finish within {wait_seconds} seconds")


def lookup_payment(send, origin):
    status, _, text = send("POST", "/api/payments", PORTAL_HOST,
        {"Origin": origin, "Idempotency-Key": LOOKUP_KEY},
        {"account_id": LOOKUP_ACCOUNT, "amount_minor": 4200, "currency": "EUR"})
    if status != 201:
        raise RuntimeError(f"creating the synthetic lookup payment returned {status}")
    return json.loads(text)["id"]


def evaluate(send, origin, users, suite, payment_id, clock=time.monotonic):
    """Ask every case through the portal BFF as its synthetic user; returns scored results."""
    sys.path.insert(0, str(KNOWLEDGE_SRC))
    from operations_knowledge.evaluation import score_case

    cookies, results = {}, []
    for case in suite["cases"]:
        user = case["user"]
        if user not in cookies:
            status, headers, _ = send("POST", "/auth/login", PORTAL_HOST, {"Origin": origin},
                                        {"username": user, "password": users[user]["password"]})
            if status != 200:
                raise RuntimeError(f"{user} cannot sign in ({status})")
            cookies[user] = session_cookie(headers)
        question = case["question"].replace("{payment_id}", payment_id)
        started = clock()
        status, _, text = send("POST", "/assistant/ask", PORTAL_HOST,
                                {"Origin": origin, "Cookie": cookies[user]}, {"question": question})
        elapsed = round((clock() - started) * 1000)
        response = json.loads(text) if status == 200 else {"status": f"http-{status}"}
        response.setdefault("latency_ms", elapsed)
        results.append(score_case(case, response, suite))
    return results


def failures(results):
    """Leaks and failed authorization or injection cases fail the run; quality is reported only."""
    return [item["id"] for item in results
            if item["leaks"] or (item["category"] in {"authorization", "injection"} and not item["passed"])]


def run_evaluation(profile):
    sys.path.insert(0, str(KNOWLEDGE_SRC))
    from operations_knowledge.evaluation import load_cases, markdown_report, summarize

    if portal_env(profile, "AI_ORCHESTRATOR_URL") == "":
        raise RuntimeError("the assistant is not enabled on this profile (web-portal has no AI_ORCHESTRATOR_URL)")
    origins = portal_env(profile, "PORTAL_ALLOWED_ORIGINS").split()
    users = json.loads(secret(profile, "web-portal-identity", "dev-users"))
    suite = load_cases(CASES)
    process, base = forward(profile)
    try:
        send = lambda *request: http_long(base, *request)  # noqa: E731
        results = evaluate(send, origins[0], users, suite, lookup_payment(send, origins[0]))
    finally:
        process.terminate()
        process.wait(timeout=10)
    summary = summarize(results)
    output = ROOT / ".local" / profile / "ai-eval"
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2) + "\n")
    (output / "report.md").write_text(markdown_report(summary, results))
    return summary, results, output


def http_long(base, method, path, host, headers=None, body=None):
    """bi_acceptance.http with a timeout long enough for CPU inference."""
    import urllib.error
    import urllib.request

    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Host": host, **(headers or {})})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=ANSWER_TIMEOUT) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode(errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ingest", "fixtures", "eval"))
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    if not PROFILE.fullmatch(args.profile):
        sys.exit("ERROR: set MINIKUBE_PROFILE to an explicit local profile, for example rpp-a")
    try:
        if args.command == "ingest":
            documents, job = ingest(args.profile)
            print(f"Ingested {documents} corpus documents with job {job}")
        elif args.command == "fixtures":
            facts, predictions = load_fixtures(args.profile)
            print(f"Loaded {facts} synthetic payment facts and {predictions} advisory predictions into ClickHouse")
        else:
            summary, results, output = run_evaluation(args.profile)
            for key, value in summary.items():
                print(f"{key}: {value}")
            print(f"Report: {output / 'report.md'}")
            failed = failures(results)
            if failed:
                sys.exit(f"ERROR assistant evaluation: leaks or failed negative cases: {', '.join(failed)}")
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        sys.exit(f"ERROR assistant {args.command}: {error}")


if __name__ == "__main__":
    main()
