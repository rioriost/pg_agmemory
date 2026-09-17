CREATE TABLE memory_ops.scope_access_event (
    tenant_id uuid NOT NULL REFERENCES memory.tenant(id),
    access_epoch bigint NOT NULL CHECK (access_epoch > 1),
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    operation text NOT NULL CHECK (operation IN ('set', 'revoke')),
    database_role name NOT NULL DEFAULT current_user,
    previous_permissions text[],
    previous_expires_at timestamptz,
    permissions text[],
    expires_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, access_epoch),
    FOREIGN KEY (tenant_id, scope_id) REFERENCES memory.scope(tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES memory.principal(tenant_id, id),
    CHECK (previous_permissions IS NOT NULL OR previous_expires_at IS NULL),
    CHECK (previous_permissions <@ ARRAY['read','write','delete','admin']::text[]),
    CHECK (permissions <@ ARRAY['read','write','delete','admin']::text[]),
    CHECK (
        (operation = 'set' AND permissions IS NOT NULL AND cardinality(permissions) > 0)
        OR (operation = 'revoke' AND permissions IS NULL AND expires_at IS NULL)
    )
);
CREATE INDEX scope_access_event_target
ON memory_ops.scope_access_event(tenant_id, scope_id, principal_id, access_epoch DESC);
ALTER TABLE memory_ops.scope_access_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.scope_access_event FORCE ROW LEVEL SECURITY;
