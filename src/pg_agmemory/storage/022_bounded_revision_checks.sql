-- Materialize assertion-local sets so stale statistics cannot multiply RLS scans.
CREATE OR REPLACE FUNCTION memory.check_assertion_history() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE
    target uuid; tenant uuid; head bigint; total bigint;
    invalid_history boolean; missing_evidence boolean;
BEGIN
    IF TG_TABLE_NAME = 'assertion' THEN
        target := NEW.id; tenant := NEW.tenant_id;
    ELSIF TG_TABLE_NAME = 'provenance_edge' THEN
        target := OLD.child_id; tenant := OLD.tenant_id;
    ELSIF TG_OP = 'DELETE' THEN
        target := OLD.assertion_id; tenant := OLD.tenant_id;
    ELSE
        target := NEW.assertion_id; tenant := NEW.tenant_id;
    END IF;
    SELECT current_revision INTO head FROM memory.assertion
    WHERE tenant_id = tenant AND id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;

    WITH revisions AS MATERIALIZED (
        SELECT r.revision, r.system_time,
               row_number() OVER (ORDER BY r.revision) AS position,
               lag(upper(r.system_time)) OVER (ORDER BY r.revision) AS previous_upper
        FROM memory.assertion_revision r
        WHERE r.tenant_id = tenant AND r.assertion_id = target
    ), history AS (
        SELECT count(*) AS total,
               coalesce(bool_or(
                   revision <> position
                   OR upper_inf(system_time) <> (revision = head)
                   OR (revision > 1 AND previous_upper IS DISTINCT FROM lower(system_time))
               ), false) AS invalid
        FROM revisions
    ), evidence AS MATERIALIZED (
        SELECT DISTINCT e.child_revision
        FROM memory.provenance_edge e
        WHERE e.tenant_id = tenant AND e.child_id = target
    )
    SELECT h.total, h.invalid,
           CASE WHEN h.total = head AND NOT h.invalid THEN EXISTS (
               SELECT revision FROM revisions
               EXCEPT
               SELECT child_revision FROM evidence
           ) ELSE false END
    INTO total, invalid_history, missing_evidence
    FROM history h;

    IF total <> head OR invalid_history THEN
        RAISE EXCEPTION 'assertion revision history must be contiguous' USING ERRCODE = '23514';
    END IF;
    IF missing_evidence THEN
        RAISE EXCEPTION 'assertion revision requires evidence' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION memory.check_relation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE
    target uuid; tenant uuid; typed boolean; subject text; predicate text; label text;
    revision_targets jsonb; target_ids uuid[];
BEGIN
    IF TG_TABLE_NAME IN ('assertion','relation') THEN
        IF TG_OP = 'DELETE' THEN
            target := OLD.id; tenant := OLD.tenant_id;
        ELSE
            target := NEW.id; tenant := NEW.tenant_id;
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        target := OLD.assertion_id; tenant := OLD.tenant_id;
    ELSE
        target := NEW.assertion_id; tenant := NEW.tenant_id;
    END IF;
    SELECT a.is_relation,a.subject,a.predicate INTO typed,subject,predicate
    FROM memory.assertion a WHERE a.tenant_id = tenant AND a.id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF NOT typed THEN
        IF EXISTS (SELECT 1 FROM memory.relation WHERE tenant_id = tenant AND id = target) THEN
            RAISE EXCEPTION 'relation requires a typed assertion' USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END IF;
    SELECT e.canonical_label INTO label FROM memory.relation r
    JOIN memory.entity e ON e.tenant_id = r.tenant_id AND e.id = r.source_id
    WHERE r.tenant_id = tenant AND r.id = target;
    IF NOT FOUND OR subject <> label
       OR predicate NOT IN ('depends_on','part_of','affects','works_for','decides') THEN
        RAISE EXCEPTION 'invalid relation identity' USING ERRCODE = '23514';
    END IF;

    -- The primary key makes this a unique, at-most-1000-entry revision lookup.
    -- A lookup avoids quadratic joins even between the materialized local sets.
    SELECT jsonb_object_agg(r.revision::text, r.target_id), array_agg(DISTINCT r.target_id)
    INTO revision_targets, target_ids
    FROM memory.relation_revision r
    WHERE r.tenant_id = tenant AND r.assertion_id = target;

    IF EXISTS (
        WITH revisions AS MATERIALIZED (
            SELECT (revision_targets ->> a.revision::text)::uuid AS target_id, a.value
            FROM memory.assertion_revision a
            WHERE a.tenant_id = tenant AND a.assertion_id = target
        ), targets AS MATERIALIZED (
            SELECT e.id, e.canonical_label
            FROM memory.entity e
            WHERE e.tenant_id = tenant AND e.id = ANY(target_ids)
        )
        -- Missing or RLS-hidden revisions yield NULL targets; entity IDs and
        -- labels are NOT NULL, so EXCEPT also rejects every missing target.
        SELECT target_id, value FROM revisions
        EXCEPT
        SELECT id, canonical_label FROM targets
    ) THEN
        RAISE EXCEPTION 'relation revision requires its exact entity target' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

-- Retain historical receipts while requiring the current schema on publication.
ALTER TABLE memory_ops.age_projection
DROP CONSTRAINT age_projection_captured_schema_version_check,
ADD CONSTRAINT age_projection_captured_schema_version_check
    CHECK (captured_schema_version IN (20, 21, 22));

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
            AND g.input_snapshot->'schema_version' = '22'::jsonb
            AND g.input_snapshot->'access_epoch' = to_jsonb(NEW.captured_access_epoch)
            AND g.input_snapshot->'deletion_epoch' = to_jsonb(NEW.captured_deletion_epoch)
    ) THEN
        RAISE EXCEPTION 'enabled AGE projection must match the recorded head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.enabled THEN
        NEW.captured_schema_version := 22;
    ELSIF TG_OP = 'UPDATE' THEN
        NEW.captured_schema_version := OLD.captured_schema_version;
    ELSE
        NEW.captured_schema_version := 20;
    END IF;
    RETURN NEW;
END;
$$;
