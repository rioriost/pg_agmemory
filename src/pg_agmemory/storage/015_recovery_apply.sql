CREATE TABLE memory_ops.recovery_key (
    tenant_id uuid PRIMARY KEY REFERENCES memory.tenant(id),
    secret bytea NOT NULL DEFAULT (
        uuid_send(gen_random_uuid()) || uuid_send(gen_random_uuid())
    ) CHECK (octet_length(secret)=32)
);
ALTER TABLE memory_ops.recovery_key ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.recovery_key FORCE ROW LEVEL SECURITY;
INSERT INTO memory_ops.recovery_key(tenant_id) SELECT id FROM memory.tenant;
CREATE FUNCTION memory.create_recovery_key() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    INSERT INTO memory_ops.recovery_key(tenant_id) VALUES (NEW.id);
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION memory.create_recovery_key() FROM PUBLIC;
CREATE TRIGGER create_recovery_key AFTER INSERT ON memory.tenant
FOR EACH ROW EXECUTE FUNCTION memory.create_recovery_key();

CREATE FUNCTION memory.recovery_apply_authorized() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
    SELECT COALESCE(current_setting('pgag.recovery_apply',true),'')='on' AND EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname=current_user AND (rolsuper OR rolbypassrls)
    )
$$;
REVOKE ALL ON FUNCTION memory.recovery_apply_authorized() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memory.recovery_apply_authorized() TO pgag_runtime;

DROP TRIGGER guard_job ON memory_ops.job;
CREATE TRIGGER guard_job BEFORE INSERT OR UPDATE ON memory_ops.job
FOR EACH ROW WHEN (NOT COALESCE(memory.recovery_apply_authorized(),false))
EXECUTE FUNCTION memory.guard_job();
DROP TRIGGER guard_processing_job ON memory_ops.job;
CREATE TRIGGER guard_processing_job BEFORE INSERT OR UPDATE ON memory_ops.job
FOR EACH ROW WHEN (NOT COALESCE(memory.recovery_apply_authorized(),false))
EXECUTE FUNCTION memory.guard_processing_job();
DROP TRIGGER guard_model_call ON memory_ops.model_call;
CREATE TRIGGER guard_model_call BEFORE INSERT OR UPDATE ON memory_ops.model_call
FOR EACH ROW WHEN (NOT COALESCE(memory.recovery_apply_authorized(),false))
EXECUTE FUNCTION memory.guard_model_call();
DROP TRIGGER guard_deletion_manifest_version ON memory_ops.deletion_request;
CREATE TRIGGER guard_deletion_manifest_version BEFORE INSERT ON memory_ops.deletion_request
FOR EACH ROW WHEN (NOT COALESCE(memory.recovery_apply_authorized(),false))
EXECUTE FUNCTION memory.guard_deletion_manifest_version();
DROP TRIGGER guard_deletion_target ON memory_ops.deletion_target;
CREATE TRIGGER guard_deletion_target BEFORE INSERT ON memory_ops.deletion_target
FOR EACH ROW WHEN (NOT COALESCE(memory.recovery_apply_authorized(),false))
EXECUTE FUNCTION memory.guard_deletion_target();
