import time
import unittest

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from ai_core.identity import IdentityError, KeycloakVerifier


class KeycloakIdentityTests(unittest.TestCase):
    def setUp(self):
        self.first = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.second = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.age = 0
        self.keyset = self.jwks(self.first, "first")
        self.fetches = 0
        def fetch():
            self.fetches += 1
            return self.keyset
        self.verifier = KeycloakVerifier("https://issuer.invalid/realms/rpp", "https://internal.invalid/realms/rpp",
                                        fetch=fetch, clock=lambda: self.age)

    def jwks(self, key, kid):
        public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": kid, "alg": "RS256", "use": "sig"}]}

    def token(self, key=None, kid="first", **overrides):
        claims = {
            "iss": self.verifier.issuer, "aud": "assistant-tools", "sub": "a-uuid-human",
            "iat": int(time.time()), "exp": int(time.time()) + 300, "jti": "an-identity-id",
            "scope": "openid profile", "resource_access": {
                "assistant-tools": {"roles": ["knowledge:read"]},
                "ai-orchestrator": {"roles": ["ai:admin"]}},
        }
        claims.update(overrides)
        return jwt.encode(claims, key or self.first, algorithm="RS256", headers={"kid": kid})

    def test_resource_roles_are_not_widened_from_other_audiences(self):
        principal = self.verifier.verify(self.token(), "assistant-tools")
        self.assertEqual(principal.permissions, {"knowledge:read"})

    def test_wrong_issuer_audience_expiry_signature_scope_fail_closed(self):
        for overrides in ({"iss": "https://wrong.invalid"}, {"aud": "ai-orchestrator"},
                            {"exp": int(time.time()) - 1}, {"scope": "profile"}, {"sub": None},
                            {"resource_access": "malformed"}):
            with self.subTest(overrides=overrides), self.assertRaises(IdentityError):
                self.verifier.verify(self.token(**overrides), "assistant-tools")
        with self.assertRaises(IdentityError):
            self.verifier.verify(self.token(key=self.second), "assistant-tools")

    def test_unknown_keys_are_bounded_and_rotation_refreshes(self):
        self.verifier.verify(self.token(), "assistant-tools")
        for _ in range(4):
            with self.assertRaises(IdentityError):
                self.verifier.verify(self.token(key=self.second, kid="second"), "assistant-tools")
        self.assertEqual(self.fetches, 1)
        self.age = 6
        self.keyset = self.jwks(self.second, "second")
        self.verifier.verify(self.token(key=self.second, kid="second"), "assistant-tools")
        self.assertEqual(self.fetches, 2)
        with self.assertRaises(IdentityError):
            self.verifier.verify(self.token(), "assistant-tools")
