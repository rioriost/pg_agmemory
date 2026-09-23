CREATE TABLE memory_ops.source_access_state (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    source_system text NOT NULL CHECK (length(source_system) BETWEEN 1 AND 256),
    dataset_id text NOT NULL CHECK (length(dataset_id) BETWEEN 1 AND 256),
    source_subject text NOT NULL CHECK (length(source_subject) BETWEEN 1 AND 256),
    sequence bigint NOT NULL CHECK (sequence >= 0),
    notice_digest text CHECK (notice_digest ~ '^[0-9a-f]{64}$'),
    decision text NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason text NOT NULL CHECK (
        reason IN ('bound', 'authorized', 'revoked', 'unavailable', 'deleted',
                   'sequence_gap', 'invalid_lease')
    ),
    acl_version text CHECK (length(acl_version) BETWEEN 1 AND 256),
    verified_at timestamptz,
    valid_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    database_role name NOT NULL DEFAULT current_user,
    PRIMARY KEY (tenant_id, scope_id, principal_id),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES memory.principal(tenant_id, id),
    CHECK (
        (sequence = 0 AND decision = 'deny' AND reason = 'bound'
            AND notice_digest IS NULL AND acl_version IS NULL
            AND verified_at IS NULL AND valid_until IS NULL)
        OR (sequence > 0 AND reason <> 'bound' AND notice_digest IS NOT NULL)
    ),
    CHECK (
        (decision = 'allow' AND reason = 'authorized'
            AND acl_version IS NOT NULL AND verified_at IS NOT NULL AND valid_until IS NOT NULL
            AND valid_until > verified_at
            AND valid_until - verified_at <= interval '300 seconds')
        OR (decision = 'deny' AND reason <> 'authorized')
    )
);

CREATE TABLE memory_ops.source_access_event (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    sequence bigint NOT NULL CHECK (sequence >= 0),
    notice_digest text CHECK (notice_digest ~ '^[0-9a-f]{64}$'),
    decision text NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason text NOT NULL CHECK (
        reason IN ('bound', 'authorized', 'revoked', 'unavailable', 'deleted',
                   'sequence_gap', 'invalid_lease')
    ),
    acl_version text CHECK (length(acl_version) BETWEEN 1 AND 256),
    verified_at timestamptz,
    valid_until timestamptz,
    access_epoch bigint NOT NULL CHECK (access_epoch >= 1),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    database_role name NOT NULL DEFAULT current_user,
    PRIMARY KEY (tenant_id, scope_id, principal_id, sequence),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES memory.principal(tenant_id, id),
    FOREIGN KEY (tenant_id, scope_id, principal_id)
        REFERENCES memory_ops.source_access_state(tenant_id, scope_id, principal_id),
    CHECK (
        (sequence = 0 AND decision = 'deny' AND reason = 'bound'
            AND notice_digest IS NULL AND acl_version IS NULL
            AND verified_at IS NULL AND valid_until IS NULL)
        OR (sequence > 0 AND reason <> 'bound' AND notice_digest IS NOT NULL)
    ),
    CHECK (
        (decision = 'allow' AND reason = 'authorized'
            AND acl_version IS NOT NULL AND verified_at IS NOT NULL AND valid_until IS NOT NULL
            AND valid_until > verified_at
            AND valid_until - verified_at <= interval '300 seconds')
        OR (decision = 'deny' AND reason <> 'authorized')
    )
);

CREATE FUNCTION memory_ops.guard_source_access_state() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'source access binding cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.sequence <> 0 THEN
            RAISE EXCEPTION 'source access binding must begin at sequence zero'
                USING ERRCODE = '23514';
        END IF;
        NEW.created_at := clock_timestamp();
        NEW.updated_at := NEW.created_at;
    ELSE
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.scope_id IS DISTINCT FROM OLD.scope_id
           OR NEW.principal_id IS DISTINCT FROM OLD.principal_id
           OR NEW.source_system IS DISTINCT FROM OLD.source_system
           OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
           OR NEW.source_subject IS DISTINCT FROM OLD.source_subject
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.sequence <= OLD.sequence THEN
            RAISE EXCEPTION 'source access identity is immutable and sequence must advance'
                USING ERRCODE = '23514';
        END IF;
        IF OLD.reason = 'deleted' AND NEW.reason IS DISTINCT FROM 'deleted' THEN
            RAISE EXCEPTION 'deleted source access is terminal' USING ERRCODE = '23514';
        END IF;
        NEW.updated_at := clock_timestamp();
    END IF;
    NEW.database_role := current_user;
    RETURN NEW;
END;
$$;

CREATE FUNCTION memory_ops.guard_source_access_event() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'source access events are immutable' USING ERRCODE = '23514';
    END IF;
    NEW.recorded_at := clock_timestamp();
    NEW.database_role := current_user;
    RETURN NEW;
END;
$$;

CREATE TRIGGER guard_source_access_state
BEFORE INSERT OR UPDATE OR DELETE ON memory_ops.source_access_state
FOR EACH ROW EXECUTE FUNCTION memory_ops.guard_source_access_state();

CREATE TRIGGER guard_source_access_event
BEFORE INSERT OR UPDATE OR DELETE ON memory_ops.source_access_event
FOR EACH ROW EXECUTE FUNCTION memory_ops.guard_source_access_event();

ALTER TABLE memory_ops.source_access_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.source_access_state FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.source_access_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.source_access_event FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memory_ops.source_access_state, memory_ops.source_access_event
    FROM PUBLIC, pgag_runtime;
REVOKE ALL ON FUNCTION memory_ops.guard_source_access_state(),
    memory_ops.guard_source_access_event() FROM PUBLIC, pgag_runtime;

ALTER TABLE memory_ops.age_projection
ADD COLUMN captured_schema_version integer NOT NULL DEFAULT 20
    CHECK (captured_schema_version IN (20, 21));

CREATE OR REPLACE FUNCTION memory_ops.guard_age_projection() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'AGE projection receipt cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.revision <> 1 THEN
            RAISE EXCEPTION 'initial AGE projection revision must be one' USING ERRCODE = '23514';
        END IF;
        NEW.created_at := clock_timestamp();
        NEW.updated_at := NEW.created_at;
    ELSE
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'invalid AGE projection revision' USING ERRCODE = '23514';
        END IF;
        NEW.updated_at := clock_timestamp();
    END IF;
    NEW.database_role := current_user;
    IF NEW.enabled AND NOT EXISTS (
        SELECT 1 FROM memory_ops.graph_generation g
        JOIN memory_ops.graph_generation_state s
            ON s.tenant_id = g.tenant_id AND s.head_id = g.id
        WHERE g.tenant_id = NEW.tenant_id AND g.id = NEW.generation_id AND g.state = 'recorded'
            AND g.artifact_digest = NEW.artifact_digest AND g.profile_digest = NEW.profile_digest
            AND g.input_digest = NEW.input_digest
            AND g.input_snapshot->'schema_version' = '21'::jsonb
            AND g.input_snapshot->'access_epoch' = to_jsonb(NEW.captured_access_epoch)
            AND g.input_snapshot->'deletion_epoch' = to_jsonb(NEW.captured_deletion_epoch)
    ) THEN
        RAISE EXCEPTION 'enabled AGE projection must match the recorded head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.enabled THEN
        NEW.captured_schema_version := 21;
    ELSIF TG_OP = 'UPDATE' THEN
        NEW.captured_schema_version := OLD.captured_schema_version;
    ELSE
        NEW.captured_schema_version := 20;
    END IF;
    RETURN NEW;
END;
$$;
