import base64
import json
import unittest
from unittest import mock

from scripts import bi_acceptance

DASHBOARD = "bfa817e5-b670-5c83-842c-ef5d57da6f8a"
ORIGIN = "http://localhost:8080"
USERS = {"ops-viewer": {"password": "viewer-pass-1"}, "payments-clerk": {"password": "clerk-pass-12"}}
COOKIE = "rpp_portal_session={}; Path=/; Max-Age=28800; HttpOnly; SameSite=Strict"


def token(**overrides):
    payload = {"user": {"username": "ops-viewer", "first_name": "ops-viewer", "last_name": "(local dev identity)"},
        "resources": [{"type": "dashboard", "id": DASHBOARD}], "rls_rules": [], "iat": 1000, "exp": 1300,
        "aud": "rpp-bi-dashboards", "type": "guest", **overrides}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


class FakePlatform:
    """A correct portal and hardened Superset; flags reproduce individual boundary failures."""

    def __init__(self, **broken):
        self.broken = broken

    def __call__(self, method, path, host, headers, body):
        headers = headers or {}
        if host == bi_acceptance.SUPERSET_HOST:
            return self.superset(method, path, headers)
        user = headers.get("Cookie", "=").split("=", 1)[1] or None
        if method == "POST" and headers.get("Origin") != ORIGIN:
            return 403, {}, "origin not allowed"
        if path == "/auth/login":
            if USERS.get(body["username"], {}).get("password") != body["password"]:
                return 401, {}, "invalid"
            return 200, {"Set-Cookie": COOKIE.format(body["username"])}, json.dumps(self.session(body["username"]))
        if path == "/auth/session":
            return 200, {}, json.dumps(self.session(user))
        if path == "/bi/guest-token":
            if user is None and not self.broken.get("anonymous_token"):
                return 401, {}, "not signed in"
            if user == "payments-clerk" and not self.broken.get("clerk_token"):
                return 403, {}, "denied"
            return 200, {}, json.dumps({"token": self.broken.get("token", token())})
        return 404, {}, ""

    def session(self, user):
        result = {"username": user, "permissions": ["bi:view"] if user == "ops-viewer" else []}
        if user == "ops-viewer" or self.broken.get("clerk_session"):
            result["bi"] = {"dashboardId": DASHBOARD, "supersetDomain": "http://bi.localhost:8080"}
        return result

    def superset(self, method, path, headers):
        if path.startswith("/embedded/"):
            if not headers.get("Referer", "").startswith(ORIGIN) and not self.broken.get("any_referrer"):
                return 403, {}, "referrer not allowed"
            ancestors = self.broken.get("ancestors", f"{ORIGIN} http://127.0.0.1:8080")
            return 200, {"Content-Security-Policy": f"default-src 'self'; frame-ancestors {ancestors}",
                "Set-Cookie": "session=abc; HttpOnly; Path=/"}, '<input id="csrf_token" type="hidden" value="tok">'
        if headers.get("X-CSRFToken") != "tok" or headers.get("Cookie") != "session=abc":
            return 400, {}, "The CSRF token is missing."
        if path == "/api/v1/chart/data":
            if self.broken.get("ad_hoc"):
                return 200, {}, "{}"
            return 403, {}, "Guest queries must use a saved chart from the embedded dashboard"
        if path == self.broken.get("open_path"):
            return 200, {}, "{}"
        if path.startswith("/api/v1/embedded_dashboard/"):
            return 200, {}, json.dumps({"result": {"dashboard_id": "1"}})
        if path == "/api/v1/dashboard/":
            return 200, {}, json.dumps({"count": 1, "ids": self.broken.get("dashboards", [1])})
        if path == "/api/v1/chart/":
            return 200, {}, json.dumps({"count": self.broken.get("charts", 0)})
        return 403, {}, "forbidden"


class VerifyTests(unittest.TestCase):
    def run_checks(self, platform):
        return bi_acceptance.verify(platform, ORIGIN, USERS, DASHBOARD)

    def test_hardened_platform_passes_every_boundary(self):
        results = self.run_checks(FakePlatform())
        self.assertEqual(len(results), 20 + len(bi_acceptance.DENIED) + 1)
        self.assertIn("payments-clerk is denied a guest token (403)", results)

    def test_each_broken_boundary_fails(self):
        for broken in (
            {"anonymous_token": True},
            {"clerk_token": True},
            {"clerk_session": True},
            {"token": token(resources=[{"type": "dashboard", "id": DASHBOARD}, {"type": "dashboard", "id": "other"}])},
            {"token": token(rls_rules=None)},
            {"token": token(exp=1000 + 3600)},
            {"token": token(password="leak")},
            {"any_referrer": True},
            {"ancestors": "*"},
            {"ancestors": "http://other.example"},
            {"open_path": "/api/v1/database/"},
            {"dashboards": [1, 2]},
            {"charts": 3},
            {"ad_hoc": True},
            {"open_path": "/api/v1/sqllab/execute/"},
            {"open_path": "/api/v1/security/csrf_token/"},
        ):
            with self.subTest(broken=broken), self.assertRaises(AssertionError):
                self.run_checks(FakePlatform(**broken))

    def test_session_cookie_must_be_http_only_and_same_site_strict(self):
        self.assertEqual(bi_acceptance.session_cookie({"Set-Cookie": COOKIE.format("v")}), "rpp_portal_session=v")
        for value in ("rpp_portal_session=v; HttpOnly", "rpp_portal_session=v; SameSite=Strict", "other=v"):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                bi_acceptance.session_cookie({"Set-Cookie": value})


class MainTests(unittest.TestCase):
    def test_profile_without_bi_fails_before_port_forwarding(self):
        with mock.patch.object(bi_acceptance, "portal_env", return_value=""), \
                mock.patch.object(bi_acceptance, "forward") as forward, \
                self.assertRaises(SystemExit) as raised:
            bi_acceptance.main(["--profile", "rpp-test"])
        forward.assert_not_called()
        self.assertIn("BI is not enabled", str(raised.exception))

    def test_kubectl_reads_use_the_explicit_profile(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            return mock.Mock(stdout=base64.b64encode(b"{}").decode())

        self.assertEqual(bi_acceptance.secret("rpp-test", "web-portal-identity", "dev-users", runner=runner), "{}")
        self.assertEqual(calls[0][:5], ["kubectl", "--context", "rpp-test", "-n", "apps"])
        self.assertIn("jsonpath={.data.dev-users}", calls[0])


if __name__ == "__main__":
    unittest.main()
