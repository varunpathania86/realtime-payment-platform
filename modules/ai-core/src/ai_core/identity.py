"""Verify resource-specific Keycloak tokens, or identity-disabled development assertions.

Signature, issuer, audience, expiry, scope and resource-role checks are deterministic
code; they never run inside LangChain or LangGraph.
"""

from __future__ import annotations

import re
import json
import os
import threading
import time
from dataclasses import dataclass
from contextvars import ContextVar
from pathlib import Path

import jwt
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ISSUER = "web-portal"
ORCHESTRATOR_AUDIENCE = "ai-orchestrator"
TOOLS_AUDIENCE = "assistant-tools"
MAX_LIFETIME_SECONDS = 300
PERMISSIONS = frozenset({
    "ai:invoke", "ai:admin", "knowledge:read", "knowledge:restricted", "analytics:read", "payments:read",
})
_SUBJECT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TENANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
payment_token_context: ContextVar[str | None] = ContextVar("payment_token", default=None)


class IdentityError(Exception):
    """Raised for any missing, malformed, expired, or wrongly addressed assertion."""


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    permissions: frozenset[str]
    assertion_id: str

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def load_public_key(pem: str | bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem.encode() if isinstance(pem, str) else pem)
    if not isinstance(key, Ed25519PublicKey):
        raise IdentityError("identity verification key must be Ed25519")
    return key


def bearer_token(header: str | None) -> str:
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token.strip():
        raise IdentityError("missing bearer identity assertion")
    return token.strip()


def verify_assertion(
    token: str, public_key: Ed25519PublicKey, audience: str, *, issuer: str = ISSUER, leeway: int = 5
) -> Principal:
    if isinstance(public_key, KeycloakVerifier):
        return public_key.verify(token, audience)
    try:
        claims = jwt.decode(
            token, public_key, algorithms=["EdDSA"], audience=audience, issuer=issuer, leeway=leeway,
            options={"require": ["exp", "iat", "sub", "aud", "iss", "jti"]},
        )
    except jwt.PyJWTError as error:
        raise IdentityError("invalid identity assertion") from error
    if not isinstance(claims["exp"], int) or not isinstance(claims["iat"], int):
        raise IdentityError("assertion times must be integers")
    if claims["exp"] - claims["iat"] > MAX_LIFETIME_SECONDS:
        raise IdentityError("assertion lifetime is too long")
    subject, tenant = claims["sub"], claims.get("tenant", "platform")
    if not isinstance(subject, str) or not _SUBJECT.fullmatch(subject):
        raise IdentityError("invalid assertion subject")
    if not isinstance(tenant, str) or not _TENANT.fullmatch(tenant):
        raise IdentityError("invalid assertion tenant")
    permissions = claims.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
        raise IdentityError("assertion permissions must be a list of strings")
    jti = claims["jti"]
    if not isinstance(jti, str) or not 8 <= len(jti) <= 128:
        raise IdentityError("invalid assertion id")
    return Principal(subject, tenant, frozenset(permissions) & PERMISSIONS, jti)


class KeycloakVerifier:
    """Strict resource-role verification with bounded, rotation-aware JWKS caching."""

    def __init__(self, issuer: str, internal_url: str, *, fetch=None, clock=time.monotonic):
        if not issuer or not internal_url:
            raise IdentityError("OIDC issuer and internal URL are required")
        self.issuer = issuer.rstrip("/")
        self.url = internal_url.rstrip("/") + "/protocol/openid-connect/certs"
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.keys = {}
        self.updated = float("-inf")
        self.lock = threading.Lock()

    def _fetch(self):
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", self.url) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 1 << 20:
                        raise IdentityError("JWKS response too large")
                return json.loads(data)

    def verify(self, token: str, audience: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
                raise IdentityError("unsupported identity signing key")
            with self.lock:
                age = self.clock() - self.updated
                if age >= 300 or (kid not in self.keys and age >= 5):
                    jwks = jwt.PyJWKSet.from_dict(self.fetch())
                    self.keys = {key.key_id: key.key for key in jwks.keys if key.algorithm_name == "RS256"}
                    self.updated = self.clock()
                key = self.keys.get(kid)
            if key is None:
                raise IdentityError("unknown identity signing key")
            claims = jwt.decode(token, key, algorithms=["RS256"], issuer=self.issuer, audience=audience,
                                options={"require": ["iss", "aud", "sub", "iat", "exp", "jti"]})
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 128:
                raise IdentityError("invalid identity subject")
            if not isinstance(claims["iat"], int) or not isinstance(claims["exp"], int):
                raise IdentityError("invalid identity times")
            if not 0 < claims["exp"] - claims["iat"] <= MAX_LIFETIME_SECONDS:
                raise IdentityError("invalid identity lifetime")
            scope = claims.get("scope")
            if not isinstance(scope, str) or "openid" not in scope.split():
                raise IdentityError("missing identity scope")
            roles = claims.get("resource_access", {}).get(audience, {}).get("roles", [])
            if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                raise IdentityError("invalid resource roles")
            jti = claims["jti"]
            if not isinstance(jti, str) or not 8 <= len(jti) <= 128:
                raise IdentityError("invalid identity id")
            return Principal(claims["sub"], "platform", frozenset(roles) & PERMISSIONS, jti)
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError) as error:
            raise IdentityError("invalid Keycloak access token") from error


def identity_key_from_env():
    """Configured OIDC never falls back to the Stage 10 development issuer."""
    if os.environ.get("OIDC_ISSUER") or os.environ.get("OIDC_INTERNAL_URL"):
        return KeycloakVerifier(os.environ.get("OIDC_ISSUER", ""), os.environ.get("OIDC_INTERNAL_URL", ""))
    return load_public_key(Path(os.environ["ASSERTION_PUBLIC_KEY_FILE"]).read_bytes())
