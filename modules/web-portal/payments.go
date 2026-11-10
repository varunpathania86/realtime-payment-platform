package main

import (
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"time"
)

func (p *portal) payments(w http.ResponseWriter, r *http.Request) {
	s, ok := p.oidcCurrent(r)
	if !ok {
		http.Error(w, "not signed in", http.StatusUnauthorized)
		return
	}
	wanted := "payments:write"
	if r.Method == http.MethodGet || r.Method == http.MethodHead {
		wanted = "payments:read"
	} else if !p.origins[r.Header.Get("Origin")] {
		http.Error(w, "origin not allowed", http.StatusForbidden)
		return
	}
	if !has(s.user.Permissions, wanted) {
		http.Error(w, "payment permission required", http.StatusForbidden)
		return
	}
	token, err := p.oidc.resourceToken(r.Context(), s, "payment-api")
	if err != nil {
		http.Error(w, "payment identity unavailable", http.StatusBadGateway)
		return
	}
	endpoint := os.Getenv("PAYMENT_API_URL")
	if endpoint == "" {
		endpoint = "http://payment-api"
	}
	target, err := url.Parse(endpoint)
	if err != nil || target.Host == "" {
		http.Error(w, "payment service unavailable", http.StatusBadGateway)
		return
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.Transport = &http.Transport{
		DialContext:           (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
		ResponseHeaderTimeout: 5 * time.Second,
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, _ error) {
		http.Error(w, "payment service unavailable", http.StatusBadGateway)
	}
	original := proxy.Director
	proxy.Director = func(req *http.Request) {
		original(req)
		req.Header.Del("Cookie")
		for key := range req.Header {
			if strings.HasPrefix(strings.ToLower(key), "x-identity") {
				req.Header.Del(key)
			}
		}
		req.Header.Set("Authorization", "Bearer "+token)
	}
	proxy.ServeHTTP(w, r)
}
