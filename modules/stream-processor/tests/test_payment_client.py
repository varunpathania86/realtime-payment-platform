import json
import os
import unittest
from unittest.mock import Mock, patch

from payment_client import PaymentClient
from fixtures import event


class PaymentClientTests(unittest.TestCase):
    def client(self):
        with patch.dict(os.environ, {
            "PAYMENT_API_URL": "http://payment-api:8080",
            "PAYMENT_TOKEN_URL": "http://identity:8080/token",
            "PAYMENT_GENERATOR_SECRET": "test-client-secret",
        }):
            return PaymentClient()

    def test_api_origin_uses_client_credentials_and_stable_idempotency(self):
        client = self.client()
        client._json = Mock(side_effect=[
            {"access_token": "test-token", "expires_in": 300},
            {"id": "payment-id", "processing_event_id": "processing-id"},
            {"id": "payment-id", "processing_event_id": "processing-id"},
        ])
        record = event(1)
        first, second = client.submit(record), client.submit(record)
        self.assertEqual(first, second)
        self.assertEqual(first["payment_id"], "payment-id")
        self.assertEqual(first["event_id"], "processing-id")
        token_request = client._json.call_args_list[0].args[0]
        self.assertIn(b"grant_type=client_credentials", token_request.data)
        self.assertIn(b"scope=openid", token_request.data)
        request = client._json.call_args_list[1].args[0]
        self.assertEqual(request.full_url, "http://payment-api:8080/payments")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("Idempotency-key"), record["event_id"])
        payload = json.loads(request.data)
        self.assertEqual(payload["country"], "DE")
        self.assertEqual(payload["payment_method"], "transfer")
        self.assertEqual(payload["simulation"]["event_time"], record["event_time"])
        self.assertNotIn("outcome", payload)
        self.assertNotIn("test-token", json.dumps(payload))

    def test_token_is_reacquired_before_expiry(self):
        client = self.client()
        client._json = Mock(return_value={"access_token": "token", "expires_in": 300})
        with patch("payment_client.time.monotonic", return_value=0):
            client.access_token()
        with patch("payment_client.time.monotonic", return_value=269):
            client.access_token()
        with patch("payment_client.time.monotonic", return_value=270):
            client.access_token()
        self.assertEqual(client._json.call_count, 2)

    def test_missing_identity_configuration_and_invalid_tokens_fail(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(KeyError):
                PaymentClient()
        for result in ({}, {"access_token": "", "expires_in": 300},
                {"access_token": "token", "expires_in": 3600}):
            client = self.client()
            client._json = Mock(return_value=result)
            with self.assertRaises(ValueError):
                client.access_token()
