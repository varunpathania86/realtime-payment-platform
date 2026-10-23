package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
)

func TestStaticServer(t *testing.T) {
	files := fstest.MapFS{
		"index.html":    {Data: []byte("<!doctype html><title>Portal</title>")},
		"assets/ui.js":  {Data: []byte("console.log('build');")},
		"assets/ui.css": {Data: []byte("body { color: black; }")},
	}
	for _, tc := range []struct {
		method, path string
		status       int
		body, kind   string
	}{
		{"GET", "/", 200, "<title>Portal</title>", "text/html"},
		{"GET", "/index.html", 200, "<title>Portal</title>", "text/html"},
		{"GET", "/dashboard", 200, "<title>Portal</title>", "text/html"},
		{"GET", "/assets/ui.js", 200, "console.log", "javascript"},
		{"GET", "/assets/ui.css", 200, "color: black", "text/css"},
		{"HEAD", "/", 200, "", "text/html"},
		{"GET", "/healthz", 200, `{"status":"ok"}`, "application/json"},
		{"GET", "/readyz", 200, `{"status":"ok"}`, "application/json"},
		{"POST", "/", 405, "method not allowed", "text/plain"},
		{"HEAD", "/healthz", 405, "method not allowed", "text/plain"},
		{"POST", "/readyz", 405, "method not allowed", "text/plain"},
		{"GET", "/assets/missing.js", 404, "404", "text/plain"},
		{"GET", "/assets/missing", 404, "404", "text/plain"},
		{"GET", "/missing.json", 404, "404", "text/plain"},
		{"GET", "/api/healthz", 404, "404", "text/plain"},
		{"GET", "/api", 404, "404", "text/plain"},
	} {
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
			w := httptest.NewRecorder()
			handler(files).ServeHTTP(w, httptest.NewRequest(tc.method, tc.path, nil))
			if w.Code != tc.status || !strings.Contains(w.Body.String(), tc.body) || !strings.Contains(w.Header().Get("Content-Type"), tc.kind) {
				t.Fatalf("got %d %v %q", w.Code, w.Header(), w.Body.String())
			}
			if tc.method == "HEAD" && tc.status == 200 && w.Body.Len() != 0 {
				t.Fatal("HEAD must not include a response body")
			}
			if w.Header().Get("Cache-Control") != "no-store" || w.Header().Get("X-Content-Type-Options") != "nosniff" {
				t.Fatal("missing cache or content type protections")
			}
			if tc.status == 405 && w.Header().Get("Allow") == "" {
				t.Fatal("missing Allow header")
			}
		})
	}
}

func TestMissingIndexAndTraversal(t *testing.T) {
	files := fstest.MapFS{"secret.txt": {Data: []byte("not outside the static root")}}
	for _, path := range []string{"/", "/dashboard", "/../../etc/passwd", "/assets/"} {
		w := httptest.NewRecorder()
		handler(files).ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusNotFound {
			t.Fatalf("%s: got %d, want 404", path, w.Code)
		}
	}
}

func TestServerBounds(t *testing.T) {
	s := newServer(":8080", fstest.MapFS{})
	if s.ReadHeaderTimeout <= 0 || s.ReadTimeout <= 0 || s.WriteTimeout <= 0 || s.IdleTimeout <= 0 || s.MaxHeaderBytes <= 0 {
		t.Fatal("all server limits must be bounded")
	}
}
