"""Pure Flink SQL writer from payment processing events to the Iceberg lakehouse.

Runs as its own job (``payment-lakehouse-v1``) beside ``payment-event-time-v1``:
Iceberg commits once per checkpoint, so a slower interval bounds small files
without changing the payment job's graph. No Python UDFs run on TaskManagers.
Tables are created by the lakehouse-catalog module; this job only appends.
"""

import os

JOB_NAME = "payment-lakehouse-v1"
SOURCE_TOPIC = "payment-processing-events-v1"
CHECKPOINT_INTERVAL_MS = 60_000
DEDUP_TTL = "24 h"
UUID = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
RFC3339_UTC = "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?Z$"

CURATED_COLUMNS = (
    "event_id", "payment_id", "account_id", "event_type", "schema_version", "event_time",
    "amount_minor", "currency", "country", "payment_method", "outcome", "account_created_at",
    "device_id", "kafka_partition", "kafka_offset", "ingested_at",
)
BRONZE_COLUMNS = (
    "event_id", "event_type", "raw_payload", "kafka_partition", "kafka_offset",
    "kafka_timestamp", "ingested_at",
)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def options(values):
    return ", ".join(f"{literal(key)} = {literal(value)}" for key, value in values.items())


def text(field):
    return f"JSON_VALUE(raw_payload, '$.{field}')"


def utc_timestamp(expression):
    # The session time zone is UTC, so this interprets the RFC3339 'Z' value as an instant.
    return f"CAST(CAST(REPLACE(REPLACE({expression}, 'T', ' '), 'Z', '') AS TIMESTAMP(6)) AS TIMESTAMP_LTZ(6))"


def json_literal(field):
    # The array wrapper exposes the JSON token, so 1 and "1" differ like Python's type checks.
    return f"JSON_QUERY(raw_payload, '$.{field}' WITH ARRAY WRAPPER)"


def validation_predicate():
    """SQL mirror of processing.decode; invalid records remain only in bronze."""
    return " AND ".join((
        f"{text('event_type')} = 'payment.processing.v1'",
        f"{json_literal('schema_version')} = '[1]'",
        *(f"REGEXP({text(field)}, {literal(UUID)})" for field in ("event_id", "account_id", "payment_id")),
        f"REGEXP({text('event_time')}, {literal(RFC3339_UTC)})",
        f"REGEXP({json_literal('amount_minor')}, '^\\[[1-9][0-9]*\\]$')",
        f"TRY_CAST({text('amount_minor')} AS BIGINT) > 0",
        f"REGEXP({text('currency')}, '^[A-Z]{{3}}$')",
        f"REGEXP({text('country')}, '^[A-Z]{{2}}$')",
        f"{text('payment_method')} IN ('card', 'transfer', 'wallet')",
        f"{text('outcome')} IN ('authorized', 'rejected')",
        f"({text('account_created_at')} IS NULL OR (REGEXP({text('account_created_at')}, {literal(RFC3339_UTC)})"
        f" AND {utc_timestamp(text('account_created_at'))} <= {utc_timestamp(text('event_time'))}))",
        f"({text('device_id')} IS NULL OR REGEXP({text('device_id')}, {literal(UUID)}))",
    ))


def bronze_insert(source, target):
    columns = ", ".join(BRONZE_COLUMNS)
    return (
        f"INSERT INTO {target} ({columns}) SELECT {text('event_id')}, {text('event_type')}, raw_payload, "
        "kafka_partition, kafka_offset, CAST(kafka_timestamp AS TIMESTAMP_LTZ(6)), "
        f"CAST(CURRENT_TIMESTAMP AS TIMESTAMP_LTZ(6)) FROM {source}"
    )


def curated_insert(source, target):
    columns = ", ".join(CURATED_COLUMNS)
    projected = ", ".join((
        f"{text('event_id')} AS event_id", f"{text('payment_id')} AS payment_id",
        f"{text('account_id')} AS account_id", f"{text('event_type')} AS event_type",
        f"TRY_CAST({text('schema_version')} AS INT) AS schema_version",
        f"{utc_timestamp(text('event_time'))} AS event_time",
        f"TRY_CAST({text('amount_minor')} AS BIGINT) AS amount_minor",
        f"{text('currency')} AS currency", f"{text('country')} AS country",
        f"{text('payment_method')} AS payment_method", f"{text('outcome')} AS outcome",
        f"{utc_timestamp(text('account_created_at'))} AS account_created_at",
        f"{text('device_id')} AS device_id", "kafka_partition", "kafka_offset",
        "CAST(CURRENT_TIMESTAMP AS TIMESTAMP_LTZ(6)) AS ingested_at", "processed_at",
    ))
    # Keep-first-row on processing time is append-only, so the Iceberg table never needs deletes.
    return (
        f"INSERT INTO {target} ({columns}) SELECT {columns} FROM ("
        f"SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY processed_at ASC) AS occurrence "
        f"FROM (SELECT {projected} FROM {source} WHERE {validation_predicate()})"
        ") WHERE occurrence = 1"
    )


def kafka_source(properties):
    connector = {
        "connector": "kafka", "topic": SOURCE_TOPIC,
        "properties.group.id": JOB_NAME,
        # Without a savepoint, restart resumes from offsets committed at completed checkpoints.
        "scan.startup.mode": "group-offsets", "properties.auto.offset.reset": "earliest",
        "format": "raw",
        **{f"properties.{key}": value for key, value in properties.items() if key != "transaction.timeout.ms"},
    }
    return (
        "CREATE TEMPORARY TABLE processing_events (raw_payload STRING, "
        "kafka_partition INT METADATA FROM 'partition' VIRTUAL, "
        "kafka_offset BIGINT METADATA FROM 'offset' VIRTUAL, "
        "kafka_timestamp TIMESTAMP_LTZ(3) METADATA FROM 'timestamp' VIRTUAL, "
        f"processed_at AS PROCTIME()) WITH ({options(connector)})"
    )


def iceberg_catalog(environ):
    catalog = {
        "type": "iceberg", "catalog-type": "rest",
        "uri": environ["LAKEHOUSE_CATALOG_URI"], "warehouse": environ["LAKEHOUSE_WAREHOUSE"],
        "credential": f"{environ['LAKEHOUSE_CLIENT_ID']}:{environ['LAKEHOUSE_CLIENT_SECRET']}",
        "scope": "PRINCIPAL_ROLE:ALL",
        "oauth2-server-uri": environ["LAKEHOUSE_CATALOG_URI"].rstrip("/") + "/v1/oauth/tokens",
        "io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
        "s3.endpoint": environ["S3_ENDPOINT"], "s3.path-style-access": "true",
        "client.region": environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        "s3.access-key-id": environ["AWS_ACCESS_KEY_ID"],
        "s3.secret-access-key": environ["AWS_SECRET_ACCESS_KEY"],
    }
    return f"CREATE CATALOG lakehouse WITH ({options(catalog)})"


def build(environ=None):
    # Imported lazily so the SQL builders stay testable without a Flink runtime.
    from pyflink.datastream import CheckpointingMode, StreamExecutionEnvironment
    from pyflink.datastream.checkpoint_config import ExternalizedCheckpointCleanup
    from pyflink.datastream.checkpoint_storage import FileSystemCheckpointStorage
    from pyflink.table import StreamTableEnvironment

    from job import kafka_properties

    environ = os.environ if environ is None else environ
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(1)
    env.enable_checkpointing(CHECKPOINT_INTERVAL_MS, CheckpointingMode.EXACTLY_ONCE)
    checkpoints = env.get_checkpoint_config()
    checkpoints.set_checkpoint_storage(FileSystemCheckpointStorage(environ["LAKEHOUSE_CHECKPOINT_URI"]))
    checkpoints.set_min_pause_between_checkpoints(10_000)
    checkpoints.set_checkpoint_timeout(120_000)
    checkpoints.enable_externalized_checkpoints(ExternalizedCheckpointCleanup.RETAIN_ON_CANCELLATION)
    table_env = StreamTableEnvironment.create(env)
    config = table_env.get_config()
    config.set("pipeline.name", JOB_NAME)
    config.set("table.local-time-zone", "UTC")
    config.set("table.exec.state.ttl", DEDUP_TTL)
    # Statements carry SASL and catalog credentials; never log them.
    table_env.execute_sql(kafka_source(kafka_properties()))
    table_env.execute_sql(iceberg_catalog(environ))
    namespace = environ.get("LAKEHOUSE_NAMESPACE", "payments")
    statements = table_env.create_statement_set()
    statements.add_insert_sql(bronze_insert("processing_events", f"lakehouse.{namespace}.payment_events_raw"))
    statements.add_insert_sql(curated_insert("processing_events", f"lakehouse.{namespace}.payment_events"))
    return statements


if __name__ == "__main__":
    build().execute()
