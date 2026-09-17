ALTER TABLE memory.object DROP CONSTRAINT object_kind_check;
ALTER TABLE memory.object ADD CHECK (
    kind IN ('episode','assertion','checkpoint','tool_effect','entity','job')
);
CREATE TABLE memory_ops.job (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    object_kind text NOT NULL DEFAULT 'job' CHECK (object_kind = 'job'),
    principal_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind = 'structured_remember'),
    recipe_version text NOT NULL CHECK (recipe_version = 'structured-remember-v1'),
    intent_digest text NOT NULL CHECK (intent_digest ~ '^[0-9a-f]{64}$'),
    retry_of uuid,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','succeeded','failed')),
    payload jsonb,
    reference_count integer NOT NULL CHECK (reference_count BETWEEN 1 AND 32),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 5),
    lease_token uuid,
    lease_until timestamptz,
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    captured_access_epoch bigint NOT NULL,
    captured_deletion_epoch bigint NOT NULL,
    result_id uuid,
    error_code text CHECK (error_code IN (
        'dependency_unavailable','stale_context','invalid_input','attempt_limit'
    )),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,id),
    UNIQUE (tenant_id,id,scope_id),
    UNIQUE (tenant_id,result_id),
    FOREIGN KEY (tenant_id,id,scope_id,object_kind)
        REFERENCES memory.object(tenant_id,id,scope_id,kind),
    FOREIGN KEY (tenant_id,principal_id) REFERENCES memory.principal(tenant_id,id),
    FOREIGN KEY (tenant_id,retry_of,scope_id,object_kind)
        REFERENCES memory.object(tenant_id,id,scope_id,kind),
    FOREIGN KEY (tenant_id,result_id,scope_id) REFERENCES memory.assertion(tenant_id,id,scope_id),
    CHECK ((state = 'running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK (state = 'running' OR (lease_token IS NULL AND lease_until IS NULL)),
    CHECK ((state = 'succeeded') = (result_id IS NOT NULL)),
    CHECK ((state IN ('pending','running') AND payload IS NOT NULL
            AND jsonb_typeof(payload) = 'object')
           OR (state IN ('succeeded','failed') AND payload IS NULL)),
    CHECK (state <> 'failed' OR error_code IS NOT NULL),
    CHECK (state NOT IN ('running','succeeded') OR error_code IS NULL)
);
CREATE INDEX job_ready ON memory_ops.job(tenant_id,principal_id,available_at,id)
WHERE state IN ('pending','running');
CREATE INDEX job_retry_parent ON memory_ops.job(tenant_id,retry_of);
CREATE TABLE memory_ops.job_input (
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    source_id uuid NOT NULL,
    PRIMARY KEY (tenant_id,job_id,source_id),
    FOREIGN KEY (tenant_id,job_id,scope_id) REFERENCES memory_ops.job(tenant_id,id,scope_id),
    FOREIGN KEY (tenant_id,source_id,scope_id) REFERENCES memory.episode(tenant_id,id,scope_id)
);
CREATE INDEX job_input_source ON memory_ops.job_input(tenant_id,source_id);
CREATE TABLE memory_ops.job_identity (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    job_id uuid NOT NULL,
    PRIMARY KEY (tenant_id,scope_id,principal_id,input_digest),
    FOREIGN KEY (tenant_id,job_id,scope_id) REFERENCES memory.object(tenant_id,id,scope_id),
    FOREIGN KEY (tenant_id,principal_id) REFERENCES memory.principal(tenant_id,id)
);

ALTER TABLE memory_ops.job ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.job FORCE ROW LEVEL SECURITY;
CREATE POLICY job_read ON memory_ops.job FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(id)
);
CREATE POLICY job_insert ON memory_ops.job FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(id)
    AND principal_id = memory.current_principal()
);
CREATE POLICY job_update ON memory_ops.job FOR UPDATE USING (
    tenant_id = memory.current_tenant() AND memory.writable(id)
    AND principal_id = memory.current_principal()
) WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(id)
    AND principal_id = memory.current_principal()
);
CREATE POLICY job_delete ON memory_ops.job FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(id)
);
ALTER TABLE memory_ops.job_input ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.job_input FORCE ROW LEVEL SECURITY;
CREATE POLICY input_read ON memory_ops.job_input FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(job_id) AND memory.visible(source_id)
);
CREATE POLICY input_insert ON memory_ops.job_input FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(job_id) AND memory.visible(source_id)
);
CREATE POLICY input_delete ON memory_ops.job_input FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(job_id)
);
ALTER TABLE memory_ops.job_identity ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.job_identity FORCE ROW LEVEL SECURITY;
CREATE POLICY identity_read ON memory_ops.job_identity FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id,'read')
    AND principal_id = memory.current_principal()
);
CREATE POLICY identity_insert ON memory_ops.job_identity FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id,'write')
    AND principal_id = memory.current_principal()
);

CREATE FUNCTION memory.guard_job() RETURNS trigger
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
        IF OLD.state IN ('succeeded','failed')
           OR (NEW.payload IS NOT NULL AND NEW.payload IS DISTINCT FROM OLD.payload) THEN
            RAISE EXCEPTION 'job intent and terminal outcomes are immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.state = 'running' THEN
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
CREATE TRIGGER guard_job BEFORE INSERT OR UPDATE ON memory_ops.job
FOR EACH ROW EXECUTE FUNCTION memory.guard_job();

CREATE FUNCTION memory.check_job_inputs() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid; tenant uuid; refs integer; body jsonb; total bigint;
BEGIN
    IF TG_TABLE_NAME = 'job' THEN
        target := NEW.id; tenant := NEW.tenant_id;
    ELSIF TG_OP = 'DELETE' THEN
        target := OLD.job_id; tenant := OLD.tenant_id;
    ELSE
        target := NEW.job_id; tenant := NEW.tenant_id;
    END IF;
    SELECT reference_count,payload INTO refs,body FROM memory_ops.job
    WHERE tenant_id = tenant AND id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO total FROM memory_ops.job_input
    WHERE tenant_id = tenant AND job_id = target;
    IF total <> refs OR (body IS NOT NULL AND (
        jsonb_array_length(body->'evidence') <> refs OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(body->'evidence') e WHERE NOT EXISTS (
                SELECT 1 FROM memory_ops.job_input i
                WHERE i.tenant_id = tenant AND i.job_id = target
                  AND i.source_id = (e->>'memory_id')::uuid
            )
        )
    )) THEN
        RAISE EXCEPTION 'job inputs are incomplete' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER job_inputs AFTER INSERT OR UPDATE ON memory_ops.job
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_job_inputs();
CREATE CONSTRAINT TRIGGER job_inputs AFTER INSERT OR DELETE ON memory_ops.job_input
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_job_inputs();
GRANT SELECT, INSERT, DELETE ON memory_ops.job, memory_ops.job_input TO pgag_runtime;
GRANT UPDATE(state,payload,attempt,lease_token,lease_until,available_at,
             captured_access_epoch,captured_deletion_epoch,result_id,error_code)
ON memory_ops.job TO pgag_runtime;
GRANT SELECT, INSERT ON memory_ops.job_identity TO pgag_runtime;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
