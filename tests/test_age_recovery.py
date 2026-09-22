"""Recovery can quarantine only an authenticated, unchanged AGE registry."""

import json
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from test_age_projection_schema import TABLE, insert, projection, values
from test_graph_generation import begin, metadata_rows, record
from test_graph_generation_recovery import reject_recovery_writes
from test_processing import configure, enqueue
from test_processing import profile as profile
from test_recovery_apply import reserve, restore_old_jobs

from pg_agmemory import recovery_apply
from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.processing_recovery import (
    capture_processing_state,
    compare_processing_state,
)
from pg_agmemory.recovery_apply import apply_bundle, export_bundle, main, secret_for, signature

pytestmark = pytest.mark.integration


def register(env, *, enabled=True):
    head = record(env, begin(env)).head
    with psycopg.connect(env.admin_url) as conn:
        insert(conn, values(env, head, enabled=enabled))
    return head


def signed(env, bundle):
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        return bundle.model_copy(update={
            "signature": signature(bundle, secret_for(conn, env.tenants[0])),
        })


def assert_unchanged(env, snapshot, registry, receipts):
    assert capture_processing_state(env.admin_url, env.tenants[0]) == snapshot
    assert projection(env) == registry
    assert metadata_rows(env) == receipts
    with psycopg.connect(env.admin_url) as conn:
        assert not conn.execute("SELECT memory.recovery_apply_authorized()").fetchone()[0]


@pytest.fixture
def pending_restore(env, profile):
    configure(env, profile, max_calls=1)
    source = env.observe("Alice / preferred_editor: Vim").json()["memory_id"]
    assert enqueue(env, source).status_code == 202
    register(env)
    old = export_bundle(env.admin_url, env.tenants[0])
    reserve(env, profile)
    latest = export_bundle(env.admin_url, env.tenants[0])
    restore_old_jobs(env, old)
    assert capture_processing_state(env.admin_url, env.tenants[0]) == old.reference
    return old, latest


@pytest.fixture
def cli_files():
    directory = Path(".review-artifacts") / ("age-recovery-contract-" + uuid4().hex)
    directory.mkdir(parents=True)
    bundle, expected = directory / "bundle.json", directory / "expected.json"
    try:
        yield bundle, expected
    finally:
        bundle.unlink(missing_ok=True)
        expected.unlink(missing_ok=True)
        directory.rmdir()


def cli_arguments(env, bundle, expected, files):
    bundle_path, expected_path = files
    bundle_path.write_text(bundle.model_dump_json())
    expected_path.write_text(expected.model_dump_json())
    return [
        "apply", "--tenant-id", str(env.tenants[0]), "--bundle", str(bundle_path),
        "--expected", str(expected_path), "--isolated",
    ]


def test_restores_operational_rows_then_quarantines_only_registry(env, pending_restore):
    old, latest = pending_restore
    registry, receipts = projection(env), metadata_rows(env)
    immutable_reference = latest.reference.model_dump_json()
    with psycopg.connect(env.admin_url) as conn:
        actor = conn.execute("SELECT current_user").fetchone()[0]
        assert conn.execute("SELECT 1 FROM pg_extension WHERE extname='age'").fetchone() is None
    final = apply_bundle(
        env.admin_url, old.reference, latest, isolated=True, disable_age_projection=True,
    )
    assert final == capture_processing_state(env.admin_url, env.tenants[0])
    check = compare_processing_state(final, latest.reference)
    assert not check.processing_state_matches and check.differences == (TABLE,)
    assert not final.restore_authorized
    assert latest.reference.model_dump_json() == immutable_reference
    assert metadata_rows(env) == receipts
    after = projection(env)
    assert not after["enabled"] and after["revision"] == registry["revision"] + 1
    assert after["updated_at"] > registry["updated_at"] and after["database_role"] == actor
    changing = {"enabled", "revision", "updated_at", "database_role"}
    assert {k: v for k, v in after.items() if k not in changing} == {
        k: v for k, v in registry.items() if k not in changing
    }
    exported = export_bundle(env.admin_url, env.tenants[0])
    assert exported.rows == latest.rows
    assert [a.table for a, b in zip(exported.content, latest.content, strict=True) if a != b] == [
        TABLE,
    ]
    assert apply_bundle(env.admin_url, final, exported, isolated=True) == final


def test_default_still_rejects_active_registry_before_writes(env, monkeypatch):
    register(env)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_active_graph_projection$"):
            apply_bundle(env.admin_url, bundle.reference, bundle, isolated=True)
    assert not writes
    assert_unchanged(env, bundle.reference, registry, receipts)


@pytest.mark.parametrize("registry_state", ["absent", "disabled"])
@pytest.mark.parametrize("opt_in", [False, True])
def test_no_enabled_registry_preserves_exact_snapshot(env, registry_state, opt_in):
    if registry_state == "disabled":
        register(env, enabled=False)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    assert apply_bundle(
        env.admin_url, bundle.reference, bundle, isolated=True, disable_age_projection=opt_in,
    ) == bundle.reference
    assert_unchanged(env, bundle.reference, registry, receipts)


@pytest.mark.parametrize("gate,code", [
    ("isolation", "recovery_isolation_required"),
    ("runtime", "admin_role_required"),
    ("other_backend", "recovery_database_in_use"),
    ("cas", "recovery_state_conflict"),
    ("tenant", "processing_recovery_lineage_mismatch"),
    ("lineage", "recovery_epoch_or_lineage_mismatch"),
    ("epoch", "recovery_epoch_or_lineage_mismatch"),
    ("immutable", "recovery_immutable_state_mismatch"),
])
def test_opt_in_is_not_authority_and_keeps_prewrite_guards(env, monkeypatch, gate, code):
    register(env)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    current = expected = bundle.reference
    registry, receipts = projection(env), metadata_rows(env)
    url = env.settings.database_url if gate == "runtime" else env.admin_url
    if gate == "cas":
        expected = expected.model_copy(update={"access_epoch": expected.access_epoch + 1})
    elif gate == "tenant":
        expected = expected.model_copy(update={"tenant_id": env.tenants[1]})
    elif gate in ("lineage", "epoch", "immutable"):
        reference = bundle.reference.model_dump(mode="json")
        if gate == "lineage":
            reference["lineage"] = "0" * 64
        elif gate == "epoch":
            reference["deletion_epoch"] += 1
        else:
            fingerprint = next(row for row in reference["tables"] if row["table"] == TABLE)
            fingerprint["digest"] = "0" * 64
        bundle = signed(env, bundle.model_copy(update={
            "reference": type(current).model_validate_json(json.dumps(reference)),
        }))
    connection = psycopg.connect(env.admin_url) if gate == "other_backend" else nullcontext()
    with connection, reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match=f"^{code}$"):
            apply_bundle(
                url, expected, bundle, isolated=gate != "isolation", disable_age_projection=True,
            )
    assert not writes
    assert_unchanged(env, current, registry, receipts)


@pytest.mark.parametrize("field", ["signature", "rows", "content", "reference"])
def test_opt_in_never_bypasses_bundle_authentication(env, monkeypatch, field):
    env.observe()
    register(env)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    body = bundle.model_dump(mode="json")
    if field == "signature":
        body["signature"] = "0" * 64
    elif field == "rows":
        body["rows"]["memory_ops.idempotency"][0]["result"] = {"private_tamper": True}
    elif field == "content":
        next(row for row in body["content"] if row["table"] == TABLE)["digest"] = "0" * 64
    else:
        body["reference"]["access_epoch"] += 1
    altered = type(bundle).model_validate_json(json.dumps(body))
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_bundle_authentication_failed$"):
            apply_bundle(
                env.admin_url, bundle.reference, altered,
                isolated=True, disable_age_projection=True,
            )
    assert not writes
    assert_unchanged(env, bundle.reference, registry, receipts)


def test_opt_in_still_requires_exact_canonical_content(env, monkeypatch):
    register(env)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    assert env.observe("New canonical content is not replaceable").status_code == 201
    current = capture_processing_state(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_content_mismatch$"):
            apply_bundle(
                env.admin_url, current, bundle, isolated=True, disable_age_projection=True,
            )
    assert not writes
    assert_unchanged(env, current, registry, receipts)


@pytest.mark.parametrize("change", ["missing", "revision", "disabled", "newer_generation"])
def test_opt_in_cannot_import_or_bypass_different_registry(env, monkeypatch, change):
    register(env)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    if change == "newer_generation":
        record(env, begin(env))
    else:
        with psycopg.connect(env.admin_url) as conn:
            if change == "missing":
                conn.execute(
                    "ALTER TABLE memory_ops.age_projection DISABLE TRIGGER guard_age_projection"
                )
                conn.execute(
                    "DELETE FROM memory_ops.age_projection WHERE tenant_id=%s", (env.tenants[0],),
                )
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                conn.execute(
                    "ALTER TABLE memory_ops.age_projection ENABLE TRIGGER guard_age_projection"
                )
            else:
                conn.execute(
                    "UPDATE memory_ops.age_projection SET enabled=%s,revision=revision+1 "
                    "WHERE tenant_id=%s", (change != "disabled", env.tenants[0]),
                )
    current = capture_processing_state(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_content_mismatch$"):
            apply_bundle(
                env.admin_url, current, bundle, isolated=True, disable_age_projection=True,
            )
    assert not writes
    assert_unchanged(env, current, registry, receipts)


def test_opt_in_cannot_refund_authenticated_reservations(env, profile, monkeypatch):
    configure(env, profile)
    assert enqueue(env, env.observe().json()["memory_id"]).status_code == 202
    register(env)
    old = export_bundle(env.admin_url, env.tenants[0])
    reserve(env, profile)
    current = capture_processing_state(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="^recovery_call_regression$"):
            apply_bundle(
                env.admin_url, current, old, isolated=True, disable_age_projection=True,
            )
    assert not writes
    assert_unchanged(env, current, registry, receipts)


def test_exhausted_registry_revision_is_rejected_before_any_write(env, monkeypatch):
    register(env)
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("ALTER TABLE memory_ops.age_projection DISABLE TRIGGER guard_age_projection")
        conn.execute(
            "UPDATE memory_ops.age_projection SET revision=%s WHERE tenant_id=%s",
            (MAX_EPOCH, env.tenants[0]),
        )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        conn.execute("ALTER TABLE memory_ops.age_projection ENABLE TRIGGER guard_age_projection")
    bundle = export_bundle(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    with reject_recovery_writes(monkeypatch) as writes:
        with pytest.raises(AdminError, match="exhausted"):
            apply_bundle(
                env.admin_url, bundle.reference, bundle,
                isolated=True, disable_age_projection=True,
            )
    assert not writes
    assert_unchanged(env, bundle.reference, registry, receipts)


def test_disable_trigger_error_rolls_back_operational_restore_and_cli_redacts_it(
    env, pending_restore, cli_files, monkeypatch, capsys,
):
    old, latest = pending_restore
    registry, receipts = projection(env), metadata_rows(env)
    args = cli_arguments(env, latest, old.reference, cli_files)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """CREATE FUNCTION memory_ops.reject_test_age_disable() RETURNS trigger
               LANGUAGE plpgsql AS $$ BEGIN
                 IF OLD.enabled AND NOT NEW.enabled THEN
                   RAISE EXCEPTION 'PRIVATE_DISABLE_TRIGGER_PAYLOAD' USING ERRCODE='23514';
                 END IF;
                 RETURN NEW;
               END $$"""
        )
        conn.execute(
            """CREATE TRIGGER reject_test_age_disable BEFORE UPDATE ON memory_ops.age_projection
               FOR EACH ROW EXECUTE FUNCTION memory_ops.reject_test_age_disable()"""
        )
    try:
        with pytest.raises(SystemExit) as error:
            main([*args, "--disable-age-projection"])
        assert error.value.code == 1
        output = capsys.readouterr()
        assert json.loads(output.out) == {
            "error": {"code": "admin_database_error", "outcome_unknown": False},
        }
        assert "PRIVATE_DISABLE_TRIGGER_PAYLOAD" not in output.out + output.err
        assert env.admin_url not in output.out + output.err
        assert_unchanged(env, old.reference, registry, receipts)
    finally:
        with psycopg.connect(env.admin_url) as conn:
            conn.execute("DROP TRIGGER reject_test_age_disable ON memory_ops.age_projection")
            conn.execute("DROP FUNCTION memory_ops.reject_test_age_disable()")


def test_failure_after_disable_rolls_back_registry_and_operational_rows(
    env, pending_restore, monkeypatch,
):
    old, latest = pending_restore
    registry, receipts = projection(env), metadata_rows(env)
    original = recovery_apply.capture_processing_connection
    quarantined = []

    def fail_after_disable(conn, tenant):
        snapshot = original(conn, tenant)
        row = conn.execute(
            "SELECT enabled,revision FROM memory_ops.age_projection WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()
        if not row["enabled"]:
            assert row["revision"] == registry["revision"] + 1
            assert compare_processing_state(snapshot, latest.reference).differences == (TABLE,)
            quarantined.append(snapshot)
            raise AdminError("injected_post_disable_failure")
        return snapshot

    with monkeypatch.context() as patch:
        patch.setattr(recovery_apply, "capture_processing_connection", fail_after_disable)
        with pytest.raises(AdminError, match="^injected_post_disable_failure$"):
            apply_bundle(
                env.admin_url, old.reference, latest, isolated=True, disable_age_projection=True,
            )
    assert len(quarantined) == 1
    assert_unchanged(env, old.reference, registry, receipts)


def test_full_post_apply_verification_must_pass_before_registry_disable(env, monkeypatch):
    env.observe()
    register(env)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    altered = type(bundle).model_validate_json(bundle.model_dump_json())
    altered.rows["memory_ops.idempotency"][0]["result"] = {"inconsistent": True}
    altered = signed(env, altered)
    original = recovery_apply.capture_processing_connection
    observed = []

    def verify_while_enabled(conn, tenant):
        observed.append(conn.execute(
            "SELECT enabled FROM memory_ops.age_projection WHERE tenant_id=%s", (tenant,),
        ).fetchone()["enabled"])
        return original(conn, tenant)

    with monkeypatch.context() as patch:
        patch.setattr(recovery_apply, "capture_processing_connection", verify_while_enabled)
        with pytest.raises(AdminError, match="^recovery_verification_failed$"):
            apply_bundle(
                env.admin_url, bundle.reference, altered,
                isolated=True, disable_age_projection=True,
            )
    assert len(observed) >= 2 and all(observed)
    assert_unchanged(env, bundle.reference, registry, receipts)


def test_cli_reports_quarantine_instead_of_claiming_exact_match(
    env, pending_restore, cli_files, monkeypatch, capsys,
):
    old, latest = pending_restore
    args = cli_arguments(env, latest, old.reference, cli_files)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    with reject_recovery_writes(monkeypatch):
        with pytest.raises(SystemExit) as error:
            main(args)
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": {"code": "recovery_active_graph_projection", "outcome_unknown": False},
    }
    main([*args, "--disable-age-projection"])
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "status": "applied", "processing_state_matches": False,
        "operational_state_restored": True, "age_projection_disabled": True,
        "projection_rebuild_required": True, "differences": [TABLE],
        "access_epoch": latest.reference.access_epoch, "restore_authorized": False,
    }
    assert compare_processing_state(
        capture_processing_state(env.admin_url, env.tenants[0]), latest.reference,
    ).differences == (TABLE,)


@pytest.mark.parametrize("registry_state", ["absent", "disabled"])
def test_cli_no_enabled_registry_reports_no_quarantine(
    env, registry_state, cli_files, monkeypatch, capsys,
):
    if registry_state == "disabled":
        register(env, enabled=False)
    bundle = export_bundle(env.admin_url, env.tenants[0])
    registry, receipts = projection(env), metadata_rows(env)
    args = cli_arguments(env, bundle, bundle.reference, cli_files)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    main([*args, "--disable-age-projection"])
    assert json.loads(capsys.readouterr().out) == {
        "status": "applied", "processing_state_matches": True,
        "operational_state_restored": True, "age_projection_disabled": False,
        "projection_rebuild_required": False, "differences": [],
        "access_epoch": bundle.reference.access_epoch, "restore_authorized": False,
    }
    assert_unchanged(env, bundle.reference, registry, receipts)


@pytest.mark.parametrize("options", [
    ["export"],
    ["export", "--isolated"],
    ["apply"],
    ["apply", "--expected", "PRIVATE_EXPECTED_FILE"],
    ["apply", "--isolated"],
])
def test_cli_quarantine_flag_is_apply_only_and_never_supplies_isolation(
    monkeypatch, capsys, options,
):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_DATABASE_URL")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid CLI options reached file access or database operation")

    monkeypatch.setattr(recovery_apply, "read_file", forbidden)
    monkeypatch.setattr(recovery_apply, "export_bundle", forbidden)
    monkeypatch.setattr(recovery_apply, "apply_bundle", forbidden)
    with pytest.raises(SystemExit) as error:
        main([
            *options, "--tenant-id", str(uuid4()), "--bundle", "PRIVATE_BUNDLE_FILE",
            "--disable-age-projection",
        ])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out and "invalid_processing_recovery_arguments" in output.err
    assert "PRIVATE_" not in output.err
