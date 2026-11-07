"""Verify Stage 11 SSO and authorization against an explicitly selected local profile."""

import argparse
import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request

from scripts.identity_browser import LocalBrowser, credentials, forward
from scripts.identity_reconcile import RealmAdmin, reconcile, render_realm
from pathlib import Path


def token_claims(token):
    # Used only for acceptance assertions after successful provider code exchange, not authorization.
    value = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


def form_request(base, path, fields):
    request = urllib.request.Request(base + path, data=urllib.parse.urlencode(fields).encode(),
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
        return json.load(response)


def human_token(browser, values, base):
    verifier, nonce, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    callback = browser.origin + "/auth/callback"
    start = browser.issuer + "/protocol/openid-connect/auth?" + urllib.parse.urlencode({
        "client_id": "web-portal", "redirect_uri": callback, "response_type": "code", "scope": "openid profile email",
        "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge,
    })
    location = browser.login(start, "ops-viewer", values["VIEWER_PASSWORD"], stop_at=callback)
    result = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    if result.get("state") != [state]:
        raise AssertionError("provider authorization state mismatch")
    tokens = form_request(base, "/realms/rpp/protocol/openid-connect/token", {
        "grant_type": "authorization_code", "client_id": "web-portal",
        "client_secret": values["PORTAL_CLIENT_SECRET"], "redirect_uri": callback,
        "code": result["code"][0], "code_verifier": verifier,
    })
    if token_claims(tokens["id_token"]).get("nonce") != nonce:
        raise AssertionError("provider authorization nonce mismatch")
    return tokens["access_token"]


def exchange(base, values, subject, audience):
    return form_request(base, "/realms/rpp/protocol/openid-connect/token", {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "client_id": "web-portal",
        "client_secret": values["PORTAL_CLIENT_SECRET"], "subject_token": subject,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token", "audience": audience,
    })["access_token"]


def direct(port, method, path, token, extra_headers=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}" + path, method=method,
                                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                             **(extra_headers or {})},
                                    data=b'{"question":"show payment totals"}' if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=240) as response:  # nosec B310
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def verify(profile, *, include_assistant=True):
    forwards, results = [], []
    def start(namespace, service, port):
        process, local_port = forward(profile, namespace, service, port)
        forwards.append(process)
        return local_port
    def expect(name, value):
        if not value:
            raise AssertionError(name)
        results.append(name)
        print("ok  " + name, flush=True)
    try:
        values, users = credentials(profile)
        origin = values["PORTAL_ORIGIN"]
        issuer = "http://identity.localhost:" + str(urllib.parse.urlparse(origin).port) + "/realms/rpp"
        admin_base = "http://127.0.0.1:" + str(start("security", "identity-provider", 8080))
        admin = RealmAdmin(admin_base, values)
        realm = render_realm(Path("platform/terraform/modules/identity/realm.json").read_text(), values)
        reconcile(admin, realm)
        reconcile(admin, realm)
        expect("existing realm reconciles twice without changing human credentials", True)
        browser = LocalBrowser(start("platform", "traefik", 80), origin, issuer)
        status, _, _ = browser.json("POST", "/bi/guest-token")
        expect("anonymous BI access denied", status == 401)
        browser.login(origin + "/auth/login", "ops-viewer", users["ops-viewer"]["password"])
        cookie = browser.session_cookie()
        expect("portal receives opaque server-side session, not a JWT", len(cookie) >= 32 and "." not in cookie)
        status, _, text = browser.json("GET", "/auth/session")
        session = json.loads(text)
        expect("Keycloak viewer accesses React and its explicit application permissions",
                status == 200 and session["username"] == "ops-viewer" and session["assistant"] and "bi" in session)
        writer = LocalBrowser(browser.forwarding_port, origin, issuer)
        writer.login(origin + "/auth/login", "payments-clerk", users["payments-clerk"]["password"])
        status, _, text = writer.json("POST", "/api/payments",
            {"account_id": "11111111-1111-4111-8111-111111111111", "amount_minor": 100, "currency": "USD"},
            {"Idempotency-Key": "stage11-identity-acceptance"})
        expect("Keycloak payment clerk can create the synthetic acceptance fixture", status == 201)
        payment_path = "/api/payments/" + json.loads(text)["id"]
        writer.json("POST", "/auth/logout")
        status, _, _ = browser.json("GET", payment_path)
        expect("BFF exchanges and forwards an audience-specific payment read token", status == 200)
        status, _, _ = browser.json("POST", "/api/payments", {})
        expect("viewer payment writes denied at the BFF", status == 403)
        status, _, _ = browser.json("POST", "/bi/guest-token")
        expect("Keycloak viewer receives a scoped Superset embedded token", status == 200)
        before = browser.password_submissions
        browser.login(values["SUPERSET_ORIGIN"] + "/login/keycloak", "ops-viewer", users["ops-viewer"]["password"])
        status, _, text = browser.json("GET", values["SUPERSET_ORIGIN"] + "/api/v1/me/")
        expect("same Keycloak SSO user accesses interactive Superset without another password",
                status == 200 and "keycloak_" in text and browser.password_submissions == before)
        browser.login(values["GRAFANA_ORIGIN"] + "/login/generic_oauth", "ops-viewer", users["ops-viewer"]["password"])
        status, _, text = browser.json("GET", values["GRAFANA_ORIGIN"] + "/api/user")
        expect("same Keycloak SSO user accesses Grafana without another password",
                status == 200 and json.loads(text).get("login") == "ops-viewer" and browser.password_submissions == before)
        status, _, text = browser.json("GET", values["GRAFANA_ORIGIN"] + "/api/user/orgs")
        expect("Grafana maps viewer to Viewer, not Admin or Editor",
                status == 200 and all(org["role"] == "Viewer" for org in json.loads(text)))
        if include_assistant:
            status, _, text = browser.json("POST", "/assistant/ask",
                {"question": "What is the payment authorization rate by country in the last hour?"})
            answer = json.loads(text)
            expect("same Keycloak viewer invokes the AI assistant through resource-specific tokens",
                    status == 200 and answer.get("status") in {"answered", "fallback"} and
                    any(tool["name"] == "payment_metrics" and tool["status"] == "ok"
                        for tool in answer.get("tools", [])))
        source = human_token(browser, values, admin_base)
        payment = exchange(admin_base, values, source, "payment-api")
        tools = exchange(admin_base, values, source, "assistant-tools")
        orchestrator = exchange(admin_base, values, source, "ai-orchestrator")
        aud = token_claims(payment)["aud"]
        expect("payment token excludes the AI audiences", "payment-api" in (aud if isinstance(aud, list) else [aud])
                and "assistant-tools" not in aud and "ai-orchestrator" not in aud)
        api_port = start("apps", "payment-api", 80)
        ai_port = start("apps", "ai-orchestrator", 8080)
        expect("viewer write denied by payment API itself", direct(api_port, "POST", "/api/payments", payment) == 403)
        expect("tools token cannot be replayed against payment API", direct(api_port, "GET", payment_path, tools) == 401)
        expect("payment token cannot be replayed against orchestrator",
                direct(ai_port, "POST", "/v1/assistant/ask", payment,
                    {"X-Assistant-Tools-Assertion": tools, "X-Assistant-Payment-Token": payment}) == 401)
        expect("missing relayed tools token fails closed", direct(ai_port, "POST", "/v1/assistant/ask", orchestrator) == 401)
        realm_id = admin.call("GET", "")["id"]
        name = "stage11-verification-" + secrets.token_hex(4)
        components = admin.call("GET", "/components?type=org.keycloak.keys.KeyProvider")
        priority = max(int(item.get("config", {}).get("priority", ["0"])[0]) for item in components) + 1
        admin.call("POST", "/components", {"name": name, "parentId": realm_id, "providerId": "rsa-generated",
                    "providerType": "org.keycloak.keys.KeyProvider",
                    "config": {"priority": [str(priority)], "enabled": ["true"], "active": ["true"],
                                "algorithm": ["RS256"], "keySize": ["2048"]}})
        component = next(item for item in admin.call("GET", "/components") if item["name"] == name)
        try:
            rotated = human_token(browser, values, admin_base)
            rotated_payment = exchange(admin_base, values, rotated, "payment-api")
            old_header = json.loads(base64.urlsafe_b64decode(source.split(".")[0] + "=="))
            new_header = json.loads(base64.urlsafe_b64decode(rotated.split(".")[0] + "=="))
            expect("Keycloak rotates the signing kid", new_header["kid"] != old_header["kid"])
            expect("payment verifier refreshes JWKS and accepts the new signer",
                    direct(api_port, "GET", payment_path, rotated_payment) == 200)
            expect("old signer remains accepted while its tokens are valid",
                    direct(api_port, "GET", payment_path, payment) == 200)
        finally:
            admin.call("DELETE", "/components/" + component["id"])
        try:
            admin.call("PUT", "", {"accessTokenLifespan": 5})
            expired = human_token(browser, values, admin_base)
            expired_payment = exchange(admin_base, values, expired, "payment-api")
            time.sleep(6)
            expect("genuinely signed expired payment token fails closed",
                    direct(api_port, "GET", payment_path, expired_payment) == 401)
        finally:
            admin.call("PUT", "", {"accessTokenLifespan": realm["accessTokenLifespan"]})
        status, _, _ = browser.json("POST", "/auth/logout")
        expect("logout revokes BFF and Keycloak sessions", status == 204)
        status, _, _ = browser.json("GET", "/auth/session")
        expect("logged-out browser cannot recover its session", status == 401)
        before = browser.password_submissions
        browser.login(origin + "/auth/login", "ops-viewer", users["ops-viewer"]["password"])
        expect("provider logout requires a fresh password, not the old SSO session",
                browser.password_submissions == before + 1)
        browser.json("POST", "/auth/logout")
        return results
    finally:
        for process in reversed(forwards):
            process.terminate()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--skip-assistant", action="store_true")
    args = parser.parse_args()
    results = verify(args.profile, include_assistant=not args.skip_assistant)
    print(f"Stage 11 live identity verification passed ({len(results)} checks).")


if __name__ == "__main__":
    main()
