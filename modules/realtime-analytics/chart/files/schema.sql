CREATE TABLE IF NOT EXISTS analytics.payment_facts
(
    event_time_ms Int64,
    event_time DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(event_time_ms),
    event_day Date MATERIALIZED toDate(event_time),
    event_id UUID,
    payment_id UUID,
    account_id UUID,
    amount_minor UInt64,
    currency FixedString(3),
    country FixedString(2),
    payment_method LowCardinality(String),
    outcome Enum8('authorized' = 1, 'rejected' = 2),
    schema_version UInt16,
    job_name LowCardinality(String),
    job_version LowCardinality(String),
    transformation_id LowCardinality(String),
    source_dataset LowCardinality(String),
    output_dataset LowCardinality(String),
    processed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(processed_at)
PARTITION BY toYYYYMM(event_day)
ORDER BY (event_day, currency, country, payment_method, event_time, event_id)
TTL event_time + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS analytics.payment_window_aggregates
(
    result_id FixedString(64),
    window_start_ms Int64,
    window_start DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(window_start_ms),
    window_day Date MATERIALIZED toDate(window_start),
    window_end_ms Int64,
    window_end DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(window_end_ms),
    currency FixedString(3),
    country FixedString(2),
    payment_method LowCardinality(String),
    payments UInt64,
    authorized UInt64,
    authorized_amount_minor UInt64,
    authorization_rate Float64,
    rejections UInt64,
    revision UInt32,
    schema_version UInt16,
    job_name LowCardinality(String),
    job_version LowCardinality(String),
    transformation_id LowCardinality(String),
    input_dataset LowCardinality(String),
    output_dataset LowCardinality(String),
    analytics_dataset LowCardinality(String)
)
ENGINE = ReplacingMergeTree(revision)
PARTITION BY toYYYYMM(window_day)
ORDER BY (window_day, currency, country, payment_method, window_start, result_id)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS analytics.payment_late_events
(
    event_time_ms Int64,
    event_time DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(event_time_ms),
    event_day Date MATERIALIZED toDate(event_time),
    event_id UUID,
    payment_id UUID,
    amount_minor UInt64,
    currency FixedString(3),
    country FixedString(2),
    payment_method LowCardinality(String),
    outcome Enum8('authorized' = 1, 'rejected' = 2),
    reason LowCardinality(String),
    watermark_ms Int64,
    watermark DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(watermark_ms),
    job_name LowCardinality(String),
    source_dataset LowCardinality(String),
    output_dataset LowCardinality(String),
    recorded_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(recorded_at)
PARTITION BY toYYYYMM(event_day)
ORDER BY (event_day, reason, event_id)
TTL event_time + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS analytics.payment_risk_predictions
(
    prediction_id FixedString(64),
    original_prediction_id FixedString(64),
    event_id UUID,
    payment_id UUID,
    event_time_ms Int64,
    event_time DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(event_time_ms),
    event_day Date MATERIALIZED toDate(event_time),
    window_revision UInt32,
    feature_version LowCardinality(String),
    model_name LowCardinality(String),
    model_version LowCardinality(String),
    model_sha256 FixedString(64),
    risk_score Float64,
    reason_codes Array(LowCardinality(String)),
    advisory_only Bool,
    prediction_time_ms Int64,
    prediction_time DateTime64(3, 'UTC') MATERIALIZED fromUnixTimestamp64Milli(prediction_time_ms),
    schema_version UInt16,
    source_dataset LowCardinality(String),
    output_dataset LowCardinality(String)
)
-- Latest revision per payment event and model version; revisions get new prediction IDs.
ENGINE = ReplacingMergeTree(window_revision)
PARTITION BY toYYYYMM(event_day)
ORDER BY (event_day, model_name, model_version, event_id)
TTL event_time + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;
