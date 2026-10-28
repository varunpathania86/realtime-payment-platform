package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
)

func TestEndpoints(t *testing.T) {
	for path, want := range map[string]map[string]string{
		"/healthz":     {"status": "ok"},
		"/readyz":      {"status": "ok"},
		"/version":     {"version": version, "commit": commit},
		"/api/healthz": {"status": "ok"},
		"/api/readyz":  {"status": "ok"},
		"/api/version": {"version": version, "commit": commit},
	} {
		t.Run(path, func(t *testing.T) {
			w := httptest.NewRecorder()
			handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
			if w.Code != http.StatusOK || w.Header().Get("Content-Type") != "application/json" {
				t.Fatalf("unexpected response: %d %v", w.Code, w.Header())
			}
			var got map[string]string
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("got %v, want %v", got, want)
			}
			if w.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("responses must not be cached")
			}
		})
	}
}

func TestStrictMethodsAndPaths(t *testing.T) {
	for _, path := range []string{"/healthz", "/readyz", "/version", "/api/healthz", "/api/readyz", "/api/version"} {
		for _, method := range []string{"HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"} {
			w := httptest.NewRecorder()
			handler().ServeHTTP(w, httptest.NewRequest(method, path, nil))
			if w.Code != http.StatusMethodNotAllowed || w.Header().Get("Allow") != "GET" {
				t.Fatalf("%s %s: got %d %v", method, path, w.Code, w.Header())
			}
		}
	}
	for _, path := range []string{"/", "/payments", "/healthz/", "/version/extra", "/api", "/api/", "/api/missing", "/api/healthz/"} {
		w := httptest.NewRecorder()
		handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusNotFound {
			t.Fatalf("%s: got %d, want 404", path, w.Code)
		}
	}
}

func TestServerBounds(t *testing.T) {
	s := newServer(":8080")
	if s.ReadHeaderTimeout <= 0 || s.ReadTimeout <= 0 || s.WriteTimeout <= 0 || s.IdleTimeout <= 0 || s.MaxHeaderBytes <= 0 {
		t.Fatal("all server limits must be bounded")
	}
}
