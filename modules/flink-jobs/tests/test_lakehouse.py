import re
import unittest

import lakehouse

ENVIRON = {
    "LAKEHOUSE_CATALOG_URI": "http://iceberg-lakehouse:8181/api/catalog/",
    "LAKEHOUSE_WAREHOUSE": "lakehouse",
    "LAKEHOUSE_CLIENT_ID": "writer-id",
    "LAKEHOUSE_CLIENT_SECRET": "synthetic-secret",
    "S3_ENDPOINT": "http://minio.data.svc.cluster.local:9000",
    "AWS_ACCESS_KEY_ID": "rpp-streaming",
    "AWS_SECRET_ACCESS_KEY": "synthetic-storage",
}


class LakehouseSqlTest(unittest.TestCase):
    def test_literals_escape_quotes(self):
        self.assertEqual(lakehouse.literal("it's"), "'it''s'")
        self.assertEqual(lakehouse.options({"a": "b'c"}), "'a' = 'b''c'")

    def test_catalog_uses_oauth_endpoint_and_s3_file_io(self):
        statement = lakehouse.iceberg_catalog(ENVIRON)
        self.assertIn("'credential' = 'writer-id:synthetic-secret'", statement)
        self.assertIn("'oauth2-server-uri' = 'http://iceberg-lakehouse:8181/api/catalog/v1/oauth/tokens'", statement)
        self.assertIn("'io-impl' = 'org.apache.iceberg.aws.s3.S3FileIO'", statement)
        self.assertIn("'client.region' = 'us-east-1'", statement)

    def test_kafka_source_is_raw_and_resumes_committed_offsets(self):
        statement = lakehouse.kafka_source({"bootstrap.servers": "kafka:9092", "transaction.timeout.ms": "1"})
        self.assertIn("'topic' = 'payment-processing-events-v1'", statement)
        self.assertIn("'properties.group.id' = 'payment-lakehouse-v1'", statement)
        self.assertIn("'scan.startup.mode' = 'group-offsets'", statement)
        self.assertIn("'format' = 'raw'", statement)
        self.assertIn("'properties.bootstrap.servers' = 'kafka:9092'", statement)
        self.assertNotIn("transaction.timeout.ms", statement)
        self.assertIn("processed_at AS PROCTIME()", statement)

    def test_bronze_keeps_every_record_and_curated_dedupes_valid_records(self):
        bronze = lakehouse.bronze_insert("source", "lakehouse.payments.payment_events_raw")
        self.assertNotIn("WHERE", bronze)
        self.assertIn(", ".join(lakehouse.BRONZE_COLUMNS), bronze)
        curated = lakehouse.curated_insert("source", "lakehouse.payments.payment_events")
        self.assertIn(", ".join(lakehouse.CURATED_COLUMNS), curated)
        self.assertIn("PARTITION BY event_id ORDER BY processed_at ASC", curated)
        self.assertIn("WHERE occurrence = 1", curated)
        self.assertIn(lakehouse.validation_predicate(), curated)

    def test_validation_regexes_match_processing_rules(self):
        self.assertRegex("2026-11-03T00:00:30.123456Z", lakehouse.RFC3339_UTC)
        self.assertNotRegex("2026-11-03 00:00:30", lakehouse.RFC3339_UTC)
        self.assertNotRegex("2026-11-03T00:00:30+01:00", lakehouse.RFC3339_UTC)
        self.assertRegex("6f1c1a52-6f4b-4a8e-9d0e-6a3f7c2b9e11", lakehouse.UUID)
        self.assertNotRegex("not-a-uuid", lakehouse.UUID)
        amount = re.search(r"REGEXP\(JSON_QUERY\(raw_payload, '\$\.amount_minor' WITH ARRAY WRAPPER\), '([^']+)'\)",
            lakehouse.validation_predicate()).group(1)
        self.assertRegex("[42]", amount)
        self.assertNotRegex("[1.5]", amount)
        self.assertNotRegex('["42"]', amount)
        self.assertNotRegex("[0]", amount)


if __name__ == "__main__":
    unittest.main()
