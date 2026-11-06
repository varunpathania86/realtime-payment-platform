import asyncio
import json
import socket
import threading
import time
import unittest
import uuid

import httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from langchain_mcp_adapters.client import MultiServerMCPClient

from ai_core.identity import Principal
from ai_core.retrieval import RetrievedChunk
from assistant_tools.backends import BackendError, ClickHouse, KnowledgeIndex, PaymentApi, Trino
from assistant_tools.server import create_app
from assistant_tools.tools import METRICS_SQL, TOOL_PERMISSIONS, Toolset

KEY = Ed25519PrivateKey.generate()
PAYMENT_ID = "0b0c8f8e-4a59-4f6b-9a39-3f1f2c1d9e01"
ALL = frozenset(TOOL_PERMISSIONS.values())


def principal(*permissions):
    return Principal("ops-engineer", "platform", frozenset(permissions), uuid.uuid4().hex)


def assertion(permissions, audience="assistant-tools"):
    now = int(time.time())
    return jwt.encode({"iss": "web-portal", "aud": audience, "sub": "ops-viewer", "iat": now, "exp": now + 60,
                        "jti": uuid.uuid4().hex, "permissions": list(permissions)}, KEY, algorithm="EdDSA")


class FakeKnowledge:
    def __init__(self):
        self.calls = []

    async def search(self, who, query, limit):
        self.calls.append((who.subject, query, limit))
        return [RetrievedChunk("c1", "docs/runbooks/streaming.md", "v1", "Recovery", "password=abc123 restore",
                                "docs/runbooks/streaming.md", "internal", 0.0321)]


class FakePayments:
    async def get(self, payment_id):
        if payment_id == PAYMENT_ID:
            return {"id": PAYMENT_ID, "account_id": "6c1d3d1a-2a4f-4bb7-8f53-0e6c3f3a1234", "amount_minor": 1250,
                    "currency": "EUR", "status": "initiated", "created_at": "2026-01-01T00:00:00Z"}
        return None


class FakeClickHouse:
    def __init__(self, fail=False):
        self.queries, self.fail = [], fail

    async def query(self, sql, parameters):
        if self.fail:
            raise BackendError("down")
        self.queries.append((sql, parameters))
        return [{"dimension": "EUR", "payments": 3, "authorized": 2}]


def toolset(clickhouse=None, trino=None):
    return Toolset(FakeKnowledge(), FakePayments(), clickhouse or FakeClickHouse(), trino)


class ToolsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_tool_checks_its_own_permission(self):
        tools = toolset()
        nobody = principal("ai:invoke")
        calls = {
            "search_knowledge": tools.search_knowledge(nobody, "restore"),
            "get_payment": tools.get_payment(nobody, PAYMENT_ID),
            "payment_metrics": tools.payment_metrics(nobody),
            "payment_history": tools.payment_history(nobody),
            "risk_model_summary": tools.risk_model_summary(nobody),
        }
        for name, call in calls.items():
            result = await call
            self.assertEqual((result["status"], result["required_permission"]), ("denied", TOOL_PERMISSIONS[name]))

    async def test_metrics_use_allowlisted_groups_and_typed_parameters(self):
        clickhouse = FakeClickHouse()
        tools = toolset(clickhouse)
        result = await tools.payment_metrics(principal(*ALL), 30, "currency")
        self.assertEqual(result["status"], "ok")
        sql, parameters = clickhouse.queries[0]
        self.assertIn("toString(currency) AS dimension", sql)
        self.assertIn("{window_minutes:UInt32}", sql)
        self.assertIn("FINAL", sql)
        self.assertEqual(parameters, {"window_minutes": 30})
        for window, group in ((0, "none"), (1441, "none"), (True, "none"), (60, "1; DROP TABLE x")):
            self.assertEqual((await tools.payment_metrics(principal(*ALL), window, group))["status"], "invalid")
        self.assertEqual(len(clickhouse.queries), 1)
        self.assertNotIn("{group}", METRICS_SQL.format(group="'all'"))

    async def test_payment_lookup_validates_masks_and_reports_missing(self):
        tools = toolset()
        result = await tools.get_payment(principal("payments:read"), PAYMENT_ID.upper())
        self.assertEqual(result["payment"]["account_hint"], "…1234")
        self.assertNotIn("account_id", result["payment"])
        missing = await tools.get_payment(principal("payments:read"), str(uuid.uuid4()))
        self.assertEqual(missing["status"], "not_found")
        self.assertEqual((await tools.get_payment(principal("payments:read"), "' OR 1=1"))["status"], "invalid")

    async def test_search_redacts_and_bounds_input(self):
        tools = toolset()
        result = await tools.search_knowledge(principal("knowledge:read"), "  how to restore  ", 3)
        self.assertEqual(result["results"][0]["content"], "password=[redacted] restore")
        self.assertEqual(tools._knowledge.calls, [("ops-engineer", "how to restore", 3)])
        for query, limit in (("x", 3), ("y" * 501, 3), ("restore", 9)):
            self.assertEqual((await tools.search_knowledge(principal("knowledge:read"), query, limit))["status"],
                            "invalid")

    async def test_history_is_unavailable_without_trino_and_risk_is_advisory(self):
        tools = toolset()
        self.assertEqual((await tools.payment_history(principal("analytics:read")))["status"], "unavailable")
        risk = await tools.risk_model_summary(principal("analytics:read"))
        self.assertIn("advisory", risk["note"])


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_clickhouse_sends_limits_parameters_and_credentials(self):
        seen = {}

        def handler(request):
            seen.update(request=request)
            return httpx.Response(200, json={"data": [{"payments": 1}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rows = await ClickHouse(client, "http://ch:8123", "rpp_ai_reader", "pw").query("SELECT 1", {"w": 5})
        request = seen["request"]
        self.assertEqual(rows, [{"payments": 1}])
        self.assertEqual(request.url.params["param_w"], "5")
        self.assertEqual(request.url.params["result_overflow_mode"], "throw")
        self.assertEqual(request.headers["X-ClickHouse-User"], "rpp_ai_reader")
        self.assertNotIn("pw", str(request.url))

    async def test_trino_follows_next_uri_and_reports_errors(self):
        pages = {
            "/v1/statement": {"nextUri": "http://trino/v1/statement/q/1"},
            "/v1/statement/q/1": {"columns": [{"name": "day"}, {"name": "payments"}], "data": [["2026-01-01", 4]],
                                    "nextUri": "http://trino/v1/statement/q/2"},
            "/v1/statement/q/2": {"columns": [{"name": "day"}, {"name": "payments"}], "data": [["2026-01-02", 5]]},
        }

        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=pages[request.url.path]))) as client:
            rows = await Trino(client, "http://trino").query("SELECT 1")
        self.assertEqual([row["payments"] for row in rows], [4, 5])
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"error": {"message": "boom"}}))) as client:
            with self.assertRaises(BackendError):
                await Trino(client, "http://trino").query("SELECT 1")

    async def test_payment_api_maps_404_and_failures(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
            self.assertIsNone(await PaymentApi(client, "http://payment-api").get(PAYMENT_ID))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            with self.assertRaises(BackendError):
                await PaymentApi(client, "http://payment-api").get(PAYMENT_ID)


    async def test_knowledge_search_degrades_to_text_when_embeddings_fail(self):
        class DownEmbeddings:
            def embed_query(self, text):
                raise httpx.ConnectError("embedding server down")

        class Cursor:
            def __init__(self):
                self.params = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, query, params):
                self.params = params

            def fetchall(self):
                return [("c1", "docs/x.md", "v1", "Restore", "text", "docs/x.md", "internal", 0.016)]

        cursor = Cursor()

        class Connection(Cursor):
            def cursor(self):
                return cursor

        index = KnowledgeIndex("dsn", DownEmbeddings(), connect=lambda *args, **kwargs: Connection())
        with self.assertLogs("assistant_tools.backends", "WARNING") as logs:
            chunks = await index.search(principal("knowledge:read"), "restore savepoint", 5)
        self.assertEqual([chunk.chunk_id for chunk in chunks], ["c1"])
        self.assertIsNone(cursor.params["embedding"])
        self.assertNotIn("restore savepoint", "".join(logs.output))

        def refused(*args, **kwargs):
            raise OSError("database down")

        with self.assertRaises(BackendError):
            await KnowledgeIndex("dsn", DownEmbeddings(), connect=refused).search(principal("knowledge:read"), "q", 5)


class McpServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clickhouse = FakeClickHouse()
        app = create_app(toolset(cls.clickhouse), KEY.public_key(), ["127.0.0.1:*"])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started and time.monotonic() < deadline:
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(5)

    def call(self, tool, arguments, token):
        client = MultiServerMCPClient({"tools": {
            "transport": "streamable_http", "url": f"http://127.0.0.1:{self.port}/mcp",
            "headers": {"Authorization": f"Bearer {token}", "X-Request-ID": "req-1"},
        }})

        async def invoke():
            async with client.session("tools") as session:
                result = await session.call_tool(tool, arguments)
                return result.structuredContent

        return asyncio.run(invoke())

    def test_health_and_tool_listing(self):
        self.assertEqual(httpx.get(f"http://127.0.0.1:{self.port}/healthz").json(), {"status": "ok"})
        client = MultiServerMCPClient({"tools": {"transport": "streamable_http",
                                                "url": f"http://127.0.0.1:{self.port}/mcp"}})
        names = {tool.name for tool in asyncio.run(client.get_tools())}
        self.assertEqual(names, set(TOOL_PERMISSIONS))

    def test_relayed_assertion_authorizes_each_call_and_is_audited(self):
        token = assertion(["ai:invoke", "analytics:read"])
        with self.assertLogs("assistant_tools.audit", "INFO") as logs:
            metrics = self.call("payment_metrics", {"window_minutes": 15}, token)
            payment = self.call("get_payment", {"payment_id": PAYMENT_ID}, token)
        self.assertEqual(metrics["status"], "ok")
        self.assertEqual(payment["status"], "denied")
        records = [json.loads(line.split(":", 2)[2]) for line in logs.output]
        self.assertEqual([(record["tool"], record["status"], record["request_id"]) for record in records],
                        [("payment_metrics", "ok", "req-1"), ("get_payment", "denied", "req-1")])
        self.assertNotIn(PAYMENT_ID, "".join(logs.output))

    def test_missing_or_wrong_audience_assertion_is_unauthenticated(self):
        for token in ("not-a-token", assertion(["analytics:read"], audience="ai-orchestrator")):
            self.assertEqual(self.call("payment_metrics", {}, token)["status"], "unauthenticated")

    def test_unexpected_host_header_is_rejected(self):
        response = httpx.post(f"http://127.0.0.1:{self.port}/mcp", headers={"Host": "evil.example"},
                                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertGreaterEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
