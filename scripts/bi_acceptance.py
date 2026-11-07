"""Verify the Stage 9 embedded BI access boundaries on an explicit local profile."""

import argparse
import base64
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse

PORTAL_HOST = "localhost"
SUPERSET_HOST = "bi.localhost"
# Privileged Superset APIs a guest token must never reach.
DENIED = (
    ("GET", "/api/v1/database/", None),
    ("GET", "/api/v1/dataset/", None),
    ("POST", "/api/v1/sqllab/execute/", {"database_id": 1, "sql": "SELECT 1", "runAsync": False}),
    ("POST", "/api/v1/database/", {"database_name": "rpp-verify", "sqlalchemy_uri": "sqlite://"}),
    ("GET", "/api/v1/security/roles/", None),
)
# An ad-hoc query against a dashboard datasource, not one of the dashboard's saved charts.
AD_HOC_QUERY = {"datasource": {"id": 1, "type": "table"}, "queries": [{"columns": ["currency"], "metrics": ["count"]}]}
CSRF = re.compile(r'id="csrf_token"[^>]*value="([^"]+)"')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_arguments):
        return None


def http(base, method, path, host, headers=None, body=None):
    """Send one request through the ingress; returns (status, headers, text) without following redirects."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Host": host, **(headers or {})})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode(errors="replace")


def claims(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def session_cookie(headers):
    value = next((v for k, v in headers.items() if k.lower() == "set-cookie"), "")
    match = re.match(r"rpp_portal_session=([^;]+)", value)
    if not match or "httponly" not in value.lower() or "samesite=strict" not in value.lower():
        raise AssertionError("login did not set an HttpOnly, SameSite=Strict session cookie")
    return "rpp_portal_session=" + match.group(1)


def verify(send, origin, users, dashboard_id):
    """Run the access-boundary checks; send(method, path, host, headers, body) -> (status, headers, text)."""
    results = []

    def expect(name, condition):
        if not condition:
            raise AssertionError(name)
        results.append(name)

    post = {"Origin": origin}
    status, _, _ = send("POST", "/bi/guest-token", PORTAL_HOST, post, None)
    expect("anonymous guest-token request is rejected (401)", status == 401)
    status, _, _ = send("POST", "/auth/login", PORTAL_HOST, {"Origin": "http://attacker.example"},
        {"username": "ops-viewer", "password": users["ops-viewer"]["password"]})
    expect("sign-in from a foreign origin is rejected (403)", status == 403)
    status, _, _ = send("POST", "/auth/login", PORTAL_HOST, post, {"username": "ops-viewer", "password": "wrong-password"})
    expect("wrong password is rejected (401)", status == 401)

    status, headers, _ = send("POST", "/auth/login", PORTAL_HOST, post,
        {"username": "payments-clerk", "password": users["payments-clerk"]["password"]})
    expect("payments-clerk can sign in", status == 200)
    clerk = {**post, "Cookie": session_cookie(headers)}
    status, _, text = send("GET", "/auth/session", PORTAL_HOST, clerk, None)
    expect("payments-clerk session exposes no dashboard", status == 200 and "bi" not in json.loads(text))
    status, _, _ = send("POST", "/bi/guest-token", PORTAL_HOST, clerk, None)
    expect("payments-clerk is denied a guest token (403)", status == 403)

    status, headers, text = send("POST", "/auth/login", PORTAL_HOST, post,
        {"username": "ops-viewer", "password": users["ops-viewer"]["password"]})
    expect("ops-viewer can sign in", status == 200)
    viewer = {**post, "Cookie": session_cookie(headers)}
    expect("ops-viewer session names only the embedded dashboard",
        json.loads(text).get("bi", {}).get("dashboardId") == dashboard_id)
    status, _, text = send("POST", "/bi/guest-token", PORTAL_HOST, viewer, None)
    expect("ops-viewer receives a guest token", status == 200)
    token = json.loads(text)["token"]
    payload = claims(token)
    expect("guest token is scoped to one dashboard without row-level rules",
        payload.get("resources") == [{"type": "dashboard", "id": dashboard_id}] and payload.get("rls_rules") == [])
    expect("guest token lives at most five minutes", 0 < payload["exp"] - payload["iat"] <= 300)
    expect("guest token carries only identity and scope claims",
        set(payload) <= {"user", "resources", "rls_rules", "iat", "exp", "aud", "type"}
        and set(payload.get("user", {})) <= {"username", "first_name", "last_name"})

    status, _, _ = send("GET", f"/embedded/{dashboard_id}", SUPERSET_HOST, {"Referer": "http://attacker.example/"}, None)
    expect("embedded page refuses a foreign referrer (403)", status == 403)
    status, headers, page = send("GET", f"/embedded/{dashboard_id}", SUPERSET_HOST, {"Referer": origin + "/analytics"}, None)
    policy = next((v for k, v in headers.items() if k.lower() == "content-security-policy"), "")
    ancestors = re.search(r"frame-ancestors ([^;]+)", policy)
    expect("embedded page loads from the portal", status == 200)
    expect("CSP frame-ancestors allows the portal origin and nothing broader",
        ancestors is not None and origin in ancestors.group(1).split() and "*" not in ancestors.group(1))

    # Use the CSRF token and session the embedded page hands every browser, so denials prove permissions.
    csrf, cookie = CSRF.search(page), re.match(r"(session=[^;]+)", next(
        (v for k, v in headers.items() if k.lower() == "set-cookie"), ""))
    expect("embedded page issues a CSRF token and session", csrf is not None and cookie is not None)
    guest = {"X-GuestToken": token, "X-CSRFToken": csrf.group(1), "Cookie": cookie.group(1)}
    # The embedded frontend legitimately reads its own dashboard; listings must stay scoped to it.
    status, _, text = send("GET", f"/api/v1/embedded_dashboard/{dashboard_id}", SUPERSET_HOST, guest, None)
    expect("guest token resolves the embedded dashboard", status == 200)
    embedded = json.loads(text)["result"]["dashboard_id"]
    status, _, text = send("GET", "/api/v1/dashboard/", SUPERSET_HOST, guest, None)
    expect("guest dashboard listing contains only the embedded dashboard",
        status in (401, 403) or (status == 200 and [str(i) for i in json.loads(text)["ids"]] == [str(embedded)]))
    status, _, text = send("GET", "/api/v1/chart/", SUPERSET_HOST, guest, None)
    expect("guest chart listing exposes no chart definitions",
        status in (401, 403) or (status == 200 and json.loads(text)["count"] == 0))
    for method, path, body in DENIED:
        status, _, _ = send(method, path, SUPERSET_HOST, guest, body)
        expect(f"guest token is denied {method} {path} ({status})", status in (401, 403, 404, 405))
    status, _, text = send("POST", "/api/v1/chart/data", SUPERSET_HOST, guest, AD_HOC_QUERY)
    expect(f"guest token cannot run ad-hoc chart queries ({status})", status == 403 and "saved chart" in text)
    status, _, _ = send("GET", "/api/v1/security/csrf_token/", SUPERSET_HOST, guest, None)
    expect(f"guest token cannot obtain an authenticated CSRF token ({status})", status in (401, 403))
    return results


def kubectl(profile, *arguments, runner=subprocess.run):
    return runner(["kubectl", "--context", profile, "-n", "apps", *arguments],
        check=True, capture_output=True, text=True, timeout=60).stdout


def secret(profile, name, key, runner=subprocess.run):
    return base64.b64decode(kubectl(profile, "get", "secret", name, "-o", f"jsonpath={{.data.{key}}}", runner=runner)).decode()


def portal_env(profile, name, runner=subprocess.run):
    return kubectl(profile, "get", "deployment", "web-portal", "-o",
        f'jsonpath={{.spec.template.spec.containers[0].env[?(@.name=="{name}")].value}}', runner=runner)


def forward(profile):
    process = subprocess.Popen(
        ["kubectl", "--context", profile, "-n", "platform", "port-forward", "--address", "127.0.0.1", "service/traefik", ":80"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        match = re.search(r"127\.0\.0\.1:(\d+)", process.stdout.readline())
        if match:
            return process, f"http://127.0.0.1:{match.group(1)}"
    process.terminate()
    raise RuntimeError("timed out starting the ingress port-forward")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    try:
        dashboard_id = portal_env(args.profile, "BI_DASHBOARD_ID")
        origins = portal_env(args.profile, "PORTAL_ALLOWED_ORIGINS").split()
        if not dashboard_id or not origins:
            raise RuntimeError("BI is not enabled on this profile (web-portal has no BI configuration)")
        issuer = portal_env(args.profile, "OIDC_ISSUER")
        if issuer:
            from scripts.identity_browser import credentials
            _, users = credentials(args.profile)
        else:
            users = json.loads(secret(args.profile, "web-portal-identity", "dev-users"))
        process, base = forward(args.profile)
        try:
            send = lambda *request: http(base, *request)  # noqa: E731
            if issuer:
                from scripts.identity_browser import OIDCSessionSender
                send = OIDCSessionSender(urllib.parse.urlparse(base).port, origins[0], issuer, send)
            results = verify(send, origins[0], users, dashboard_id)
        finally:
            process.terminate()
            process.wait(timeout=10)
    except (AssertionError, RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        sys.exit(f"ERROR BI verification: {error}")
    for name in results:
        print(f"ok  {name}")
    print(f"BI verification passed ({len(results)} checks)")


if __name__ == "__main__":
    main()
