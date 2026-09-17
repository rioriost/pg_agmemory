SET LOCAL row_security = off;

CREATE TABLE memory.episode_lexical (
    tenant_id uuid NOT NULL,
    episode_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    profile text NOT NULL CHECK (profile = 'ja-janome-0.5.0-v1'),
    search_text tsvector NOT NULL,
    PRIMARY KEY (tenant_id, episode_id, profile),
    FOREIGN KEY (tenant_id, episode_id, scope_id)
        REFERENCES memory.episode(tenant_id, id, scope_id) ON DELETE CASCADE
);
CREATE TABLE memory.assertion_lexical (
    tenant_id uuid NOT NULL,
    assertion_id uuid NOT NULL,
    revision bigint NOT NULL,
    scope_id uuid NOT NULL,
    profile text NOT NULL CHECK (profile = 'ja-janome-0.5.0-v1'),
    search_text tsvector NOT NULL,
    PRIMARY KEY (tenant_id, assertion_id, revision, profile),
    FOREIGN KEY (tenant_id, assertion_id, revision, scope_id)
        REFERENCES memory.assertion_revision(tenant_id, assertion_id, revision, scope_id)
        ON DELETE CASCADE
);
CREATE INDEX episode_lexical_fts ON memory.episode_lexical USING gin(search_text);
CREATE INDEX assertion_lexical_fts ON memory.assertion_lexical USING gin(search_text);

ALTER TABLE memory.episode_lexical ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.episode_lexical FORCE ROW LEVEL SECURITY;
CREATE POLICY episode_lexical_read ON memory.episode_lexical FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(episode_id)
);
CREATE POLICY episode_lexical_insert ON memory.episode_lexical FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(episode_id)
);
ALTER TABLE memory.assertion_lexical ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.assertion_lexical FORCE ROW LEVEL SECURITY;
CREATE POLICY assertion_lexical_read ON memory.assertion_lexical FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.visible(assertion_id)
);
CREATE POLICY assertion_lexical_insert ON memory.assertion_lexical FOR INSERT WITH CHECK (
    tenant_id = memory.current_tenant() AND memory.writable(assertion_id)
);
GRANT SELECT, INSERT ON memory.episode_lexical, memory.assertion_lexical TO pgag_runtime;
