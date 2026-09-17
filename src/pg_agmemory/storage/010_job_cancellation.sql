ALTER TABLE memory_ops.job
    DROP CONSTRAINT job_state_check,
    DROP CONSTRAINT job_check3,
    ADD CONSTRAINT job_state_check
        CHECK (state IN ('pending','running','succeeded','failed','cancelled')),
    ADD CONSTRAINT job_payload_state_check
        CHECK ((state IN ('pending','running') AND payload IS NOT NULL
                AND jsonb_typeof(payload) = 'object')
               OR (state IN ('succeeded','failed','cancelled') AND payload IS NULL)),
    ADD CONSTRAINT job_cancelled_error_check
        CHECK (state <> 'cancelled' OR error_code IS NULL);

CREATE OR REPLACE FUNCTION memory.guard_job() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE now_at timestamptz := clock_timestamp(); total bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'jobs:' || NEW.tenant_id::text || NEW.scope_id::text, 0));
        SELECT count(*) INTO total FROM memory_ops.job
        WHERE tenant_id = NEW.tenant_id AND scope_id = NEW.scope_id
          AND state IN ('pending','running');
        IF total >= 100 OR NEW.state <> 'pending' OR NEW.attempt <> 0 THEN
            RAISE EXCEPTION 'invalid initial job or full queue' USING ERRCODE = '23514';
        END IF;
        NEW.created_at := now_at;
        NEW.available_at := now_at;
    ELSE
        IF OLD.state IN ('succeeded','failed','cancelled')
           OR (NEW.payload IS NOT NULL AND NEW.payload IS DISTINCT FROM OLD.payload) THEN
            RAISE EXCEPTION 'job intent and terminal outcomes are immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.state = 'cancelled' THEN
            IF OLD.state NOT IN ('pending','running') OR NEW.attempt <> OLD.attempt THEN
                RAISE EXCEPTION 'invalid job cancellation' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.state = 'running' THEN
            IF NOT (
                (NEW.attempt = OLD.attempt + 1 AND NEW.lease_token IS DISTINCT FROM OLD.lease_token
                 AND ((OLD.state = 'pending' AND OLD.available_at <= now_at)
                      OR (OLD.state = 'running' AND OLD.lease_until <= now_at)))
                OR (OLD.state = 'running' AND NEW.attempt = OLD.attempt
                    AND NEW.lease_token = OLD.lease_token AND OLD.lease_until > now_at
                    AND NEW.lease_until > OLD.lease_until)
            ) THEN
                RAISE EXCEPTION 'invalid job claim or heartbeat' USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.state <> 'running' OR NEW.attempt <> OLD.attempt THEN
            RAISE EXCEPTION 'invalid job transition' USING ERRCODE = '23514';
        END IF;
        IF NEW.state IN ('succeeded','pending') AND OLD.lease_until <= now_at THEN
            RAISE EXCEPTION 'expired job lease' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.state = 'running' AND (
        NEW.lease_until <= now_at OR NEW.lease_until > now_at + interval '300 seconds'
    ) THEN
        RAISE EXCEPTION 'invalid lease duration' USING ERRCODE = '23514';
    END IF;
    NEW.updated_at := now_at;
    RETURN NEW;
END;
$$;
