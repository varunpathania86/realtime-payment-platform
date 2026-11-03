import importlib.util
from pathlib import Path
import subprocess
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT, local_modules

SPEC = importlib.util.spec_from_file_location(
    "clickhouse_benchmark", ROOT / "modules/clickhouse-analytics/benchmark.py",
)
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class ClickHouseAnalyticsTests(unittest.TestCase):
    def test_chart_renders_hardened_persistent_stateful_workload(self):
        rendered = subprocess.run(
            [
                "helm", "template", "clickhouse-analytics",
                str(ROOT / "modules/clickhouse-analytics/chart"), "--namespace", "apps",
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )
        resources = list(yaml.safe_load_all(rendered.stdout))
        validate_chart(resources)
        schema_job = next(item for item in resources if item and item["kind"] == "Job")
        self.assertEqual(schema_job["metadata"]["annotations"]["helm.sh/hook"], "post-install,post-upgrade")
        schema_container = schema_job["spec"]["template"]["spec"]["containers"][0]
        self.assertFalse(schema_container["securityContext"]["allowPrivilegeEscalation"])
        workload = next(item for item in resources if item and item["kind"] == "StatefulSet")
        pod = workload["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(container["imagePullPolicy"], "Never")
        self.assertEqual(container["env"][-1]["valueFrom"]["secretKeyRef"], {
            "name": "clickhouse-analytics", "key": "password",
        })
        self.assertEqual(workload["spec"]["replicas"], 1)
        self.assertIn({"name": "data", "mountPath": "/var/lib/clickhouse"}, container["volumeMounts"])
        self.assertIn(
            {"name": "users", "mountPath": "/etc/clickhouse-server/users.d"},
            container["volumeMounts"],
        )
        self.assertIn(
            {"name": "users", "emptyDir": {}},
            pod["volumes"],
        )
        claim = next(item for item in resources if item and item["kind"] == "PersistentVolumeClaim")
        self.assertEqual(claim["spec"]["resources"]["requests"]["storage"], "8Gi")
        schema = next(item for item in resources if item and item["kind"] == "ConfigMap")["data"]["schema.sql"]
        self.assertIn("ReplacingMergeTree(revision)", schema)
        self.assertIn("TTL event_time + INTERVAL 90 DAY DELETE", schema)

    def test_chart_is_selected_only_for_the_analytics_feature(self):
        self.assertIn("clickhouse-analytics", local_modules(features={"analytics"}))
        self.assertNotIn("clickhouse-analytics", local_modules(features={"streaming"}))
        self.assertNotIn("clickhouse-analytics", local_modules(features=set()))

    def test_benchmark_uses_nearest_rank_percentiles_and_validates_inputs(self):
        samples = list(range(1, 101))
        self.assertEqual(BENCHMARK.percentile(samples, 50), 50)
        self.assertEqual(BENCHMARK.percentile(samples, 95), 95)
        self.assertEqual(BENCHMARK.percentile(samples, 99), 99)
        for invalid in ([], 0, 100.1, "95", True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                BENCHMARK.percentile(samples, invalid)


if __name__ == "__main__":
    unittest.main()
