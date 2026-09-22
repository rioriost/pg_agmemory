CREATE TABLE memory_ops.graph_generation (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    id uuid NOT NULL,
    parent_id uuid,
    state text NOT NULL CHECK (state IN ('building','recorded','abandoned')),
    profile_digest text NOT NULL CHECK (profile_digest ~ '^[0-9a-f]{64}$'),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    input_snapshot jsonb NOT NULL CHECK (jsonb_typeof(input_snapshot)='object'),
    artifact_digest text CHECK (artifact_digest ~ '^[0-9a-f]{64}$'),
    abandon_reason text CHECK (length(abandon_reason) BETWEEN 1 AND 256),
    database_role name NOT NULL DEFAULT current_user,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    PRIMARY KEY (tenant_id,id),
    FOREIGN KEY (tenant_id,parent_id) REFERENCES memory_ops.graph_generation(tenant_id,id),
    CHECK (id<>parent_id),
    CHECK (
        (state='building' AND artifact_digest IS NULL AND abandon_reason IS NULL
            AND finished_at IS NULL)
        OR (state='recorded' AND artifact_digest IS NOT NULL AND abandon_reason IS NULL
            AND finished_at IS NOT NULL)
        OR (state='abandoned' AND artifact_digest IS NULL AND abandon_reason IS NOT NULL
            AND finished_at IS NOT NULL)
    )
);
CREATE UNIQUE INDEX graph_generation_building
    ON memory_ops.graph_generation(tenant_id) WHERE state='building';

CREATE TABLE memory_ops.graph_generation_state (
    tenant_id uuid PRIMARY KEY REFERENCES memory.tenant(id),
    revision bigint NOT NULL CHECK (revision>=1),
    head_id uuid,
    building_id uuid,
    FOREIGN KEY (tenant_id,head_id) REFERENCES memory_ops.graph_generation(tenant_id,id),
    FOREIGN KEY (tenant_id,building_id) REFERENCES memory_ops.graph_generation(tenant_id,id),
    CHECK (head_id<>building_id)
);

CREATE FUNCTION memory_ops.guard_graph_generation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'building' THEN
            RAISE EXCEPTION 'graph generation must begin in building state'
                USING ERRCODE='23514';
        END IF;
        NEW.database_role:=current_user;
        NEW.created_at:=clock_timestamp();
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'graph generation history is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.state<>'building' OR NEW.state NOT IN ('recorded','abandoned')
       OR ROW(NEW.tenant_id,NEW.id,NEW.parent_id,NEW.profile_digest,NEW.input_digest,
              NEW.input_snapshot,NEW.database_role,NEW.created_at)
          IS DISTINCT FROM
          ROW(OLD.tenant_id,OLD.id,OLD.parent_id,OLD.profile_digest,OLD.input_digest,
              OLD.input_snapshot,OLD.database_role,OLD.created_at) THEN
        RAISE EXCEPTION 'invalid graph generation transition' USING ERRCODE='23514';
    END IF;
    NEW.finished_at:=clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_graph_generation BEFORE INSERT OR UPDATE OR DELETE ON memory_ops.graph_generation
FOR EACH ROW EXECUTE FUNCTION memory_ops.guard_graph_generation();

CREATE FUNCTION memory_ops.guard_graph_generation_state() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'graph generation state cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.revision<>1 THEN
            RAISE EXCEPTION 'initial graph generation revision must be one'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.revision<>OLD.revision+1 THEN
        RAISE EXCEPTION 'invalid graph generation state revision' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_graph_generation_state
BEFORE INSERT OR UPDATE OR DELETE ON memory_ops.graph_generation_state
FOR EACH ROW EXECUTE FUNCTION memory_ops.guard_graph_generation_state();

CREATE FUNCTION memory_ops.check_graph_generation_state() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE
    ledger memory_ops.graph_generation_state%ROWTYPE;
    building_count bigint;
BEGIN
    SELECT * INTO ledger FROM memory_ops.graph_generation_state WHERE tenant_id=NEW.tenant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'graph generation state is missing' USING ERRCODE='23514';
    END IF;
    IF ledger.head_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM memory_ops.graph_generation
        WHERE tenant_id=ledger.tenant_id AND id=ledger.head_id AND state='recorded'
    ) THEN
        RAISE EXCEPTION 'graph generation head must be recorded' USING ERRCODE='23514';
    END IF;
    SELECT count(*) INTO building_count FROM memory_ops.graph_generation
        WHERE tenant_id=ledger.tenant_id AND state='building';
    IF (ledger.building_id IS NULL AND building_count<>0)
       OR (ledger.building_id IS NOT NULL AND (
           building_count<>1 OR NOT EXISTS (
               SELECT 1 FROM memory_ops.graph_generation
               WHERE tenant_id=ledger.tenant_id AND id=ledger.building_id AND state='building'
                   AND parent_id IS NOT DISTINCT FROM ledger.head_id
           )
       )) THEN
        RAISE EXCEPTION 'graph generation building state is inconsistent' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER graph_generation_state_complete
AFTER INSERT OR UPDATE ON memory_ops.graph_generation DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memory_ops.check_graph_generation_state();
CREATE CONSTRAINT TRIGGER graph_generation_state_complete
AFTER INSERT OR UPDATE ON memory_ops.graph_generation_state DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memory_ops.check_graph_generation_state();

ALTER TABLE memory_ops.graph_generation ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.graph_generation FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.graph_generation_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.graph_generation_state FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memory_ops.graph_generation, memory_ops.graph_generation_state
    FROM PUBLIC, pgag_runtime;
REVOKE ALL ON FUNCTION memory_ops.guard_graph_generation(),
    memory_ops.guard_graph_generation_state(), memory_ops.check_graph_generation_state()
    FROM PUBLIC, pgag_runtime;
