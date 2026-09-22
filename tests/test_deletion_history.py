import hashlib
import json
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory.admin import AdminError
from pg_agmemory.deletion_history import DeletionHistory, export_deletions, main


def forget(env, source, mode="purge", headers=None):
    response = env.client.post(
        "/v1/forget", headers=headers or env.headers(),
        json={"memory_ids": [source], "mode": mode, "reason": "private deletion reason"},
    )
    assert response.status_code == 202, response.text
    return response.json()


def runtime(conn, env, index=0):
    conn.execute(
        """SELECT set_config('pgag.tenant_id',%s,true),
                  set_config('pgag.principal_id',%s,true),
                  set_config('pgag.subject',%s,true)""",
        (str(env.tenants[index if index < 2 else 0]),
         str(env.principals[index]), env.subjects[index]),
    )


def receipt(conn, env, *, count=1, version=1, epoch=2, mode="purge"):
    deletion = uuid4()
    conn.execute(
        """INSERT INTO memory_ops.deletion_request
           (tenant_id,id,principal_id,mode,state,object_count,deletion_epoch,target_manifest_version)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (env.tenants[0], deletion, env.principals[0], mode,
         "active_store_purged" if mode == "purge" else "blocked_for_reads", count, epoch, version),
    )
    return deletion


def target(conn, env, deletion, source, *, scope=None):
    conn.execute(
        """INSERT INTO memory_ops.deletion_target(tenant_id,deletion_id,object_id,scope_id,ordinal)
           VALUES (%s,%s,%s,%s,(SELECT COALESCE(max(ordinal),0)+1
               FROM memory_ops.deletion_target WHERE tenant_id=%s AND deletion_id=%s))""",
        (env.tenants[0], deletion, source, scope or env.scopes[0], env.tenants[0], deletion),
    )


def tombstone(conn, env, source):
    conn.execute(
        """INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id)
           VALUES (%s,%s,%s)""", (env.tenants[0], source, env.scopes[0]),
    )


def test_empty_history_is_content_free_and_not_restore_authority(env):
    history = export_deletions(env.admin_url, env.tenants[0])
    assert history.records == () and history.deletion_epoch == 1
    assert not history.restore_authorized and not history.includes_acl_policy_and_call_accounting
    assert len(history.digest()) == 64
    assert DeletionHistory.model_validate_json(history.model_dump_json()) == history


def test_suppress_remains_an_unsupported_native_operation(env):
    source = env.observe().json()["memory_id"]
    response = env.client.post(
        "/v1/forget", headers=env.headers(),
        json={"memory_ids": [source], "mode": "suppress", "reason": "unsupported"},
    )
    assert response.status_code == 422
    assert export_deletions(env.admin_url, env.tenants[0]).records == ()


def test_schema_mixed_history_export_binds_receipts_exact_closure_and_mode(env):
    source = env.observe().json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    # Suppress is a stored ledger mode, not an enabled Native/API operation.
    with psycopg.connect(env.admin_url) as conn:
        first = receipt(conn, env, mode="suppress", count=2)
        for item in (source, assertion):
            target(conn, env, first, item)
            tombstone(conn, env, item)
        conn.execute("UPDATE memory.tenant SET deletion_epoch=2 WHERE id=%s", (env.tenants[0],))
    other = env.observe(content="SECOND_PRIVATE_PAYLOAD").json()["memory_id"]
    second = forget(env, other)
    positive = env.observe(content="POSITIVE_CONTROL").json()["memory_id"]
    history = export_deletions(env.admin_url, env.tenants[0])
    assert [row.deletion_id for row in history.records] == [
        first, UUID(second["deletion_id"]),
    ]
    assert [row.mode for row in history.records] == ["suppress", "purge"]
    assert [row.deletion_epoch for row in history.records] == [2, 3]
    assert {str(t.object_id) for t in history.records[0].targets} == {source, assertion}
    assert [str(t.object_id) for t in history.records[1].targets] == [other]
    assert {item["memory_id"] for item in env.recall().json()["items"]} == {positive}
    assert "SECOND_PRIVATE_PAYLOAD" not in history.model_dump_json()
    assert "private deletion reason" not in history.model_dump_json()
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM memory.episode WHERE tenant_id=%s", (env.tenants[0],),
        ).fetchone()[0] == 2


def test_replay_and_preview_do_not_duplicate_or_create_manifest(env):
    source = env.observe().json()["memory_id"]
    headers = env.headers()
    before = export_deletions(env.admin_url, env.tenants[0])
    preview = env.client.post(
        "/v1/forget", headers=env.headers(),
        json={"memory_ids": [source], "mode": "preview", "reason": "preview"},
    )
    assert preview.status_code == 202 and preview.json()["changed"] is False
    assert export_deletions(env.admin_url, env.tenants[0]) == before
    first = forget(env, source, headers=headers)
    assert forget(env, source, headers=headers) == first
    history = export_deletions(env.admin_url, env.tenants[0])
    assert len(history.records) == len(history.records[0].targets) == 1


def test_schema13_upgrade_preserves_unknown_legacy_mapping_without_guessing(database):
    admin_url, _, legacy = database
    with psycopg.connect(admin_url) as conn:
        for record in legacy:
            assert conn.execute(
                """SELECT target_manifest_version,manifest_xid
                   FROM memory_ops.deletion_request WHERE tenant_id=%s AND id=%s""",
                (record["tenant"], record["legacy_deletion_id"]),
            ).fetchone() == (0, None)
            assert conn.execute(
                "SELECT count(*) FROM memory_ops.deletion_target WHERE tenant_id=%s",
                (record["tenant"],),
            ).fetchone()[0] == 0
            with pytest.raises(AdminError, match="deletion_history_incomplete"):
                export_deletions(admin_url, record["tenant"])


@pytest.mark.parametrize("case", ["missing_targets", "missing_tombstone", "wrong_count",
                                "unbound_tombstone", "legacy_version", "extra_targets"])
def test_incomplete_transaction_cannot_commit_and_changes_roll_back(env, case):
    source = env.observe().json()["memory_id"]
    other = env.observe().json()["memory_id"]
    with pytest.raises(psycopg.IntegrityError):
        with psycopg.connect(env.settings.database_url) as conn:
            runtime(conn, env)
            if case == "unbound_tombstone":
                tombstone(conn, env, source)
            else:
                deletion = receipt(
                    conn, env, count=2 if case == "wrong_count" else 1,
                    version=0 if case == "legacy_version" else 1,
                )
                if case != "missing_targets":
                    target(conn, env, deletion, source)
                    if case != "missing_tombstone":
                        tombstone(conn, env, source)
                    if case == "extra_targets":
                        target(conn, env, deletion, other)
                        tombstone(conn, env, other)
    assert len(env.recall().json()["items"]) == 2
    assert export_deletions(env.admin_url, env.tenants[0]).records == ()


def test_committed_manifest_is_sealed_and_runtime_cannot_mutate_it(env):
    source = env.observe().json()["memory_id"]
    deletion = forget(env, source)["deletion_id"]
    other = env.observe().json()["memory_id"]
    with pytest.raises(psycopg.errors.CheckViolation, match="sealed"):
        with psycopg.connect(env.settings.database_url) as conn:
            runtime(conn, env)
            target(conn, env, deletion, other)
    for statement in (
        "UPDATE memory_ops.deletion_target SET scope_id=scope_id",
        "DELETE FROM memory_ops.deletion_target",
        "UPDATE memory_ops.deletion_request SET target_manifest_version=0",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(env.settings.database_url) as conn:
                runtime(conn, env)
                conn.execute(statement)


def test_early_constraint_check_cannot_allow_same_transaction_overfill(env):
    source = env.observe().json()["memory_id"]
    other = env.observe().json()["memory_id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(env.settings.database_url) as conn:
            runtime(conn, env)
            deletion = receipt(conn, env)
            target(conn, env, deletion, source)
            tombstone(conn, env, source)
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            target(conn, env, deletion, other)
    assert export_deletions(env.admin_url, env.tenants[0]).records == ()


def test_wrong_target_scope_is_rejected_even_for_admin(env):
    source = env.observe().json()["memory_id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(env.admin_url) as conn:
            deletion = receipt(conn, env)
            target(conn, env, deletion, source, scope=env.scopes[2])
            tombstone(conn, env, source)
    assert export_deletions(env.admin_url, env.tenants[0]).records == ()


def test_duplicate_target_and_duplicate_epoch_cannot_be_committed(env):
    source = env.observe().json()["memory_id"]
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(env.admin_url) as conn:
            deletion = receipt(conn, env, count=2)
            target(conn, env, deletion, source)
            target(conn, env, deletion, source)
    forget(env, source)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(env.admin_url) as conn:
            receipt(conn, env, epoch=2)


def test_target_insert_cannot_attach_to_another_actors_receipt(env):
    source = env.observe().json()["memory_id"]
    deletion = forget(env, source)["deletion_id"]
    other = env.observe(index=2).json()["memory_id"]
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege)):
        with psycopg.connect(env.settings.database_url) as conn:
            runtime(conn, env, 2)
            target(conn, env, deletion, other, scope=env.scopes[2])


def test_target_read_is_tenant_scope_and_receipt_actor_bound(env):
    source = env.observe().json()["memory_id"]
    forget(env, source)
    for index in (0, 1, 2):
        with psycopg.connect(env.settings.database_url) as conn:
            runtime(conn, env, index)
            rows = conn.execute("SELECT object_id FROM memory_ops.deletion_target").fetchall()
            assert rows == ([(UUID(source),)] if index == 0 else [])
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','delete'])""",
            (env.tenants[0], env.scopes[0], env.principals[2]),
        )
    with psycopg.connect(env.settings.database_url) as conn:
        runtime(conn, env, 2)
        assert conn.execute("SELECT object_id FROM memory_ops.deletion_target").fetchall() == []


def test_export_admin_only_unknown_tenant_and_private_new_file(env, tmp_path, monkeypatch, capsys):
    with pytest.raises(AdminError, match="admin_role_required"):
        export_deletions(env.settings.database_url, env.tenants[0])
    with pytest.raises(AdminError, match="tenant_not_found"):
        export_deletions(env.admin_url, uuid4())
    source = env.observe().json()["memory_id"]
    forget(env, source)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    path = tmp_path / "deletions.json"
    argv = ["export", "--tenant-id", str(env.tenants[0]), "--out", str(path)]
    main(argv)
    result = json.loads(capsys.readouterr().out)
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.stat().st_mode & 0o777 == 0o600
    saved = path.read_bytes()
    with pytest.raises(SystemExit):
        main(argv)
    assert path.read_bytes() == saved
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "deletion_history_output_failed"


def test_incomplete_legacy_export_creates_no_success_file(database, tmp_path, monkeypatch, capsys):
    admin_url, _, legacy = database
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", admin_url)
    path = tmp_path / "incomplete.json"
    with pytest.raises(SystemExit):
        main(["export", "--tenant-id", str(legacy[0]["tenant"]), "--out", str(path)])
    assert not path.exists()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "deletion_history_incomplete"


@pytest.mark.parametrize("limit", ["MAX_RECEIPTS", "MAX_TARGETS", "MAX_EXPORT_BYTES"])
def test_export_bounds_fail_without_truncation(env, tmp_path, monkeypatch, capsys, limit):
    source = env.observe().json()["memory_id"]
    forget(env, source)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    monkeypatch.setattr("pg_agmemory.deletion_history." + limit, 0)
    path = tmp_path / "oversized.json"
    with pytest.raises(SystemExit):
        main(["export", "--tenant-id", str(env.tenants[0]), "--out", str(path)])
    assert not path.exists()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "deletion_history_too_large"


def test_history_closed_binding_rejects_gaps_unknowns_and_scope_contracts(env):
    source = env.observe().json()["memory_id"]
    forget(env, source)
    original = export_deletions(env.admin_url, env.tenants[0]).model_dump(mode="json")
    for field, value in (
        ("deletion_epoch", 5), ("schema_version", 13), ("schema_version", 18),
        ("restore_authorized", True),
        ("includes_acl_policy_and_call_accounting", True), ("records", []),
    ):
        with pytest.raises(ValidationError):
            DeletionHistory.model_validate_json(json.dumps(original | {field: value}))
    for field, value in (
        ("object_count", 2), ("state", "blocked_for_reads"), ("target_manifest_version", 0),
        ("deletion_epoch", 3), ("targets", []),
    ):
        with pytest.raises(ValidationError):
            DeletionHistory.model_validate_json(json.dumps(original | {
                "records": [original["records"][0] | {field: value}],
            }))
