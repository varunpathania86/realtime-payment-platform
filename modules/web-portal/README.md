# Web portal

Accessible React platform-status page and, from Stage 9, the embedded payment
operations dashboard and (Stage 10) the operations assistant. Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Display explicit loading, success, and failure states for API health, readiness,
and build metadata, with a retry action. Show the UI build version and commit so
a local rebuild is visible.

When BI is enabled, the `/analytics` page signs in a synthetic local development
user and embeds the [Superset dashboard](../bi-dashboards/README.md). The Go
server is the backend-for-frontend: it keeps the session and requests a
five-minute guest token scoped to one dashboard. React never receives Superset
or database credentials. Keycloak replaces this local identity in Stage 11
([ADR-025](../../docs/adr/ADR-025-interview-roadmap-and-read-only-mcp.md),
[ADR-028](../../docs/adr/ADR-028-embedded-superset-bi.md)).

When the assistant is enabled, signed-in users with `ai:invoke` also see the
operations assistant on `/analytics`. The Go server is its only caller: for
each question it signs two 120-second Ed25519 assertions, one for
[ai-orchestrator](../ai-orchestrator/README.md) and one the orchestrator relays
to [assistant-tools](../assistant-tools/README.md), and forwards only the user's
assistant permissions
([ADR-030](../../docs/adr/ADR-030-assistant-identity-assertions.md)). React never
sees the assertions or the signing key.

## Layout

See the [Helm chart guide](chart/README.md) for template purposes and how browser
traffic reaches the portal and API.

```text
.
├── Dockerfile
├── Makefile
├── README.md
├── main.go             # Standard-library static server
├── bi.go               # Local development sign-in and Superset guest tokens
├── assistant.go        # Assistant BFF: assertion signing and orchestrator proxy
├── *_test.go
├── package.json
├── package-lock.json
├── src/                # React UI and typed status client
├── tests/              # Offline Node unit tests
└── tsconfig*.json
```

## Prerequisites

Run root `make setup` and `make doctor`; see [developer setup](../../docs/developer-setup.md).
Node.js and Go versions come from root `.tool-versions`. Make installs the exact
locked npm dependencies when needed; no additional global frontend tooling is used.

## Quick start

```bash
make build
make test
./bin/web-portal
```

Open `http://localhost:8080`. The static server does not proxy the API, so standalone
execution shows a retryable error until ingress routes `/api` to the payment API.
From the repository root, `make dev-up` deploys the environment, `make dev-update`
rebuilds the local images, and `make smoke` checks the routed endpoints.
New development bases run the stack in three independent clusters. The chart's
positive integer `replicaCount` defaults to one; use per-cluster
`applications.web-portal` in `DEV_CONFIG` to change it independently of the API.
See [topology sizing](../../docs/developer-setup.md#configuring-counts-and-benchmark-node-budgets).
Use `MINIKUBE_PROFILE=<base>-b` with `make dev-access`/`make smoke` to select
another member; lifecycle commands select the whole base using `DEV_PROFILE`.

## Make targets

| Target | Description |
|---|---|
| `build` | Type-check and build `dist/` and `bin/web-portal` |
| `test` | Compile and run offline Node tests and Go unit tests |
| `lint` | Type-check application/tests, run `go vet`, verify Go formatting |
| `fmt` | Format Go source; frontend uses shared editor settings |
| `clean` | Remove `dist/`, `.test-dist/`, and `bin/` |
| `help` | List targets |

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `HTTP_ADDR` | `:8080` | Static server listen address |
| `STATIC_DIR` | `dist` | Directory containing compiled `index.html` |
| `VERSION` | `dev` | Make variable / image argument; sets `VITE_APP_VERSION` |
| `COMMIT` | `unknown` | Make variable / image argument; sets `VITE_APP_COMMIT` |
| `NODE_VERSION` | None | Required image build argument from root `.tool-versions` |
| `GO_VERSION` | None | Required image build argument from root `.tool-versions` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Unset | Optional Collector HTTP(S) origin; enables server/browser telemetry |
| `OTEL_CLUSTER_NAME` | Unset | Cluster resource identity |
| `PORTAL_SESSION_KEY` | Unset | HMAC key for the session cookie (at least 32 characters); with `PORTAL_DEV_USERS` enables `/auth/*` |
| `PORTAL_DEV_USERS` | Unset | JSON map of synthetic users: `{"name": {"password": "...", "permissions": ["bi:view"]}}` (passwords at least 12 characters) |
| `PORTAL_ALLOWED_ORIGINS` | Unset | Space-separated browser origins accepted on POST requests |
| `BI_SUPERSET_URL` | Unset | Cluster-internal Superset URL; enables `/bi/guest-token` (all `BI_*` then required) |
| `BI_SUPERSET_PUBLIC_URL` | Unset | Superset URL the browser loads the iframe from |
| `BI_DASHBOARD_ID` | Unset | Embedded dashboard UUID |
| `BI_TOKEN_ISSUER_USER` / `BI_TOKEN_ISSUER_PASSWORD` | Unset | Superset account allowed only to grant guest tokens |
| `AI_ORCHESTRATOR_URL` | Unset | Cluster-internal orchestrator URL; with the key file enables `/assistant/ask` (both required) |
| `AI_ASSERTION_PRIVATE_KEY_FILE` | Unset | PKCS#8 PEM Ed25519 private key that signs assistant assertions |

### Stage 4 telemetry

The Go server emits structured JSON logs with trace/span correlation and
privacy-safe server spans. The browser uses standard OTel fetch instrumentation
for only the three fixed status URLs (no queries or cross-origin requests) and
a custom `portal.status.refresh` interaction span. A bounded two-second runtime
configuration lookup enables telemetry only when the server exporter is set.
Failures are reported without blocking status operations.

Browser OTLP goes to the same-origin `POST /telemetry/v1/traces` proxy, not a
public Collector. It requires a matching Origin and JSON, caps requests at
64 KiB and five per second per process, strips browser credentials, refuses
redirects and bounds Collector calls to two seconds. `/telemetry/config` exposes
only an enabled flag, never the Collector address. Payment bodies, raw payment
URLs and credentials are not instrumented.
See [shared observability](../../docs/runbooks/observability.md).

The browser fetches same-origin `/api/healthz`, `/api/readyz`, and `/api/version`.
Ingress preserves `/api`; the payment API accepts that prefix. No cross-origin
configuration or proxy is needed. Requests are uncached and time out after ten seconds.
Retry re-fetches all three endpoints.

### Stage 9 sign-in and embedded BI

`POST /auth/login` (JSON, at most 4 KiB) checks the synthetic users and sets
an 8-hour HMAC-signed `rpp_portal_session` cookie (HttpOnly, `SameSite=Strict`).
`GET /auth/session` returns the user, its permissions and, for `bi:view`, the
dashboard UUID and Superset URL. `POST /auth/logout` clears the cookie.
`POST /bi/guest-token` returns 401 without a session, 403 without `bi:view`,
404 when BI is not configured and 502 if Superset is unavailable. Every POST
requires an allowed `Origin`. Permissions are re-read on every request, so
removing a user from the Secret revokes access after a restart. The endpoints
return 404 when sign-in is not configured.

### Stage 10 operations assistant

`POST /assistant/ask` takes `{"question": "..."}` (1–1000 characters, JSON body
at most 4 KiB) and returns the orchestrator's response unchanged: `status`,
`answer`, `citations`, `tools`, `request_id` and `latency_ms`. It returns 401
without a session, 403 without `ai:invoke`, 404 when the assistant is not
configured, 429 when two questions are already in flight, 502 if the
orchestrator fails and 504 after 210 seconds. The route extends the server's
10-second write deadline because local CPU inference is slow. The portal logs
the request ID, user, upstream status and duration, never the question or the
answer. `GET /auth/session` reports `"assistant": true` only when the route is
configured and the user has `ai:invoke`. React renders the answer, citations
and tool statuses as text. See the
[assistant runbook](../../docs/runbooks/ai-assistant.md).

The non-root scratch image serves the SPA and `GET /healthz`, `GET /readyz` JSON
probes on port 8080. Missing assets and misrouted API requests return `404`;
extensionless UI paths fall back to `index.html`. Responses are uncached so
rebuilds appear on reload. The static server uses bounded timeouts and graceful
shutdown, without another web-server dependency. UI version and commit are
compile-time public metadata, not runtime configuration or secrets.

## Documentation

The Helm chart supports `image.digest` (takes precedence over `image.tag`).
Local Terraform uses loaded development tags with pull policy `Never`; OCI
publication packages a versioned copy referencing the verified GHCR image digest
with `IfNotPresent`. Source chart and stable catalog versions are not changed.
Run `make security` from this directory for a separate advisory Trivy IaC scan;
it is not part of the module's build/test/lint contract.
The module-owned `.trivyignore.yaml` records exact-file exceptions for
Kubernetes-managed health, release-supplied namespaces, and intentional local
images. Rationales and expiry dates appear in the report; see the
[triage record](../../docs/runbooks/security-checks.md#mitigation).

- [Master plan, Stage 1](../../docs/master-plan.md#stage-1-one-day-terraform-foundation)
- [Module development](../../docs/module-development.md)
- [Developer setup](../../docs/developer-setup.md)
