SET LOCAL row_security = off;

ALTER TABLE memory.episode_lexical
DROP CONSTRAINT episode_lexical_profile_check,
ADD CONSTRAINT episode_lexical_profile_check
    CHECK (profile IN ('ja-janome-0.5.0-v1', 'en-snowball-v1'));
ALTER TABLE memory.assertion_lexical
DROP CONSTRAINT assertion_lexical_profile_check,
ADD CONSTRAINT assertion_lexical_profile_check
    CHECK (profile IN ('ja-janome-0.5.0-v1', 'en-snowball-v1'));

INSERT INTO memory.episode_lexical (tenant_id, episode_id, scope_id, profile, search_text)
SELECT e.tenant_id, e.id, e.scope_id, 'en-snowball-v1',
       to_tsvector('pg_catalog.english'::regconfig, e.content)
FROM memory.episode e JOIN memory.object o USING (tenant_id, id)
WHERE NOT EXISTS (
    SELECT 1 FROM memory_ops.object_tombstone t
    WHERE t.tenant_id = o.tenant_id AND t.object_id = o.id
);

INSERT INTO memory.assertion_lexical
    (tenant_id, assertion_id, revision, scope_id, profile, search_text)
SELECT a.tenant_id, a.id, r.revision, a.scope_id, 'en-snowball-v1',
       to_tsvector('pg_catalog.english'::regconfig,
                   a.subject || ' ' || a.predicate || ' ' || r.value)
FROM memory.assertion a JOIN memory.object o USING (tenant_id, id)
JOIN memory.assertion_revision r
    ON r.tenant_id = a.tenant_id AND r.assertion_id = a.id
WHERE NOT EXISTS (
    SELECT 1 FROM memory_ops.object_tombstone t
    WHERE t.tenant_id = o.tenant_id AND t.object_id = o.id
);

-- Keep old receipts readable, but require schema 23 for newly enabled projections.
ALTER TABLE memory_ops.age_projection
DROP CONSTRAINT age_projection_captured_schema_version_check,
ADD CONSTRAINT age_projection_captured_schema_version_check
    CHECK (captured_schema_version IN (20, 21, 22, 23));

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
            AND g.input_snapshot->'schema_version' = '23'::jsonb
            AND g.input_snapshot->'access_epoch' = to_jsonb(NEW.captured_access_epoch)
            AND g.input_snapshot->'deletion_epoch' = to_jsonb(NEW.captured_deletion_epoch)
    ) THEN
        RAISE EXCEPTION 'enabled AGE projection must match the recorded head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.enabled THEN
        NEW.captured_schema_version := 23;
    ELSIF TG_OP = 'UPDATE' THEN
        NEW.captured_schema_version := OLD.captured_schema_version;
    ELSE
        NEW.captured_schema_version := 20;
    END IF;
    RETURN NEW;
END;
$$;
