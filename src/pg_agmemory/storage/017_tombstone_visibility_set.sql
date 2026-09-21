-- Both IDs are NOT NULL; the tenant-local anti-set is equivalent to NOT EXISTS.
ALTER POLICY object_read ON memory.object USING (
    tenant_id=memory.current_tenant()
    AND scope_id IN (
        SELECT scope_id FROM memory.scope_member
        WHERE tenant_id=memory.current_tenant() AND principal_id=memory.current_principal()
        AND ('read'=ANY(permissions) OR 'admin'=ANY(permissions))
        AND (expires_at IS NULL OR expires_at>statement_timestamp())
    )
    AND id NOT IN (
        SELECT object_id FROM memory_ops.object_tombstone
        WHERE tenant_id=memory.current_tenant()
    )
);
