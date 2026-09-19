ALTER TABLE memory_ops.deletion_request
    ADD COLUMN target_manifest_version smallint NOT NULL DEFAULT 0
        CHECK (target_manifest_version IN (0,1)),
    ADD COLUMN manifest_xid xid8,
    ADD CONSTRAINT deletion_epoch_unique UNIQUE (tenant_id,deletion_epoch);
ALTER TABLE memory_ops.deletion_request ALTER COLUMN target_manifest_version SET DEFAULT 1;
ALTER TABLE memory_ops.object_tombstone
    ADD CONSTRAINT tombstone_scope_unique UNIQUE (tenant_id,object_id,scope_id);

CREATE TABLE memory_ops.deletion_target (
    tenant_id uuid NOT NULL,
    deletion_id uuid NOT NULL,
    object_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 1 AND 10100),
    PRIMARY KEY (tenant_id,deletion_id,object_id),
    UNIQUE (tenant_id,deletion_id,ordinal),
    FOREIGN KEY (tenant_id,deletion_id) REFERENCES memory_ops.deletion_request(tenant_id,id),
    FOREIGN KEY (tenant_id,object_id,scope_id)
        REFERENCES memory_ops.object_tombstone(tenant_id,object_id,scope_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX deletion_target_object ON memory_ops.deletion_target(tenant_id,object_id);
ALTER TABLE memory_ops.deletion_target ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ops.deletion_target FORCE ROW LEVEL SECURITY;
CREATE POLICY deletion_target_read ON memory_ops.deletion_target FOR SELECT USING (
    tenant_id=memory.current_tenant() AND memory.permitted(scope_id,'read')
    AND EXISTS (
        SELECT 1 FROM memory_ops.deletion_request d
        WHERE d.tenant_id=deletion_target.tenant_id AND d.id=deletion_target.deletion_id
        AND d.principal_id=memory.current_principal()
    )
);
CREATE POLICY deletion_target_insert ON memory_ops.deletion_target FOR INSERT WITH CHECK (
    tenant_id=memory.current_tenant() AND memory.permitted(scope_id,'delete')
    AND EXISTS (
        SELECT 1 FROM memory_ops.deletion_request d
        WHERE d.tenant_id=deletion_target.tenant_id AND d.id=deletion_target.deletion_id
        AND d.principal_id=memory.current_principal() AND d.target_manifest_version=1
    )
);
GRANT SELECT,INSERT ON memory_ops.deletion_target TO pgag_runtime;

CREATE FUNCTION memory.guard_deletion_manifest_version() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.target_manifest_version<>1 OR NEW.object_count NOT BETWEEN 1 AND 10100
       OR (NEW.mode='purge')<>(NEW.state='active_store_purged') THEN
        RAISE EXCEPTION 'invalid deletion manifest' USING ERRCODE='23514';
    END IF;
    NEW.manifest_xid:=pg_current_xact_id();
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_deletion_manifest_version BEFORE INSERT ON memory_ops.deletion_request
FOR EACH ROW EXECUTE FUNCTION memory.guard_deletion_manifest_version();

CREATE FUNCTION memory.check_deletion_manifest() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE actual bigint;
BEGIN
    SELECT count(*) INTO actual FROM memory_ops.deletion_target
      WHERE tenant_id=NEW.tenant_id AND deletion_id=NEW.id;
    IF actual<>NEW.object_count THEN
        RAISE EXCEPTION 'deletion manifest incomplete' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER deletion_manifest_complete
AFTER INSERT ON memory_ops.deletion_request DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memory.check_deletion_manifest();

CREATE FUNCTION memory.guard_deletion_target() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM memory_ops.deletion_request d
        WHERE d.tenant_id=NEW.tenant_id AND d.id=NEW.deletion_id
        AND d.target_manifest_version=1 AND d.manifest_xid=pg_current_xact_id()
        AND NEW.ordinal BETWEEN 1 AND d.object_count
    ) THEN
        RAISE EXCEPTION 'deletion manifest sealed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_deletion_target BEFORE INSERT ON memory_ops.deletion_target
FOR EACH ROW EXECUTE FUNCTION memory.guard_deletion_target();

CREATE FUNCTION memory.check_tombstone_manifest() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM memory_ops.deletion_target
        WHERE tenant_id=NEW.tenant_id AND object_id=NEW.object_id AND scope_id=NEW.scope_id
    ) THEN
        RAISE EXCEPTION 'tombstone manifest missing' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER tombstone_manifest_complete
AFTER INSERT ON memory_ops.object_tombstone DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION memory.check_tombstone_manifest();

REVOKE ALL ON FUNCTION memory.guard_deletion_manifest_version(),
    memory.check_deletion_manifest(), memory.guard_deletion_target(),
    memory.check_tombstone_manifest() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memory.guard_deletion_manifest_version(),
    memory.check_deletion_manifest(), memory.guard_deletion_target(),
    memory.check_tombstone_manifest() TO pgag_runtime;
