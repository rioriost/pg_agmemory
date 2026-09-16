ALTER TABLE memory.object DROP CONSTRAINT object_kind_check;
ALTER TABLE memory.object ADD CHECK (
    kind IN ('episode','assertion','checkpoint','tool_effect','entity')
);
ALTER TABLE memory.assertion ADD COLUMN is_relation boolean NOT NULL DEFAULT false;

CREATE TABLE memory.entity (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'entity' CHECK (kind = 'entity'),
    entity_type text NOT NULL CHECK (
        entity_type IN ('person','organization','project','component','incident','task','decision','other')
    ),
    canonical_label text NOT NULL CHECK (length(canonical_label) BETWEEN 1 AND 256),
    reference_count integer NOT NULL CHECK (reference_count BETWEEN 1 AND 32),
    explicit_intent boolean NOT NULL CHECK (explicit_intent),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, id, scope_id, kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind)
);
CREATE TABLE memory.entity_evidence (
    tenant_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    source_id uuid NOT NULL,
    quote text NOT NULL CHECK (length(quote) BETWEEN 1 AND 4096),
    PRIMARY KEY (tenant_id, entity_id, source_id),
    FOREIGN KEY (tenant_id, entity_id, scope_id)
        REFERENCES memory.entity(tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, source_id, scope_id)
        REFERENCES memory.episode(tenant_id, id, scope_id)
);
CREATE INDEX entity_evidence_source ON memory.entity_evidence(tenant_id, source_id);
CREATE TABLE memory.relation (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    source_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, id, scope_id)
        REFERENCES memory.assertion(tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, source_id, scope_id)
        REFERENCES memory.entity(tenant_id, id, scope_id)
);
CREATE INDEX relation_source ON memory.relation(tenant_id, source_id, id);
CREATE TABLE memory.relation_revision (
    tenant_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    revision bigint NOT NULL,
    target_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, assertion_id, revision),
    FOREIGN KEY (tenant_id, assertion_id, scope_id)
        REFERENCES memory.relation(tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, assertion_id, revision, scope_id)
        REFERENCES memory.assertion_revision(tenant_id, assertion_id, revision, scope_id),
    FOREIGN KEY (tenant_id, target_id, scope_id)
        REFERENCES memory.entity(tenant_id, id, scope_id)
);
CREATE INDEX relation_target ON memory.relation_revision(tenant_id, target_id, assertion_id, revision);

DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['entity','entity_evidence','relation','relation_revision'] LOOP
        EXECUTE format('ALTER TABLE memory.%I ENABLE ROW LEVEL SECURITY', tab);
        EXECUTE format('ALTER TABLE memory.%I FORCE ROW LEVEL SECURITY', tab);
    END LOOP;
    FOREACH tab IN ARRAY ARRAY['entity','relation'] LOOP
        EXECUTE format('CREATE POLICY object_read ON memory.%I FOR SELECT USING
            (tenant_id = memory.current_tenant() AND memory.visible(id))', tab);
        EXECUTE format('CREATE POLICY object_insert ON memory.%I FOR INSERT WITH CHECK
            (tenant_id = memory.current_tenant() AND memory.writable(id))', tab);
        EXECUTE format('CREATE POLICY object_delete ON memory.%I FOR DELETE USING
            (tenant_id = memory.current_tenant() AND memory.deletable(id))', tab);
    END LOOP;
END;
$$;
CREATE POLICY evidence_read ON memory.entity_evidence FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(entity_id) AND memory.visible(source_id)
);
CREATE POLICY evidence_insert ON memory.entity_evidence FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(entity_id) AND memory.visible(source_id)
);
CREATE POLICY evidence_delete ON memory.entity_evidence FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(entity_id)
);
CREATE FUNCTION memory.check_entity_evidence() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid; tenant uuid; refs integer; total bigint;
BEGIN
    IF TG_TABLE_NAME = 'entity' THEN
        target := NEW.id; tenant := NEW.tenant_id;
    ELSIF TG_OP = 'DELETE' THEN
        target := OLD.entity_id; tenant := OLD.tenant_id;
    ELSE
        target := NEW.entity_id; tenant := NEW.tenant_id;
    END IF;
    SELECT reference_count INTO refs FROM memory.entity
    WHERE tenant_id = tenant AND id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO total FROM memory.entity_evidence
    WHERE tenant_id = tenant AND entity_id = target;
    IF total <> refs THEN
        RAISE EXCEPTION 'entity requires complete evidence' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER entity_evidence AFTER INSERT ON memory.entity
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_entity_evidence();
CREATE CONSTRAINT TRIGGER entity_evidence AFTER INSERT OR DELETE ON memory.entity_evidence
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_entity_evidence();
CREATE POLICY source_read ON memory.relation AS RESTRICTIVE FOR SELECT
USING (memory.visible(source_id));
CREATE POLICY source_insert ON memory.relation AS RESTRICTIVE FOR INSERT
WITH CHECK (memory.visible(source_id));
CREATE POLICY revision_read ON memory.relation_revision FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(assertion_id)
    AND memory.visible(target_id)
    AND EXISTS (SELECT 1 FROM memory.relation r
                WHERE r.tenant_id = relation_revision.tenant_id AND r.id = assertion_id)
);
CREATE POLICY revision_insert ON memory.relation_revision FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(assertion_id)
    AND memory.visible(target_id)
    AND EXISTS (SELECT 1 FROM memory.relation r
                WHERE r.tenant_id = relation_revision.tenant_id AND r.id = assertion_id)
);
CREATE POLICY revision_delete ON memory.relation_revision FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(assertion_id)
);

CREATE FUNCTION memory.check_relation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid; tenant uuid; typed boolean; subject text; predicate text; label text;
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
    IF EXISTS (
        SELECT 1 FROM memory.assertion_revision a
        LEFT JOIN memory.relation_revision r ON r.tenant_id = a.tenant_id
          AND r.assertion_id = a.assertion_id AND r.revision = a.revision
        LEFT JOIN memory.entity e ON e.tenant_id = r.tenant_id AND e.id = r.target_id
        WHERE a.tenant_id = tenant AND a.assertion_id = target
          AND (e.id IS NULL OR a.value <> e.canonical_label)
    ) THEN
        RAISE EXCEPTION 'relation revision requires its exact entity target' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER relation_shape AFTER INSERT OR UPDATE ON memory.assertion
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_relation();
CREATE CONSTRAINT TRIGGER relation_shape AFTER INSERT OR UPDATE OR DELETE ON memory.assertion_revision
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_relation();
CREATE CONSTRAINT TRIGGER relation_shape AFTER INSERT OR DELETE ON memory.relation
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_relation();
CREATE CONSTRAINT TRIGGER relation_shape AFTER INSERT OR DELETE ON memory.relation_revision
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_relation();

DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['checkpoint_reference','tool_effect_reference'] LOOP
        EXECUTE format('ALTER TABLE memory.%I DROP CONSTRAINT %I',
                       tab, tab || '_source_kind_check');
        EXECUTE format('ALTER TABLE memory.%I ADD CHECK
                       (source_kind IN (''episode'',''assertion'',''entity''))', tab);
        EXECUTE format('ALTER TABLE memory.%I DROP CONSTRAINT %I', tab, tab || '_check');
        EXECUTE format('ALTER TABLE memory.%I ADD CHECK
                       (source_kind = ''assertion'' OR source_revision = 1)', tab);
        EXECUTE format('ALTER TABLE memory.%I ADD COLUMN entity_id uuid GENERATED ALWAYS AS
                       (CASE WHEN source_kind = ''entity'' THEN source_id END) STORED', tab);
        EXECUTE format('ALTER TABLE memory.%I ADD FOREIGN KEY (tenant_id,entity_id)
                       REFERENCES memory.entity(tenant_id,id)', tab);
    END LOOP;
END;
$$;
GRANT SELECT, INSERT, DELETE ON memory.entity, memory.entity_evidence,
    memory.relation, memory.relation_revision
TO pgag_runtime;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
