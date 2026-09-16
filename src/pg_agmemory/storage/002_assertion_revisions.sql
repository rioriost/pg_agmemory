SET LOCAL row_security = off;
CREATE EXTENSION IF NOT EXISTS btree_gist;

DROP TRIGGER assertion_evidence ON memory.assertion;
DROP TRIGGER evidence_removed ON memory.provenance_edge;
DROP FUNCTION memory.check_evidence();

CREATE TABLE memory.assertion_revision (
    tenant_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision BETWEEN 1 AND 1000),
    value text NOT NULL,
    valid_time tstzrange NOT NULL CHECK (
        NOT isempty(valid_time)
        AND (lower_inf(valid_time) OR lower_inc(valid_time))
        AND NOT upper_inc(valid_time)
    ),
    system_time tstzrange NOT NULL CHECK (
        NOT isempty(system_time) AND NOT lower_inf(system_time)
        AND isfinite(lower(system_time)) AND lower_inc(system_time)
        AND NOT upper_inc(system_time)
        AND (upper_inf(system_time) OR isfinite(upper(system_time)))
    ),
    epistemic_status text NOT NULL DEFAULT 'reported' CHECK (epistemic_status = 'reported'),
    explicit_intent boolean NOT NULL CHECK (explicit_intent),
    correction_reason text CHECK (
        (revision = 1 AND correction_reason IS NULL)
        OR (revision > 1 AND correction_reason IS NOT NULL
            AND length(correction_reason) BETWEEN 1 AND 256)
    ),
    search_text tsvector GENERATED ALWAYS AS (to_tsvector('simple', value)) STORED,
    PRIMARY KEY (tenant_id, assertion_id, revision),
    UNIQUE (tenant_id, assertion_id, revision, scope_id),
    FOREIGN KEY (tenant_id, assertion_id, scope_id)
        REFERENCES memory.assertion(tenant_id, id, scope_id),
    EXCLUDE USING gist (tenant_id WITH =, assertion_id WITH =, system_time WITH &&)
);
INSERT INTO memory.assertion_revision
    (tenant_id, assertion_id, scope_id, revision, value, valid_time, system_time,
     epistemic_status, explicit_intent)
SELECT tenant_id, id, scope_id, 1, value, valid_time, system_time,
       epistemic_status, explicit_intent FROM memory.assertion;

ALTER TABLE memory.provenance_edge ADD COLUMN child_revision bigint NOT NULL DEFAULT 1;
ALTER TABLE memory.provenance_edge DROP CONSTRAINT provenance_edge_pkey;
ALTER TABLE memory.provenance_edge ADD PRIMARY KEY
    (tenant_id, child_id, child_revision, parent_id);
ALTER TABLE memory.provenance_edge ADD FOREIGN KEY
    (tenant_id, child_id, child_revision, scope_id)
    REFERENCES memory.assertion_revision(tenant_id, assertion_id, revision, scope_id);

ALTER TABLE memory.assertion
    DROP COLUMN search_text,
    DROP COLUMN value,
    DROP COLUMN valid_time,
    DROP COLUMN system_time,
    DROP COLUMN epistemic_status,
    DROP COLUMN explicit_intent,
    ADD COLUMN current_revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN search_text tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', subject || ' ' || predicate)) STORED;
ALTER TABLE memory.assertion ADD FOREIGN KEY (tenant_id, id, current_revision)
    REFERENCES memory.assertion_revision(tenant_id, assertion_id, revision)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX assertion_fts ON memory.assertion USING gin(search_text);
CREATE INDEX assertion_revision_fts ON memory.assertion_revision USING gin(search_text);
CREATE INDEX assertion_revision_valid ON memory.assertion_revision USING gist(valid_time);

ALTER TABLE memory.assertion_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.assertion_revision FORCE ROW LEVEL SECURITY;
CREATE POLICY revision_read ON memory.assertion_revision FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(assertion_id)
);
CREATE POLICY revision_insert ON memory.assertion_revision FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(assertion_id)
);
CREATE POLICY revision_close ON memory.assertion_revision FOR UPDATE USING (
    tenant_id = memory.current_tenant() AND memory.writable(assertion_id)
) WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(assertion_id)
);
CREATE POLICY revision_delete ON memory.assertion_revision FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(assertion_id)
);
CREATE POLICY assertion_head ON memory.assertion FOR UPDATE USING (
    tenant_id = memory.current_tenant() AND memory.writable(id)
) WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(id)
);

CREATE FUNCTION memory.adopt_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE head bigint; previous_time tstzrange; adopted_at timestamptz;
BEGIN
    SELECT current_revision INTO STRICT head FROM memory.assertion
    WHERE tenant_id = NEW.tenant_id AND id = NEW.assertion_id FOR UPDATE;
    adopted_at := clock_timestamp();
    IF NEW.revision = 1 THEN
        IF head <> 1 THEN
            RAISE EXCEPTION 'invalid initial revision' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.revision <> head + 1 THEN
            RAISE EXCEPTION 'revision conflict' USING ERRCODE = '23514';
        END IF;
        SELECT system_time INTO STRICT previous_time FROM memory.assertion_revision
        WHERE tenant_id = NEW.tenant_id AND assertion_id = NEW.assertion_id AND revision = head;
        IF NOT upper_inf(previous_time) OR adopted_at <= lower(previous_time) THEN
            RAISE EXCEPTION 'system clock or revision conflict' USING ERRCODE = '40001';
        END IF;
        UPDATE memory.assertion_revision
        SET system_time = tstzrange(lower(previous_time), adopted_at, '[)')
        WHERE tenant_id = NEW.tenant_id AND assertion_id = NEW.assertion_id AND revision = head;
        UPDATE memory.assertion SET current_revision = NEW.revision
        WHERE tenant_id = NEW.tenant_id AND id = NEW.assertion_id;
    END IF;
    NEW.system_time := tstzrange(adopted_at, NULL, '[)');
    RETURN NEW;
END;
$$;
CREATE TRIGGER adopt_revision BEFORE INSERT ON memory.assertion_revision
FOR EACH ROW EXECUTE FUNCTION memory.adopt_revision();

CREATE FUNCTION memory.close_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT upper_inf(OLD.system_time) OR upper_inf(NEW.system_time)
       OR lower(NEW.system_time) IS DISTINCT FROM lower(OLD.system_time)
       OR upper(NEW.system_time) > clock_timestamp() THEN
        RAISE EXCEPTION 'only closing a current system interval is allowed' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER close_revision BEFORE UPDATE ON memory.assertion_revision
FOR EACH ROW EXECUTE FUNCTION memory.close_revision();

CREATE FUNCTION memory.check_assertion_history() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid; tenant uuid; head bigint; total bigint;
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
    SELECT count(*) INTO total FROM memory.assertion_revision
    WHERE tenant_id = tenant AND assertion_id = target;
    IF total <> head OR EXISTS (
        SELECT 1 FROM memory.assertion_revision r
        WHERE r.tenant_id = tenant AND r.assertion_id = target
          AND (r.revision > head OR upper_inf(r.system_time) <> (r.revision = head)
            OR (r.revision > 1 AND NOT EXISTS (
                SELECT 1 FROM memory.assertion_revision p
                WHERE p.tenant_id = tenant AND p.assertion_id = target
                  AND p.revision = r.revision - 1
                  AND upper(p.system_time) = lower(r.system_time)
            )))
    ) THEN
        RAISE EXCEPTION 'assertion revision history must be contiguous' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM memory.assertion_revision r
        WHERE r.tenant_id = tenant AND r.assertion_id = target
          AND NOT EXISTS (
            SELECT 1 FROM memory.provenance_edge e
            WHERE e.tenant_id = tenant AND e.child_id = target AND e.child_revision = r.revision
          )
    ) THEN
        RAISE EXCEPTION 'assertion revision requires evidence' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER assertion_history AFTER INSERT OR UPDATE ON memory.assertion
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_assertion_history();
CREATE CONSTRAINT TRIGGER revision_history AFTER INSERT OR UPDATE OR DELETE
ON memory.assertion_revision DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memory.check_assertion_history();
CREATE CONSTRAINT TRIGGER evidence_removed AFTER DELETE ON memory.provenance_edge
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_assertion_history();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM memory.assertion_revision r WHERE NOT EXISTS (
            SELECT 1 FROM memory.provenance_edge e
            WHERE e.tenant_id = r.tenant_id AND e.child_id = r.assertion_id
              AND e.child_revision = r.revision
        )
    ) THEN
        RAISE EXCEPTION 'legacy assertion is missing evidence' USING ERRCODE = '23514';
    END IF;
END;
$$;
GRANT SELECT, INSERT, DELETE ON memory.assertion_revision TO pgag_runtime;
GRANT UPDATE(system_time) ON memory.assertion_revision TO pgag_runtime;
GRANT UPDATE(current_revision) ON memory.assertion TO pgag_runtime;
GRANT SELECT ON public.pgag_schema_migration TO pgag_runtime;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
