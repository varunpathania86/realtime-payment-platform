import subprocess
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT

CHART = ROOT / "modules/trino-analytics/chart"


def render(*arguments):
    return subprocess.run(
        ["helm", "template", "trino-analytics", str(CHART), "--namespace", "apps", *arguments],
        check=True, capture_output=True, text=True, timeout=30,
    )


def properties(text):
    return dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#"))


class TrinoAnalyticsTests(unittest.TestCase):
    def test_chart_renders_hardened_single_coordinator(self):
        resources = [item for item in yaml.safe_load_all(render().stdout) if item]
        validate_chart(resources)
        workload = next(item for item in resources if item["kind"] == "Deployment")
        self.assertEqual(workload["spec"]["replicas"], 1)
        self.assertEqual(workload["spec"]["strategy"], {"type": "Recreate"})
        pod = workload["spec"]["template"]["spec"]
        mounts = {mount["mountPath"] for mount in pod["containers"][0]["volumeMounts"]}
        self.assertLessEqual({"/data/trino", "/tmp", "/etc/trino/catalog"}, mounts)
        catalogs = next(volume for volume in pod["volumes"] if volume["name"] == "catalogs")
        self.assertEqual([item["path"] for item in catalogs["configMap"]["items"]],
            ["lakehouse.properties", "lakehouse_admin.properties"])

    def test_query_catalog_is_read_only_and_credentials_come_from_secrets(self):
        resources = [item for item in yaml.safe_load_all(render().stdout) if item]
        data = next(item for item in resources if item["kind"] == "ConfigMap")["data"]
        reader, maintainer = properties(data["lakehouse.properties"]), properties(data["lakehouse_admin.properties"])
        self.assertEqual(reader["iceberg.security"], "read_only")
        self.assertNotIn("iceberg.security", maintainer)
        self.assertEqual(reader["iceberg.rest-catalog.oauth2.credential"], "${ENV:LAKEHOUSE_READER_CREDENTIAL}")
        self.assertEqual(maintainer["iceberg.rest-catalog.oauth2.credential"],
            "${ENV:LAKEHOUSE_MAINTAINER_CREDENTIAL}")
        self.assertEqual(reader["iceberg.rest-catalog.oauth2.server-uri"],
            "http://iceberg-lakehouse:8181/api/catalog/v1/oauth/tokens")
        self.assertEqual(reader["s3.aws-secret-key"], "${ENV:AWS_SECRET_ACCESS_KEY}")
        self.assertNotIn("cache-manager", data["config.properties"])

    def test_multiple_coordinators_are_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError) as error:
            render("--set", "replicaCount=2")
        self.assertIn("replicaCount must be 1", error.exception.stderr)


if __name__ == "__main__":
    unittest.main()
