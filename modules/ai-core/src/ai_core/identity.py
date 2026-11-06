"""Verify the short-lived identity assertions minted by the web-portal BFF.

Assertions are Ed25519-signed JWTs with one audience each, so a token minted for the
orchestrator cannot be replayed against the MCP server and vice versa. Token validation and
authorization are deterministic code; they never run inside LangChain or LangGraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ISSUER = "web-portal"
ORCHESTRATOR_AUDIENCE = "ai-orchestrator"
TOOLS_AUDIENCE = "assistant-tools"
MAX_LIFETIME_SECONDS = 300
PERMISSIONS = frozenset({
    "ai:invoke", "knowledge:read", "knowledge:restricted", "analytics:read", "payments:read",
})
_SUBJECT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TENANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


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
