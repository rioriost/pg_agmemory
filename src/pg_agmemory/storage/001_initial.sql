CREATE ROLE pgag_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE SCHEMA memory;
CREATE SCHEMA memory_ops;
REVOKE ALL ON SCHEMA memory, memory_ops FROM PUBLIC;
GRANT USAGE ON SCHEMA memory, memory_ops TO pgag_runtime;

CREATE TABLE memory.tenant (
    id uuid PRIMARY KEY,
    access_epoch bigint NOT NULL DEFAULT 1,
    deletion_epoch bigint NOT NULL DEFAULT 1,
    dedup_secret bytea NOT NULL CHECK (octet_length(dedup_secret) = 32)
);
CREATE TABLE memory.principal (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    id uuid NOT NULL,
    external_subject text NOT NULL UNIQUE,
    PRIMARY KEY (tenant_id, id)
);
CREATE TABLE memory.scope (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    id uuid NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE TABLE memory.scope_member (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    permissions text[] NOT NULL CHECK (
        permissions <@ ARRAY['read','write','delete','admin']::text[]
    ),
    expires_at timestamptz,
    PRIMARY KEY (tenant_id, scope_id, principal_id),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES memory.principal(tenant_id, id)
);
CREATE FUNCTION memory.current_tenant() RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN nullif(current_setting('pgag.tenant_id', true), '')::uuid;
CREATE FUNCTION memory.current_principal() RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN nullif(current_setting('pgag.principal_id', true), '')::uuid;
CREATE FUNCTION memory.permitted(target_scope uuid, permission text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN EXISTS (
    SELECT 1 FROM memory.scope_member
    WHERE tenant_id = memory.current_tenant() AND scope_id = target_scope
      AND principal_id = memory.current_principal()
      AND (permission = ANY(permissions) OR 'admin' = ANY(permissions))
      AND (expires_at IS NULL OR expires_at > statement_timestamp())
);

CREATE TABLE memory.object (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('episode','assertion')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, scope_id),
    UNIQUE (tenant_id, id, scope_id, kind),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id)
);
CREATE TABLE memory_ops.object_tombstone (
    tenant_id uuid NOT NULL,
    object_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, object_id),
    FOREIGN KEY (tenant_id, object_id, scope_id)
        REFERENCES memory.object(tenant_id, id, scope_id)
);
ALTER TABLE memory_ops.object_tombstone ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.object_tombstone FORCE ROW LEVEL SECURITY;
CREATE POLICY tombstone_read ON memory_ops.object_tombstone FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'read')
);
CREATE POLICY tombstone_insert ON memory_ops.object_tombstone FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'delete')
);
CREATE FUNCTION memory.visible(target_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN EXISTS (
    SELECT 1 FROM memory.object WHERE tenant_id = memory.current_tenant() AND id = target_id
);
CREATE FUNCTION memory.writable(target_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN EXISTS (
    SELECT 1 FROM memory.object
    WHERE tenant_id = memory.current_tenant() AND id = target_id
      AND memory.permitted(scope_id, 'write')
);
CREATE FUNCTION memory.deletable(target_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN EXISTS (
    SELECT 1 FROM memory.object
    WHERE tenant_id = memory.current_tenant() AND id = target_id
      AND memory.permitted(scope_id, 'delete')
);

CREATE TABLE memory.episode (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'episode' CHECK (kind = 'episode'),
    occurred_at timestamptz NOT NULL,
    content text NOT NULL,
    consent_reference text NOT NULL,
    search_text tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, id, scope_id, kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind)
);
CREATE TABLE memory.assertion (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    scope_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'assertion' CHECK (kind = 'assertion'),
    subject text NOT NULL,
    predicate text NOT NULL CHECK (predicate ~ '^[a-z][a-z0-9_]{0,63}$'),
    value text NOT NULL,
    valid_time tstzrange NOT NULL CHECK (
        NOT isempty(valid_time)
        AND (lower_inf(valid_time) OR lower_inc(valid_time))
        AND NOT upper_inc(valid_time)
    ),
    system_time tstzrange NOT NULL DEFAULT tstzrange(clock_timestamp(), NULL, '[)')
        CHECK (NOT isempty(system_time) AND NOT lower_inf(system_time)
               AND lower_inc(system_time) AND NOT upper_inc(system_time)),
    epistemic_status text NOT NULL DEFAULT 'reported' CHECK (epistemic_status = 'reported'),
    explicit_intent boolean NOT NULL CHECK (explicit_intent),
    search_text tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', subject || ' ' || predicate || ' ' || value)) STORED,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, id, scope_id, kind)
        REFERENCES memory.object(tenant_id, id, scope_id, kind)
);
CREATE TABLE memory.provenance_edge (
    tenant_id uuid NOT NULL,
    child_id uuid NOT NULL,
    parent_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    quote text NOT NULL CHECK (length(quote) > 0),
    PRIMARY KEY (tenant_id, child_id, parent_id),
    FOREIGN KEY (tenant_id, child_id, scope_id)
        REFERENCES memory.assertion(tenant_id, id, scope_id),
    FOREIGN KEY (tenant_id, parent_id, scope_id)
        REFERENCES memory.episode(tenant_id, id, scope_id)
);
CREATE INDEX provenance_parent ON memory.provenance_edge(tenant_id, parent_id);
CREATE INDEX object_scope ON memory.object(tenant_id, scope_id, created_at);
CREATE INDEX episode_fts ON memory.episode USING gin(search_text);
CREATE INDEX assertion_fts ON memory.assertion USING gin(search_text);

CREATE FUNCTION memory.check_evidence() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid;
BEGIN
    IF TG_TABLE_NAME = 'assertion' THEN
        target := NEW.id;
    ELSE
        target := OLD.child_id;
    END IF;
    IF EXISTS (SELECT 1 FROM memory.assertion
               WHERE tenant_id = memory.current_tenant() AND id = target)
       AND NOT EXISTS (SELECT 1 FROM memory.provenance_edge
                       WHERE tenant_id = memory.current_tenant() AND child_id = target) THEN
        RAISE EXCEPTION 'assertion requires evidence' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER assertion_evidence AFTER INSERT ON memory.assertion
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_evidence();
CREATE CONSTRAINT TRIGGER evidence_removed AFTER DELETE ON memory.provenance_edge
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory.check_evidence();

CREATE TABLE memory_ops.source_event (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    event_digest text NOT NULL,
    request_digest text NOT NULL,
    object_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, scope_id, event_digest),
    FOREIGN KEY (tenant_id, object_id) REFERENCES memory.object(tenant_id, id)
);
CREATE TABLE memory_ops.idempotency (
    tenant_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    operation text NOT NULL,
    key_digest text NOT NULL,
    request_digest text NOT NULL,
    result jsonb NOT NULL,
    PRIMARY KEY (tenant_id, principal_id, operation, key_digest),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES memory.principal(tenant_id, id)
);
CREATE TABLE memory_ops.deletion_request (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    id uuid NOT NULL,
    principal_id uuid NOT NULL,
    mode text NOT NULL CHECK (mode IN ('suppress','purge')),
    state text NOT NULL CHECK (state IN ('blocked_for_reads','active_store_purged')),
    object_count integer NOT NULL,
    deletion_epoch bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES memory.principal(tenant_id, id)
);
CREATE TABLE memory_ops.audit_event (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    id bigint GENERATED ALWAYS AS IDENTITY,
    principal_id uuid NOT NULL,
    action text NOT NULL,
    target_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES memory.principal(tenant_id, id)
);

ALTER TABLE memory.tenant ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.tenant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_context ON memory.tenant USING (id = memory.current_tenant());
ALTER TABLE memory.principal ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.principal FORCE ROW LEVEL SECURITY;
CREATE POLICY principal_subject ON memory.principal FOR SELECT
USING (external_subject = current_setting('pgag.subject', true));
ALTER TABLE memory.scope ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.scope FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_context ON memory.scope FOR SELECT
USING (tenant_id = memory.current_tenant() AND memory.permitted(id, 'read'));
ALTER TABLE memory.scope_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.scope_member FORCE ROW LEVEL SECURITY;
CREATE POLICY member_context ON memory.scope_member FOR SELECT
USING (tenant_id = memory.current_tenant() AND principal_id = memory.current_principal());
ALTER TABLE memory.object ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.object FORCE ROW LEVEL SECURITY;
CREATE POLICY object_read ON memory.object FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'read')
    AND NOT EXISTS (
        SELECT 1 FROM memory_ops.object_tombstone t
        WHERE t.tenant_id = object.tenant_id AND t.object_id = object.id
    )
);
CREATE POLICY object_write ON memory.object FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'write')
);

DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['episode','assertion'] LOOP
        EXECUTE format('ALTER TABLE memory.%I ENABLE ROW LEVEL SECURITY', tab);
        EXECUTE format('ALTER TABLE memory.%I FORCE ROW LEVEL SECURITY', tab);
        EXECUTE format(
            'CREATE POLICY content_read ON memory.%I FOR SELECT USING
             (tenant_id = memory.current_tenant() AND memory.visible(id))', tab);
        EXECUTE format(
            'CREATE POLICY content_write ON memory.%I FOR INSERT WITH CHECK
             (tenant_id = memory.current_tenant() AND memory.writable(id))', tab);
        EXECUTE format(
            'CREATE POLICY content_delete ON memory.%I FOR DELETE USING
             (tenant_id = memory.current_tenant() AND memory.deletable(id))', tab);
    END LOOP;
END;
$$;
ALTER TABLE memory.provenance_edge ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.provenance_edge FORCE ROW LEVEL SECURITY;
CREATE POLICY evidence_read ON memory.provenance_edge FOR SELECT USING (
    tenant_id = memory.current_tenant()
    AND memory.visible(child_id) AND memory.visible(parent_id)
);
CREATE POLICY evidence_write ON memory.provenance_edge FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant()
    AND memory.writable(child_id) AND memory.visible(parent_id)
);
CREATE POLICY evidence_delete ON memory.provenance_edge FOR DELETE USING (
    tenant_id = memory.current_tenant() AND memory.deletable(child_id)
);

DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['source_event','idempotency','deletion_request','audit_event'] LOOP
        EXECUTE format('ALTER TABLE memory_ops.%I ENABLE ROW LEVEL SECURITY', tab);
        EXECUTE format('ALTER TABLE memory_ops.%I FORCE ROW LEVEL SECURITY', tab);
    END LOOP;
END;
$$;
CREATE POLICY source_context ON memory_ops.source_event USING (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'read')
) WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'write')
);
CREATE POLICY idempotency_context ON memory_ops.idempotency USING (
    tenant_id = memory.current_tenant() AND principal_id = memory.current_principal()
);
CREATE POLICY deletion_context ON memory_ops.deletion_request USING (
    tenant_id = memory.current_tenant() AND principal_id = memory.current_principal()
);
CREATE POLICY audit_context ON memory_ops.audit_event FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND principal_id = memory.current_principal()
);

GRANT SELECT ON ALL TABLES IN SCHEMA memory TO pgag_runtime;
GRANT INSERT ON memory.object, memory.episode, memory.assertion, memory.provenance_edge
TO pgag_runtime;
GRANT UPDATE(deletion_epoch) ON memory.tenant TO pgag_runtime;
GRANT DELETE ON memory.episode, memory.assertion, memory.provenance_edge TO pgag_runtime;
GRANT SELECT, INSERT ON memory_ops.source_event, memory_ops.idempotency,
    memory_ops.deletion_request, memory_ops.object_tombstone TO pgag_runtime;
GRANT INSERT ON memory_ops.audit_event TO pgag_runtime;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA memory_ops TO pgag_runtime;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA memory FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memory TO pgag_runtime;
