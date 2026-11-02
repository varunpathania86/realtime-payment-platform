CREATE TABLE payment_event_dead_letters (
    topic text NOT NULL,
    partition integer NOT NULL,
    record_offset bigint NOT NULL,
    record_key bytea NOT NULL,
    record_value bytea NOT NULL,
    failure_code text NOT NULL CHECK (failure_code IN ('invalid_avro', 'invalid_event', 'identity_conflict')),
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (topic, partition, record_offset)
);
