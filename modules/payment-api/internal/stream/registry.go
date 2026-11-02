package stream

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/varunpathania86/realtime-payment-platform/modules/payment-api/internal/event"
)

type Registry struct {
	base   string
	client *http.Client
}

func NewRegistry(base string) (*Registry, error) {
	u, err := url.Parse(base)
	if err != nil || u.Host == "" || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("REGISTRY_URL must be an HTTP(S) URL without credentials, query, or fragment")
	}
	return &Registry{base: strings.TrimRight(base, "/"), client: &http.Client{Timeout: 5 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

func (r *Registry) request(ctx context.Context, method, path string, body, result any) error {
	var data []byte
	var err error
	if body != nil {
		data, err = json.Marshal(body)
		if err != nil {
			return err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, r.base+path, bytes.NewReader(data))
	if err != nil {
		return errors.New("invalid registry request")
	}
	req.Header.Set("Content-Type", "application/vnd.schemaregistry.v1+json")
	response, err := r.client.Do(req)
	if err != nil {
		return errors.New("registry request failed")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("registry returned HTTP %d", response.StatusCode)
	}
	data, err = io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
	if err != nil || len(data) > 1<<20 {
		return errors.New("invalid registry response size")
	}
	if result == nil {
		return nil
	}
	if err := json.Unmarshal(data, result); err != nil {
		return errors.New("invalid registry JSON response")
	}
	return nil
}

func (r *Registry) Register(ctx context.Context, subject, schema string) (uint32, *event.Codec, error) {
	codec, err := event.NewCodec(schema)
	if err != nil {
		return 0, nil, err
	}
	path := url.PathEscape(subject)
	if err := r.request(ctx, http.MethodPut, "/config/"+path, map[string]string{"compatibility": "BACKWARD_TRANSITIVE"}, nil); err != nil {
		return 0, nil, err
	}
	var response struct {
		ID uint32 `json:"id"`
	}
	if err := r.request(ctx, http.MethodPost, "/subjects/"+path+"/versions", map[string]string{"schemaType": "AVRO", "schema": schema}, &response); err != nil {
		return 0, nil, err
	}
	if response.ID == 0 {
		return 0, nil, errors.New("registry returned missing schema ID")
	}
	return response.ID, codec, nil
}

func (r *Registry) Codec(ctx context.Context, id uint32) (*event.Codec, error) {
	var response struct {
		Schema string `json:"schema"`
	}
	if err := r.request(ctx, http.MethodGet, fmt.Sprintf("/schemas/ids/%d", id), nil, &response); err != nil {
		return nil, err
	}
	return event.NewCodec(response.Schema)
}
