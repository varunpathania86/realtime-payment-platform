import os
import unittest
from unittest.mock import patch

from scripts.dbx_provision import (
    API_CONTRACT_VERSION,
    DBXClient,
    EXPECTED_HOST,
    ProvisioningError,
    build_connections,
)


class DBXProvisioningConfigTests(unittest.TestCase):
    def test_provisions_separate_metadata_and_knowledge_accounts(self):
        connections = build_connections({
            "PAYMENT_DATABASE_URL": "postgres://payments:test@payments-postgres.data.svc.cluster.local:5432/payments",
            "BI_DATABASE_PASSWORD": "bi-test",
            "POLARIS_DATABASE_PASSWORD": "polaris-test",
            "KNOWLEDGE_READER_PASSWORD": "knowledge-test",
            "ANALYTICS_PASSWORD": "admin-test",
            "ANALYTICS_READER_PASSWORD": "reader-test",
            "ANALYTICS_READER_USERNAME": "rpp_ai_reader",
        })
        self.assertEqual([item["database"] for item in connections],
            ["payments", "superset", "polaris", "ai_knowledge", "analytics"])
        self.assertEqual(connections[3]["username"], "rpp_knowledge_reader")
        self.assertEqual(connections[3]["host"], "knowledge-postgres.data.svc.cluster.local")
        self.assertEqual(connections[-1]["username"], "rpp_ai_reader")
        self.assertEqual(connections[-1]["password"], "reader-test")
        self.assertTrue(all(item["read_only"] for item in connections))

    def test_builds_only_connections_for_enabled_services(self):
        connections = build_connections(
            {
                "PAYMENT_DATABASE_URL": "postgres://payments:pgsecret@payments-postgres.data.svc.cluster.local:5432/payments?sslmode=disable",
                "KAFKA_USERNAME": "payments",
                "KAFKA_PASSWORD": "kafkasecret",
            }
        )
        self.assertEqual(
            [connection["db_type"] for connection in connections],
            ["postgres", "mq"],
        )
        postgres, kafka = connections
        self.assertEqual(postgres["password"], "pgsecret")
        self.assertEqual(postgres["driver_profile"], "postgres")
        self.assertTrue(postgres["read_only"])
        self.assertTrue(postgres["save_password"])
        self.assertEqual(kafka["driver_profile"], "kafka")
        self.assertEqual(
            kafka["external_config"]["extra"]["bootstrapServers"],
            "payments-kafka.data.svc.cluster.local:9092",
        )
        self.assertEqual(
            kafka["external_config"]["extra"]["saslPassword"], "kafkasecret"
        )
        self.assertEqual(
            kafka["external_config"]["tls"]["ca_cert_path"],
            "/etc/dbx/kafka/ca.pem",
        )
        self.assertTrue(kafka["read_only"])

    def test_adds_analytics_and_lakehouse_connections_only_when_present(self):
        connections = build_connections(
            {
                "PAYMENT_DATABASE_URL": "postgresql://payments:pgsecret@db.example.invalid:30432/payments?sslmode=verify-full",
                "ANALYTICS_PASSWORD": "chsecret",
                "TRINO_ENABLED": "1",
            }
        )
        self.assertEqual(
            [connection["db_type"] for connection in connections],
            ["postgres", "clickhouse", "trino"],
        )
        self.assertEqual(connections[0]["ca_cert_path"], "/etc/dbx/postgres/ca.crt")
        self.assertTrue(connections[0]["ssl"])
        self.assertEqual(connections[1]["host"], "realtime-analytics.apps.svc.cluster.local")
        self.assertEqual(connections[1]["password"], "chsecret")
        self.assertEqual(connections[1]["driver_profile"], "clickhouse")
        self.assertEqual(connections[2]["driver_profile"], "trino")
        self.assertEqual(connections[2]["host"], "lakehouse-query.apps.svc.cluster.local")
        self.assertTrue(connections[2]["read_only"])
        self.assertTrue(connections[2]["save_password"])
        self.assertEqual(connections[2]["password"], "")

    def test_rejects_incomplete_payment_database_url(self):
        with self.assertRaisesRegex(ValueError, "payment database URL"):
            build_connections({"PAYMENT_DATABASE_URL": "mysql://incomplete"})


class DBXClientContractTests(unittest.TestCase):
    def test_provision_rejects_profiles_that_lost_browser_driver_identity(self):
        client = DBXClient("http://dbx-workbench.apps.svc.cluster.local:4224", opener=object())
        responses = [None, None, [{"id": "rpp-payments-postgres", "db_type": "postgres"}]]
        client.request = lambda *args, **kwargs: responses.pop(0)
        with self.assertRaisesRegex(ProvisioningError, "did not persist"):
            client.provision("test-login", [{
                "id": "rpp-payments-postgres", "db_type": "postgres", "driver_profile": "postgres",
            }])

    def test_completed_sync_job_is_replaced_on_adapter_or_credential_revision(self):
        from scripts.module_catalog import ROOT

        terraform = (ROOT / "platform/terraform/environments/local/dbx.tf").read_text()
        self.assertIn('resource "terraform_data" "dbx_connection_revision"', terraform)
        self.assertIn('replace_triggered_by = [terraform_data.dbx_connection_revision]', terraform)
        self.assertIn('filesha256("${local.root}/scripts/dbx_provision.py")', terraform)
        self.assertIn('kubernetes_secret_v1.dbx_workbench[0].data["password"]', terraform)

    def test_api_client_requires_the_pinned_in_cluster_service(self):
        for url in (
            "https://dbx-workbench.apps.svc.cluster.local",
            "http://example.invalid",
            "http://dbx-workbench.apps.svc.cluster.local/path",
            "http://user:password@dbx-workbench.apps.svc.cluster.local",
        ):
            with self.subTest(url=url), self.assertRaises(ProvisioningError):
                DBXClient(url)

    def test_contract_version_mismatch_fails_before_network_access(self):
        with patch.dict(
            os.environ,
            {"DBX_API_CONTRACT_VERSION": "0.6.68", "DBX_API_URL": ""},
        ):
            from scripts import dbx_provision

            with self.assertRaisesRegex(ProvisioningError, "contract version"):
                dbx_provision.main()
        self.assertEqual(API_CONTRACT_VERSION, "0.6.67")
        self.assertEqual(EXPECTED_HOST, "dbx-workbench.apps.svc.cluster.local")

    def test_provision_is_upsert_then_verifies_saved_ids_and_types(self):
        client = DBXClient(
            "http://dbx-workbench.apps.svc.cluster.local:4224",
            opener=object(),
        )
        calls = []
        configs = [
            {"id": "rpp-payments-postgres", "db_type": "postgres", "password": "do-not-log"}
        ]
        responses = [
            None,
            None,
            [{"id": "rpp-payments-postgres", "db_type": "postgres"}],
        ]

        def request(method, path, payload=None):
            calls.append((method, path, payload))
            return responses.pop(0)

        client.request = request
        client.provision("login-secret", configs)
        self.assertEqual(
            [(method, path) for method, path, _ in calls],
            [
                ("POST", "/auth/login"),
                ("POST", "/connection/save"),
                ("GET", "/connection/list"),
            ],
        )
        self.assertEqual(calls[0][2], {"password": "login-secret"})
        self.assertEqual(calls[1][2], {"configs": configs})
        self.assertNotIn("removedIds", calls[1][2])

    def test_provision_fails_closed_when_dbx_did_not_save_every_connection(self):
        client = DBXClient(
            "http://dbx-workbench.apps.svc.cluster.local:4224",
            opener=object(),
        )
        responses = [None, None, []]
        client.request = lambda *args, **kwargs: responses.pop(0)
        with self.assertRaisesRegex(ProvisioningError, "did not persist"):
            client.provision(
                "login-secret",
                [{"id": "rpp-payments-postgres", "db_type": "postgres"}],
            )

    def test_unsupported_response_body_is_not_included_in_errors(self):
        client = DBXClient(
            "http://dbx-workbench.apps.svc.cluster.local:4224",
            opener=object(),
        )

        class BadResponseOpener:
            def open(self, request, timeout):
                raise OSError("sensitive database password echoed by server")

        client.opener = BadResponseOpener()
        with self.assertRaises(ProvisioningError) as error:
            client.request("POST", "/connection/save", {"secret": "hidden"})
        self.assertNotIn("sensitive", str(error.exception))
        self.assertNotIn("hidden", str(error.exception))

    def test_api_errors_do_not_echo_secret_response_bodies(self):
        from urllib.error import HTTPError
        from io import BytesIO

        class BadResponseOpener:
            def open(self, request, timeout):
                raise HTTPError(
                    request.full_url,
                    500,
                    "internal error",
                    {},
                    BytesIO(b"password=must-not-be-logged"),
                )

        client = DBXClient(
            "http://dbx-workbench.apps.svc.cluster.local:4224",
            opener=BadResponseOpener(),
        )
        with self.assertRaises(ProvisioningError) as error:
            client.request("POST", "/connection/save", {"password": "hidden"})
        self.assertIn("HTTP 500", str(error.exception))
        self.assertNotIn("must-not-be-logged", str(error.exception))
        self.assertNotIn("hidden", str(error.exception))


if __name__ == "__main__":
    unittest.main()
