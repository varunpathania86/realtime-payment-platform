# Payment API

Minimal Stage 1 Go status API. Part of [realtime-payment-platform](../../README.md).

## Responsibilities

Serve liveness, readiness, and build metadata. There are no payment operations or
database dependencies in Stage 1; readiness reports that this process is ready.

## Layout

```text
.
├── Dockerfile
├── Makefile
├── README.md
├── go.mod
├── main.go
└── main_test.go
```

## Prerequisites

Run root `make setup` and `make doctor`; see [developer setup](../../docs/developer-setup.md).
Go and container build versions come from root `.tool-versions`.

## Quick start

```bash
make build
make test
./bin/payment-api
```

From the repository root, `make dev-up` deploys the local environment,
`make dev-update` rebuilds local images, and `make smoke` checks the routed endpoints.

## Make targets

| Target | Description |
|---|---|
| `build` | Build `bin/payment-api` |
| `test` | Run offline unit tests |
| `lint` | Run `go vet` and verify formatting |
| `fmt` | Format Go source |
| `clean` | Remove `bin/` |
| `help` | List targets |

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `HTTP_ADDR` | `:8080` | HTTP listen address |
| `VERSION` | `dev` | Make variable / image build argument |
| `COMMIT` | `unknown` | Make variable / image build argument |
| `GO_VERSION` | None | Required image build argument from root `.tool-versions` |

Only `GET` is accepted, including rejection of `HEAD` with `405` and `Allow: GET`.
Responses are JSON and are not cached:

- `/healthz`: `{"status":"ok"}`
- `/readyz`: `{"status":"ok"}`
- `/version`: `{"version":"dev","commit":"unknown"}`

The same handlers are available at `/api/healthz`, `/api/readyz`, and `/api/version`
for standard Ingress routing that preserves the prefix. Unknown paths return `404`,
including unknown `/api/...` paths.
The server bounds header/read/write/idle time and allows ten seconds for graceful
shutdown on `SIGTERM` or `SIGINT`. The static binary runs as non-root in scratch.

## Documentation

The Helm chart supports `image.digest` (takes precedence over `image.tag`).
Local Terraform uses loaded development tags with pull policy `Never`; OCI
publication packages a versioned copy referencing the verified GHCR image digest
with `IfNotPresent`. Source chart and stable catalog versions are not changed.
Run `make security` from this directory for a separate advisory Trivy IaC scan;
it is not part of the module's build/test/lint contract.

- [Master plan, Stage 1](../../docs/master-plan.md#stage-1-one-day-terraform-foundation)
- [Module development](../../docs/module-development.md)
- [Developer setup](../../docs/developer-setup.md)
