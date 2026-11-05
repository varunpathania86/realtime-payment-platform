import re
import subprocess
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT, local_modules

MODULE = ROOT / "modules/assistant-tools"


def resources(*arguments):
    rendered = subprocess.run(
        ["helm", "template", "assistant-tools", str(MODULE / "chart"), "--namespace", "apps", *arguments],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return [item for item in yaml.safe_load_all(rendered.stdout) if item]


class AssistantToolsPackagingTests(unittest.TestCase):
    def test_chart_is_internal_and_uses_reader_secrets(self):
        items = resources()
        validate_chart(items)
        self.assertEqual({item["kind"] for item in items}, {"Deployment", "Service"})
        service = next(item for item in items if item["kind"] == "Service")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        pod = next(item for item in items if item["kind"] == "Deployment")["spec"]["template"]["spec"]
        environment = {item["name"]: item for item in pod["containers"][0]["env"]}
        self.assertEqual(environment["PGPASSWORD"]["valueFrom"]["secretKeyRef"],
                        {"name": "knowledge-database", "key": "reader-password"})
        self.assertEqual(environment["CLICKHOUSE_PASSWORD"]["valueFrom"]["secretKeyRef"],
                        {"name": "realtime-analytics", "key": "ai-reader-password"})
        self.assertEqual(environment["CLICKHOUSE_USER"]["value"], "rpp_ai_reader")
        self.assertNotIn("password", environment["KNOWLEDGE_DSN"]["value"])
        self.assertIn("user=rpp_knowledge_reader", environment["KNOWLEDGE_DSN"]["value"])
        self.assertIn("assistant-tools.apps.svc.cluster.local:*", environment["ALLOWED_HOSTS"]["value"])
        self.assertEqual(environment["TRINO_URL"]["value"], "")
        identity = next(volume for volume in pod["volumes"] if volume["name"] == "identity")
        self.assertEqual(identity["secret"]["secretName"], "ai-identity")

    def test_trino_is_optional(self):
        pod = next(item for item in resources("--set", "trino.url=http://lakehouse-query:8080")
                    if item["kind"] == "Deployment")["spec"]["template"]["spec"]
        environment = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
        self.assertEqual(environment["TRINO_URL"], "http://lakehouse-query:8080")

    def test_dockerfile_pins_shared_ai_runtime(self):
        dockerfile = (MODULE / "Dockerfile").read_text()
        pins = dict(line.split()[:2] for line in (ROOT / ".tool-versions").read_text().splitlines()
                    if line.strip() and not line.startswith("#"))
        self.assertEqual(re.search(r"ARG AI_PYTHON_VERSION=(\S+)", dockerfile).group(1), pins["ai-python"])
        self.assertIn("COPY --from=ai-core requirements.txt", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)

    def test_selected_only_for_ai_feature(self):
        self.assertIn("assistant-tools", local_modules(features={"ai"}))
        self.assertNotIn("assistant-tools", local_modules(features={"streaming", "analytics"}))


if __name__ == "__main__":
    unittest.main()
