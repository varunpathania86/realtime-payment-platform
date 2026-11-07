package main

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestPaymentKeycloakAuthorizationAndRotation(t *testing.T) {
	first, _ := rsa.GenerateKey(rand.Reader, 2048)
	second, _ := rsa.GenerateKey(rand.Reader, 2048)
	keys := map[string]*rsa.PrivateKey{"first": first}
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		set := []any{}
		for kid, key := range keys {
			set = append(set, map[string]string{"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
				"n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": "AQAB"})
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": set})
	}))
	defer provider.Close()
	issuer := provider.URL + "/realms/rpp"
	handler, err := paymentIdentity(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(204) }),
		func(k string) string {
			if k == "OIDC_ISSUER" || k == "OIDC_INTERNAL_URL" {
				return issuer
			}
			return ""
		})
	if err != nil {
		t.Fatal(err)
	}
	sign := func(kid string, key *rsa.PrivateKey, overrides map[string]any) string {
		claims := map[string]any{"iss": issuer, "aud": "payment-api", "sub": "viewer",
			"iat": time.Now().Unix(), "exp": time.Now().Add(time.Minute).Unix(), "scope": "openid",
			"resource_access": map[string]any{"payment-api": map[string]any{"roles": []string{"payments:read"}}}}
		for k, v := range overrides {
			claims[k] = v
		}
		header, _ := json.Marshal(map[string]string{"alg": "RS256", "kid": kid})
		body, _ := json.Marshal(claims)
		value := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(body)
		hash := sha256.Sum256([]byte(value))
		sig, _ := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, hash[:])
		return value + "." + base64.RawURLEncoding.EncodeToString(sig)
	}
	check := func(method, token string, expected int) {
		t.Helper()
		request := httptest.NewRequest(method, "/api/payments", nil)
		request.Header.Set("Authorization", "Bearer "+token)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != expected {
			t.Fatalf("%s got %d, expected %d", method, response.Code, expected)
		}
	}
	valid := sign("first", first, nil)
	check("GET", valid, 204)
	check("POST", valid, 403)
	for name, overrides := range map[string]map[string]any{
		"expired":           {"exp": time.Now().Add(-time.Minute).Unix()},
		"issuer":            {"iss": "https://wrong.invalid"},
		"audience":          {"aud": "assistant-tools"},
		"missing subject":   {"sub": ""},
		"long lifetime":     {"exp": time.Now().Add(time.Hour).Unix()},
		"future issue time": {"iat": time.Now().Add(time.Minute).Unix()},
	} {
		t.Run(name, func(t *testing.T) { check("GET", sign("first", first, overrides), 401) })
	}
	check("GET", sign("first", first, map[string]any{"scope": "profile"}), 403)
	check("GET", sign("unknown", second, nil), 401)
	keys = map[string]*rsa.PrivateKey{"second": second}
	check("GET", sign("second", second, nil), 204)
	check("POST", sign("second", second, map[string]any{"resource_access": map[string]any{
		"payment-api": map[string]any{"roles": []string{"payments:write"}}}}), 204)
}
