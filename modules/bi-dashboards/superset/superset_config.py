"""Superset settings for the local embedded payment-operations dashboard.

Secrets arrive only through environment variables backed by Kubernetes Secrets.
"""

import os
from urllib.parse import quote

from rpp_bi_assets import EMBEDDED_UUID
from rpp_bi_security import RppSecurityManager
from superset.config import TALISMAN_CONFIG as DEFAULT_TALISMAN_CONFIG

GUEST_ROLE_NAME = "RppEmbeddedViewer"
TOKEN_ISSUER_ROLE_NAME = "RppGuestTokenIssuer"
EMBED_ORIGINS = os.environ.get("SUPERSET_EMBED_ORIGINS", "").split()


def database_uri(environ):
    user = quote(environ["SUPERSET_DATABASE_USER"], safe="")
    password = quote(environ["SUPERSET_DATABASE_PASSWORD"], safe="")
    host = environ["SUPERSET_DATABASE_HOST"]
    port = environ.get("SUPERSET_DATABASE_PORT", "5432")
    name = environ["SUPERSET_DATABASE_NAME"]
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def guest_token_allowed(guest_user, dashboard_uuid=None):
    """Accept only one-dashboard tokens without row-level clauses."""
    dashboard_uuid = dashboard_uuid if dashboard_uuid is not None else EMBEDDED_UUID
    resources = guest_user.get("resources") or []
    return bool(dashboard_uuid) and not guest_user.get("rls") and resources == [
        {"type": "dashboard", "id": dashboard_uuid},
    ]


def talisman_config(origins):
    policy = {key: value for key, value in DEFAULT_TALISMAN_CONFIG["content_security_policy"].items()}
    # Remove third-party map and telemetry endpoints; this deployment uses none.
    policy["img-src"] = ["'self'", "blob:", "data:"]
    policy["connect-src"] = ["'self'"]
    policy["frame-ancestors"] = list(origins) or ["'none'"]
    return {
        **DEFAULT_TALISMAN_CONFIG,
        "content_security_policy": policy,
        # CSP frame-ancestors replaces X-Frame-Options, which cannot list several origins.
        "frame_options": None,
        "force_https": False,
        "session_cookie_secure": False,
    }


SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = database_uri(os.environ)
FEATURE_FLAGS = {"EMBEDDED_SUPERSET": True, "DISABLE_EMBEDDED_SUPERSET_LOGOUT": True}
GUEST_TOKEN_JWT_SECRET = os.environ["SUPERSET_GUEST_TOKEN_SECRET"]
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_TOKEN_JWT_EXP_SECONDS = 300
GUEST_TOKEN_JWT_AUDIENCE = "rpp-bi-dashboards"
GUEST_TOKEN_VALIDATOR_HOOK = guest_token_allowed
CUSTOM_SECURITY_MANAGER = RppSecurityManager
TALISMAN_ENABLED = True
TALISMAN_CONFIG = talisman_config(EMBED_ORIGINS)
ENABLE_PROXY_FIX = True
PUBLIC_ROLE_LIKE = None
AUTH_USER_REGISTRATION = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SHOW_STACKTRACE = False
CONTENT_SECURITY_POLICY_WARNING = False
# No Redis or Celery: synchronous queries, no reports/alerts/thumbnails (ADR-028).
RATELIMIT_STORAGE_URI = "memory://"
SQLLAB_CTAS_NO_LIMIT = False
ROW_LIMIT = 10000
SQL_MAX_ROW = 10000
