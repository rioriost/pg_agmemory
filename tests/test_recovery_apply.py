import asyncio
import json

import psycopg
import pytest
from psycopg.rows import dict_row
from test_processing import configure, enqueue, get, install_extraction, run
from test_processing import profile as profile

from pg_agmemory.admin import AdminError
from pg_agmemory.capture_policy import CapturePolicy, CapturePolicyRequest, capture_policy
from pg_agmemory.jobs import job_transaction
from pg_agmemory.processing import Processing
from pg_agmemory.processing_recovery import capture_processing_state
from pg_agmemory.recovery_apply import (
    apply_bundle,
    export_bundle,
    insert_rows,
    main,
    secret_for,
    signature,
)


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
