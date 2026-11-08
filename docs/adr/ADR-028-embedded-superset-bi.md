# ADR-028: Embedded Superset BI behind a portal-issued guest token

- **Status:** Accepted
- **Date:** 2026-11-05
- **Deciders:** varunpathania86

## Context

Stage 9 adds business dashboards to the React portal. The dashboards read
current operational data from ClickHouse and Iceberg history through Trino.
The exit criterion is strict: a viewer reaches only the intended dashboard, and
the browser can't call privileged Superset APIs or see database credentials.

Several forces shape the design:

- Keycloak arrives in Stage 11. Until then
  [ADR-025](ADR-025-interview-roadmap-and-read-only-mcp.md) allows an explicit,
  non-production local development identity.
- Superset 6.1.0 guest tokens authorize a dashboard's charts. However, chart-data
  requests built from the embedded frontend can carry an ad-hoc query context.
  Out of the box, Superset checks such requests only against the dashboard's
  datasources, not against its saved charts.
- The ClickHouse risk-prediction and late-event tables need a writer. The
  existing Flink job already inserts window aggregates and payment facts into
  ClickHouse over HTTP.
- The laptop has about 15.5 GiB RAM, so every extra component has to justify
  its memory.

## Decision

1. **The portal is the only token issuer.** The Go web-portal BFF signs in
    synthetic local users from a Kubernetes Secret. It keeps an HMAC-signed,
    HttpOnly, `SameSite=Strict` session cookie and checks the `Origin` header on
    every POST. For users with `bi:view`, it requests a short-lived guest token
    for exactly one embedded dashboard, using a dedicated Superset issuer
    account. React receives only that token and the dashboard UUID.
2. **Superset is hardened for embedded use.** A custom security manager does
    the following:
    - Limits guest chart-data requests to saved charts on the embedded dashboard
      and to their stored query context.
    - Denies SQL Lab, free-form SQL and other privileged APIs to the guest role.
    - Gives the issuer account only the `grant_guest_token` permission.

    Explicit Content Security Policy `frame-ancestors` and the
    embedded-dashboard allow-list both use the configured portal origins.
    Database connections are read-only: the SELECT-only `rpp_bi_reader`
    ClickHouse user, and the `lakehouse` Trino catalog without write access.
3. **Assets are code.** A bootstrap Job imports the databases, datasets, charts
    and the dashboard with stable UUIDs, and stores each chart's query context.
    A captured frontend-payload fixture keeps those query contexts aligned with
    what the embedded SDK actually sends.
4. **Flink writes the new tables (Option A).** The existing ClickHouse HTTP
    sink also inserts advisory risk predictions and late events. No new
    ClickHouse Kafka engine or extra consumer is added.
5. **There is no Redis or Celery.** Dashboards run synchronous queries on one
    Gunicorn replica. The chart refuses more than one replica, because sessions
    and caches are not shared.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Portal-issued guest token (chosen) | Supported embedded SDK; no Superset credentials in React; one-dashboard scope | Requires guest-path hardening in Superset |
| Superset login inside the iframe | No BFF code | Users get full Superset sessions and privileged APIs |
| Custom React charts over a portal API | Full control | Re-implements BI; doesn't demonstrate Superset |
| ClickHouse Kafka engine for risk/late events (Option B) | Decouples ClickHouse from Flink | A second consumer path, offsets to operate and duplicate parsing logic |
| Redis and Celery workers | Async queries, caching and reports | About 0.5–1 GiB more memory and no Stage 9 requirement for them |

## Consequences

- Viewers see one dashboard. Users without `bi:view` get 403, and the browser
  never holds the issuer password, Superset secret key or database credentials.
- The local identity is labelled non-production. Stage 11 replaces it with
  Keycloak token validation and doesn't extend it.
- Changing a chart means updating the stored query context and re-capturing
  the fixture (see the [runbook](../runbooks/bi-dashboards.md)).
- BI requires analytics and adds about 1 GiB of limits on cluster 1. The
  Iceberg history chart appears only when the lakehouse is enabled. Trino has
  no authentication until Stage 11.
- Row-level security waits for a concrete tenant boundary.
