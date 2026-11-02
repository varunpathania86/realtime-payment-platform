ALTER TABLE payment_outbox
    ADD COLUMN traceparent text NOT NULL DEFAULT '' CHECK (length(traceparent) <= 55),
    ADD COLUMN tracestate text NOT NULL DEFAULT '' CHECK (length(tracestate) <= 512);
