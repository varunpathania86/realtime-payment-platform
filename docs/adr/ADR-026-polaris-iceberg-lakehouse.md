# ADR-026: Apache Polaris Iceberg lakehouse with a separate Flink SQL writer

- **Status:** Accepted
- **Date:** 2026-11-04
- **Deciders:** varunpathania86

## Context

Stage 8 adds durable, replayable payment history beside ClickHouse's fast
operational aggregates. It needs an Iceberg REST catalog, a streaming writer,
SQL access, schema and partition evolution, time travel and table maintenance.
These must fit the existing local stack: PostgreSQL, MinIO, a Flink 1.20
session cluster that already runs the stateful `payment-event-time-v1` job,
and a resource-constrained single-cluster developer host.

A catalog is not itself an OLAP or OLTP database. It stores table names and
the current metadata-file pointer for each table, and it swaps that pointer
atomically on every commit. Query engines read the table data directly from
object storage.

A local prototype verified this design: Polaris 1.8.0, Iceberg 1.12.0,
Flink 1.20.3 and Trino 484.

## Decision

- **Catalog:** use Apache Polaris (`lakehouse-catalog` module).
  - It persists to the existing local PostgreSQL in a dedicated `polaris`
    role, database and `polaris_schema`.
  - Terraform creates these as the database owner.
  - An init container bootstraps the realm idempotently.
  - A post-install hook converges the catalog, namespace, tables, principals
    and grants. Converging means it creates whatever is missing and leaves
    existing objects unchanged.
- **Catalog RBAC:** separate catalog roles enforce three principals.
  - `flink-lakehouse-writer` may read and write table data.
  - `trino-lakehouse-reader` may only read.
  - `lakehouse-maintainer` manages catalog content for evolution and
    maintenance.
  - The writer has no table-creation privilege, because the setup hook owns
    table definitions. This mirrors the ClickHouse schema hook.
- **Tables:** two tables, because their quality guarantees differ.
  - Bronze `payments.payment_events_raw` stores every record as received,
    including invalid ones, with Kafka partition, offset and timestamp. It is
    partitioned by `day(kafka_timestamp)`.
  - Curated `payments.payment_events` stores records validated with the same
    rules as `processing.decode`. Duplicate `event_id` values are removed by
    keeping the first one seen within 24 hours. It is partitioned by
    `day(event_time)` and is append-only.
  - Both use Iceberg hidden partitioning. This is defined through the REST
    API, because Flink DDL supports only identity partitions.
- **Writer:** a separate pure-SQL Flink job, `payment-lakehouse-v1`, in the
  same session cluster.
  - It has no Python UDFs.
  - It uses a 60-second checkpoint interval. Iceberg commits once per
    checkpoint, so this bounds small files independently of the payment job's
    10-second interval.
  - The payment job's operator graph and savepoint compatibility are
    unchanged.
  - The TaskManager gains a second slot and 512 MiB when the lakehouse is
    enabled.
- **SQL engine:** Trino (`lakehouse-query` module), with two Iceberg REST
  catalogs.
  - `lakehouse` is read-only (`iceberg.security=read_only`) and uses the
    reader principal.
  - `lakehouse_admin` uses the maintainer principal for `ALTER TABLE`,
    `optimize`, `expire_snapshots` and `remove_orphan_files`.
- **Feature gating:** an opt-in `lakehouse` feature requires `streaming`.
  - It supports the profile-local PostgreSQL only. The CNPG full-stack lab is
    rejected by a Terraform precondition.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Polaris REST catalog (chosen) | Apache project, the basis of Snowflake Open Catalog, catalog-enforced RBAC, PostgreSQL persistence | JVM service of about 400 MiB; the admin tool is a separate image |
| Lakekeeper | Small Rust binary | Less widely recognized; same REST protocol, so it gives no functional gain here |
| Iceberg JDBC or Hadoop catalog | No extra service | No REST protocol, no catalog RBAC, ties every engine to database credentials |
| Add Iceberg sink to `payment-event-time-v1` | One job | 10-second commits create small files; failures couple to payment windows; savepoint graph changes |
| Upsert curated table with equality deletes | Latest state per key | Delete files conflict with compaction and add read cost; append-only facts suffice |
| Flink creates tables | Fewer setup steps | Identity partitions only; the writer would need table-creation privilege |

## Consequences

- Delivery is at least once in two known windows.
  - After a restart without a savepoint, the job resumes from committed Kafka
    group offsets and curated deduplication state is lost.
  - Between an Iceberg commit and the Kafka offset commit, a crash can replay
    records.
  - Restoring from the stop-with-savepoint URI preserves exactly-once commits.
  - Bronze keeps Kafka coordinates, so duplicates can be found.
- Flink, Polaris and Trino share the profile's MinIO streaming credential.
  Polaris RBAC separates catalog operations, but object-store access is not
  yet scoped per engine. Stage 16 adds scoped identities and credential
  vending.
- Flink needs shaded `hadoop-client-api` and `hadoop-client-runtime` JARs
  even with the REST catalog and S3FileIO. All four JARs are pinned and
  checksum-verified.
- Trino rejects snapshot expiry and orphan-file removal below seven days
  unless a session property lowers the minimum. Demonstration commands set it
  explicitly.
- Follow-up work:
  - Stage 9 Superset connects to the read-only Trino catalog.
  - Stage 10 MCP tools may query curated history through the reader.
  - Keycloak (Stage 11) replaces static principal credentials.
