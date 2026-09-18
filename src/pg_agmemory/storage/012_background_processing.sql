CREATE TABLE memory.scope_synthesis_policy (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    access_epoch bigint NOT NULL CHECK (access_epoch > 1),
    policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object'),
    PRIMARY KEY (tenant_id,scope_id),
    FOREIGN KEY (tenant_id,scope_id) REFERENCES memory.scope(tenant_id,id)
);
ALTER TABLE memory.scope_synthesis_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.scope_synthesis_policy FORCE ROW LEVEL SECURITY;
CREATE POLICY synthesis_read ON memory.scope_synthesis_policy FOR SELECT USING (
    tenant_id=memory.current_tenant() AND memory.permitted(scope_id,'read')
);
GRANT SELECT ON memory.scope_synthesis_policy TO pgag_runtime;

CREATE TABLE memory_ops.synthesis_policy_event (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    access_epoch bigint NOT NULL,
    database_role name NOT NULL DEFAULT current_user,
    previous_policy jsonb NOT NULL,
    policy jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,access_epoch),
    FOREIGN KEY (tenant_id,scope_id) REFERENCES memory.scope(tenant_id,id)
);
ALTER TABLE memory_ops.synthesis_policy_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.synthesis_policy_event FORCE ROW LEVEL SECURITY;

ALTER TABLE memory_ops.job
    DROP CONSTRAINT job_kind_check,
    DROP CONSTRAINT job_recipe_version_check,
    DROP CONSTRAINT job_reference_count_check,
    DROP CONSTRAINT job_check2,
    DROP CONSTRAINT job_error_code_check,
    ADD COLUMN processing_result jsonb,
    ADD CONSTRAINT job_kind_recipe_check CHECK (
        (kind='structured_remember' AND recipe_version='structured-remember-v1')
        OR (kind='extract' AND recipe_version='source-extraction-v1')
        OR (kind='embed' AND recipe_version='canonical-embedding-v1')
        OR (kind='compact' AND recipe_version='working-compaction-v1')
    ),
    ADD CHECK (reference_count BETWEEN 1 AND 101),
    ADD CHECK (
        (kind='structured_remember' AND processing_result IS NULL
         AND ((state='succeeded')=(result_id IS NOT NULL)))
        OR (kind<>'structured_remember' AND result_id IS NULL
            AND ((state='succeeded')=(processing_result IS NOT NULL)))
    ),
    ADD CHECK (error_code IN (
        'dependency_unavailable','stale_context','invalid_input','attempt_limit',
        'policy_denied','provider_failed','billing_unknown','compaction_conflict'
    ));
GRANT UPDATE(processing_result) ON memory_ops.job TO pgag_runtime;

ALTER TABLE memory_ops.job_input
    DROP CONSTRAINT job_input_tenant_id_source_id_scope_id_fkey,
    ADD COLUMN source_revision integer NOT NULL DEFAULT 1 CHECK (source_revision BETWEEN 1 AND 1000),
    ADD FOREIGN KEY (tenant_id,source_id,scope_id)
        REFERENCES memory.object(tenant_id,id,scope_id);

CREATE OR REPLACE FUNCTION memory.check_job_inputs() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE target uuid; tenant uuid; refs integer; body jsonb; total bigint; job_kind text;
BEGIN
    IF TG_TABLE_NAME='job' THEN target:=NEW.id; tenant:=NEW.tenant_id;
    ELSIF TG_OP='DELETE' THEN target:=OLD.job_id; tenant:=OLD.tenant_id;
    ELSE target:=NEW.job_id; tenant:=NEW.tenant_id; END IF;
    SELECT reference_count,payload,kind INTO refs,body,job_kind FROM memory_ops.job
      WHERE tenant_id=tenant AND id=target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO total FROM memory_ops.job_input
      WHERE tenant_id=tenant AND job_id=target;
    IF total<>refs THEN
        RAISE EXCEPTION 'job inputs are incomplete' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM memory_ops.job_input i JOIN memory.object o
          ON o.tenant_id=i.tenant_id AND o.id=i.source_id
        WHERE i.tenant_id=tenant AND i.job_id=target AND NOT (
            (o.kind='episode' AND i.source_revision=1 AND EXISTS (
                SELECT 1 FROM memory.episode e WHERE e.tenant_id=tenant AND e.id=i.source_id))
            OR (job_kind IN ('embed','compact') AND o.kind='assertion' AND EXISTS (
                SELECT 1 FROM memory.assertion_revision r WHERE r.tenant_id=tenant
                AND r.assertion_id=i.source_id AND r.revision=i.source_revision))
            OR (job_kind='compact' AND o.kind='entity' AND i.source_revision=1)
            OR (job_kind='compact' AND o.kind='checkpoint' AND i.source_revision=1 AND EXISTS (
                SELECT 1 FROM memory.checkpoint c WHERE c.tenant_id=tenant AND c.id=i.source_id))
        )
    ) THEN RAISE EXCEPTION 'invalid job source' USING ERRCODE='23514'; END IF;
    IF body IS NOT NULL AND job_kind='structured_remember' AND (
        jsonb_array_length(body->'evidence')<>refs OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(body->'evidence') e WHERE NOT EXISTS (
                SELECT 1 FROM memory_ops.job_input i WHERE i.tenant_id=tenant AND i.job_id=target
                AND i.source_id=(e->>'memory_id')::uuid AND i.source_revision=1
            )
        )
    ) THEN RAISE EXCEPTION 'job intent mismatch' USING ERRCODE='23514'; END IF;
    IF body IS NOT NULL AND job_kind<>'structured_remember' AND (
        jsonb_array_length(body->'input_refs')<>refs OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(body->'input_refs') e WHERE NOT EXISTS (
                SELECT 1 FROM memory_ops.job_input i WHERE i.tenant_id=tenant AND i.job_id=target
                AND i.source_id=(e->>'memory_id')::uuid
                AND i.source_revision=(e->>'revision')::integer
            )
        )
    ) THEN RAISE EXCEPTION 'job intent mismatch' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END;
$$;

CREATE TABLE memory_ops.model_call (
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    policy_epoch bigint NOT NULL,
    lease_token uuid NOT NULL,
    profile_digest text NOT NULL CHECK (profile_digest ~ '^[0-9a-f]{64}$'),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    input_bytes integer NOT NULL CHECK (input_bytes BETWEEN 1 AND 262144),
    max_output_tokens integer CHECK (max_output_tokens BETWEEN 1 AND 4096),
    outcome text NOT NULL DEFAULT 'unknown' CHECK (outcome IN ('unknown','succeeded','failed')),
    billing_unknown boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,job_id),
    FOREIGN KEY (tenant_id,job_id,scope_id) REFERENCES memory.object(tenant_id,id,scope_id)
);
CREATE INDEX model_call_budget ON memory_ops.model_call(tenant_id,scope_id,policy_epoch);
ALTER TABLE memory_ops.model_call ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.model_call FORCE ROW LEVEL SECURITY;
CREATE POLICY call_read ON memory_ops.model_call FOR SELECT USING (
    tenant_id=memory.current_tenant() AND memory.permitted(scope_id,'read')
);
CREATE POLICY call_insert ON memory_ops.model_call FOR INSERT WITH CHECK (
    tenant_id=memory.current_tenant() AND memory.writable(job_id) AND EXISTS (
        SELECT 1 FROM memory_ops.job j WHERE j.tenant_id=model_call.tenant_id
        AND j.id=model_call.job_id AND j.principal_id=memory.current_principal()
    )
);
CREATE POLICY call_update ON memory_ops.model_call FOR UPDATE USING (
    tenant_id=memory.current_tenant() AND memory.writable(job_id) AND EXISTS (
        SELECT 1 FROM memory_ops.job j WHERE j.tenant_id=model_call.tenant_id
        AND j.id=model_call.job_id AND j.principal_id=memory.current_principal()
    )
);
CREATE FUNCTION memory.guard_model_call() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE j memory_ops.job; p memory.scope_synthesis_policy; total bigint;
BEGIN
    SELECT * INTO STRICT j FROM memory_ops.job WHERE tenant_id=NEW.tenant_id AND id=NEW.job_id;
    IF j.state<>'running' OR j.kind='structured_remember'
       OR j.lease_token<>NEW.lease_token OR j.lease_until<=clock_timestamp() THEN
        RAISE EXCEPTION 'invalid call lease' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.outcome<>'unknown' OR NEW.outcome='unknown' THEN
            RAISE EXCEPTION 'call outcome immutable' USING ERRCODE='23514';
        END IF;
    ELSE
        -- The ADMIN policy stays SELECT-only; serialize reservations without locking its row.
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'model-calls:' || NEW.tenant_id::text || NEW.scope_id::text,0));
        SELECT * INTO STRICT p FROM memory.scope_synthesis_policy
          WHERE tenant_id=NEW.tenant_id AND scope_id=NEW.scope_id;
        SELECT count(*) INTO total FROM memory_ops.model_call
          WHERE tenant_id=NEW.tenant_id AND scope_id=NEW.scope_id AND policy_epoch=p.access_epoch;
        IF NOT (p.policy->>'enabled')::boolean OR p.access_epoch<>NEW.policy_epoch
           OR p.policy->>'profile_digest'<>NEW.profile_digest
           OR NOT (p.policy->'kinds' ? j.kind)
           OR NEW.input_bytes>(p.policy->>'max_input_bytes')::integer
           OR (j.kind='embed' AND NEW.max_output_tokens IS NOT NULL)
           OR (j.kind<>'embed' AND (NEW.max_output_tokens IS NULL
               OR NEW.max_output_tokens>(p.policy->>'max_output_tokens')::integer))
           OR total>=(p.policy->>'max_calls')::integer OR NEW.outcome<>'unknown'
           OR NOT NEW.billing_unknown THEN
            RAISE EXCEPTION 'call budget denied' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_model_call BEFORE INSERT OR UPDATE ON memory_ops.model_call
FOR EACH ROW EXECUTE FUNCTION memory.guard_model_call();
GRANT SELECT,INSERT ON memory_ops.model_call TO pgag_runtime;
GRANT UPDATE(outcome,billing_unknown) ON memory_ops.model_call TO pgag_runtime;

ALTER TABLE memory.assertion_revision
    DROP CONSTRAINT assertion_revision_epistemic_status_check,
    DROP CONSTRAINT assertion_revision_explicit_intent_check,
    ADD CHECK (
        (epistemic_status='reported' AND explicit_intent)
        OR (epistemic_status='inferred' AND NOT explicit_intent)
    );
CREATE TABLE memory.assertion_derivation (
    tenant_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    revision integer NOT NULL,
    scope_id uuid NOT NULL,
    job_id uuid NOT NULL,
    metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata)='object'),
    PRIMARY KEY (tenant_id,assertion_id,revision),
    FOREIGN KEY (tenant_id,assertion_id,revision,scope_id)
        REFERENCES memory.assertion_revision(tenant_id,assertion_id,revision,scope_id),
    FOREIGN KEY (tenant_id,job_id,scope_id) REFERENCES memory.object(tenant_id,id,scope_id)
);
CREATE TABLE memory_ops.extraction_candidate (
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 15),
    candidate jsonb NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('published','duplicate','quarantined')),
    reason text NOT NULL,
    assertion_id uuid,
    PRIMARY KEY (tenant_id,job_id,ordinal),
    FOREIGN KEY (tenant_id,job_id,scope_id) REFERENCES memory_ops.job(tenant_id,id,scope_id),
    FOREIGN KEY (tenant_id,assertion_id,scope_id) REFERENCES memory.object(tenant_id,id,scope_id)
);
ALTER TABLE memory.assertion_derivation ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.assertion_derivation FORCE ROW LEVEL SECURITY;
CREATE POLICY derivation_read ON memory.assertion_derivation FOR SELECT USING (
    tenant_id=memory.current_tenant() AND memory.visible(assertion_id)
);
CREATE POLICY derivation_insert ON memory.assertion_derivation FOR INSERT WITH CHECK (
    tenant_id=memory.current_tenant() AND memory.writable(assertion_id) AND memory.visible(job_id)
);
CREATE POLICY derivation_delete ON memory.assertion_derivation FOR DELETE USING (
    tenant_id=memory.current_tenant() AND memory.deletable(assertion_id)
);
ALTER TABLE memory_ops.extraction_candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.extraction_candidate FORCE ROW LEVEL SECURITY;
CREATE POLICY candidate_read ON memory_ops.extraction_candidate FOR SELECT USING (
    tenant_id=memory.current_tenant() AND memory.visible(job_id) AND EXISTS (
        SELECT 1 FROM memory_ops.job j WHERE j.tenant_id=extraction_candidate.tenant_id
        AND j.id=extraction_candidate.job_id AND j.reference_count=(
            SELECT count(*) FROM memory_ops.job_input i
            WHERE i.tenant_id=j.tenant_id AND i.job_id=j.id AND memory.visible(i.source_id)
        )
    )
);
CREATE POLICY candidate_insert ON memory_ops.extraction_candidate FOR INSERT WITH CHECK (
    tenant_id=memory.current_tenant() AND memory.writable(job_id)
);
CREATE POLICY candidate_delete ON memory_ops.extraction_candidate FOR DELETE USING (
    tenant_id=memory.current_tenant() AND memory.deletable(job_id)
);
GRANT SELECT,INSERT,DELETE ON memory.assertion_derivation,memory_ops.extraction_candidate
TO pgag_runtime;

CREATE FUNCTION memory.guard_processing_job() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE p memory.scope_synthesis_policy; total bigint; t memory.tenant;
BEGIN
    IF NEW.kind='structured_remember' THEN RETURN NEW; END IF;
    IF TG_OP='INSERT' OR NEW.state='succeeded' THEN
        SELECT * INTO STRICT p FROM memory.scope_synthesis_policy
          WHERE tenant_id=NEW.tenant_id AND scope_id=NEW.scope_id;
        SELECT * INTO STRICT t FROM memory.tenant WHERE id=NEW.tenant_id;
        IF NOT (p.policy->>'enabled')::boolean OR NOT (p.policy->'kinds' ? NEW.kind)
           OR NEW.captured_access_epoch<>t.access_epoch
           OR NEW.captured_deletion_epoch<>t.deletion_epoch THEN
            RAISE EXCEPTION 'processing policy denied' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.payload->>'profile_digest'<>p.policy->>'profile_digest'
               OR (NEW.payload->>'policy_epoch')::bigint<>p.access_epoch
               OR (NEW.payload->>'scope_id')::uuid<>NEW.scope_id
               OR NEW.payload->>'kind'<>NEW.kind
               OR NEW.payload->>'recipe_version'<>NEW.recipe_version THEN
                RAISE EXCEPTION 'invalid processing intent' USING ERRCODE='23514';
            END IF;
            SELECT count(*) INTO total FROM memory_ops.job
              WHERE tenant_id=NEW.tenant_id AND scope_id=NEW.scope_id
              AND state IN ('pending','running');
            IF total>=(p.policy->>'max_pending_jobs')::integer THEN
                RAISE EXCEPTION 'processing queue full' USING ERRCODE='23514';
            END IF;
        ELSE
            IF NOT EXISTS (
                SELECT 1 FROM memory_ops.model_call c WHERE c.tenant_id=NEW.tenant_id
                  AND c.job_id=NEW.id AND c.lease_token=OLD.lease_token
                  AND c.outcome='succeeded' AND c.policy_epoch=p.access_epoch
                  AND c.profile_digest=p.policy->>'profile_digest'
            ) THEN RAISE EXCEPTION 'processing call missing' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_processing_job BEFORE INSERT OR UPDATE ON memory_ops.job
FOR EACH ROW EXECUTE FUNCTION memory.guard_processing_job();

CREATE FUNCTION memory.check_inferred_derivation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE target uuid; tenant uuid; rev bigint;
BEGIN
    target:=NEW.assertion_id; tenant:=NEW.tenant_id; rev:=NEW.revision;
    IF EXISTS (
        SELECT 1 FROM memory.assertion_revision r
        WHERE r.tenant_id=tenant AND r.assertion_id=target AND r.revision=rev
        AND r.epistemic_status='inferred' AND NOT EXISTS (
            SELECT 1 FROM memory.assertion_derivation d JOIN memory_ops.job j
              ON j.tenant_id=d.tenant_id AND j.id=d.job_id
            WHERE d.tenant_id=tenant AND d.assertion_id=target AND d.revision=rev
              AND j.kind='extract' AND j.state='succeeded'
        )
    ) THEN RAISE EXCEPTION 'inferred assertion requires derivation' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER inferred_derivation AFTER INSERT ON memory.assertion_revision
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_inferred_derivation();
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
