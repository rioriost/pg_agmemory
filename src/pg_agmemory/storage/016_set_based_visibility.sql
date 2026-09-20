-- Statement-local sets preserve the same invoker/RLS visibility without per-row SQL calls.
ALTER POLICY object_read ON memory.object USING (
    tenant_id=memory.current_tenant()
    AND scope_id IN (
        SELECT scope_id FROM memory.scope_member
        WHERE tenant_id=memory.current_tenant() AND principal_id=memory.current_principal()
        AND ('read'=ANY(permissions) OR 'admin'=ANY(permissions))
        AND (expires_at IS NULL OR expires_at>statement_timestamp())
    )
    AND NOT EXISTS (
        SELECT 1 FROM memory_ops.object_tombstone t
        WHERE t.tenant_id=object.tenant_id AND t.object_id=object.id
    )
);
ALTER POLICY content_read ON memory.episode USING (
    tenant_id=memory.current_tenant()
    AND id IN (SELECT id FROM memory.object WHERE tenant_id=memory.current_tenant())
);
ALTER POLICY content_read ON memory.assertion USING (
    tenant_id=memory.current_tenant()
    AND id IN (SELECT id FROM memory.object WHERE tenant_id=memory.current_tenant())
);
ALTER POLICY revision_read ON memory.assertion_revision USING (
    tenant_id=memory.current_tenant()
    AND assertion_id IN (SELECT id FROM memory.object WHERE tenant_id=memory.current_tenant())
);
ALTER POLICY episode_embedding_read ON memory.episode_embedding USING (
    tenant_id=memory.current_tenant()
    AND episode_id IN (SELECT id FROM memory.object WHERE tenant_id=memory.current_tenant())
);
ALTER POLICY assertion_embedding_read ON memory.assertion_embedding USING (
    tenant_id=memory.current_tenant()
    AND assertion_id IN (SELECT id FROM memory.object WHERE tenant_id=memory.current_tenant())
);
ALTER POLICY episode_lexical_read ON memory.episode_lexical USING (
    tenant_id=memory.current_tenant()
    AND episode_id IN (SELECT id FROM memory.object WHERE tenant_id=memory.current_tenant())
);
ALTER POLICY assertion_lexical_read ON memory.assertion_lexical USING (
    tenant_id=memory.current_tenant()
    AND assertion_id IN (SELECT id FROM memory.object WHERE tenant_id=memory.current_tenant())
);
