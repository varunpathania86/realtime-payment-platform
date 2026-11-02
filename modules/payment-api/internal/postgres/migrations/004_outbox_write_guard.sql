LOCK TABLE payments IN SHARE ROW EXCLUSIVE MODE;

CREATE FUNCTION serialize_payment_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.account_id::text, 0));
    RETURN NEW;
END;
$$;

CREATE FUNCTION enqueue_initiated_payment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    new_event_id uuid := gen_random_uuid();
BEGIN
    INSERT INTO payment_outbox (event_id, payment_id, account_id, event_data)
    VALUES (new_event_id, NEW.id, NEW.account_id, jsonb_build_object(
        'event_id', new_event_id,
        'event_type', 'payment.initiated.v1',
        'event_time', to_char(NEW.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'producer', 'payment-api',
        'correlation_id', NEW.id,
        'causation_id', NULL,
        'schema_version', 1,
        'payload', jsonb_build_object('payment_id', NEW.id, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency)
    ));
    RETURN NEW;
END;
$$;

CREATE TRIGGER payment_account_order BEFORE INSERT ON payments
FOR EACH ROW EXECUTE FUNCTION serialize_payment_account();
CREATE TRIGGER payment_initiated_outbox AFTER INSERT ON payments
FOR EACH ROW EXECUTE FUNCTION enqueue_initiated_payment();

INSERT INTO payment_outbox (event_id, payment_id, account_id, event_data)
SELECT md5('payment.initiated.v1:' || p.id::text)::uuid, p.id, p.account_id,
    jsonb_build_object(
        'event_id', md5('payment.initiated.v1:' || p.id::text)::uuid,
        'event_type', 'payment.initiated.v1',
        'event_time', to_char(p.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'producer', 'payment-api',
        'correlation_id', p.id,
        'causation_id', NULL,
        'schema_version', 1,
        'payload', jsonb_build_object('payment_id', p.id, 'amount_minor', p.amount_minor, 'currency', p.currency)
    )
FROM payments p
WHERE NOT EXISTS (SELECT 1 FROM payment_outbox o WHERE o.payment_id = p.id)
ORDER BY p.created_at, p.id;
