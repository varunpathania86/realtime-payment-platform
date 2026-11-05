import re
import subprocess
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT, local_modules

MODULE = ROOT / "modules/ai-orchestrator"


def resources(*arguments):
    rendered = subprocess.run(
        ["helm", "template", "ai-orchestrator", str(MODULE / "chart"), "--namespace", "apps", *arguments],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return [item for item in yaml.safe_load_all(rendered.stdout) if item]


class AiOrchestratorPackagingTests(unittest.TestCase):
    def test_chart_is_internal_and_holds_no_database_credentials(self):
        items = resources()
        validate_chart(items)
        self.assertEqual({item["kind"] for item in items}, {"Deployment", "Service"})
        pod = next(item for item in items if item["kind"] == "Deployment")["spec"]["template"]["spec"]
        environment = {item["name"]: item for item in pod["containers"][0]["env"]}
        self.assertFalse(any("valueFrom" in item for item in environment.values()))
        self.assertEqual(environment["ASSISTANT_TOOLS_URL"]["value"], "http://assistant-tools:8080/mcp")
        self.assertEqual(environment["LLM_CHAT_URL"]["value"], "http://llm-serving-chat:8080/v1")

    def test_model_alias_matches_llm_serving(self):
        orchestrator = yaml.safe_load((MODULE / "chart/values.yaml").read_text())
        serving = yaml.safe_load((ROOT / "modules/llm-serving/chart/values.yaml").read_text())
        self.assertEqual(orchestrator["llm"]["model"], serving["models"]["chat"]["name"])
        self.assertIn(f'"{serving["models"]["chat"]["name"]}"', (MODULE / "src/ai_orchestrator/api.py").read_text())

    def test_dockerfile_pins_shared_ai_runtime(self):
        dockerfile = (MODULE / "Dockerfile").read_text()
        pins = dict(line.split()[:2] for line in (ROOT / ".tool-versions").read_text().splitlines()
                    if line.strip() and not line.startswith("#"))
        self.assertEqual(re.search(r"ARG AI_PYTHON_VERSION=(\S+)", dockerfile).group(1), pins["ai-python"])
        self.assertIn("ai_orchestrator.api", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)

    def test_selected_only_for_ai_feature(self):
        self.assertIn("ai-orchestrator", local_modules(features={"ai"}))
        self.assertNotIn("ai-orchestrator", local_modules(features={"streaming", "analytics", "bi"}))


class PortalAssistantChartTests(unittest.TestCase):
    """The web-portal BFF is the orchestrator's only caller and the only holder of the signing key."""

    def portal(self, *arguments):
        rendered = subprocess.run(
            ["helm", "template", "web-portal", str(ROOT / "modules/web-portal/chart"), "--namespace", "apps",
            "--set", "identity.enabled=true", "--set-json", 'identity.allowedOrigins=["http://localhost:8080"]',
             *arguments],
            check=True, capture_output=True, text=True, timeout=30,
        )
        items = [item for item in yaml.safe_load_all(rendered.stdout) if item]
        validate_chart(items)
        return next(item for item in items if item["kind"] == "Deployment")["spec"]["template"]["spec"]

    def test_assistant_mounts_only_the_portal_signing_key(self):
        pod = self.portal("--set", "assistant.enabled=true")
        container = pod["containers"][0]
        environment = {item["name"]: item.get("value") for item in container["env"]}
        self.assertEqual(environment["AI_ORCHESTRATOR_URL"], "http://ai-orchestrator:8080")
        self.assertEqual(environment["AI_ASSERTION_PRIVATE_KEY_FILE"], "/assistant-identity/private-key.pem")
        self.assertEqual(container["volumeMounts"], [
            {"name": "assistant-identity", "mountPath": "/assistant-identity", "readOnly": True}])
        secret = pod["volumes"][0]["secret"]
        self.assertEqual(secret["secretName"], "web-portal-assistant-signer")
        self.assertEqual(secret["defaultMode"], 0o440)
        self.assertEqual(pod["securityContext"]["fsGroup"], 65532)
        orchestrator = next(item for item in resources() if item["kind"] == "Deployment")
        self.assertNotIn("web-portal-assistant-signer", yaml.safe_dump(orchestrator))

    def test_assistant_is_off_by_default_and_validated(self):
        pod = self.portal()
        self.assertNotIn("volumes", pod)
        self.assertFalse(any(item["name"].startswith("AI_") for item in pod["containers"][0]["env"]))
        with self.assertRaises(subprocess.CalledProcessError):
            self.portal("--set", "assistant.enabled=true", "--set", "assistant.orchestratorUrl=ai-orchestrator")


class AiPlatformWiringTests(unittest.TestCase):
    terraform = ROOT / "platform/terraform/environments/local"

    def test_signing_key_reaches_only_the_portal_and_public_key_the_verifiers(self):
        ai = (self.terraform / "ai.tf").read_text()
        self.assertIn('algorithm = "ED25519"', ai)
        self.assertIn('data = { "private-key.pem" = tls_private_key.ai_identity[0].private_key_pem_pkcs8 }', ai)
        self.assertIn('data = { "public-key.pem" = tls_private_key.ai_identity[0].public_key_pem }', ai)
        self.assertEqual(ai.count("private_key_pem_pkcs8"), 1)
        self.assertIn("condition     = var.analytics_enabled", ai)

    def test_knowledge_roles_are_separate_and_the_reader_is_read_only(self):
        ai = (self.terraform / "ai.tf").read_text()
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector;", ai)
        self.assertIn("GRANT USAGE, CREATE ON SCHEMA public TO rpp_knowledge_writer;", ai)
        self.assertIn("GRANT USAGE ON SCHEMA public TO rpp_knowledge_reader;", ai)
        self.assertIn("ALTER ROLE rpp_knowledge_reader SET default_transaction_read_only = on;", ai)
        self.assertNotIn("GRANT USAGE, CREATE ON SCHEMA public TO rpp_knowledge_reader", ai)
        self.assertIn('image = "pgvector/pgvector:${local.tool_versions["pgvector"]}"', ai)

    def test_lean_profile_keeps_object_storage_and_identity_without_streaming_or_bi(self):
        streaming = (self.terraform / "streaming.tf").read_text()
        self.assertIn("!var.analytics_enabled || var.streaming_enabled || var.ai_enabled", streaming)
        self.assertEqual(streaming.count("local.object_store_enabled ?"), 5)
        bi = (self.terraform / "bi.tf").read_text()
        self.assertIn("count = local.portal_identity_enabled ? 1 : 0", bi)
        self.assertIn('payments-clerk = ["ai:invoke", "payments:read"]', bi)
        moved = (self.terraform / "moved.tf").read_text()
        for user in ("portal-session-key", "ops-viewer", "payments-clerk"):
            self.assertIn(f'to   = random_password.portal["{user}"]', moved)
        main = (self.terraform / "main.tf").read_text()
        self.assertIn('var.ai_enabled ? ["ai"] : []', main)
        self.assertIn("aiReader = { enabled = var.ai_enabled }", main)
        self.assertIn('each.key == "llm-serving" ? 1800 : 600', main)
        self.assertIn('each.key == "assistant-tools" ? [yamlencode({ trino', main)
        self.assertIn("ai-reader-password", (self.terraform / "analytics.tf").read_text())
        local_dev = (ROOT / "scripts/local-dev.sh").read_text()
        self.assertIn("arguments+=(--feature ai)", local_dev)
        self.assertIn('if [ "$object_store" = true ]; then', local_dev)
        self.assertIn('ai_enabled: ($key == "a" and ($config[0].ai // false))', (ROOT / "scripts/multi-cluster.sh").read_text())


if __name__ == "__main__":
    unittest.main()

    def test_shared_portal_identity_values_do_not_redirect_assertion_verifiers(self):
        # main.tf passes one values block to every application; its portal identity must not
        # replace the AI verifiers' public-key Secret.
        shared = ["--set", "identity.enabled=true", "--set", "identity.existingSecret=web-portal-identity"]
        for module in ("ai-orchestrator", "assistant-tools"):
            with self.subTest(module=module):
                rendered = subprocess.run(
                    ["helm", "template", module, str(ROOT / "modules" / module / "chart"),
                    "--namespace", "apps", *shared],
                    check=True, capture_output=True, text=True, timeout=30,
                )
                deployment = next(item for item in yaml.safe_load_all(rendered.stdout)
                                    if item and item["kind"] == "Deployment")
                volumes = {volume["name"]: volume for volume in deployment["spec"]["template"]["spec"]["volumes"]}
                self.assertEqual(volumes["identity"]["secret"]["secretName"], "ai-identity")
