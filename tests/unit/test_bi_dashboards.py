import json
import re
import subprocess
import sys
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT

MODULE = ROOT / "modules/bi-dashboards"
CHART = MODULE / "chart"
sys.path.insert(0, str(MODULE / "superset"))

import rpp_bi_assets as assets  # noqa: E402

CLICKHOUSE_URI = "clickhousedb://rpp_bi_reader:XXXXXXXXXX@realtime-analytics:8123/analytics"
TRINO_URI = "trino://superset@lakehouse-query:8080/lakehouse"


def render(*arguments):
    return subprocess.run(
        ["helm", "template", "bi-dashboards", str(CHART), "--namespace", "apps", *arguments],
        check=True, capture_output=True, text=True, timeout=30,
    )


def resources(*arguments):
    return [item for item in yaml.safe_load_all(render(*arguments).stdout) if item]


def environment(container):
    return {item["name"]: item.get("value", item.get("valueFrom")) for item in container["env"]}


def pins():
    lines = (ROOT / ".tool-versions").read_text().splitlines()
    return dict(line.split() for line in lines if line.strip() and not line.startswith("#"))


class ChartTests(unittest.TestCase):
    def test_chart_renders_hardened_web_server_setup_hook_and_ingress(self):
        rendered = resources()
        validate_chart(rendered)
        kinds = {item["kind"]: item for item in rendered}
        self.assertEqual(set(kinds), {"Deployment", "Job", "Service", "Ingress"})
        job = kinds["Job"]
        self.assertEqual(job["metadata"]["annotations"]["helm.sh/hook"], "pre-install,pre-upgrade")
        self.assertIn("rpp_bi_bootstrap.py", job["spec"]["template"]["spec"]["containers"][0]["command"][-1])
        for pod in (kinds["Deployment"]["spec"]["template"]["spec"], job["spec"]["template"]["spec"]):
            container = pod["containers"][0]
            self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
            self.assertEqual({mount["mountPath"] for mount in container["volumeMounts"]},
                {"/app/superset_home", "/tmp"})
            self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(kinds["Ingress"]["spec"]["rules"][0]["host"], "bi.localhost")
        self.assertEqual(kinds["Service"]["spec"]["ports"][0]["port"], 8088)

    def test_secrets_come_only_from_secret_references(self):
        rendered = {item["kind"]: item for item in resources()}
        web = environment(rendered["Deployment"]["spec"]["template"]["spec"]["containers"][0])
        setup = environment(rendered["Job"]["spec"]["template"]["spec"]["containers"][0])
        secret_names = {"SUPERSET_SECRET_KEY", "SUPERSET_GUEST_TOKEN_SECRET", "SUPERSET_DATABASE_PASSWORD"}
        for name in secret_names:
            self.assertEqual(web[name]["secretKeyRef"]["name"], "bi-dashboards")
        self.assertNotIn("SUPERSET_ADMIN_PASSWORD", web)
        self.assertNotIn("SUPERSET_CLICKHOUSE_PASSWORD", web)
        self.assertEqual(setup["SUPERSET_CLICKHOUSE_PASSWORD"]["secretKeyRef"],
            {"name": "realtime-analytics", "key": "bi-reader-password"})
        self.assertEqual(setup["SUPERSET_TOKEN_ISSUER_PASSWORD"]["secretKeyRef"],
            {"name": "bi-dashboards", "key": "token-issuer-password"})
        self.assertEqual(web["SUPERSET_EMBED_ORIGINS"], "http://localhost:8080")

    def test_published_digest_and_origins_are_rendered(self):
        digest = "sha256:" + "a" * 64
        rendered = {item["kind"]: item for item in resources(
            "--set", f"image.digest={digest}", "--set", "image.repository=ghcr.io/example/bi-dashboards",
            "--set", "embed.origins={http://localhost:8080,http://portal.localhost}")}
        container = rendered["Deployment"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], f"ghcr.io/example/bi-dashboards@{digest}")
        self.assertEqual(environment(container)["SUPERSET_EMBED_ORIGINS"],
            "http://localhost:8080 http://portal.localhost")

    def test_multiple_replicas_are_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError) as error:
            render("--set", "replicaCount=2")
        self.assertIn("replicaCount must be 1", error.exception.stderr)


class AssetTests(unittest.TestCase):
    def bundle(self, trino=None):
        return {path: yaml.safe_load(text) for path, text in assets.bundle(CLICKHOUSE_URI, trino).items()}

    def test_bundle_uses_stable_identifiers_and_read_only_databases(self):
        first, second = self.bundle(TRINO_URI), self.bundle(TRINO_URI)
        self.assertEqual(first, second)
        databases = [content for path, content in first.items() if path.startswith("databases/")]
        self.assertEqual(len(databases), 2)
        for database in databases:
            self.assertFalse(database["expose_in_sqllab"])
            self.assertFalse(database["allow_dml"])
            self.assertFalse(database.get("allow_file_upload", False))
        self.assertEqual(assets.EMBEDDED_UUID, assets.stable_uuid("embedded/payment-operations"))
        uuids = [content["uuid"] for content in first.values() if isinstance(content, dict) and "uuid" in content]
        self.assertEqual(len(uuids), len(set(uuids)))

    def test_trino_history_is_optional(self):
        without = self.bundle()
        self.assertFalse(any("lakehouse" in path for path in without))
        dashboard = next(content for path, content in without.items() if path.startswith("dashboards/"))
        charts = {item["meta"]["uuid"] for item in dashboard["position"].values()
            if isinstance(item, dict) and item.get("type") == "CHART"}
        self.assertEqual(charts, {assets.stable_uuid(f"chart/{key}") for key, *_ in assets.CHARTS
            if assets.DATASETS[_[1]]["database"] == assets.CLICKHOUSE})
        self.assertIn(assets.stable_uuid("chart/lakehouse-daily-amount"), json.dumps(self.bundle(TRINO_URI)))

    def test_stored_query_context_matches_captured_frontend_payloads(self):
        captured = json.loads((MODULE / "tests/fixtures/frontend-chart-queries.json").read_text())
        self.assertEqual(set(captured), {key for key, *_ in assets.CHARTS})
        for key, _title, _dataset, viz_type, params, _width in assets.CHARTS:
            with self.subTest(chart=key):
                self.assertEqual(captured[key]["viz_type"], viz_type)
                fields = ("metrics", "columns", "groupby", "orderby")
                project = lambda queries: [{field: query[field] for field in fields  # noqa: E731
                    if query.get(field) is not None} for query in queries]
                stored = json.loads(json.dumps(assets.chart_queries(viz_type, params)))
                self.assertEqual(project(stored), project(captured[key]["queries"]))


class BuildTests(unittest.TestCase):
    def test_dockerfile_defaults_match_pinned_superset(self):
        dockerfile = (MODULE / "Dockerfile").read_text()
        defaults = dict(re.findall(r"^ARG (SUPERSET_\w+)=(\S+)$", dockerfile, re.MULTILINE))
        self.assertEqual(defaults, {"SUPERSET_VERSION": pins()["superset"],
            "SUPERSET_DIGEST": pins()["superset-image-digest"]})
        self.assertIn("USER superset", dockerfile)

    def test_build_paths_pass_superset_pins(self):
        for script in ("scripts/local-dev.sh", "scripts/publish_modules.py"):
            text = (ROOT / script).read_text()
            self.assertIn("SUPERSET_VERSION=", text, script)
            self.assertIn("SUPERSET_DIGEST=", text, script)


if __name__ == "__main__":
    unittest.main()


class PlatformTests(unittest.TestCase):
    terraform = ROOT / "platform/terraform/environments/local"

    def portal(self, *arguments):
        rendered = subprocess.run(
            ["helm", "template", "web-portal", str(ROOT / "modules/web-portal/chart"), *arguments],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
        deployment = next(item for item in yaml.safe_load_all(rendered) if item and item["kind"] == "Deployment")
        return deployment["spec"]["template"]["spec"]["containers"][0]

    def test_terraform_embeds_the_bundled_dashboard_through_the_access_origin(self):
        bi = (self.terraform / "bi.tf").read_text()
        self.assertIn(f'bi_dashboard_id = "{assets.EMBEDDED_UUID}"', bi)
        self.assertIn('"http://localhost:${var.access_port}"', bi)
        self.assertIn('bi_public_url = "http://bi.localhost:${var.access_port}"', bi)
        self.assertIn("condition     = !var.bi_enabled || var.analytics_enabled", bi)
        main = (self.terraform / "main.tf").read_text()
        self.assertIn('"bi-dashboards"], name)', main)
        self.assertIn('var.bi_enabled ? ["bi"] : []', main)
        self.assertIn('export TF_VAR_access_port="$LOCAL_PORT"', (ROOT / "scripts/local-dev.sh").read_text())

    def test_portal_identity_and_bi_settings_come_from_values_and_secrets(self):
        self.assertNotIn("env", self.portal())
        container = self.portal(
            "--set", "identity.enabled=true", "--set-json", 'identity.allowedOrigins=["http://localhost:8080"]',
            "--set", "bi.enabled=true", "--set", "bi.publicUrl=http://bi.localhost:8080",
            "--set", f"bi.dashboardId={assets.EMBEDDED_UUID}",
        )
        env = environment(container)
        self.assertEqual(env["PORTAL_ALLOWED_ORIGINS"], "http://localhost:8080")
        self.assertEqual(env["BI_DASHBOARD_ID"], assets.EMBEDDED_UUID)
        for name, secret, key in (
            ("PORTAL_SESSION_KEY", "web-portal-identity", "session-key"),
            ("PORTAL_DEV_USERS", "web-portal-identity", "dev-users"),
            ("BI_TOKEN_ISSUER_PASSWORD", "bi-dashboards", "token-issuer-password"),
        ):
            self.assertEqual(env[name], {"secretKeyRef": {"name": secret, "key": key}})
        identity_only = environment(self.portal(
            "--set", "identity.enabled=true", "--set-json", 'identity.allowedOrigins=["http://localhost:8080"]'))
        self.assertFalse(any(name.startswith("BI_") for name in identity_only))

    def test_portal_rejects_incomplete_identity_or_bi_settings(self):
        for arguments in (
            ("--set", "identity.enabled=true"),
            ("--set", "identity.enabled=true", "--set-json", 'identity.allowedOrigins=["localhost:8080"]'),
            (
                "--set", "identity.enabled=true", "--set-json", 'identity.allowedOrigins=["http://a"]',
                "--set", "bi.enabled=true",
            ),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(subprocess.CalledProcessError):
                self.portal(*arguments)
