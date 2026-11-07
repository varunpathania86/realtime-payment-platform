# Runbook: Payment observability

See the [shared chart guide](../../platform/terraform/modules/observability/chart/README.md)
for resource ownership and the [identity runbook](unified-identity.md) for
Stage 11 Grafana SSO. Explicit Grafana client roles map to Viewer/Editor/Admin;
unknown roles fail closed. Identity-enabled single profiles reuse Grafana
without installing the multi-cluster telemetry collectors/backends.

- **Severity:** Medium
- **Owner:** Platform maintainers
- **Last reviewed:** 2026-11-02

## Symptoms

Grafana shows API errors, sustained receipt-consumer lag, or an aging outbox.
Prometheus evaluates the three provisioned rules after five sustained minutes.
Alertmanager groups alerts for local inspection; external notifications are not
configured. A local no-op receiver does not page an operator.

## Impact

Telemetry loss does not block payment processing. An outbox backlog delays
events; consumer lag delays receipt effects. API errors can prevent creation.
The shared monitoring cluster is not highly available.
Health probes without upstream context and idle SQL polls are not traced.
HTTP metrics still include probes. Tempo has a 768 MiB container limit and
640 MiB Go heap target; raise reviewed budgets before increasing local load.

## Diagnosis

Start `make dev-observability` (add `DEV_PROFILE=<base>` for another owned lab).
Sign in as `admin`; retrieve the generated local password without committing it:

```bash
kubectl --context rpp-a -n telemetry get secret grafana-admin \
  -o jsonpath='{.data.password}' | base64 --decode; echo
```

Open the **Payment execution** dashboard. Enable exemplars on API latency,
select a trace marker, open Tempo, then use **Logs for this span** to query Loki.
Alternatively search JSON logs by `trace_id` in Explore. Identifiers are log
fields, not Prometheus labels. Browser traces cover only fixed same-origin
status requests; payment payloads and arbitrary URLs are not collected.

### API errors

Check `rpp_http_requests_total` by status and the affected bounded route. Follow
an error trace to HTTP/SQL spans. Check database readiness and the
[database runbook](payment-database.md). Do not restart healthy replicas to hide
errors.

### Consumer lag

Check `kafka_consumer_group_lag_ratio`, consumer pod readiness and Kafka ISR metrics.
The pinned receiver declares lag with unit `1`, so Prometheus translation adds
`_ratio`; the value still counts **records**, not a percentage. A negative
partition value means no committed offset yet; it is displayed explicitly,
not reported as zero.
Confirm the consumer group has members. Follow
[event recovery](event-publication.md); do not reset offsets to skip effects.
Kafka metrics use the Collector's Franz client feature gate, verified against
the pinned Kafka 4 broker. They are collected once from cluster A using TLS/SCRAM, not once per
collector. PostgreSQL server metrics use CNPG's native port 9187 exporter.

### Outbox backlog

Check `rpp_outbox_pending`, `rpp_outbox_oldest_pending_age_seconds` and
`rpp_outbox_metrics_errors_total`. Backlog is observed every 15 seconds by each
relay; queries time out after two seconds. Failed observations are logged and
counted; the last successful gauges remain, so also check scrape health and
observation errors. Confirm Kafka and schema-registry availability before
requeueing dead rows using the event-recovery procedure.

## Mitigation

Restore the failing dependency. Exporters use bounded memory queues/timeouts;
monitoring outages can lose telemetry. Collector file offsets are ephemeral:
restarting a collector resumes at the current end, not durable log replay.
Backends use local PVCs with 24-hour Prometheus/Loki/Tempo retention, not backups.
Never delete payment namespaces, Kafka data, or CNPG state to repair monitoring.

Collectors/backend ingestion use internal services and private node-network
ports, without Internet exposure or production authentication. Only Grafana
has local authentication. Do not expose these NodePorts on a public host;
production transport/authentication and HA need separate reviewed hardening.

## Verification

Confirm all three collector pods are ready and cluster A's five backends are
ready. Generate a synthetic payment, inspect its API/outbox/producer/consumer
trace, query correlated JSON logs, and check metric exemplars. Confirm every
alert annotation points to this runbook. Use an isolated lab for deliberate
latency/dependency failures, never the shared development database.

## Prevention and follow-up

Keep instrumentation labels bounded and queue/resource budgets explicit.
Review sampling, retention, alert routing, durable collection, and backend HA
before a production rollout. Traces are not durable data lineage: identity
design follows in Stage 5 and persistent pipeline lineage in Stage 6.
