# ADR-033: Optional in-cluster DBX development workbench

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** varunpathania86

## Context

The local platform needs a browser-based database workbench that can use
in-cluster Services and existing Kubernetes Secret credentials without copying
them into developer-managed connection files. The selected DBX Web release
provides login protection and a connection list/save API, but upstream labels
those endpoints internal and unstable rather than a public integration
contract. The official image also defaults to root and writes server data and
backups to paths that must be made writable.

## Decision

Offer DBX as an opt-in shared Terraform-managed local workload, not a product
module. Pin the Apache-2.0 v0.6.67 image by OCI index digest and couple a
small tested provisioning adapter to its exact 0.6.67 connection API. The
adapter uses only the authenticated `/api/auth/login`, `/api/connection/save`
and `/api/connection/list` routes; it never edits DBX's private storage.

The adapter synchronizes supported connection profiles from existing
Kubernetes Secrets, uses stable IDs and upserts without deleting other
profiles. Run DBX non-root with read-only root filesystem, a persistent data
volume, `DBX_DISABLE_PASSWORD_VIEWER=true`, and no externally routed Service.
Expose the UI only through an explicit loopback port-forward. Do not configure
DBX cloud sync, remote AI providers or agent integrations.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Optional in-cluster DBX with a version-pinned API adapter | Browser UI reaches internal Services; existing credentials stay server-side; Terraform owns lifecycle | Upstream API is unstable; upgrades require explicit adapter contract and live acceptance |
| Native desktop clients through `access-local` | No added workload; uses supported database protocols | Separate client connections, drivers and certificate setup; less integrated browser experience |
| Write DBX's SQLite or private files directly | Could avoid HTTP calls | Private storage schema and encryption are unsupported; unsafe and upgrade-fragile; rejected |
| Wait for a public provisioning API | Stable supported integration boundary | No suitable public API exists in the pinned DBX release |

## Consequences

- The adapter's version pin, deterministic contract tests and live connection
  verification are mandatory upgrade gates. A DBX image upgrade must fail
  closed until these are updated and verified.
- The generated DBX password is one shared local workbench login, not Keycloak
  SSO. There is no OIDC integration in this deployment.
- The feature is opt-in and adds a 512 MiB cluster-A memory budget. The browser
  port-forward binds to loopback; a Kubernetes Service remains ClusterIP.
- Saved connections use existing source Secrets: PostgreSQL and Kafka always,
  ClickHouse when analytics is enabled, and Trino when the lakehouse is
  enabled. The adapter marks profiles read-only and exposes no secret values
  through setup or logs.
- DBX does not receive remote sync or AI configuration. Developers must not
  configure cloud sync or submit payment data to hosted providers.
- Kafka and Trino require server-side driver agents absent from the upstream
  web image. Pin and checksum-verify those artifacts and their Java runtime;
  register them through the version-coupled runtime API, not private storage
  writes. Fresh non-root bootstrap and real metadata/query operations are
  acceptance checks, not merely saved profile presence. MinIO remains a
  separate console because this DBX version has no S3 connector.
- Existing native access forwards and Kafka desktop listeners remain
  independent optional paths.
