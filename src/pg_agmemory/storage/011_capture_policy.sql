CREATE TABLE memory.scope_capture_policy (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    access_epoch bigint NOT NULL CHECK (access_epoch > 1),
    enabled boolean NOT NULL,
    source_namespaces text[],
    consent_references text[],
    max_content_bytes integer NOT NULL CHECK (max_content_bytes BETWEEN 1 AND 262144),
    PRIMARY KEY (tenant_id, scope_id),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id),
    CHECK (source_namespaces IS NULL OR (
        cardinality(source_namespaces) <= 64 AND array_position(source_namespaces, NULL) IS NULL
    )),
    CHECK (consent_references IS NULL OR (
        cardinality(consent_references) <= 64 AND array_position(consent_references, NULL) IS NULL
    ))
);
ALTER TABLE memory.scope_capture_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.scope_capture_policy FORCE ROW LEVEL SECURITY;
CREATE POLICY capture_policy_read ON memory.scope_capture_policy FOR SELECT USING (
    tenant_id = memory.current_tenant() AND memory.permitted(scope_id, 'read')
);
GRANT SELECT ON memory.scope_capture_policy TO pgag_runtime;

CREATE TABLE memory_ops.capture_policy_event (
    tenant_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    access_epoch bigint NOT NULL CHECK (access_epoch > 1),
    database_role name NOT NULL DEFAULT current_user,
    previous_policy jsonb NOT NULL CHECK (jsonb_typeof(previous_policy) = 'object'),
    policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, access_epoch),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id)
);
ALTER TABLE memory_ops.capture_policy_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.capture_policy_event FORCE ROW LEVEL SECURITY;
