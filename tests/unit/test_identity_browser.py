import http.cookiejar
import unittest
from unittest.mock import Mock, patch

from scripts.identity_browser import LocalBrowser, LoginForm, OIDCSessionSender


class IdentityBrowserTests(unittest.TestCase):
    def test_login_form_parses_entities_and_ignores_other_forms(self):
        form = LoginForm()
        form.feed('<form id="other" action="http://wrong.invalid"></form>'
                    '<form id="kc-form-login" action="http://identity.localhost:8080/login?a=1&amp;b=2"></form>')
        self.assertEqual(form.action, "http://identity.localhost:8080/login?a=1&b=2")

    def test_browser_refuses_remote_redirects_and_different_ports(self):
        browser = LocalBrowser(12345, "http://localhost:8080", "http://identity.localhost:8080/realms/rpp")
        for url in ("https://identity.localhost:8080/login", "http://remote.invalid:8080/login",
                    "http://localhost:9999/login"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                browser.call("GET", url)

    def test_native_sso_requests_use_the_resource_origin(self):
        browser = LocalBrowser(12345, "http://localhost:8080", "http://identity.localhost:8080/realms/rpp")
        with patch.object(browser, "call") as call:
            browser.json("GET", "http://grafana.localhost:8080/api/user")
            self.assertEqual(call.call_args.args[3]["Origin"], "http://grafana.localhost:8080")
            browser.json("POST", "/bi/guest-token")
            self.assertEqual(call.call_args.args[3]["Origin"], "http://localhost:8080")

    def test_acceptance_sender_uses_code_flow_and_validates_cookie_flags(self):
        transport = Mock(return_value=(403, {}, "denied"))
        sender = OIDCSessionSender(12345, "http://localhost:8080",
                                    "http://identity.localhost:8080/realms/rpp", transport)
        cookie = http.cookiejar.Cookie(0, "rpp_portal_session", "opaque-session", None, False,
                                        "localhost.local", False, False, "/", True, False, None, True,
                                        None, None, {"HttpOnly": None, "SameSite": "Strict"}, False)
        with patch("scripts.identity_browser.LocalBrowser") as factory:
            factory.return_value.cookies = [cookie]
            factory.return_value.json.return_value = (200, {}, '{"username":"ops-viewer"}')
            status, headers, _ = sender("POST", "/auth/login", "localhost",
                {"Origin": "http://localhost:8080"}, {"username": "ops-viewer", "password": "synthetic"})
            self.assertEqual(status, 200)
            self.assertIn("HttpOnly; SameSite=Strict", headers["Set-Cookie"])
            factory.return_value.login.assert_called_once()
        self.assertEqual(sender("POST", "/auth/login", "localhost", {"Origin": "http://foreign.invalid"}, {})[0], 403)
        self.assertEqual(transport.call_args.args[1], "/bi/guest-token")
