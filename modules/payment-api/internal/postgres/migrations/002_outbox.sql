CREATE TABLE payment_outbox (
    sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    event_id uuid PRIMARY KEY,
    payment_id uuid NOT NULL UNIQUE REFERENCES payments(id),
    account_id uuid NOT NULL,
    event_data jsonb NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'published', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    failure_code text CHECK (failure_code IN ('publication_failed')),
    CHECK ((state = 'published') = (published_at IS NOT NULL))
);
CREATE INDEX payment_outbox_pending ON payment_outbox (sequence) WHERE state <> 'published';
CREATE INDEX payment_outbox_account_order ON payment_outbox (account_id, sequence) WHERE state <> 'published';

INSERT INTO payment_outbox (event_id, payment_id, account_id, event_data)
SELECT md5('payment.initiated.v1:' || id::text)::uuid, id, account_id,
    jsonb_build_object(
        'event_id', md5('payment.initiated.v1:' || id::text)::uuid,
        'event_type', 'payment.initiated.v1',
        'event_time', to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'producer', 'payment-api',
        'correlation_id', id,
        'causation_id', NULL,
        'schema_version', 1,
        'payload', jsonb_build_object('payment_id', id, 'amount_minor', amount_minor, 'currency', currency)
    )
FROM payments
ORDER BY created_at, id;

CREATE TABLE payment_event_receipts (
    event_id uuid PRIMARY KEY,
    payment_id uuid NOT NULL,
    account_id uuid NOT NULL,
    event_data jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);
