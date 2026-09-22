CREATE TABLE memory_ops.age_projection (
    tenant_id uuid PRIMARY KEY REFERENCES memory.tenant(id),
    generation_id uuid NOT NULL,
    graph_name text NOT NULL UNIQUE CHECK (graph_name ~ '^pgag_age_[0-9a-f]{32}$'),
    artifact_digest text NOT NULL CHECK (artifact_digest ~ '^[0-9a-f]{64}$'),
    profile_digest text NOT NULL CHECK (profile_digest ~ '^[0-9a-f]{64}$'),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    captured_access_epoch bigint NOT NULL CHECK (captured_access_epoch>0),
    captured_deletion_epoch bigint NOT NULL CHECK (captured_deletion_epoch>0),
    age_commit text NOT NULL CHECK (age_commit='72707aab7ce982bf13cad3d102bd869dab07d64b'),
    node_count integer NOT NULL CHECK (node_count BETWEEN 0 AND 10000),
    edge_revision_count integer NOT NULL CHECK (edge_revision_count BETWEEN 0 AND 40000),
    enabled boolean NOT NULL DEFAULT false,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    database_role name NOT NULL DEFAULT current_user,
    FOREIGN KEY (tenant_id,generation_id) REFERENCES memory_ops.graph_generation(tenant_id,id),
    CHECK (graph_name='pgag_age_' || replace(generation_id::text,'-',''))
);

CREATE FUNCTION memory_ops.guard_age_projection() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'AGE projection receipt cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.revision<>1 THEN
            RAISE EXCEPTION 'initial AGE projection revision must be one' USING ERRCODE='23514';
        END IF;
        NEW.created_at:=clock_timestamp();
        NEW.updated_at:=NEW.created_at;
    ELSE
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.revision<>OLD.revision+1 THEN
            RAISE EXCEPTION 'invalid AGE projection revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    END IF;
    NEW.database_role:=current_user;
    IF NEW.enabled AND NOT EXISTS (
        SELECT 1 FROM memory_ops.graph_generation g
        JOIN memory_ops.graph_generation_state s
            ON s.tenant_id=g.tenant_id AND s.head_id=g.id
        WHERE g.tenant_id=NEW.tenant_id AND g.id=NEW.generation_id AND g.state='recorded'
            AND g.artifact_digest=NEW.artifact_digest AND g.profile_digest=NEW.profile_digest
            AND g.input_digest=NEW.input_digest
            AND g.input_snapshot->'schema_version'='20'::jsonb
            AND g.input_snapshot->'access_epoch'=to_jsonb(NEW.captured_access_epoch)
            AND g.input_snapshot->'deletion_epoch'=to_jsonb(NEW.captured_deletion_epoch)
    ) THEN
        RAISE EXCEPTION 'enabled AGE projection must match the recorded head'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_age_projection
BEFORE INSERT OR UPDATE OR DELETE ON memory_ops.age_projection
FOR EACH ROW EXECUTE FUNCTION memory_ops.guard_age_projection();

ALTER TABLE memory_ops.age_projection ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.age_projection FORCE ROW LEVEL SECURITY;
CREATE POLICY age_projection_read ON memory_ops.age_projection
    FOR SELECT TO pgag_runtime USING (tenant_id=memory.current_tenant());
REVOKE ALL ON memory_ops.age_projection FROM PUBLIC, pgag_runtime;
GRANT SELECT ON memory_ops.age_projection TO pgag_runtime;
REVOKE ALL ON FUNCTION memory_ops.guard_age_projection() FROM PUBLIC, pgag_runtime;
