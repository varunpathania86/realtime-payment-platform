import copy
import json
import unittest
from unittest.mock import Mock

from scripts.identity_reconcile import reconcile, render_realm


class RealmReconciliationTests(unittest.TestCase):
    def test_scope_bindings_are_explicitly_updated_on_existing_clients(self):
        realm = {"realm": "rpp", "clients": [{"clientId": "portal", "defaultClientScopes": ["basic"]}],
                "roles": {"client": {}}, "groups": [], "users": []}
        admin = Mock()
        def call(method, path, data=None):
            if method != "GET":
                return None
            return {
                "/client-scopes": [{"name": "basic", "id": "scope-basic"}],
                "/clients?clientId=portal": [{"id": "portal-id"}],
                "/clients/portal-id/default-client-scopes": [],
            }[path]
        admin.call.side_effect = call
        reconcile(admin, realm)
        admin.call.assert_any_call("PUT", "/clients/portal-id/default-client-scopes/scope-basic")
    def test_substitution_json_escapes_secrets_and_rejects_missing_values(self):
        realm = render_realm('{"secret":"${SECRET}"}', {"SECRET": 'quoted"\\value'})
        self.assertEqual(realm["secret"], 'quoted"\\value')
        with self.assertRaises(KeyError):
            render_realm('{"secret":"${MISSING}"}', {})

    def test_existing_database_reconciliation_is_repeatable_without_password_reset(self):
        realm = {"realm": "rpp", "enabled": True,
                "clients": [{"clientId": "api", "enabled": True}],
                "roles": {"client": {"api": [{"name": "read"}, {"name": "write"}]}},
                "groups": [{"name": "viewer", "clientRoles": {"api": ["read"]}}],
                "users": [{"username": "viewer", "enabled": True, "groups": ["/viewer"],
                            "credentials": [{"type": "password", "value": "must-not-reset"}]}]}
        role_read, role_write = {"name": "read", "id": "r"}, {"name": "write", "id": "w"}
        def call(method, path, data=None):
            if method != "GET":
                return None
            responses = {
                "/clients?clientId=api": [{"id": "api-id"}],
                "/clients/api-id/roles": [role_read, role_write],
                "/groups": [{"name": "viewer", "id": "group-id"}],
                "/groups/group-id/role-mappings/clients/api-id": [role_read],
                "/users?exact=true&username=viewer": [{"id": "user-id"}],
                "/users/user-id/role-mappings/clients/api-id": [role_write],
                "/users/user-id/groups": [{"path": "/viewer", "id": "group-id"}],
            }
            return copy.deepcopy(responses[path])
        admin = Mock()
        admin.call.side_effect = call
        reconcile(admin, realm)
        first = admin.call.call_args_list
        admin.reset_mock()
        reconcile(admin, realm)
        self.assertEqual(first, admin.call.call_args_list)
        writes = [entry for entry in first if entry.args[0] != "GET"]
        self.assertNotIn("must-not-reset", json.dumps([entry.args for entry in writes]))
        self.assertIn(unittest.mock.call("DELETE", "/users/user-id/role-mappings/clients/api-id", [role_write]), first)
