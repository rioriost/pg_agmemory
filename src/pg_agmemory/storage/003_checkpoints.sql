ALTER TABLE memory.object DROP CONSTRAINT object_kind_check;
ALTER TABLE memory.object ADD CHECK (kind IN ('episode','assertion','checkpoint'));

CREATE TABLE memory.checkpoint_run (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    run_id uuid NOT NULL,
    harness_id text NOT NULL,
    harness_version text NOT NULL,
    state_schema_version integer NOT NULL CHECK (state_schema_version = 1),
    PRIMARY KEY (tenant_id, scope_id, run_id),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id)
);
CREATE TABLE memory.checkpoint_branch (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    run_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    head_id uuid,
    head_kind text NOT NULL DEFAULT 'checkpoint' CHECK (head_kind = 'checkpoint'),
    sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0),
    invalidated boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, scope_id, run_id, branch_id),
    FOREIGN KEY (tenant_id, scope_id, run_id)
        REFERENCES memory.checkpoint_run(tenant_id, scope_id, run_id),
    FOREIGN KEY (tenant_id, head_id, scope_id, head_kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind),
    CHECK ((head_id IS NULL) = (sequence = 0))
);
CREATE TABLE memory.checkpoint (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'checkpoint' CHECK (kind = 'checkpoint'),
    run_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    ordinal bigint GENERATED ALWAYS AS IDENTITY,
    parent_id uuid,
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    event_watermark bigint NOT NULL CHECK (event_watermark >= 0),
    reference_count integer NOT NULL CHECK (reference_count BETWEEN 0 AND 100),
    access_epoch bigint NOT NULL,
    deletion_epoch bigint NOT NULL,
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, scope_id, run_id),
    UNIQUE (tenant_id, id, scope_id),
    UNIQUE (tenant_id, scope_id, run_id, branch_id, sequence),
    FOREIGN KEY (tenant_id, id, scope_id, kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind),
    FOREIGN KEY (tenant_id, scope_id, run_id, branch_id)
        REFERENCES memory.checkpoint_branch(tenant_id, scope_id, run_id, branch_id),
    FOREIGN KEY (tenant_id, parent_id, scope_id, run_id)
        REFERENCES memory.checkpoint(tenant_id, id, scope_id, run_id)
);
CREATE INDEX checkpoint_parent ON memory.checkpoint(tenant_id, parent_id);
CREATE TABLE memory.checkpoint_reference (
    tenant_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    source_id uuid NOT NULL,
    source_revision bigint NOT NULL CHECK (source_revision BETWEEN 1 AND 1000),
    source_kind text NOT NULL CHECK (source_kind IN ('episode','assertion')),
    episode_id uuid GENERATED ALWAYS AS
        (CASE WHEN source_kind = 'episode' THEN source_id END) STORED,
    assertion_id uuid GENERATED ALWAYS AS
        (CASE WHEN source_kind = 'assertion' THEN source_id END) STORED,
    PRIMARY KEY (tenant_id, checkpoint_id, source_id, source_revision),
    FOREIGN KEY (tenant_id, checkpoint_id, scope_id)
        REFERENCES memory.checkpoint(tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, source_id, scope_id, source_kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind),
    FOREIGN KEY (tenant_id, episode_id) REFERENCES memory.episode(tenant_id, id),
    FOREIGN KEY (tenant_id, assertion_id, source_revision)
        REFERENCES memory.assertion_revision(tenant_id, assertion_id, revision),
    CHECK (source_kind <> 'episode' OR source_revision = 1)
);
CREATE INDEX checkpoint_source ON memory.checkpoint_reference(tenant_id, source_id);

DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['checkpoint_run','checkpoint_branch','checkpoint',
                              'checkpoint_reference'] LOOP
        EXECUTE format('ALTER TABLE memory.%I ENABLE ROW LEVEL SECURITY', tab);
        EXECUTE format('ALTER TABLE memory.%I FORCE ROW LEVEL SECURITY', tab);
    END LOOP;
    FOREACH tab IN ARRAY ARRAY['checkpoint_run','checkpoint_branch'] LOOP
        EXECUTE format(
            'CREATE POLICY scope_read ON memory.%I FOR SELECT USING
             (tenant_id = memory.current_tenant() AND memory.permitted(scope_id, ''read''))', tab);
        EXECUTE format(
            'CREATE POLICY scope_insert ON memory.%I FOR INSERT WITH CHECK
             (tenant_id = memory.current_tenant() AND memory.permitted(scope_id, ''write''))', tab);
    END LOOP;
END;
$$;
CREATE POLICY branch_update ON memory.checkpoint_branch FOR UPDATE USING (
    tenant_id = memory.current_tenant()
    AND (memory.permitted(scope_id, 'write') OR memory.permitted(scope_id, 'delete'))
) WITH CHECK (
    tenant_id = memory.current_tenant()
    AND (memory.permitted(scope_id, 'write') OR memory.permitted(scope_id, 'delete'))
);
CREATE POLICY checkpoint_read ON memory.checkpoint FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(id)
);
CREATE POLICY checkpoint_insert ON memory.checkpoint FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(id)
);
CREATE POLICY checkpoint_delete ON memory.checkpoint FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(id)
);
CREATE POLICY reference_read ON memory.checkpoint_reference FOR SELECT USING (
    tenant_id = memory.current_tenant()
    AND memory.visible(checkpoint_id) AND memory.visible(source_id)
);
CREATE POLICY reference_insert ON memory.checkpoint_reference FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant()
    AND memory.writable(checkpoint_id) AND memory.visible(source_id)
);
CREATE POLICY reference_delete ON memory.checkpoint_reference FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(checkpoint_id)
);

CREATE FUNCTION memory.adopt_checkpoint() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE branch memory.checkpoint_branch; parent memory.checkpoint;
BEGIN
    SELECT * INTO STRICT branch FROM memory.checkpoint_branch
    WHERE tenant_id = NEW.tenant_id AND scope_id = NEW.scope_id
      AND run_id = NEW.run_id AND branch_id = NEW.branch_id FOR UPDATE;
    IF branch.invalidated OR NEW.sequence <> branch.sequence + 1
       OR (branch.sequence > 0 AND NEW.parent_id IS DISTINCT FROM branch.head_id) THEN
        RAISE EXCEPTION 'checkpoint head conflict' USING ERRCODE = '23514';
    END IF;
    IF NEW.parent_id IS NOT NULL THEN
        SELECT * INTO STRICT parent FROM memory.checkpoint
        WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_id;
        IF parent.scope_id <> NEW.scope_id OR parent.run_id <> NEW.run_id
           OR parent.ordinal >= NEW.ordinal OR NEW.event_watermark < parent.event_watermark
           OR (branch.sequence = 0 AND parent.branch_id = NEW.branch_id) THEN
            RAISE EXCEPTION 'invalid checkpoint parent' USING ERRCODE = '23514';
        END IF;
    END IF;
    UPDATE memory.checkpoint_branch SET head_id = NEW.id, sequence = NEW.sequence
    WHERE tenant_id = NEW.tenant_id AND scope_id = NEW.scope_id
      AND run_id = NEW.run_id AND branch_id = NEW.branch_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER adopt_checkpoint BEFORE INSERT ON memory.checkpoint
FOR EACH ROW EXECUTE FUNCTION memory.adopt_checkpoint();

CREATE FUNCTION memory.check_checkpoint() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid; tenant uuid; expected integer; actual bigint;
BEGIN
    IF TG_TABLE_NAME = 'checkpoint' THEN
        target := NEW.id; tenant := NEW.tenant_id;
    ELSIF TG_OP = 'DELETE' THEN
        target := OLD.checkpoint_id; tenant := OLD.tenant_id;
    ELSE
        target := NEW.checkpoint_id; tenant := NEW.tenant_id;
    END IF;
    SELECT reference_count INTO expected FROM memory.checkpoint
    WHERE tenant_id = tenant AND id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO actual FROM memory.checkpoint_reference
    WHERE tenant_id = tenant AND checkpoint_id = target;
    IF actual <> expected THEN
        RAISE EXCEPTION 'checkpoint references are incomplete' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER checkpoint_references AFTER INSERT ON memory.checkpoint
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_checkpoint();
CREATE CONSTRAINT TRIGGER checkpoint_references AFTER INSERT OR DELETE ON memory.checkpoint_reference
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_checkpoint();

CREATE FUNCTION memory.guard_checkpoint_branch() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.invalidated OR (NEW.head_id IS DISTINCT FROM OLD.head_id
        AND (NEW.sequence <> OLD.sequence + 1 OR NEW.invalidated))
       OR (NEW.head_id IS NOT DISTINCT FROM OLD.head_id AND NEW.sequence <> OLD.sequence) THEN
        RAISE EXCEPTION 'checkpoint branch cannot be rewound or reopened' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_checkpoint_branch BEFORE UPDATE ON memory.checkpoint_branch
FOR EACH ROW EXECUTE FUNCTION memory.guard_checkpoint_branch();
CREATE FUNCTION memory.check_checkpoint_head() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM memory.checkpoint_branch b
        WHERE b.tenant_id = NEW.tenant_id AND b.scope_id = NEW.scope_id
          AND b.run_id = NEW.run_id AND b.branch_id = NEW.branch_id
          AND NOT b.invalidated AND b.sequence > 0
          AND NOT EXISTS (
            SELECT 1 FROM memory.checkpoint c
            WHERE c.tenant_id = b.tenant_id AND c.id = b.head_id
              AND c.scope_id = b.scope_id AND c.run_id = b.run_id
              AND c.branch_id = b.branch_id AND c.sequence = b.sequence
          )
    ) THEN
        RAISE EXCEPTION 'checkpoint branch head is missing' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER checkpoint_head AFTER INSERT OR UPDATE ON memory.checkpoint_branch
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_checkpoint_head();

GRANT SELECT, INSERT ON memory.checkpoint_run, memory.checkpoint_branch TO pgag_runtime;
GRANT UPDATE(head_id, sequence, invalidated) ON memory.checkpoint_branch TO pgag_runtime;
GRANT SELECT, INSERT, DELETE ON memory.checkpoint, memory.checkpoint_reference TO pgag_runtime;
GRANT USAGE ON SEQUENCE memory.checkpoint_ordinal_seq TO pgag_runtime;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
