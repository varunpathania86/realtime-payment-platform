"""Isolated real-Keycloak checks; run with make identity-integration after IDENTITY=1 make setup."""

import base64
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]
CALLBACK = "http://127.0.0.1:7777/auth/callback"


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=180).strip()


class CallbackRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if new_url.startswith(CALLBACK):
            return None
        return super().redirect_request(request, response, code, message, headers, new_url)


class LoopbackBrowserCookies(http.cookiejar.DefaultCookiePolicy):
    def return_ok_secure(self, cookie, request):
        # Browsers permit Secure cookies on trustworthy localhost HTTP; urllib does not.
        parsed = urllib.parse.urlparse(request.full_url)
        if parsed.scheme == "http" and parsed.hostname == "localhost":
            return True
        return super().return_ok_secure(cookie, request)


class LoginForm(HTMLParser):
    action = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values["action"]


class IdentityProviderCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = "rpp-identity-check-" + uuid.uuid4().hex[:12]
        cls.database = cls.network + "-db"
        cls.provider = cls.network + "-kc"
        cls.directory = tempfile.TemporaryDirectory(prefix="rpp-identity-check-")
        cls.addClassCleanup(cls.directory.cleanup)
        docker("network", "create", cls.network)
        cls.addClassCleanup(docker, "network", "rm", cls.network)
        pins = dict(line.split() for line in (ROOT / ".tool-versions").read_text().splitlines()
                    if line and not line.startswith("#"))
        cls.credentials = {key: secrets.token_urlsafe(36) for key in (
            "PORTAL_CLIENT_SECRET", "GRAFANA_CLIENT_SECRET", "SUPERSET_CLIENT_SECRET",
            "ARGOCD_CLIENT_SECRET", "VIEWER_PASSWORD", "CLERK_PASSWORD", "ENGINEER_PASSWORD",
        )}
        cls.credentials.update({
            "PORTAL_ORIGIN": CALLBACK.removesuffix("/auth/callback"),
            "GRAFANA_ORIGIN": "http://grafana.example.invalid",
            "SUPERSET_ORIGIN": "http://bi.example.invalid",
            "ARGOCD_ORIGIN": "http://argocd.example.invalid",
        })
        env = Path(cls.directory.name) / "database.env"
        password = secrets.token_urlsafe(36)
        env.write_text(f"POSTGRES_USER=keycloak\nPOSTGRES_DB=keycloak\nPOSTGRES_PASSWORD={password}\n")
        env.chmod(0o600)
        docker("run", "-d", "--name", cls.database, "--network", cls.network, "--network-alias", "identity-db",
                "--user", "70:70", "--read-only",
                "--tmpfs", "/var/lib/postgresql/data:uid=70,gid=70",
                "--tmpfs", "/var/run/postgresql:uid=70,gid=70", "--tmpfs", "/tmp",
                "--env-file", str(env), f"postgres:{pins['postgres']}")
        cls.addClassCleanup(docker, "rm", "-f", cls.database)
        for _ in range(60):
            status = subprocess.run(
                ["docker", "exec", cls.database, "pg_isready", "-U", "keycloak", "-d", "keycloak"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if status.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("isolated identity PostgreSQL did not become ready")
        image = subprocess.check_output(
            ["bash", str(ROOT / "scripts/identity-runtime.sh"), "image"], text=True,
        ).strip()
        env = Path(cls.directory.name) / "provider.env"
        values = {
            **cls.credentials, "KC_DB": "postgres", "KC_DB_URL_HOST": "identity-db",
            "KC_DB_URL_DATABASE": "keycloak", "KC_DB_USERNAME": "keycloak", "KC_DB_PASSWORD": password,
            "KC_HTTP_ENABLED": "true", "KC_HOSTNAME": "localhost",
        }
        env.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        env.chmod(0o600)
        realm = ROOT / "platform/terraform/modules/identity/realm.json"
        docker("run", "-d", "--name", cls.provider, "--network", cls.network,
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp:uid=1000", "--tmpfs", "/opt/keycloak/data:uid=1000",
                "-v", f"{realm}:/opt/keycloak/data/import/rpp-realm.json:ro",
                "-p", "127.0.0.1::8080", "--env-file", str(env), image)
        cls.addClassCleanup(docker, "rm", "-f", cls.provider)
        port = docker("port", cls.provider, "8080/tcp").rsplit(":", 1)[1]
        cls.base = f"http://localhost:{port}"
        cls.realm = cls.base + "/realms/rpp"
        for _ in range(120):
            try:
                cls.metadata = cls.json_request(cls.realm + "/.well-known/openid-configuration")
                break
            except urllib.error.HTTPError as error:
                error.close()
                time.sleep(1)
            except (urllib.error.URLError, ConnectionError):
                time.sleep(1)
        else:
            raise RuntimeError(f"isolated Keycloak did not become ready; inspect container {cls.provider}")

    @staticmethod
    def json_request(url, data=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        with urllib.request.urlopen(url, data=body, timeout=10) as response:
            return json.load(response)

    def authorize(self, user, challenge=True):
        self.jar = http.cookiejar.CookieJar(policy=LoopbackBrowserCookies())
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar), CallbackRedirect(),
        )
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        query = {
            "client_id": "web-portal", "redirect_uri": CALLBACK, "response_type": "code",
            "scope": "openid profile email", "state": state, "nonce": nonce,
        }
        if challenge:
            query.update({
                "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
                "code_challenge_method": "S256",
            })
        with opener.open(self.metadata["authorization_endpoint"] + "?" + urllib.parse.urlencode(query), timeout=10) as response:
            form = LoginForm()
            form.feed(response.read().decode())
        self.assertTrue(form.action, "Keycloak did not display its username/password login form")
        password = self.credentials[{"ops-viewer": "VIEWER_PASSWORD", "payments-clerk": "CLERK_PASSWORD"}[user]]
        body = urllib.parse.urlencode({"username": user, "password": password, "credentialId": ""}).encode()
        with self.assertRaises(urllib.error.HTTPError) as redirect:
            opener.open(form.action, data=body, timeout=10)
        self.addCleanup(redirect.exception.close)
        if redirect.exception.code != 302:
            logs = docker("logs", self.provider)
            events = [line for line in logs.splitlines() if "type=" in line]
            cookies = [(cookie.name, cookie.domain, cookie.path, cookie.secure) for cookie in self.jar]
            self.fail(f"login returned {redirect.exception.code}; cookies: {cookies}; identity events: {events[-3:]}")
        location = redirect.exception.headers["Location"]
        self.assertTrue(location.startswith(CALLBACK))
        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        self.assertEqual(params["state"], [state])
        self.assertNotIn("access_token", params)
        self.assertNotIn("id_token", params)
        return params["code"][0], verifier, nonce

    def exchange(self, code, verifier):
        return self.json_request(self.metadata["token_endpoint"], {
            "grant_type": "authorization_code", "client_id": "web-portal",
            "client_secret": self.credentials["PORTAL_CLIENT_SECRET"],
            "redirect_uri": CALLBACK, "code": code, "code_verifier": verifier,
        })

    def introspect(self, token):
        return self.json_request(self.metadata["introspection_endpoint"], {
            "client_id": "web-portal", "client_secret": self.credentials["PORTAL_CLIENT_SECRET"], "token": token,
        })

    def test_browser_code_flow_returns_active_short_lived_viewer_token(self):
        code, verifier, _ = self.authorize("ops-viewer")
        tokens = self.exchange(code, verifier)
        self.assertLessEqual(tokens["expires_in"], 300)
        active = self.introspect(tokens["access_token"])
        self.assertTrue(active["active"])
        self.assertEqual(active["iss"], self.realm)
        self.assertIn("web-portal", active["aud"])
        resources = active["resource_access"]
        self.assertEqual(resources["payment-api"]["roles"], ["payments:read"])
        self.assertNotIn("knowledge:restricted", resources["assistant-tools"]["roles"])
        self.assertIn("ai:invoke", resources["ai-orchestrator"]["roles"])
        jwks = self.json_request(self.metadata["jwks_uri"])
        self.assertTrue(any(key["use"] == "sig" for key in jwks["keys"]))
        with self.assertRaises(urllib.error.HTTPError) as reused:
            self.exchange(code, verifier)
        self.addCleanup(reused.exception.close)
        self.assertEqual(reused.exception.code, 400)
        self.assertFalse(self.introspect("invalid-token")["active"])

    def test_clerk_cannot_acquire_knowledge_or_dashboard_permissions(self):
        code, verifier, _ = self.authorize("payments-clerk")
        resources = self.introspect(self.exchange(code, verifier)["access_token"])["resource_access"]
        self.assertIn("payments:write", resources["payment-api"]["roles"])
        self.assertEqual(resources["assistant-tools"]["roles"], ["payments:read"])
        self.assertNotIn("web-portal", resources)

    def test_pkce_is_required_and_wrong_verifier_cannot_exchange_code(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.authorize("ops-viewer", challenge=False)
        self.addCleanup(missing.exception.close)
        self.assertEqual(missing.exception.code, 302)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(missing.exception.headers["Location"]).query)
        self.assertEqual(query["error"], ["invalid_request"])
        code, _, _ = self.authorize("ops-viewer")
        with self.assertRaises(urllib.error.HTTPError) as wrong:
            self.exchange(code, secrets.token_urlsafe(48))
        self.addCleanup(wrong.exception.close)
        self.assertEqual(wrong.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
