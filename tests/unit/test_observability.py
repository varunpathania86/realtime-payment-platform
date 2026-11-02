import json
import subprocess
import unittest

import yaml

from scripts.module_catalog import ROOT


CHART = ROOT / "platform/terraform/modules/observability/chart"


class ObservabilityTests(unittest.TestCase):
    def render(self, backends):
        command = ["helm", "template", "observability", str(CHART), "--namespace", "telemetry"]
        values = {
            "backends": str(backends).lower(), "cluster": "verification-a",
            "backendAddress": "192.168.10.2", "grafanaPassword": "verification-only",
        }
        values.update({f"images.{name}": f"{name}:test" for name in
                        ("otelcol", "prometheus", "alertmanager", "loki", "tempo", "grafana")})
        for key, value in values.items():
            command.extend(["--set", f"{key}={value}"])
        result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return list(yaml.safe_load_all(result.stdout))

    def test_only_cluster_a_runs_persistent_backends(self):
        shared = self.render(True)
        remote = self.render(False)
        deployments = [item["metadata"]["name"] for item in shared if item and item["kind"] == "Deployment"]
        self.assertCountEqual(deployments, ["prometheus", "alertmanager", "loki", "tempo", "grafana"])
        self.assertFalse(any(item and item["kind"] == "Deployment" for item in remote))
        self.assertEqual(sum(item["kind"] == "PersistentVolumeClaim" for item in shared if item), 5)
        for item in shared:
            if item and item["kind"] in {"Deployment", "DaemonSet"}:
                pod = item["spec"]["template"]["spec"]
                self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                for container in pod["containers"]:
                    self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
                    self.assertIn("limits", container["resources"])
                    if container["name"] == "tempo":
                        self.assertEqual(container["resources"]["limits"]["memory"], "768Mi")
                        self.assertIn({"name": "GOMEMLIMIT", "value": "640MiB"}, container["env"])
                        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/ready")

    def test_collectors_export_all_signals_and_only_one_scrapes_kafka(self):
        for shared in (False, True):
            resources = self.render(shared)
            daemon = next(item for item in resources if item and item["kind"] == "DaemonSet")
            self.assertIn("--feature-gates=receiver.kafkametricsreceiver.UseFranzGo",
                            daemon["spec"]["template"]["spec"]["containers"][0]["args"])
            collector = next(item for item in resources if item and item["kind"] == "ConfigMap"
                                and item["metadata"]["name"] == "collector")
            config = yaml.safe_load(collector["data"]["config.yaml"])
            self.assertCountEqual(config["service"]["pipelines"], ["traces", "metrics", "logs"])
            self.assertEqual("kafkametrics" in config["receivers"], shared)
            self.assertEqual(config["receivers"]["filelog"]["include"], ["/var/log/pods/apps_*/*/*.log"])
            self.assertEqual(config["receivers"]["otlp"]["protocols"]["http"]["endpoint"], "0.0.0.0:4318")
            self.assertNotIn("cors", config["receivers"]["otlp"]["protocols"]["http"])

    def test_every_alert_has_an_existing_runbook_anchor_and_dashboard_has_exemplars(self):
        datasources = yaml.safe_load((CHART / "config/datasources.yaml").read_text())["datasources"]
        tempo = next(source for source in datasources if source["uid"] == "tempo")
        query = tempo["jsonData"]["tracesToLogsV2"]["query"]
        self.assertIn("$${__trace.traceId}", query)
        self.assertIn("$${__tags}", query)
        alerts = yaml.safe_load((CHART / "config/alerts.yaml").read_text())["groups"][0]["rules"]
        self.assertEqual(len(alerts), 3)
        self.assertIn("kafka_consumer_group_lag_ratio", alerts[1]["expr"])
        for rule in alerts:
            path, anchor = rule["annotations"]["runbook_url"].split("/blob/main/")[1].split("#")
            text = (ROOT / path).read_text()
            headings = [line.lstrip("# ").lower().replace(" ", "-")
                        for line in text.splitlines() if line.startswith("#")]
            self.assertIn(anchor, headings)
            self.assertEqual(rule["for"], "5m")
        dashboard = json.loads((CHART / "config/payments.json").read_text())
        self.assertTrue(any(target.get("exemplar") for panel in dashboard["panels"]
                            for target in panel.get("targets", [])))
