"""Authenticated API origin for live synthetic fixtures and load tests."""

import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PaymentClient:
    def __init__(self):
        self.api = os.environ["PAYMENT_API_URL"].rstrip("/")
        self.identity = os.environ["PAYMENT_TOKEN_URL"]
        self.secret = os.environ["PAYMENT_GENERATOR_SECRET"]
        self.opener = build_opener(NoRedirect)
        self.token = None
        self.expires = 0

    def _json(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.loads(response.read(1 << 20))
        except HTTPError as error:
            error.close()
            raise RuntimeError(f"payment origin HTTP {error.code}; inspect API/identity logs") from None

    def access_token(self):
        if time.monotonic() >= self.expires:
            result = self._json(Request(self.identity, data=urlencode({
                "grant_type": "client_credentials", "client_id": "payment-generator",
                "client_secret": self.secret, "scope": "openid",
            }).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}))
            if not isinstance(result.get("access_token"), str) or not result["access_token"]:
                raise ValueError("identity did not return an access token")
            lifetime = result.get("expires_in")
            if not isinstance(lifetime, int) or lifetime <= 30 or lifetime > 300:
                raise ValueError("unexpected service token lifetime")
            self.token = result["access_token"]
            self.expires = time.monotonic() + lifetime - 30
        return self.token

    def create(self, event):
        payload = {key: event[key] for key in (
            "account_id", "amount_minor", "currency", "country", "payment_method")}
        for field in ("account_created_at", "device_id"):
            if event.get(field) is not None:
                payload[field] = event[field]
        payload["simulation"] = {"event_time": event["event_time"], "outcome": event["outcome"]}
        return self._json(Request(self.api + "/payments", data=json.dumps(payload).encode(), headers={
            "Authorization": "Bearer " + self.access_token(), "Content-Type": "application/json",
            "Idempotency-Key": event["event_id"],
        }))

    def submit(self, event):
        result = self.create(event)
        return {**event, "event_id": result["processing_event_id"], "payment_id": result["id"]}
