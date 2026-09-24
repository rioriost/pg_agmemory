import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from pydantic import ValidationError
from test_processing import configure, enqueue, get, install_extraction, run
from test_processing import profile as profile

from pg_agmemory.admin import AdminError
from pg_agmemory.capture_policy import CapturePolicy, CapturePolicyRequest, capture_policy
from pg_agmemory.jobs import job_transaction
from pg_agmemory.processing import Processing
from pg_agmemory.processing_recovery import capture_processing_state
from pg_agmemory.recovery_apply import (
    CONTENT_TABLES,
    REPLACE_TABLES,
    ROW_TABLES,
    RecoveryBundle,
    apply_bundle,
    export_bundle,
    insert_rows,
    main,
    secret_for,
    signature,
    table_rows,
)

SOURCE_ACCESS_TABLES = ("memory_ops.source_access_state", "memory_ops.source_access_event")


@pytest.mark.parametrize("table", SOURCE_ACCESS_TABLES)
def test_source_authority_is_exact_content_never_replaced_or_imported(table):
    from pg_agmemory.processing_recovery import TABLES

    assert table in TABLES and table in CONTENT_TABLES
    assert table not in REPLACE_TABLES and table not in ROW_TABLES
    assert TABLES[table] == (
        "scope_id,principal_id,sequence" if table.endswith("_event") else "scope_id,principal_id"
    )


@pytest.mark.parametrize("table", SOURCE_ACCESS_TABLES)
@pytest.mark.parametrize("section", ["content", "reference", "rows"])
def test_source_authority_fingerprints_cannot_be_omitted_or_imported(table, section):
    from test_processing_recovery import snapshot

    from pg_agmemory.processing_recovery import StateFingerprint

    bundle = RecoveryBundle(
        reference=snapshot(),
        content=tuple(StateFingerprint(table=t, rows=0, digest="c" * 64) for t in CONTENT_TABLES),
        rows={t: [] for t in ROW_TABLES}, signature="d" * 64,
    ).model_dump(mode="json")
    if section == "rows":
        bundle["rows"][table] = []
    else:
        fingerprints = bundle["content"] if section == "content" else bundle["reference"]["tables"]
        fingerprints[:] = [row for row in fingerprints if row["table"] != table]
    with pytest.raises(ValidationError):
        RecoveryBundle.model_validate_json(json.dumps(bundle))


@pytest.mark.parametrize("table", SOURCE_ACCESS_TABLES)
@pytest.mark.parametrize("section", ["content", "reference"])
def test_source_authority_fingerprint_tampering_is_authenticated(env, table, section):
    bundle = export_bundle(env.admin_url, env.tenants[0])
    value = bundle.model_dump(mode="json")
    fingerprints = value["content"] if section == "content" else value["reference"]["tables"]
    next(row for row in fingerprints if row["table"] == table)["digest"] = "0" * 64
    forged = RecoveryBundle.model_validate_json(json.dumps(value))
    with pytest.raises(AdminError, match="^recovery_bundle_authentication_failed$"):
        apply_bundle(env.admin_url, bundle.reference, forged, isolated=True)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == bundle.reference


def bind_source_authority(env):
    from pg_agmemory.source_access import SourceAccessRequest, SourceIdentity, source_access

    principal = uuid4()
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
            (env.tenants[0], principal, "recovery-source-" + principal.hex),
        )
        epoch = conn.execute(
            "SELECT access_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],),
        ).fetchone()[0]
    source = SourceIdentity(
        source_system="synthetic-recovery", dataset_id="fixture", source_subject=principal.hex,
    )
    request = SourceAccessRequest(
        operation="bind", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=principal, expected_access_epoch=epoch, source=source,
    )
    with source_access(env.admin_url, request) as result:
        return request, result


def source_authority_rows(env):
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        return {table: table_rows(conn, env.tenants[0], table) for table in SOURCE_ACCESS_TABLES}


@pytest.mark.parametrize("incoming", ["older", "newer"])
def test_changed_source_authority_is_not_reset_or_imported_by_authenticated_recovery(env, incoming):
    from pg_agmemory.source_access import SourceAccessRequest, SourceNotice, source_access

    request, bound = bind_source_authority(env)
    old = export_bundle(env.admin_url, env.tenants[0])
    original = source_authority_rows(env)
    with source_access(env.admin_url, SourceAccessRequest(
        operation="apply", tenant_id=request.tenant_id, scope_id=request.scope_id,
        principal_id=request.principal_id, expected_access_epoch=bound.access_epoch,
        notice=SourceNotice(
            source=request.source, sequence=1, decision="deny", reason="revoked",
        ),
    )) as denied:
        assert denied.sequence == 1 and denied.access_epoch == bound.access_epoch
    latest = export_bundle(env.admin_url, env.tenants[0])
    if incoming == "newer":
        # Model the exact older logical dump, including original timestamps and cursor.
        with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
            for table in SOURCE_ACCESS_TABLES:
                conn.execute(psycopg.sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(
                    psycopg.sql.Identifier(*table.split(".")),
                    psycopg.sql.Identifier("guard_" + table.split(".")[1]),
                ))
            conn.execute(
                "DELETE FROM memory_ops.source_access_event WHERE tenant_id=%s",
                (env.tenants[0],),
            )
            conn.execute(
                "DELETE FROM memory_ops.source_access_state WHERE tenant_id=%s",
                (env.tenants[0],),
            )
            for table in SOURCE_ACCESS_TABLES:
                insert_rows(conn, table, original[table])
                conn.execute(psycopg.sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(
                    psycopg.sql.Identifier(*table.split(".")),
                    psycopg.sql.Identifier("guard_" + table.split(".")[1]),
                ))
        expected, bundle = old.reference, latest
    else:
        expected, bundle = latest.reference, old
    assert capture_processing_state(env.admin_url, env.tenants[0]) == expected
    before = source_authority_rows(env)
    with pytest.raises(AdminError, match="^recovery_content_mismatch$"):
        apply_bundle(env.admin_url, expected, bundle, isolated=True)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == expected
    assert source_authority_rows(env) == before


def test_unchanged_source_lease_and_cursor_survive_without_revalidation_or_refresh(
    env, monkeypatch,
):
    from pg_agmemory.source_access import SourceAccessRequest, SourceNotice, source_access

    request, bound = bind_source_authority(env)
    with psycopg.connect(env.admin_url) as conn:
        now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    with source_access(env.admin_url, SourceAccessRequest(
        operation="apply", tenant_id=request.tenant_id, scope_id=request.scope_id,
        principal_id=request.principal_id, expected_access_epoch=bound.access_epoch,
        notice=SourceNotice(
            source=request.source, sequence=1, decision="allow", reason="authorized",
            acl_version="lease-one", verified_at=now, valid_until=now + timedelta(seconds=120),
        ),
    )) as allowed:
        assert allowed.effective_permissions == ["read"]
    bundle = export_bundle(env.admin_url, env.tenants[0])
    before = source_authority_rows(env)

    def forbidden(*args, **kwargs):
        pytest.fail("recovery must not invoke the source coordinator")

    monkeypatch.setattr("pg_agmemory.source_access._apply_source_access", forbidden)
    assert apply_bundle(env.admin_url, bundle.reference, bundle, isolated=True) == bundle.reference
    assert source_authority_rows(env) == before
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            """SELECT expires_at FROM memory.scope_member
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (request.tenant_id, request.scope_id, request.principal_id),
        ).fetchone() == (allowed.expires_at,)


def test_graph_generation_metadata_is_verified_but_never_imported():
    tables = {
        "memory_ops.graph_generation", "memory_ops.graph_generation_state",
        "memory_ops.age_projection",
    }
    assert tables <= CONTENT_TABLES.keys()
    assert tables.isdisjoint(ROW_TABLES)


def test_dataset_emergency_revocation_restores_without_replaying_source_notices(env, monkeypatch):
    from pg_agmemory.source_access import (
        SourceAccessRequest,
        SourceDatasetIdentity,
        SourceNotice,
        source_access,
    )
    from pg_agmemory.source_dataset import SourceDatasetRequest, source_dataset

    for _ in range(2):
        request, bound = bind_source_authority(env)
        with psycopg.connect(env.admin_url) as conn:
            now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        with source_access(env.admin_url, SourceAccessRequest(
            operation="apply", tenant_id=request.tenant_id, scope_id=request.scope_id,
            principal_id=request.principal_id, expected_access_epoch=bound.access_epoch,
            notice=SourceNotice(
                source=request.source, sequence=1, decision="allow", reason="authorized",
                acl_version="dataset-lease", verified_at=now,
                valid_until=now + timedelta(seconds=120),
            ),
        )) as allowed:
            assert allowed.effective_permissions == ["read"]
    old = export_bundle(env.admin_url, env.tenants[0])
    source_before = source_authority_rows(env)
    inspection = SourceDatasetRequest(
        operation="get", tenant_id=env.tenants[0],
        dataset=SourceDatasetIdentity(source_system="synthetic-recovery", dataset_id="fixture"),
    )
    with source_dataset(env.admin_url, inspection) as plan:
        assert plan.matched_targets == 2
    with source_dataset(env.admin_url, SourceDatasetRequest(
        operation="revoke", tenant_id=inspection.tenant_id, dataset=inspection.dataset,
        expected_access_epoch=plan.access_epoch, expected_target_digest=plan.target_digest,
    )) as revoked:
        assert revoked.changed_targets == 2
        assert revoked.access_epoch == old.reference.access_epoch + 2
    latest = export_bundle(env.admin_url, env.tenants[0])
    assert source_authority_rows(env) == source_before

    # Model loading only the changed operational rows from the older isolated dump.
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        conn.execute("SET LOCAL pgag.recovery_apply='on'")
        for table in ("memory_ops.scope_access_event", "memory.scope_member"):
            conn.execute(psycopg.sql.SQL("DELETE FROM {} WHERE tenant_id=%s").format(
                psycopg.sql.Identifier(*table.split(".")),
            ), (env.tenants[0],))
            insert_rows(conn, table, old.rows[table])
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s",
            (old.reference.access_epoch, env.tenants[0]),
        )
    assert capture_processing_state(env.admin_url, env.tenants[0]) == old.reference

    def forbidden(*args, **kwargs):
        pytest.fail("recovery must not consume or replay source notifications")

    monkeypatch.setattr("pg_agmemory.source_access._apply_source_access", forbidden)
    assert apply_bundle(env.admin_url, old.reference, latest, isolated=True) == latest.reference
    assert source_authority_rows(env) == source_before
    with source_dataset(env.admin_url, inspection) as recovered:
        assert recovered.access_epoch == revoked.access_epoch
        assert recovered.target_digest == plan.target_digest
        assert all(target.decision == "allow" and target.sequence == 1
                   and not target.membership_exists and not target.effective_permissions
                   for target in recovered.targets)


@pytest.mark.parametrize("schema", [18, 19, 20, 21])
def test_previous_schema_bundle_requires_matching_version(env, schema):
    bundle = export_bundle(env.admin_url, env.tenants[0]).model_dump(mode="json")
    bundle["reference"]["schema_version"] = schema
    with pytest.raises(ValidationError):
        RecoveryBundle.model_validate_json(json.dumps(bundle))


def reserve(env, profile):
    async def operation():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            claim = await jobs.claim(lease_seconds=60, profile_digest=profile.digest)
            await Processing(jobs.memory).prepare(
                claim["job_id"], claim["lease_token"], profile, reserve=True,
            )
            await jobs.fail(claim["job_id"], claim["lease_token"], "billing_unknown", retry=False)
    asyncio.run(operation())


def restore_old_jobs(env, old):
    # Test-only equivalent of loading the older operational rows from a dump.
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        conn.execute("SET LOCAL pgag.recovery_apply='on'")
        conn.execute("DELETE FROM memory_ops.model_call WHERE tenant_id=%s", (env.tenants[0],))
        insert_rows(conn, "memory_ops.model_call", old.rows["memory_ops.model_call"])
        insert_rows(conn, "memory_ops.job", old.rows["memory_ops.job"], upsert=True)


@pytest.mark.parametrize("outcome", ["unknown", "succeeded"])
def test_applies_original_call_and_job_state_without_new_ids_or_calls(
    env, profile, monkeypatch, outcome,
):
    configure(env, profile, max_calls=1)
    source = env.observe("Alice / preferred_editor: Vim").json()["memory_id"]
    job = enqueue(env, source).json()["job_id"]
    old = export_bundle(env.admin_url, env.tenants[0])
    if outcome == "unknown":
        reserve(env, profile)
    else:
        # Empty successful extraction changes no immutable memory content.
        from pg_agmemory.providers import ExtractionResult
        async def extract(data):
            return ExtractionResult(model=profile.settings.text_model,
                                    input_digest=data.digest(), candidates=[])
        monkeypatch.setattr(profile.provider, "extract", extract)
        assert run(env, profile)["outcome"] == "succeeded"
    latest = export_bundle(env.admin_url, env.tenants[0])
    restore_old_jobs(env, old)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == old.reference
    final = apply_bundle(env.admin_url, old.reference, latest, isolated=True)
    assert final == latest.reference
    assert capture_processing_state(env.admin_url, env.tenants[0]) == latest.reference
    assert not final.restore_authorized
    calls = install_extraction(monkeypatch, profile)
    assert enqueue(env, source).json()["job_id"] == job
    if outcome == "unknown":
        assert get(env, job).json()["call"]["billing_unknown"] is True
        assert enqueue(env, source, retry_of=job).json()["code"] == "job_retry_unknown"
    assert run(env, profile) == {"outcome": "idle"} and calls == []
    other = env.observe("Alice / preferred_editor: Vim second source").json()["memory_id"]
    assert enqueue(env, other).status_code == 202
    assert run(env, profile)["outcome"] == "failed" and calls == []


def test_runtime_cannot_enable_historical_write_path(env):
    with psycopg.connect(env.settings.database_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT secret FROM memory_ops.recovery_key")
    with psycopg.connect(env.settings.database_url) as conn:
        conn.execute("SET LOCAL pgag.recovery_apply='on'")
        assert conn.execute("SELECT memory.recovery_apply_authorized()").fetchone() == (False,)
        conn.execute(
            """SELECT set_config('pgag.tenant_id',%s,true),
                      set_config('pgag.principal_id',%s,true),
                      set_config('pgag.subject',%s,true)""",
            (str(env.tenants[0]), str(env.principals[0]), env.subjects[0]),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """INSERT INTO memory_ops.deletion_request
                   (tenant_id,id,principal_id,mode,state,object_count,deletion_epoch,
                    target_manifest_version) VALUES (%s,gen_random_uuid(),%s,
                   'purge','active_store_purged',1,2,0)""",
                (env.tenants[0], env.principals[0]),
            )


def test_isolation_role_and_cas_are_required(env):
    bundle = export_bundle(env.admin_url, env.tenants[0])
    with pytest.raises(AdminError, match="isolation_required"):
        apply_bundle(env.admin_url, bundle.reference, bundle, isolated=False)
    with pytest.raises(AdminError, match="admin_role_required"):
        apply_bundle(env.settings.database_url, bundle.reference, bundle, isolated=True)
    with psycopg.connect(env.admin_url):
        with pytest.raises(AdminError, match="database_in_use"):
            apply_bundle(env.admin_url, bundle.reference, bundle, isolated=True)
    env.observe()
    with pytest.raises(AdminError, match="state_conflict"):
        apply_bundle(env.admin_url, bundle.reference, bundle, isolated=True)
    current = capture_processing_state(env.admin_url, env.tenants[0])
    with pytest.raises(AdminError, match="content_mismatch"):
        apply_bundle(env.admin_url, current, bundle, isolated=True)


@pytest.mark.parametrize("field", ["signature", "access_epoch", "rows", "content"])
def test_bundle_tampering_cannot_write_state(env, field):
    env.observe()
    bundle = export_bundle(env.admin_url, env.tenants[0])
    value = bundle.model_dump(mode="json")
    if field == "signature":
        value["signature"] = "0" * 64
    elif field == "access_epoch":
        value["reference"]["access_epoch"] += 1
    elif field == "rows":
        value["rows"]["memory_ops.idempotency"][0]["result"] = {"tampered": True}
    else:
        value["content"][0]["digest"] = "0" * 64
    forged = type(bundle).model_validate_json(json.dumps(value))
    with pytest.raises(AdminError, match="authentication_failed"):
        apply_bundle(env.admin_url, bundle.reference, forged, isolated=True)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == bundle.reference


def test_missing_reservation_in_older_authentic_bundle_is_not_a_refund(env, profile):
    configure(env, profile)
    source = env.observe().json()["memory_id"]
    enqueue(env, source)
    old = export_bundle(env.admin_url, env.tenants[0])
    reserve(env, profile)
    current = capture_processing_state(env.admin_url, env.tenants[0])
    with pytest.raises(AdminError, match="call_regression"):
        apply_bundle(env.admin_url, current, old, isolated=True)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == current


def test_mid_apply_failure_rolls_back_deletes_updates_and_context(env, monkeypatch):
    env.observe()
    bundle = export_bundle(env.admin_url, env.tenants[0])
    original = insert_rows
    calls = 0
    def broken(conn, table, rows, **kwargs):
        nonlocal calls
        original(conn, table, rows, **kwargs)
        calls += 1
        if calls == 4:
            raise AdminError("injected_restore_failure")
    monkeypatch.setattr("pg_agmemory.recovery_apply.insert_rows", broken)
    with pytest.raises(AdminError, match="injected_restore_failure"):
        apply_bundle(env.admin_url, bundle.reference, bundle, isolated=True)
    assert calls == 4
    assert capture_processing_state(env.admin_url, env.tenants[0]) == bundle.reference
    with psycopg.connect(env.admin_url) as conn:
        assert not conn.execute("SELECT memory.recovery_apply_authorized()").fetchone()[0]


def test_post_write_verification_rolls_back_authenticated_inconsistent_rows(env):
    env.observe()
    bundle = export_bundle(env.admin_url, env.tenants[0])
    altered = type(bundle).model_validate_json(bundle.model_dump_json())
    altered.rows["memory_ops.idempotency"][0]["result"] = {"inconsistent": True}
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        altered = altered.model_copy(update={
            "signature": signature(altered, secret_for(conn, env.tenants[0])),
        })
    with pytest.raises(AdminError, match="verification_failed"):
        apply_bundle(env.admin_url, bundle.reference, altered, isolated=True)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == bundle.reference


def test_cli_private_export_and_exact_application(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    path, expected = tmp_path / "bundle.json", tmp_path / "expected.json"
    current = capture_processing_state(env.admin_url, env.tenants[0])
    expected.write_text(current.model_dump_json())
    flags = ["--tenant-id", str(env.tenants[0]), "--bundle", str(path)]
    main(["export", *flags])
    assert json.loads(capsys.readouterr().out)["contains_operational_rows"] is True
    assert path.stat().st_mode & 0o777 == 0o600
    main(["apply", *flags, "--expected", str(expected), "--isolated"])
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert capture_processing_state(env.admin_url, env.tenants[0]) == current


def test_latest_capture_and_synthesis_policy_are_applied_with_original_epochs(env, profile):
    configure(env, profile, max_calls=1)
    old = export_bundle(env.admin_url, env.tenants[0])
    configured = configure(env, profile, enabled=False)
    with capture_policy(env.admin_url, CapturePolicyRequest(
        operation="set", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        expected_access_epoch=configured.access_epoch,
        policy=CapturePolicy(enabled=False, source_namespaces=None, consent_references=None,
                             max_content_bytes=1000),
    )):
        pass
    latest = export_bundle(env.admin_url, env.tenants[0])
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        for table in (
            "memory.scope_capture_policy", "memory.scope_synthesis_policy",
            "memory_ops.capture_policy_event", "memory_ops.synthesis_policy_event",
        ):
            conn.execute(psycopg.sql.SQL("DELETE FROM {} WHERE tenant_id=%s").format(
                psycopg.sql.Identifier(*table.split(".")),
            ), (env.tenants[0],))
            insert_rows(conn, table, old.rows[table])
        conn.execute("UPDATE memory.tenant SET access_epoch=%s WHERE id=%s",
                     (old.reference.access_epoch, env.tenants[0]))
    assert capture_processing_state(env.admin_url, env.tenants[0]) == old.reference
    assert apply_bundle(env.admin_url, old.reference, latest, isolated=True) == latest.reference
    assert env.observe().status_code == 403


def test_authenticated_but_incomplete_access_history_is_rejected(env, profile):
    configure(env, profile)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    altered = type(bundle).model_validate_json(bundle.model_dump_json())
    altered.rows["memory_ops.synthesis_policy_event"].clear()
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        altered = altered.model_copy(update={
            "signature": signature(altered, secret_for(conn, env.tenants[0])),
        })
    with pytest.raises(AdminError, match="access_history_regression"):
        apply_bundle(env.admin_url, bundle.reference, altered, isolated=True)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == bundle.reference
