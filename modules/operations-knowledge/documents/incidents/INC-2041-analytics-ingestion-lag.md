# INC-2041: Realtime analytics ingestion lag

Synthetic incident record for the Stage 10 assistant. Classification: restricted.

## Summary

Dashboards showed payment facts up to 40 minutes behind the payment API for one
afternoon. No payment was lost; Kafka retained every event and the stream
processor caught up after remediation.

## Timeline

- 13:05 UTC: The freshness alert for `analytics.payment_facts` fired.
- 13:20 UTC: On-call confirmed ClickHouse insert latency above 30 seconds.
- 13:45 UTC: Disk usage on the analytics volume reached 87 percent and
  background merges were throttled.
- 14:10 UTC: The volume was expanded and merges recovered.
- 14:40 UTC: Freshness returned below one minute.

## Root cause

A synthetic load test wrote 30 days of window aggregates in one burst. The
ClickHouse volume crossed its merge-throttling threshold, so parts accumulated
and every HTTP insert from the stream processor slowed down.

## Remediation

- Expand the analytics volume and confirm `system.merges` drains.
- Cap load-test windows to one day per run.
- Alert at 75 percent analytics disk usage instead of 85 percent.

## Escalation

Escalation bridge code: AMBER-HERON-7731. Share it only with the analytics
on-call rotation.
