ALTER TABLE memory.object DROP CONSTRAINT object_kind_check;
ALTER TABLE memory.object ADD CHECK (kind IN ('episode','assertion','checkpoint','tool_effect'));
ALTER TABLE memory.checkpoint_run
    ADD COLUMN effects_invalidated boolean NOT NULL DEFAULT false;
CREATE POLICY run_invalidate ON memory.checkpoint_run FOR UPDATE USING (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'delete')
) WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'delete')
    AND effects_invalidated
);
GRANT UPDATE(effects_invalidated) ON memory.checkpoint_run TO pgag_runtime;

CREATE TABLE memory.tool_effect (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'tool_effect' CHECK (kind = 'tool_effect'),
    run_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    tool_name text NOT NULL CHECK (length(tool_name) BETWEEN 1 AND 256),
    action_fingerprint text NOT NULL CHECK (action_fingerprint ~ '^[0-9a-f]{64}$'),
    external_idempotency_key text NOT NULL CHECK (external_idempotency_key ~ '^[0-9a-f]{64}$'),
    current_revision integer NOT NULL DEFAULT 1 CHECK (current_revision BETWEEN 1 AND 4),
    reference_count integer NOT NULL CHECK (reference_count BETWEEN 0 AND 100),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, scope_id),
    UNIQUE (tenant_id, scope_id, run_id, operation_id),
    FOREIGN KEY (tenant_id, id, scope_id, kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind),
    FOREIGN KEY (tenant_id, scope_id, run_id)
        REFERENCES memory.checkpoint_run(tenant_id, scope_id, run_id)
);
CREATE TABLE memory.tool_effect_revision (
    tenant_id uuid NOT NULL,
    effect_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision BETWEEN 1 AND 4),
    status text NOT NULL CHECK (status IN ('planned','dispatched','unknown','confirmed','failed')),
    recorded_at timestamptz NOT NULL,
    actor_id uuid NOT NULL,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 256),
    receipt_reference text,
    receipt_source text CHECK (receipt_source IN ('provider_receipt','operator_review')),
    origin text NOT NULL CHECK (origin IN ('api','checkpoint_restore')),
    PRIMARY KEY (tenant_id, effect_id, revision),
    FOREIGN KEY (tenant_id, effect_id) REFERENCES memory.tool_effect(tenant_id, id),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES memory.principal(tenant_id, id),
    CHECK (
        (status IN ('confirmed','failed') AND receipt_reference IS NOT NULL
         AND length(receipt_reference) BETWEEN 1 AND 256 AND receipt_source IS NOT NULL)
        OR (status NOT IN ('confirmed','failed') AND receipt_reference IS NULL
            AND receipt_source IS NULL)
    )
);
ALTER TABLE memory.tool_effect ADD FOREIGN KEY (tenant_id, id, current_revision)
    REFERENCES memory.tool_effect_revision(tenant_id, effect_id, revision)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE memory.tool_effect_reference (
    tenant_id uuid NOT NULL,
    effect_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    source_id uuid NOT NULL,
    source_revision bigint NOT NULL CHECK (source_revision BETWEEN 1 AND 1000),
    source_kind text NOT NULL CHECK (source_kind IN ('episode','assertion')),
    episode_id uuid GENERATED ALWAYS AS
        (CASE WHEN source_kind = 'episode' THEN source_id END) STORED,
    assertion_id uuid GENERATED ALWAYS AS
        (CASE WHEN source_kind = 'assertion' THEN source_id END) STORED,
    PRIMARY KEY (tenant_id, effect_id, source_id, source_revision),
    FOREIGN KEY (tenant_id, effect_id, scope_id)
        REFERENCES memory.tool_effect(tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, source_id, scope_id, source_kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind),
    FOREIGN KEY (tenant_id, episode_id) REFERENCES memory.episode(tenant_id, id),
    FOREIGN KEY (tenant_id, assertion_id, source_revision)
        REFERENCES memory.assertion_revision(tenant_id, assertion_id, revision),
    CHECK (source_kind <> 'episode' OR source_revision = 1)
);
CREATE INDEX tool_effect_source ON memory.tool_effect_reference(tenant_id, source_id);
CREATE TABLE memory_ops.tool_effect_identity (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    run_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    effect_id uuid NOT NULL,
    request_digest text NOT NULL,
    PRIMARY KEY (tenant_id, scope_id, run_id, operation_id),
    FOREIGN KEY (tenant_id, effect_id, scope_id)
        REFERENCES memory.object(tenant_id, id, scope_id)
);

ALTER TABLE memory.tool_effect ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.tool_effect FORCE ROW LEVEL SECURITY;
CREATE POLICY effect_read ON memory.tool_effect FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(id)
);
CREATE POLICY effect_insert ON memory.tool_effect FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(id)
);
CREATE POLICY effect_update ON memory.tool_effect FOR UPDATE USING (
    tenant_id = memory.current_tenant() AND memory.writable(id)
) WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(id)
);
CREATE POLICY effect_delete ON memory.tool_effect FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(id)
);
DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['tool_effect_revision','tool_effect_reference'] LOOP
        EXECUTE format('ALTER TABLE memory.%I ENABLE ROW LEVEL SECURITY', tab);
        EXECUTE format('ALTER TABLE memory.%I FORCE ROW LEVEL SECURITY', tab);
        EXECUTE format('CREATE POLICY effect_read ON memory.%I FOR SELECT USING
            (tenant_id = memory.current_tenant() AND memory.visible(effect_id))', tab);
        EXECUTE format('CREATE POLICY effect_insert ON memory.%I FOR INSERT WITH CHECK
            (tenant_id = memory.current_tenant() AND memory.writable(effect_id))', tab);
        EXECUTE format('CREATE POLICY effect_delete ON memory.%I FOR DELETE USING
            (tenant_id = memory.current_tenant() AND memory.deletable(effect_id))', tab);
    END LOOP;
END;
$$;
CREATE POLICY source_read ON memory.tool_effect_reference AS RESTRICTIVE FOR SELECT USING (
    memory.visible(source_id)
);
CREATE POLICY source_insert ON memory.tool_effect_reference AS RESTRICTIVE FOR INSERT
WITH CHECK (memory.visible(source_id));
ALTER TABLE memory_ops.tool_effect_identity ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.tool_effect_identity FORCE ROW LEVEL SECURITY;
CREATE POLICY identity_read ON memory_ops.tool_effect_identity FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'read')
);
CREATE POLICY identity_insert ON memory_ops.tool_effect_identity FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'write')
);

CREATE FUNCTION memory.guard_tool_effect() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE invalidated boolean; total bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'effect-run:' || NEW.tenant_id::text || NEW.scope_id::text || NEW.run_id::text, 0));
    SELECT effects_invalidated INTO STRICT invalidated FROM memory.checkpoint_run
    WHERE tenant_id = NEW.tenant_id AND scope_id = NEW.scope_id AND run_id = NEW.run_id;
    SELECT count(*) INTO total FROM memory.tool_effect
    WHERE tenant_id = NEW.tenant_id AND scope_id = NEW.scope_id AND run_id = NEW.run_id;
    IF invalidated OR total >= 100 OR NEW.current_revision <> 1 THEN
        RAISE EXCEPTION 'effect run is invalidated or full' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_tool_effect BEFORE INSERT ON memory.tool_effect
FOR EACH ROW EXECUTE FUNCTION memory.guard_tool_effect();

CREATE FUNCTION memory.adopt_tool_effect_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE head integer; previous memory.tool_effect_revision; adopted_at timestamptz;
BEGIN
    SELECT current_revision INTO STRICT head FROM memory.tool_effect
    WHERE tenant_id = NEW.tenant_id AND id = NEW.effect_id FOR UPDATE;
    IF NEW.status = 'dispatched' AND EXISTS (
        SELECT 1 FROM memory.tool_effect e JOIN memory.checkpoint_run r
          USING (tenant_id,scope_id,run_id)
        WHERE e.tenant_id = NEW.tenant_id AND e.id = NEW.effect_id AND r.effects_invalidated
    ) THEN
        RAISE EXCEPTION 'effect run is invalidated' USING ERRCODE = '23514';
    END IF;
    adopted_at := clock_timestamp();
    IF NEW.revision = 1 THEN
        IF head <> 1 OR NEW.status <> 'planned' OR NEW.origin <> 'api' THEN
            RAISE EXCEPTION 'effect must begin planned' USING ERRCODE = '23514';
        END IF;
    ELSE
        SELECT * INTO STRICT previous FROM memory.tool_effect_revision
        WHERE tenant_id = NEW.tenant_id AND effect_id = NEW.effect_id AND revision = head;
        IF NEW.revision <> head + 1 OR NOT (
            (previous.status = 'planned' AND NEW.status IN ('dispatched','unknown'))
            OR (previous.status = 'dispatched' AND NEW.status IN ('unknown','confirmed','failed'))
            OR (previous.status = 'unknown' AND NEW.status IN ('confirmed','failed'))
        ) OR (NEW.origin = 'checkpoint_restore'
              AND NOT (previous.status = 'dispatched' AND NEW.status = 'unknown')) THEN
            RAISE EXCEPTION 'invalid effect transition' USING ERRCODE = '23514';
        END IF;
        IF adopted_at <= previous.recorded_at THEN
            RAISE EXCEPTION 'system clock conflict' USING ERRCODE = '40001';
        END IF;
        UPDATE memory.tool_effect SET current_revision = NEW.revision
        WHERE tenant_id = NEW.tenant_id AND id = NEW.effect_id;
    END IF;
    NEW.recorded_at := adopted_at;
    NEW.actor_id := memory.current_principal();
    RETURN NEW;
END;
$$;
CREATE TRIGGER adopt_tool_effect_revision BEFORE INSERT ON memory.tool_effect_revision
FOR EACH ROW EXECUTE FUNCTION memory.adopt_tool_effect_revision();

CREATE FUNCTION memory.check_tool_effect() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid; tenant uuid; head integer; refs integer; total bigint;
BEGIN
    IF TG_TABLE_NAME = 'tool_effect' THEN
        target := NEW.id; tenant := NEW.tenant_id;
    ELSIF TG_OP = 'DELETE' THEN
        target := OLD.effect_id; tenant := OLD.tenant_id;
    ELSE
        target := NEW.effect_id; tenant := NEW.tenant_id;
    END IF;
    SELECT current_revision,reference_count INTO head,refs FROM memory.tool_effect
    WHERE tenant_id = tenant AND id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO total FROM memory.tool_effect_revision
    WHERE tenant_id = tenant AND effect_id = target;
    IF total <> head OR EXISTS (
        SELECT 1 FROM memory.tool_effect_revision
        WHERE tenant_id = tenant AND effect_id = target AND revision > head
    ) THEN
        RAISE EXCEPTION 'effect history must be contiguous' USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO total FROM memory.tool_effect_reference
    WHERE tenant_id = tenant AND effect_id = target;
    IF total <> refs THEN
        RAISE EXCEPTION 'effect references are incomplete' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER effect_history AFTER INSERT OR UPDATE ON memory.tool_effect
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_tool_effect();
CREATE CONSTRAINT TRIGGER effect_history AFTER INSERT OR DELETE ON memory.tool_effect_revision
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_tool_effect();
CREATE CONSTRAINT TRIGGER effect_references AFTER INSERT OR DELETE ON memory.tool_effect_reference
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_tool_effect();

CREATE FUNCTION memory.check_checkpoint_run_active() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM memory.checkpoint_run
        WHERE tenant_id = NEW.tenant_id AND scope_id = NEW.scope_id
          AND run_id = NEW.run_id AND effects_invalidated
    ) THEN
        RAISE EXCEPTION 'checkpoint run is invalidated' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER checkpoint_active_run BEFORE INSERT ON memory.checkpoint
FOR EACH ROW EXECUTE FUNCTION memory.check_checkpoint_run_active();

GRANT SELECT, INSERT, DELETE ON memory.tool_effect, memory.tool_effect_revision,
    memory.tool_effect_reference TO pgag_runtime;
GRANT UPDATE(current_revision) ON memory.tool_effect TO pgag_runtime;
GRANT SELECT, INSERT ON memory_ops.tool_effect_identity TO pgag_runtime;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
