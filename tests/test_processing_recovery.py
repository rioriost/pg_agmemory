import asyncio
import hashlib
import json
import os
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError
from test_processing import configure, enqueue, install_extraction, run
from test_processing import profile as profile

from pg_agmemory.admin import AdminError
from pg_agmemory.jobs import job_transaction
from pg_agmemory.processing import Processing
from pg_agmemory.processing_recovery import (
    TABLES,
    ProcessingRecoverySnapshot,
    StateFingerprint,
    capture_processing_state,
    compare_processing_state,
    main,
)
from pg_agmemory.providers import ProviderFailure


def snapshot():
    return ProcessingRecoverySnapshot(
        tenant_id=uuid4(), lineage="a" * 64, access_epoch=1, deletion_epoch=1,
        tables=tuple(StateFingerprint(table=t, rows=0, digest="b" * 64) for t in TABLES),
    )


@pytest.mark.parametrize("case", [
    "missing", "duplicate", "reordered", "unknown", "schema", "previous_schema", "authority",
])
def test_snapshot_requires_complete_versioned_state(case):
    value = snapshot().model_dump(mode="json")
    if case == "missing":
        value["tables"].pop()
    elif case == "duplicate":
        value["tables"].append(value["tables"][0])
    elif case == "reordered":
        value["tables"].reverse()
    elif case == "unknown":
        value["tables"][0]["table"] = "arbitrary.table"
    elif case == "schema":
        value["schema_version"] = 13
    elif case == "previous_schema":
        value["schema_version"] = 18
    else:
        value["restore_authorized"] = True
    with pytest.raises(ValidationError):
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("field", ["access_epoch", "deletion_epoch", *TABLES])
def test_every_component_blocks_on_mismatch_in_either_direction(field):
    before = snapshot()
    values = before.model_dump(mode="json")
    if field in ("access_epoch", "deletion_epoch"):
        values[field] += 1
    else:
        next(row for row in values["tables"] if row["table"] == field)["digest"] = "c" * 64
    after = ProcessingRecoverySnapshot.model_validate_json(json.dumps(values))
    for left, right in ((before, after), (after, before)):
        result = compare_processing_state(left, right)
        assert not result.processing_state_matches and result.differences == (field,)
        assert not result.restore_authorized


@pytest.mark.parametrize("field,value", [("tenant_id", str(uuid4())), ("lineage", "c" * 64)])
def test_other_tenant_or_recreated_lineage_is_not_comparable(field, value):
    before = snapshot()
    after = ProcessingRecoverySnapshot.model_validate_json(json.dumps(
        before.model_dump(mode="json") | {field: value}
    ))
    with pytest.raises(AdminError, match="lineage_mismatch"):
        compare_processing_state(before, after)


def test_capture_is_read_only_deterministic_private_and_admin_only(env):
    env.observe(content="PRIVATE_SOURCE_MUST_NOT_APPEAR")
    before = capture_processing_state(env.admin_url, env.tenants[0])
    assert before.schema_version == 19 and len(before.tables) == 23
    assert {row.table: row.rows for row in before.tables[-2:]} == {
        "memory_ops.graph_generation": 0, "memory_ops.graph_generation_state": 0,
    }
    assert capture_processing_state(env.admin_url, env.tenants[0]) == before
    assert compare_processing_state(before, before).processing_state_matches
    assert not compare_processing_state(before, before).restore_authorized
    assert "PRIVATE_SOURCE_MUST_NOT_APPEAR" not in before.model_dump_json()
    assert env.subjects[0] not in before.model_dump_json()
    with psycopg.connect(env.admin_url) as conn:
        secret = conn.execute(
            "SELECT encode(dedup_secret,'hex') FROM memory.tenant WHERE id=%s", (env.tenants[0],),
        ).fetchone()[0]
        assert secret not in before.model_dump_json()
    with pytest.raises(AdminError, match="admin_role_required"):
        capture_processing_state(env.settings.database_url, env.tenants[0])
    with pytest.raises(AdminError, match="tenant_not_found"):
        capture_processing_state(env.admin_url, uuid4())


@pytest.mark.parametrize("outcome", ["unknown", "failed", "succeeded"])
def test_actual_durable_reservations_and_semantic_identities_detect_rollback(
    env, profile, monkeypatch, outcome,
):
    configure(env, profile, max_calls=1)
    source = env.observe("Alice / preferred_editor: Vim").json()["memory_id"]
    baseline = capture_processing_state(env.admin_url, env.tenants[0])
    response = enqueue(env, source)
    assert response.status_code == 202
    if outcome == "unknown":
        async def reserve():
            async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
                claim = await jobs.claim(lease_seconds=60, profile_digest=profile.digest)
                assert claim is not None
                await Processing(jobs.memory).prepare(
                    claim["job_id"], claim["lease_token"], profile, reserve=True,
                )
        asyncio.run(reserve())
    else:
        if outcome == "failed":
            async def fail(data):
                raise ProviderFailure(
                    "provider_unavailable", retryable=False, unknown=False,
                )
            monkeypatch.setattr(profile.provider, "extract", fail)
        else:
            install_extraction(monkeypatch, profile)
        run(env, profile)
    latest = capture_processing_state(env.admin_url, env.tenants[0])
    result = compare_processing_state(baseline, latest)
    assert not result.processing_state_matches
    assert {"memory_ops.model_call", "memory_ops.job_identity", "memory_ops.job"} <= set(
        result.differences
    )
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT outcome FROM memory_ops.model_call WHERE tenant_id=%s", (env.tenants[0],),
        ).fetchall() == [(outcome,)]
    assert capture_processing_state(env.admin_url, env.tenants[0]) == latest
    configure(env, profile, max_calls=2)
    changed = capture_processing_state(env.admin_url, env.tenants[0])
    assert {"access_epoch", "memory.scope_synthesis_policy",
            "memory_ops.synthesis_policy_event"} <= set(
                compare_processing_state(latest, changed).differences
            )


def test_cli_exports_privately_checks_and_refuses_stale_state(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    path = tmp_path / "processing.json"
    flags = ["--tenant-id", str(env.tenants[0]), "--file", str(path)]
    main(["export", *flags])
    result = json.loads(capsys.readouterr().out)
    payload = path.read_bytes()
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert path.stat().st_mode & 0o777 == 0o600 and not result["restore_authorized"]
    main(["check", *flags])
    assert json.loads(capsys.readouterr().out) == {
        "processing_state_matches": True, "differences": [], "restore_authorized": False,
    }
    env.observe(content="CHANGED_PRIVATE_INPUT")
    with pytest.raises(SystemExit) as error:
        main(["check", *flags])
    assert error.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["processing_state_matches"] and "memory.object" in result["differences"]
    assert "CHANGED_PRIVATE_INPUT" not in str(result)
    with pytest.raises(SystemExit):
        main(["export", *flags])
    assert path.read_bytes() == payload


@pytest.mark.parametrize("limit", ["MAX_ROWS", "MAX_BYTES", "MAX_SECONDS"])
def test_limits_refuse_partial_success_and_release_barrier(env, monkeypatch, limit):
    monkeypatch.setattr("pg_agmemory.processing_recovery." + limit, 0)
    with pytest.raises(AdminError, match="processing_recovery_(limit|timeout)"):
        capture_processing_state(env.admin_url, env.tenants[0])
    assert env.observe().status_code == 201


@pytest.mark.parametrize("case", ["bad_json", "extra_field", "oversized", "symlink", "fifo"])
def test_invalid_reference_fails_without_reading_database(tmp_path, monkeypatch, capsys, case):
    path = tmp_path / "reference.json"
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_DSN")
    def forbidden(*args):
        pytest.fail("invalid reference reached database")
    monkeypatch.setattr("pg_agmemory.processing_recovery.capture_processing_state", forbidden)
    if case == "symlink":
        other = tmp_path / "other"
        other.write_text(snapshot().model_dump_json())
        path.symlink_to(other)
    elif case == "fifo":
        os.mkfifo(path)
    else:
        data = ("{" if case == "bad_json" else "x" * 32769 if case == "oversized"
                else json.dumps(snapshot().model_dump(mode="json") | {"extra": "forbidden"}))
        path.write_text(data)
    with pytest.raises(SystemExit):
        main(["check", "--tenant-id", str(uuid4()), "--file", str(path)])
    output = capsys.readouterr().out
    assert "PRIVATE_DSN" not in output and "error" in json.loads(output)
