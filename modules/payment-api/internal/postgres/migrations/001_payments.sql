CREATE TABLE payments (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL,
    amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    currency text NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'CAD', 'AUD', 'CHF', 'SGD')),
    country text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    payment_method text NOT NULL CHECK (payment_method IN ('card', 'transfer', 'wallet')),
    account_created_at text NOT NULL DEFAULT '',
    device_id text NOT NULL DEFAULT '',
    simulation_event_time text NOT NULL DEFAULT '',
    simulation_outcome text NOT NULL DEFAULT '' CHECK (simulation_outcome IN ('', 'authorized', 'rejected')),
    status text NOT NULL CHECK (status = 'initiated'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    idempotency_key text NOT NULL UNIQUE CHECK (octet_length(idempotency_key) BETWEEN 1 AND 128)
);
