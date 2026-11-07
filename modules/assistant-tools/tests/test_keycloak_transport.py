import unittest

import httpx

from ai_core.identity import payment_token_context
from assistant_tools.backends import PaymentApi


class PaymentTokenTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_payment_token_is_request_local_and_forwarded_only_to_payment_api(self):
        headers = []
        def handler(request):
            headers.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"id": "synthetic-payment"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = PaymentApi(client, "http://payment-api:8080")
            context = payment_token_context.set("payment-resource-token")
            try:
                await backend.get("synthetic-payment")
            finally:
                payment_token_context.reset(context)
            await backend.get("synthetic-payment")
        self.assertEqual(headers, ["Bearer payment-resource-token", None])
