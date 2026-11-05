"""Idempotently provision Superset roles, users, dashboard assets and embedding.

Run inside the Superset image after `superset db upgrade` and `superset init`.
Reconciles exact role permissions on every run so manual drift is reverted.
"""

import os
import uuid

import yaml

from rpp_bi_assets import CLICKHOUSE, DASHBOARD_UUID, EMBEDDED_UUID, bundle, database_file

# Minimal read permissions for guest-token viewers; dashboard and dataset scope comes from the token.
GUEST_PERMISSIONS = (
    ("can_read", "Dashboard"),
    ("can_read", "Chart"),
    ("can_read", "EmbeddedDashboard"),
    ("can_dashboard", "Superset"),
    ("can_explore_json", "Superset"),
    # The embedded app reads the guest's own identity and roles from /api/v1/me/ and /me/roles/.
    ("can_read", "CurrentUserRestApi"),
)
# can_read on SecurityRestApi only exposes /csrf_token/, which guest_token POSTs require.
ISSUER_PERMISSIONS = (("can_grant_guest_token", "SecurityRestApi"), ("can_read", "SecurityRestApi"))


def ensure_role(security, name, permissions):
    role = security.find_role(name) or security.add_role(name)
    resolved = []
    for permission, view in permissions:
        item = security.find_permission_view_menu(permission, view)
        if item is None:
            raise RuntimeError(f"Superset permission {permission} on {view} does not exist")
        resolved.append(item)
    role.permissions = resolved
    return role


def ensure_user(security, username, password, role):
    user = security.find_user(username=username)
    if user is None:
        user = security.add_user(username, username, "rpp", f"{username}@rpp.invalid", role, password)
        if not user:
            raise RuntimeError(f"could not create Superset user {username}")
    else:
        user.roles = [role]
        user.active = True
        security.reset_password(user.id, password)
    return user


def ensure_embedded(session, dashboard_id, origins):
    from superset.models.embedded_dashboard import EmbeddedDashboard

    embedded = session.query(EmbeddedDashboard).filter_by(dashboard_id=dashboard_id).one_or_none()
    if embedded is None:
        embedded = EmbeddedDashboard(dashboard_id=dashboard_id)
        session.add(embedded)
    embedded.uuid = uuid.UUID(EMBEDDED_UUID)
    embedded.allow_domain_list = ",".join(origins)
    return embedded


def with_metadata_type(contents, model):
    """Return the bundle with metadata.yaml declaring the model class an import command validates."""
    metadata = yaml.safe_load(contents["metadata.yaml"])
    return {**contents, "metadata.yaml": yaml.safe_dump({**metadata, "type": model})}


def masked_uri(uri):
    """Return the URI with Superset's password mask so import keeps the separately supplied password."""
    from sqlalchemy.engine.url import make_url
    from superset.constants import PASSWORD_MASK

    return make_url(uri).set(password=PASSWORD_MASK).render_as_string(hide_password=False)


def run(environ):
    from flask import g
    from superset import db, security_manager
    from superset.commands.chart.importers.v1 import ImportChartsCommand
    from superset.commands.dashboard.importers.v1 import ImportDashboardsCommand
    from superset.commands.database.importers.v1 import ImportDatabasesCommand
    from superset.commands.dataset.importers.v1 import ImportDatasetsCommand
    from superset.models.dashboard import Dashboard
    # Imported after create_app(): importing Superset config modules earlier initialises a second app.
    from superset_config import GUEST_ROLE_NAME, TOKEN_ISSUER_ROLE_NAME

    admin = ensure_user(
        security_manager, environ["SUPERSET_ADMIN_USER"], environ["SUPERSET_ADMIN_PASSWORD"],
        security_manager.find_role("Admin"),
    )
    ensure_user(
        security_manager, environ["SUPERSET_TOKEN_ISSUER_USER"], environ["SUPERSET_TOKEN_ISSUER_PASSWORD"],
        ensure_role(security_manager, TOKEN_ISSUER_ROLE_NAME, ISSUER_PERMISSIONS),
    )
    ensure_role(security_manager, GUEST_ROLE_NAME, GUEST_PERMISSIONS)
    db.session.commit()
    g.user = admin
    contents = bundle(masked_uri(environ["SUPERSET_CLICKHOUSE_URI"]), environ.get("SUPERSET_TRINO_URI") or None)
    passwords = {f"databases/{database_file(CLICKHOUSE)}.yaml": environ["SUPERSET_CLICKHOUSE_PASSWORD"]}
    # The dashboard importer never overwrites dependencies, so update each layer with its own command.
    for command, model in (
        (ImportDatabasesCommand, "Database"), (ImportDatasetsCommand, "SqlaTable"),
        (ImportChartsCommand, "Slice"), (ImportDashboardsCommand, "Dashboard"),
    ):
        command(with_metadata_type(contents, model), passwords=passwords, overwrite=True).run()
    dashboard = db.session.query(Dashboard).filter_by(uuid=uuid.UUID(DASHBOARD_UUID)).one()
    ensure_embedded(db.session, dashboard.id, environ.get("SUPERSET_EMBED_ORIGINS", "").split())
    db.session.commit()
    print(f"Provisioned dashboard {dashboard.slug}; embedded uuid {EMBEDDED_UUID}", flush=True)


def main():
    from superset.app import create_app

    app = create_app()
    with app.app_context(), app.test_request_context():
        run(os.environ)


if __name__ == "__main__":
    main()
