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
        'payload', jsonb_build_object('payment_id', NEW.id, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency, 'country', NEW.country, 'payment_method', NEW.payment_method, 'account_created_at', NEW.account_created_at, 'device_id', NEW.device_id, 'simulation_event_time', NEW.simulation_event_time, 'simulation_outcome', NEW.simulation_outcome)
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
        'payload', jsonb_build_object('payment_id', p.id, 'amount_minor', p.amount_minor, 'currency', p.currency, 'country', p.country, 'payment_method', p.payment_method, 'account_created_at', p.account_created_at, 'device_id', p.device_id, 'simulation_event_time', p.simulation_event_time, 'simulation_outcome', p.simulation_outcome)
    )
FROM payments p
WHERE NOT EXISTS (SELECT 1 FROM payment_outbox o WHERE o.payment_id = p.id)
ORDER BY p.created_at, p.id;
