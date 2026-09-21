-- Preserve tombstone metadata permissions without a scalar membership call per target.
ALTER POLICY tombstone_read ON memory_ops.object_tombstone USING (
    tenant_id=memory.current_tenant()
    AND scope_id IN (
        SELECT scope_id FROM memory.scope_member
        WHERE tenant_id=memory.current_tenant() AND principal_id=memory.current_principal()
        AND ('read'=ANY(permissions) OR 'admin'=ANY(permissions))
        AND (expires_at IS NULL OR expires_at>statement_timestamp())
    )
);
