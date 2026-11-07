"""Loopback-only browser harness for the local Authorization Code workflow."""

import base64
import http.client
import http.cookiejar
from html.parser import HTMLParser
import json
import re
import select
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action", "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


class LocalCookies(http.cookiejar.DefaultCookiePolicy):
    def return_ok_secure(self, cookie, request):
        host = urllib.parse.urlparse(request.full_url).hostname or ""
        if host == "localhost" or host.endswith(".localhost"):
            return True
        return super().return_ok_secure(cookie, request)


class LocalBrowser:
    def __init__(self, forwarding_port, origin, issuer):
        self.forwarding_port = forwarding_port
        self.origin = origin.rstrip("/")
        self.issuer = issuer.rstrip("/")
        self.cookies = http.cookiejar.CookieJar(policy=LocalCookies())
        self.password_submissions = 0
        parsed = urllib.parse.urlparse(origin)
        self.public_port = parsed.port
        self.allowed_hosts = {"localhost", "127.0.0.1", "identity.localhost", "bi.localhost", "grafana.localhost"}
        port = forwarding_port
        class Connection(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.create_connection(("127.0.0.1", port), self.timeout)
        class Handler(urllib.request.HTTPHandler):
            def http_open(self, request):
                return self.do_open(Connection, request)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Handler(),
                                                    NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self, method, url, body=None, headers=None):
        url = urllib.parse.urljoin(self.origin + "/", url)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in self.allowed_hosts or parsed.port != self.public_port:
            raise ValueError("browser harness refuses non-local identity redirects")
        request = urllib.request.Request(url, method=method, data=body, headers=headers or {})
        try:
            response = self.opener.open(request, timeout=240)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read(1 << 20).decode()

    def json(self, method, url, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        parsed = urllib.parse.urlparse(urllib.parse.urljoin(self.origin + "/", url))
        origin = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        return self.call(method, url, data, {"Content-Type": "application/json", "Origin": origin, **(headers or {})})

    def login(self, start, username, password, *, stop_at=None):
        url, method, body = start, "GET", None
        submitted = False
        for _ in range(16):
            if stop_at and url.startswith(stop_at):
                return url
            status, headers, text = self.call(method, url, body,
                                            {"Content-Type": "application/x-www-form-urlencoded"} if body else {})
            if status in (301, 302, 303, 307, 308):
                url, method, body = urllib.parse.urljoin(url, headers["Location"]), "GET", None
                continue
            form = LoginForm()
            form.feed(text)
            if form.action:
                if submitted:
                    raise RuntimeError("Keycloak rejected the synthetic login")
                self.password_submissions += 1
                submitted = True
                url, method = form.action, "POST"
                body = urllib.parse.urlencode({"username": username, "password": password, "credentialId": ""}).encode()
                continue
            if status != 200:
                raise RuntimeError(f"local OIDC browser workflow returned {status}")
            return url
        raise RuntimeError("local OIDC redirect limit exceeded")

    def session_cookie(self):
        for cookie in self.cookies:
            if cookie.name == "rpp_portal_session":
                return cookie.value
        raise RuntimeError("OIDC callback did not issue a portal session")


def secret(profile, namespace, name):
    result = subprocess.run(["kubectl", "--context", profile, "-n", namespace, "get", "secret", name, "-o", "json"],
                            check=True, capture_output=True, text=True, timeout=30)
    return {key: base64.b64decode(value).decode() for key, value in json.loads(result.stdout)["data"].items()}


def forward(profile, namespace, service, remote_port):
    process = subprocess.Popen(["kubectl", "--context", profile, "-n", namespace, "port-forward",
                                "--address", "127.0.0.1", "service/" + service, ":" + str(remote_port)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    deadline = time.monotonic() + 30
    while process.poll() is None and time.monotonic() < deadline:
        if not select.select([process.stdout], [], [], 1)[0]:
            continue
        match = re.search(r"127\.0\.0\.1:(\d+)", process.stdout.readline())
        if match:
            return process, int(match[1])
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError("local service forwarding did not become ready")


def credentials(profile):
    values = secret(profile, "security", "identity-provider")
    return values, {
        name: {"password": values[key]}
        for name, key in (("ops-viewer", "VIEWER_PASSWORD"), ("payments-clerk", "CLERK_PASSWORD"),
                            ("ops-engineer", "ENGINEER_PASSWORD"))
    }


class OIDCSessionSender:
    """Keep existing BI/AI acceptance scenarios while using real browser code flows."""

    def __init__(self, port, origin, issuer, transport):
        self.port, self.origin, self.issuer, self.transport = port, origin, issuer, transport

    def __call__(self, method, path, host, headers=None, body=None):
        if method != "POST" or path != "/auth/login":
            return self.transport(method, path, host, headers, body)
        if (headers or {}).get("Origin") != self.origin:
            # OIDC login is a GET redirect protected by state, not a password POST.
            # Preserve the scenario's foreign-origin mutation check at the real BI boundary.
            return self.transport("POST", "/bi/guest-token", host, headers, None)
        browser = LocalBrowser(self.port, self.origin, self.issuer)
        try:
            browser.login(self.origin + "/auth/login", body["username"], body["password"])
        except RuntimeError:
            return 401, {}, "Keycloak rejected login"
        status, response_headers, text = browser.json("GET", "/auth/session")
        cookie = next(item for item in browser.cookies if item.name == "rpp_portal_session")
        if not cookie.has_nonstandard_attr("HttpOnly") or cookie.get_nonstandard_attr("SameSite") != "Strict":
            raise RuntimeError("portal session cookie lacks required protections")
        return status, {**dict(response_headers), "Set-Cookie": cookie.name + "=" + cookie.value + "; HttpOnly; SameSite=Strict"}, text
