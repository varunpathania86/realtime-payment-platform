import importlib.util
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.module_catalog import ROOT


class SupersetOIDCTests(unittest.TestCase):
    def manager(self, claims):
        module = ModuleType("superset.security.manager")
        module.SupersetSecurityManager = object
        spec = importlib.util.spec_from_file_location("rpp_sso_security",
            ROOT / "modules/bi-dashboards/superset/rpp_bi_security.py")
        security = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"superset.security.manager": module}):
            spec.loader.exec_module(security)
        response = Mock()
        response.json.return_value = claims
        remote = Mock()
        remote.get.return_value = response
        instance = security.RppSecurityManager()
        instance.appbuilder = SimpleNamespace(sm=SimpleNamespace(oauth_remotes={"keycloak": remote}))
        return instance, remote

    def test_verified_userinfo_maps_only_explicit_application_roles(self):
        manager, remote = self.manager({"sub": "a-synthetic-uuid", "roles": ["Gamma", "unmapped-role"]})
        user = manager.oauth_user_info("keycloak")
        self.assertEqual(user["username"], "keycloak_a-synthetic-uuid")
        self.assertEqual(user["role_keys"], ["Gamma"])
        remote.get.assert_called_once_with("userinfo")

    def test_missing_unknown_or_malformed_roles_do_not_register(self):
        for claims in ({}, {"sub": "subject", "roles": ["viewer"]}, {"sub": "subject", "roles": "Admin"},
                        {"sub": "subject", "roles": [{}]}, {"sub": 1, "roles": ["Admin"]}):
            with self.subTest(claims=claims):
                manager, _ = self.manager(claims)
                self.assertEqual(manager.oauth_user_info("keycloak"), {})
        manager, remote = self.manager({"sub": "subject", "roles": ["Admin"]})
        self.assertEqual(manager.oauth_user_info("unconfigured-provider"), {})
        remote.get.assert_not_called()
