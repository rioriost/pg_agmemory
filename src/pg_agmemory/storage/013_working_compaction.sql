ALTER TABLE memory.episode ADD COLUMN source_namespace text;

CREATE TABLE memory.working_event (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    run_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    sequence bigint NOT NULL CHECK (sequence>0),
    source_id uuid NOT NULL,
    source_revision integer NOT NULL DEFAULT 1 CHECK (source_revision=1),
    PRIMARY KEY (tenant_id,scope_id,run_id,branch_id,sequence),
    UNIQUE (tenant_id,scope_id,run_id,branch_id,source_id),
    FOREIGN KEY (tenant_id,scope_id,run_id,branch_id)
        REFERENCES memory.checkpoint_branch(tenant_id,scope_id,run_id,branch_id),
    FOREIGN KEY (tenant_id,source_id,scope_id) REFERENCES memory.episode(tenant_id,id,scope_id)
);
CREATE INDEX working_event_source ON memory.working_event(tenant_id,source_id);
CREATE TABLE memory.working_snapshot (
    tenant_id uuid NOT NULL,
    checkpoint_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    job_id uuid NOT NULL,
    coverage_start bigint NOT NULL CHECK (coverage_start=1),
    coverage_end bigint NOT NULL CHECK (coverage_end>0),
    summary text NOT NULL CHECK (length(summary) BETWEEN 1 AND 65536),
    input_refs jsonb NOT NULL CHECK (jsonb_typeof(input_refs)='array'
                                   AND jsonb_array_length(input_refs) BETWEEN 1 AND 100),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    model jsonb NOT NULL,
    recipe_version text NOT NULL CHECK (recipe_version='working-compaction-v1'),
    PRIMARY KEY (tenant_id,checkpoint_id),
    FOREIGN KEY (tenant_id,checkpoint_id,scope_id)
        REFERENCES memory.checkpoint(tenant_id,id,scope_id),
    FOREIGN KEY (tenant_id,job_id,scope_id) REFERENCES memory.object(tenant_id,id,scope_id)
);
ALTER TABLE memory.working_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.working_event FORCE ROW LEVEL SECURITY;
CREATE POLICY event_read ON memory.working_event FOR SELECT USING (
    tenant_id=memory.current_tenant() AND memory.permitted(scope_id,'read')
    AND memory.visible(source_id)
);
CREATE POLICY event_insert ON memory.working_event FOR INSERT WITH CHECK (
    tenant_id=memory.current_tenant() AND memory.permitted(scope_id,'write')
    AND memory.visible(source_id)
);
CREATE POLICY event_delete ON memory.working_event FOR DELETE USING (
    tenant_id=memory.current_tenant() AND memory.permitted(scope_id,'delete')
);
CREATE FUNCTION memory.guard_working_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE latest bigint;
BEGIN
    PERFORM 1 FROM memory.checkpoint_branch b JOIN memory.checkpoint_run r
      USING (tenant_id,scope_id,run_id)
      WHERE b.tenant_id=NEW.tenant_id AND b.scope_id=NEW.scope_id AND b.run_id=NEW.run_id
      AND b.branch_id=NEW.branch_id AND NOT b.invalidated AND NOT r.effects_invalidated
      FOR UPDATE OF b;
    IF NOT FOUND THEN RAISE EXCEPTION 'invalid working run' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(max(sequence),0) INTO latest FROM memory.working_event
      WHERE tenant_id=NEW.tenant_id AND scope_id=NEW.scope_id
      AND run_id=NEW.run_id AND branch_id=NEW.branch_id;
    IF NEW.sequence<>latest+1 OR latest>=1000 THEN
        RAISE EXCEPTION 'invalid event sequence' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_working_event BEFORE INSERT ON memory.working_event
FOR EACH ROW EXECUTE FUNCTION memory.guard_working_event();
ALTER TABLE memory.working_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.working_snapshot FORCE ROW LEVEL SECURITY;
CREATE POLICY snapshot_read ON memory.working_snapshot FOR SELECT USING (
    tenant_id=memory.current_tenant() AND memory.visible(checkpoint_id)
);
CREATE POLICY snapshot_insert ON memory.working_snapshot FOR INSERT WITH CHECK (
    tenant_id=memory.current_tenant() AND memory.writable(checkpoint_id)
);
CREATE POLICY snapshot_delete ON memory.working_snapshot FOR DELETE USING (
    tenant_id=memory.current_tenant() AND memory.deletable(checkpoint_id)
);
GRANT SELECT,INSERT,DELETE ON memory.working_event,memory.working_snapshot TO pgag_runtime;

ALTER TABLE memory_ops.extraction_candidate
    ADD COLUMN adopted_assertion_id uuid,
    ADD COLUMN adopted_by uuid,
    ADD FOREIGN KEY (tenant_id,adopted_assertion_id,scope_id)
        REFERENCES memory.object(tenant_id,id,scope_id),
    ADD FOREIGN KEY (tenant_id,adopted_by) REFERENCES memory.principal(tenant_id,id),
    ADD UNIQUE (tenant_id,adopted_assertion_id),
    ADD CHECK ((adopted_assertion_id IS NULL)=(adopted_by IS NULL)),
    ADD CHECK (adopted_assertion_id IS NULL OR disposition='quarantined');

CREATE POLICY candidate_adopt ON memory_ops.extraction_candidate FOR UPDATE USING (
    tenant_id=memory.current_tenant() AND memory.writable(job_id)
) WITH CHECK (
    tenant_id=memory.current_tenant() AND memory.writable(job_id)
    AND adopted_by=memory.current_principal() AND memory.writable(adopted_assertion_id)
);
GRANT UPDATE(adopted_assertion_id,adopted_by) ON memory_ops.extraction_candidate TO pgag_runtime;

CREATE FUNCTION memory.guard_candidate_adoption() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF OLD.adopted_assertion_id IS NOT NULL OR OLD.disposition<>'quarantined'
       OR NEW.adopted_assertion_id IS NULL
       OR NEW.adopted_by IS DISTINCT FROM memory.current_principal()
       OR NOT EXISTS (
           SELECT 1 FROM memory.assertion_revision r JOIN memory.assertion_derivation d
             ON d.tenant_id=r.tenant_id AND d.assertion_id=r.assertion_id
               AND d.revision=r.revision
           WHERE r.tenant_id=NEW.tenant_id AND r.assertion_id=NEW.adopted_assertion_id
             AND r.revision=1 AND r.scope_id=NEW.scope_id
             AND r.explicit_intent AND r.epistemic_status='reported'
             AND d.job_id=NEW.job_id
             AND d.metadata->>'source_class'='caller_explicit_adoption'
             AND (d.metadata->>'candidate_ordinal')::integer=NEW.ordinal
       ) THEN
        RAISE EXCEPTION 'invalid candidate adoption' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_candidate_adoption BEFORE UPDATE ON memory_ops.extraction_candidate
FOR EACH ROW EXECUTE FUNCTION memory.guard_candidate_adoption();
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
