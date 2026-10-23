# Web portal

Accessible Stage 1 React platform-status page. Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Display explicit loading, success, and failure states for API health, readiness,
and build metadata, with a retry action. Show the UI build version and commit so
a local rebuild is visible. No payment operations or identity integration yet.

## Layout

```text
.
├── Dockerfile
├── Makefile
├── README.md
├── main.go             # Standard-library static server
├── main_test.go
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

The browser fetches same-origin `/api/healthz`, `/api/readyz`, and `/api/version`.
Ingress preserves `/api`; the payment API accepts that prefix. No cross-origin
configuration or proxy is needed. Requests are uncached and time out after ten seconds.
Retry re-fetches all three endpoints.

The non-root scratch image serves the SPA and `GET /healthz`, `GET /readyz` JSON
probes on port 8080. Missing assets and misrouted API requests return `404`;
extensionless UI paths fall back to `index.html`. Responses are uncached so
rebuilds appear on reload. The static server uses bounded timeouts and graceful
shutdown, without another web-server dependency. UI version and commit are
compile-time public metadata, not runtime configuration or secrets.

## Documentation

- [Master plan, Stage 1](../../docs/master-plan.md#stage-1-one-day-terraform-foundation)
- [Module development](../../docs/module-development.md)
- [Developer setup](../../docs/developer-setup.md)
