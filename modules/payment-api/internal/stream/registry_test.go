package stream

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestRegistryUsesCompatibleSubjectAndSchemaID(t *testing.T) {
	schema, err := os.ReadFile("../../../event-contracts/schemas/payment-initiated-v1.avsc")
	if err != nil {
		t.Fatal(err)
	}
	paths := []string{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.Method+" "+r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodPut {
			_, _ = w.Write([]byte(`{"compatibility":"BACKWARD_TRANSITIVE"}`))
			return
		}
		_, _ = w.Write([]byte(`{"id":42}`))
	}))
	defer server.Close()
	registry, err := NewRegistry(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	id, codec, err := registry.Register(context.Background(), "payments.initiated.v1-value", string(schema))
	if err != nil || id != 42 || codec == nil {
		t.Fatalf("registration: %d %v", id, err)
	}
	if len(paths) != 2 || paths[0] != "PUT /config/payments.initiated.v1-value" || paths[1] != "POST /subjects/payments.initiated.v1-value/versions" {
		t.Fatalf("wrong registry API: %v", paths)
	}
}

func TestRegistryRejectsFailuresAndRedirects(t *testing.T) {
	for _, status := range []int{302, 401, 409, 500} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status) }))
			defer server.Close()
			registry, err := NewRegistry(server.URL)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := registry.Codec(context.Background(), 42); err == nil {
				t.Fatal("registry failure hidden")
			}
		})
	}
	for _, base := range []string{"", "file:///tmp/schema", "http://user:password@registry", "http://registry?key=secret"} {
		if _, err := NewRegistry(base); err == nil {
			t.Fatalf("invalid registry URL %q", base)
		}
	}
}
