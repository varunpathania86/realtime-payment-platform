package httpapi

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/getkin/kin-openapi/openapi3"
	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/payment"
)

func TestOpenAPIContract(t *testing.T) {
	document, err := openapi3.NewLoader().LoadFromFile("../../openapi.yaml")
	if err != nil {
		t.Fatal(err)
	}
	if err := document.Validate(context.Background()); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		method, path, contractPath, payload, key, status string
		storeError                                       error
	}{
		{method: "GET", path: "/healthz", contractPath: "/healthz", status: "200"},
		{method: "GET", path: "/readyz", contractPath: "/readyz", status: "200"},
		{method: "GET", path: "/version", contractPath: "/version", status: "200"},
		{method: "POST", path: "/payments", contractPath: "/payments", payload: body, key: "key", status: "201"},
		{method: "POST", path: "/payments", contractPath: "/payments", payload: "{}", key: "key", status: "400"},
		{method: "POST", path: "/payments", contractPath: "/payments", payload: body, key: "key", status: "409", storeError: payment.ErrConflict},
		{method: "GET", path: "/payments/" + account, contractPath: "/payments/{id}", status: "404", storeError: payment.ErrNotFound},
	} {
		t.Run(test.method+test.path+test.status, func(t *testing.T) {
			response := request(&fakeStore{err: test.storeError}, test.method, test.path, test.payload, test.key)
			var value any
			if err := json.Unmarshal(response.Body.Bytes(), &value); err != nil {
				t.Fatal(err)
			}
			operation := document.Paths.Value(test.contractPath).GetOperation(test.method)
			schema := operation.Responses.Value(test.status).Value.Content["application/json"].Schema.Value
			if err := schema.VisitJSON(value); err != nil {
				t.Fatalf("response violates OpenAPI: %v", err)
			}
		})
	}
}
