package main

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
)

func paymentIdentity(next http.Handler, getenv func(string) string) (http.Handler, error) {
	issuer := strings.TrimRight(getenv("OIDC_ISSUER"), "/")
	if issuer == "" {
		if getenv("OIDC_INTERNAL_URL") != "" {
			return nil, errors.New("OIDC_ISSUER is required with OIDC_INTERNAL_URL")
		}
		return next, nil
	}
	internal := strings.TrimRight(getenv("OIDC_INTERNAL_URL"), "/")
	if internal == "" {
		return nil, errors.New("OIDC_INTERNAL_URL is required with OIDC_ISSUER")
	}
	client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	keys := oidc.NewRemoteKeySet(oidc.ClientContext(context.Background(), client), internal+"/protocol/openid-connect/certs")
	verify := oidc.NewVerifier(issuer, keys, &oidc.Config{ClientID: "payment-api", SupportedSigningAlgs: []string{"RS256"}})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/api")
		if path != "/payments" && !strings.HasPrefix(path, "/payments/") {
			next.ServeHTTP(w, r)
			return
		}
		scheme, raw, ok := strings.Cut(r.Header.Get("Authorization"), " ")
		if !ok || !strings.EqualFold(scheme, "Bearer") || strings.ContainsAny(raw, " \t\r\n") {
			http.Error(w, "bearer token required", http.StatusUnauthorized)
			return
		}
		token, err := verify.Verify(r.Context(), raw)
		if err != nil || token.Subject == "" {
			http.Error(w, "invalid bearer token", http.StatusUnauthorized)
			return
		}
		var claims struct {
			Scope     string `json:"scope"`
			Issued    int64  `json:"iat"`
			Expires   int64  `json:"exp"`
			Resources map[string]struct {
				Roles []string `json:"roles"`
			} `json:"resource_access"`
		}
		if token.Claims(&claims) != nil || claims.Issued <= 0 || claims.Expires-claims.Issued <= 0 ||
			claims.Expires-claims.Issued > 300 || claims.Issued > time.Now().Unix()+5 {
			http.Error(w, "invalid token claims", http.StatusUnauthorized)
			return
		}
		wanted := "payments:write"
		if r.Method == http.MethodGet || r.Method == http.MethodHead {
			wanted = "payments:read"
		}
		allowed := false
		for _, permission := range claims.Resources["payment-api"].Roles {
			allowed = allowed || permission == wanted
		}
		if !allowed || !strings.Contains(" "+claims.Scope+" ", " openid ") {
			http.Error(w, "payment permission required", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	}), nil
}
