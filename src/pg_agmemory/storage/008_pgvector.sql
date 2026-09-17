CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public VERSION '0.8.6';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname = 'vector' AND e.extversion = '0.8.6' AND n.nspname = 'public'
    ) THEN
        RAISE EXCEPTION 'pgvector 0.8.6 in public is required';
    END IF;
END;
$$;

CREATE TABLE memory.episode_embedding (
    tenant_id uuid NOT NULL,
    episode_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision = 1),
    scope_id uuid NOT NULL,
    model_name text NOT NULL CHECK (length(model_name) BETWEEN 1 AND 256),
    model_revision text NOT NULL CHECK (length(model_revision) BETWEEN 1 AND 256),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    embedding public.vector(768) NOT NULL
        CHECK (abs(public.vector_norm(embedding) - 1) < 0.0001),
    PRIMARY KEY (tenant_id, episode_id, revision, model_name, model_revision),
    FOREIGN KEY (tenant_id, episode_id, scope_id)
        REFERENCES memory.episode(tenant_id, id, scope_id) ON DELETE CASCADE
);
CREATE TABLE memory.assertion_embedding (
    tenant_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    revision bigint NOT NULL,
    scope_id uuid NOT NULL,
    model_name text NOT NULL CHECK (length(model_name) BETWEEN 1 AND 256),
    model_revision text NOT NULL CHECK (length(model_revision) BETWEEN 1 AND 256),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    embedding public.vector(768) NOT NULL
        CHECK (abs(public.vector_norm(embedding) - 1) < 0.0001),
    PRIMARY KEY (tenant_id, assertion_id, revision, model_name, model_revision),
    FOREIGN KEY (tenant_id, assertion_id, revision, scope_id)
        REFERENCES memory.assertion_revision(tenant_id, assertion_id, revision, scope_id)
        ON DELETE CASCADE
);

ALTER TABLE memory.episode_embedding ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.episode_embedding FORCE ROW LEVEL SECURITY;
CREATE POLICY episode_embedding_read ON memory.episode_embedding FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(episode_id)
);
CREATE POLICY episode_embedding_insert ON memory.episode_embedding FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(episode_id)
);
ALTER TABLE memory.assertion_embedding ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.assertion_embedding FORCE ROW LEVEL SECURITY;
CREATE POLICY assertion_embedding_read ON memory.assertion_embedding FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(assertion_id)
);
CREATE POLICY assertion_embedding_insert ON memory.assertion_embedding FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(assertion_id)
);
GRANT SELECT, INSERT ON memory.episode_embedding, memory.assertion_embedding TO pgag_runtime;

CREATE FUNCTION memory.guard_embedding_count() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE total bigint;
BEGIN
    IF TG_TABLE_NAME = 'episode_embedding' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'embedding:' || NEW.tenant_id::text || ':' || NEW.episode_id::text || ':1', 0));
        SELECT count(*) INTO total FROM memory.episode_embedding
        WHERE tenant_id = NEW.tenant_id AND episode_id = NEW.episode_id;
    ELSE
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'embedding:' || NEW.tenant_id::text || ':' || NEW.assertion_id::text
            || ':' || NEW.revision::text, 0));
        SELECT count(*) INTO total FROM memory.assertion_embedding
        WHERE tenant_id = NEW.tenant_id AND assertion_id = NEW.assertion_id
          AND revision = NEW.revision;
    END IF;
    IF total >= 8 THEN
        RAISE EXCEPTION 'embedding model limit exceeded' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER episode_embedding_count BEFORE INSERT ON memory.episode_embedding
FOR EACH ROW EXECUTE FUNCTION memory.guard_embedding_count();
CREATE TRIGGER assertion_embedding_count BEFORE INSERT ON memory.assertion_embedding
FOR EACH ROW EXECUTE FUNCTION memory.guard_embedding_count();
