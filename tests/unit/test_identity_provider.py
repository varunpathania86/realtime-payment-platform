import json
import re
import subprocess
import unittest

import yaml

from scripts.check_local_chart import validate as validate_chart
from scripts.module_catalog import ROOT
from scripts.topology_config import validate as validate_topology


IDENTITY = ROOT / "platform/terraform/modules/identity"


class IdentityRealmTests(unittest.TestCase):
    def setUp(self):
        self.realm = json.loads((IDENTITY / "realm.json").read_text())
        self.clients = {client["clientId"]: client for client in self.realm["clients"]}
        self.groups = {group["name"]: group for group in self.realm["groups"]}
        self.users = {user["username"]: user for user in self.realm["users"]}

    def test_keycloak_manages_users_without_ldap_or_public_registration(self):
        self.assertEqual(self.realm["realm"], "rpp")
        self.assertNotIn("components", self.realm)
        self.assertFalse(self.realm["registrationAllowed"])
        self.assertTrue(self.realm["bruteForceProtected"])
        self.assertEqual(self.realm["accessTokenLifespan"], 300)
        self.assertTrue(self.realm["revokeRefreshToken"])
        self.assertEqual(self.realm["refreshTokenMaxReuse"], 0)
        for user in self.users.values():
            self.assertTrue(user["email"].endswith("@example.invalid"))
            self.assertRegex(user["credentials"][0]["value"], r"^\$\{[A-Z_]+\}$")

    def test_portal_requires_confidential_code_flow_and_s256_pkce(self):
        client = self.clients["web-portal"]
        self.assertTrue(client["standardFlowEnabled"])
        self.assertFalse(client["publicClient"])
        self.assertFalse(client["directAccessGrantsEnabled"])
        self.assertFalse(client["serviceAccountsEnabled"])
        self.assertEqual(client["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertEqual(client["redirectUris"], ["${PORTAL_ORIGIN}/auth/callback"])
        self.assertNotIn("*", json.dumps(client))
        self.assertIn("basic", client["defaultClientScopes"])
        audience = next(mapper for mapper in client["protocolMappers"]
                        if mapper["protocolMapper"] == "oidc-audience-mapper")
        self.assertEqual(audience["config"]["included.client.audience"], "web-portal")

    def test_resource_clients_cannot_be_used_to_log_in(self):
        for name in ("payment-api", "ai-orchestrator", "assistant-tools"):
            self.assertTrue(self.clients[name]["bearerOnly"])
            self.assertNotIn("secret", self.clients[name])
            self.assertNotIn("redirectUris", self.clients[name])

    def test_roles_are_explicit_and_viewers_cannot_write_or_read_restricted_knowledge(self):
        self.assertEqual(set(self.groups), {"viewer", "analyst", "developer", "platform-admin"})
        for name in ("viewer", "analyst"):
            roles = self.groups[name]["clientRoles"]
            self.assertEqual(roles["payment-api"], ["payments:read"])
            self.assertNotIn("knowledge:restricted", roles["assistant-tools"])
            self.assertNotIn("ai:admin", roles["ai-orchestrator"])
        self.assertIn("payments:write", self.groups["developer"]["clientRoles"]["payment-api"])
        self.assertIn("ai:admin", self.groups["platform-admin"]["clientRoles"]["ai-orchestrator"])
        clerk = self.users["payments-clerk"]
        self.assertNotIn("groups", clerk)
        self.assertEqual(clerk["clientRoles"]["assistant-tools"], ["payments:read"])
        for group in self.groups.values():
            for client, roles in group["clientRoles"].items():
                declared = {role["name"] for role in self.realm["roles"]["client"][client]}
                self.assertTrue(set(roles) <= declared, (group["name"], client))

    def test_only_reserved_argocd_client_is_disabled(self):
        for name in ("grafana", "superset", "argocd"):
            client = self.clients[name]
            self.assertEqual(client["enabled"], name != "argocd")
            self.assertFalse(client["directAccessGrantsEnabled"])
            self.assertFalse(client["serviceAccountsEnabled"])
            self.assertNotIn("*", json.dumps(client))

    def test_grafana_uses_the_keycloak_username_and_strict_explicit_roles(self):
        template = (ROOT / "platform/terraform/modules/observability/chart/templates/backends.yaml").read_text()
        self.assertIn("GF_AUTH_GENERIC_OAUTH_LOGIN_ATTRIBUTE_PATH, value: preferred_username", template)
        self.assertIn('GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_STRICT, value: "true"', template)
        self.assertIn("contains(roles[*], 'Viewer')", template)

    def test_every_realm_substitution_is_provided_by_terraform(self):
        placeholders = set(re.findall(r"\$\{([A-Z_]+)\}", (IDENTITY / "realm.json").read_text()))
        terraform = (IDENTITY / "main.tf").read_text()
        for key in placeholders:
            self.assertRegex(terraform, rf"\b{key}\s*=")
        self.assertRegex((ROOT / "platform/terraform/environments/local/identity.tf").read_text(),
                        r'source\s*=\s*"\.\./\.\./modules/identity"')


class IdentityPackagingTests(unittest.TestCase):
    def test_payment_oidc_configuration_reaches_the_http_container_not_only_migrations(self):
        result = subprocess.run(
            ["helm", "template", "payment-api", str(ROOT / "modules/payment-api/chart"),
                "--set", "oidc.enabled=true", "--set", "oidc.issuer=http://identity.localhost:8080/realms/rpp",
                "--set", "oidc.internalUrl=http://identity-provider:8080/realms/rpp"],
            capture_output=True, text=True, check=True)
        deployment = next(doc for doc in yaml.safe_load_all(result.stdout)
                            if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"] == "payment-api")
        pod = deployment["spec"]["template"]["spec"]
        env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
        self.assertEqual(env["OIDC_ISSUER"], "http://identity.localhost:8080/realms/rpp")
        self.assertEqual(env["OIDC_INTERNAL_URL"], "http://identity-provider:8080/realms/rpp")
        self.assertNotIn("OIDC_ISSUER", {item["name"] for item in pod["initContainers"][0]["env"]})

    def test_identity_feature_is_opt_in_and_budgeted_on_cluster_a(self):
        settings = json.loads((ROOT / "templates/identity-topology.json").read_text())
        self.assertTrue(validate_topology(settings)["identity"])
        settings["identity"] = "true"
        with self.assertRaisesRegex(ValueError, "identity must be a boolean"):
            validate_topology(settings)
        settings["identity"] = True
        settings["nodes"]["a"]["memory_mib"] = 5119
        with self.assertRaisesRegex(ValueError, "5120 MiB"):
            validate_topology(settings)
        settings = json.loads((ROOT / "templates/ai-topology.json").read_text())
        settings["identity"] = True
        settings["nodes"]["a"]["memory_mib"] = 10239
        with self.assertRaisesRegex(ValueError, "10240 MiB"):
            validate_topology(settings)
        self.assertIn('identity_enabled: ($key == "a" and ($config[0].identity // false))',
                        (ROOT / "scripts/multi-cluster.sh").read_text())

    def test_chart_preserves_local_runtime_contract_and_does_not_expose_administration(self):
        rendered = subprocess.run(
            ["helm", "template", "identity-provider", str(IDENTITY / "chart"), "--namespace", "security",
                "--set", "image=rpp-local/identity-provider:dev-0123456789abcdef",
                "--set", "hostname=http://identity.localhost:9080"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        resources = [item for item in yaml.safe_load_all(rendered.stdout) if item]
        validate_chart(resources)
        ingress = next(item for item in resources if item["kind"] == "Ingress")
        rule = ingress["spec"]["rules"][0]
        self.assertEqual(rule["host"], "identity.localhost")
        self.assertEqual({item["path"] for item in rule["http"]["paths"]}, {"/realms/rpp", "/resources"})
        service = next(item for item in resources if item["kind"] == "Service")
        self.assertEqual([port["port"] for port in service["spec"]["ports"]], [8080])
        deployment = next(item for item in resources if item["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["args"], ["start", "--optimized", "--import-realm"])
        self.assertEqual(container["readinessProbe"]["httpGet"]["port"], "management")
        self.assertEqual(deployment["metadata"]["namespace"], "security")

    def test_runtime_uses_verified_image_and_setup_and_doctor_are_wired(self):
        pins = dict(line.split() for line in (ROOT / ".tool-versions").read_text().splitlines()
                    if line and not line.startswith("#"))
        self.assertRegex(pins["keycloak-image-digest"], r"^sha256:[a-f0-9]{64}$")
        runtime = (ROOT / "platform/runtime/identity-provider/Dockerfile").read_text()
        self.assertIn("@${KEYCLOAK_DIGEST}", runtime)
        self.assertIn("build --db=postgres --health-enabled=true", runtime)
        self.assertIn("bash scripts/identity-runtime.sh setup", (ROOT / "Makefile").read_text())
        self.assertIn("IDENTITY=1 make setup", (ROOT / "scripts/doctor.sh").read_text())
        script = (ROOT / "scripts/local-dev.sh").read_text()
        self.assertIn(".identity_enabled == true", script)
        self.assertIn('settings["local_images"]["identity-provider"] = sys.argv[2]', script)

    def test_runtime_tag_is_stable(self):
        def image():
            return subprocess.check_output(
                ["bash", str(ROOT / "scripts/identity-runtime.sh"), "image"], text=True, timeout=10,
            ).strip()
        self.assertEqual(image(), image())
        self.assertRegex(image(), r"^rpp-local/identity-provider:dev-[a-f0-9]{16}$")


if __name__ == "__main__":
    unittest.main()
