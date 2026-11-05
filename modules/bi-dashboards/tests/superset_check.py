"""Superset-dependent checks; `make integration` runs them inside the module image."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SUPERSET_SECRET_KEY", "check-only-secret-key-not-used-anywhere")
os.environ.setdefault("SUPERSET_GUEST_TOKEN_SECRET", "check-only-guest-secret")
os.environ.setdefault("SUPERSET_DATABASE_HOST", "metadata.invalid")
os.environ.setdefault("SUPERSET_DATABASE_NAME", "superset")
os.environ.setdefault("SUPERSET_DATABASE_USER", "superset")
os.environ.setdefault("SUPERSET_DATABASE_PASSWORD", "p@ss/word")
os.environ.setdefault("SUPERSET_EMBED_ORIGINS", "http://localhost:8080 http://portal.localhost")

import rpp_bi_bootstrap as bootstrap  # noqa: E402
import superset_config as config  # noqa: E402
from rpp_bi_assets import EMBEDDED_UUID  # noqa: E402
from rpp_bi_security import RppSecurityManager, free_form_sql, guest_chart_allowed  # noqa: E402
from superset.exceptions import SupersetSecurityException  # noqa: E402
from superset.security.manager import SupersetSecurityManager  # noqa: E402

# Real models need an initialised app and metadata database; the chart lookup is mocked anyway.
SLICE_MODULE = {"superset.models.slice": SimpleNamespace(Slice="Slice")}
DASHBOARD = object()
OTHER_DASHBOARD = object()


def guest(resources, rls=None):
    return {"user": {"username": "viewer"}, "resources": resources, "rls": rls or []}


def dataset(identifier=7, kind="table"):
    return SimpleNamespace(id=identifier, type=kind)


def chart(datasource_id=7, kind="table", dashboards=(DASHBOARD,)):
    return SimpleNamespace(datasource_id=datasource_id, datasource_type=kind, dashboards=list(dashboards))


def query(**extras):
    return SimpleNamespace(extras=extras)


class ConfigTests(unittest.TestCase):
    def test_metadata_uri_quotes_credentials(self):
        self.assertEqual(config.SQLALCHEMY_DATABASE_URI,
            "postgresql+psycopg2://superset:p%40ss%2Fword@metadata.invalid:5432/superset")

    def test_guest_tokens_are_limited_to_the_embedded_dashboard_without_rls(self):
        own = [{"type": "dashboard", "id": EMBEDDED_UUID}]
        self.assertTrue(config.guest_token_allowed(guest(own)))
        self.assertIs(config.GUEST_TOKEN_VALIDATOR_HOOK, config.guest_token_allowed)
        for payload in (
            guest(own, rls=[{"clause": "1=1"}]),
            guest([{"type": "dashboard", "id": "another"}]),
            guest([*own, {"type": "dashboard", "id": "another"}]),
            guest([]),
        ):
            self.assertFalse(config.guest_token_allowed(payload))
        self.assertFalse(config.guest_token_allowed(guest(own), dashboard_uuid=""))

    def test_embedding_policy_lists_only_configured_frame_ancestors(self):
        policy = config.TALISMAN_CONFIG["content_security_policy"]
        self.assertEqual(policy["frame-ancestors"], ["http://localhost:8080", "http://portal.localhost"])
        self.assertEqual(policy["connect-src"], ["'self'"])
        self.assertIsNone(config.TALISMAN_CONFIG["frame_options"])
        self.assertEqual(config.talisman_config([])["content_security_policy"]["frame-ancestors"], ["'none'"])
        self.assertTrue(config.FEATURE_FLAGS["EMBEDDED_SUPERSET"])
        self.assertIs(config.CUSTOM_SECURITY_MANAGER, RppSecurityManager)
        self.assertIsNone(config.PUBLIC_ROLE_LIKE)


class GuestQueryTests(unittest.TestCase):
    def test_saved_chart_must_query_its_dataset_from_an_accessible_dashboard(self):
        allowed = lambda dashboard: dashboard is DASHBOARD  # noqa: E731
        self.assertTrue(guest_chart_allowed(chart(), dataset(), allowed))
        self.assertFalse(guest_chart_allowed(None, dataset(), allowed))
        self.assertFalse(guest_chart_allowed(chart(), None, allowed))
        self.assertFalse(guest_chart_allowed(chart(datasource_id=8), dataset(), allowed))
        self.assertFalse(guest_chart_allowed(chart(kind="query"), dataset(), allowed))
        self.assertFalse(guest_chart_allowed(chart(dashboards=(OTHER_DASHBOARD,)), dataset(), allowed))

    def test_free_form_where_and_having_are_detected(self):
        self.assertFalse(free_form_sql([query(), query(time_grain_sqla="P1D", where="", having=None)]))
        self.assertTrue(free_form_sql([query(), query(where="1=1")]))
        self.assertTrue(free_form_sql([query(having="count(*) > 0")]))

    def manager(self, guest_user=True):
        manager = RppSecurityManager.__new__(RppSecurityManager)
        manager.is_guest_user = lambda: guest_user
        manager.has_guest_access = lambda dashboard: dashboard is DASHBOARD
        return manager

    def context(self, form_data, queries=()):
        return SimpleNamespace(form_data=form_data, datasource=dataset(), queries=list(queries), slice_=None)

    def test_guest_query_binds_the_saved_chart_before_the_builtin_guard(self):
        saved = chart()
        context = self.context({"slice_id": 4}, [query()])
        with mock.patch.dict(sys.modules, SLICE_MODULE), mock.patch("superset.db") as db, \
                mock.patch.object(SupersetSecurityManager, "raise_for_access") as builtin:
            db.session.get.return_value = saved
            self.manager().raise_for_access(query_context=context)
        self.assertIs(context.slice_, saved)
        self.assertEqual(db.session.get.call_args.args, ("Slice", 4))
        builtin.assert_called_once_with(query_context=context)

    def test_guest_query_without_a_valid_saved_chart_is_rejected(self):
        cases = (
            ({}, [query()], chart()),
            ({"slice_id": "4"}, [query()], chart()),
            ({"slice_id": 4}, [query()], None),
            ({"slice_id": 4}, [query()], chart(datasource_id=8)),
            ({"slice_id": 4}, [query(where="1=1")], chart()),
        )
        for form_data, queries, saved in cases:
            with self.subTest(form_data=form_data, saved=saved), mock.patch.dict(sys.modules, SLICE_MODULE), \
                    mock.patch("superset.db") as db, \
                    mock.patch.object(SupersetSecurityManager, "raise_for_access") as builtin:
                db.session.get.return_value = saved
                with self.assertRaises(SupersetSecurityException):
                    self.manager().raise_for_access(query_context=self.context(form_data, queries))
                builtin.assert_not_called()

    def test_non_guest_queries_use_only_the_builtin_checks(self):
        context = self.context({}, [query(where="1=1")])
        with mock.patch.object(SupersetSecurityManager, "raise_for_access") as builtin:
            self.manager(guest_user=False).raise_for_access(query_context=context)
        builtin.assert_called_once_with(query_context=context)
        self.assertIsNone(context.slice_)


class BootstrapTests(unittest.TestCase):
    def test_roles_stay_read_only_and_scoped(self):
        views = {view for _permission, view in bootstrap.GUEST_PERMISSIONS}
        self.assertFalse(views & {"Database", "Dataset", "SQLLab", "SqlLab", "SecurityRestApi", "Log"})
        self.assertEqual({permission for permission, _view in bootstrap.ISSUER_PERMISSIONS},
            {"can_grant_guest_token", "can_read"})
        self.assertEqual({view for _permission, view in bootstrap.ISSUER_PERMISSIONS}, {"SecurityRestApi"})

    def test_import_uri_carries_the_password_mask(self):
        from superset.constants import PASSWORD_MASK

        masked = bootstrap.masked_uri("clickhousedb://rpp_bi_reader@realtime-analytics:8123/analytics")
        self.assertEqual(masked, f"clickhousedb://rpp_bi_reader:{PASSWORD_MASK}@realtime-analytics:8123/analytics")

    def test_layer_import_sets_the_metadata_model_type(self):
        contents = {"metadata.yaml": "version: 1.0.0\ntype: Dashboard\n", "charts/a.yaml": "x: 1\n"}
        updated = bootstrap.with_metadata_type(contents, "Slice")
        self.assertIn("type: Slice", updated["metadata.yaml"])
        self.assertIn("version: 1.0.0", updated["metadata.yaml"])
        self.assertEqual(updated["charts/a.yaml"], contents["charts/a.yaml"])
        self.assertIn("type: Dashboard", contents["metadata.yaml"])


if __name__ == "__main__":
    unittest.main()
