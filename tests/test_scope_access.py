import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import ValidationError

from pg_agmemory.api import TransactionBoundary
from pg_agmemory.jobs import job_transaction
from pg_agmemory.models import Remember
from pg_agmemory.scope_access import MAX_EPOCH, ScopeAccessError, ScopeAccessRequest, scope_access
from pg_agmemory.service import MemoryError


def request(env, operation="get", index=0, **changes):
    return ScopeAccessRequest(
        operation=operation,
        tenant_id=env.tenants[index],
        scope_id=env.scopes[index],
        principal_id=env.principals[index],
        **changes,
    )


def execute(env, operation="get", **changes):
    with scope_access(env.admin_url, request(env, operation, **changes)) as result:
        return result


def audit_rows(env):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute(
            """SELECT access_epoch,operation,database_role::text,previous_permissions,
                      previous_expires_at,permissions,expires_at
               FROM memory_ops.scope_access_event WHERE tenant_id=%s ORDER BY access_epoch""",
            (env.tenants[0],),
        ).fetchall()


def cli(env, operation, *arguments, url=None, index=0):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pg_agmemory.cli",
            "scope-access",
            operation,
            "--tenant-id",
            str(env.tenants[index]),
            "--scope-id",
            str(env.scopes[index]),
            "--principal-id",
            str(env.principals[index]),
            *arguments,
        ],
        env={"PATH": os.environ["PATH"], "PGAG_ADMIN_DATABASE_URL": url or env.admin_url},
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "set"},
        {"operation": "revoke"},
        {"expected_access_epoch": 1},
        {"permissions": ["read"]},
        {"no_expiry": True},
        {"expires_at": "2100-01-01T00:00:00Z"},
        {"operation": "set", "expected_access_epoch": 1, "permissions": [], "no_expiry": True},
        {
            "operation": "set",
            "expected_access_epoch": 1,
            "permissions": ["read", "read"],
            "no_expiry": True,
        },
        {
            "operation": "set",
            "expected_access_epoch": 1,
            "permissions": ["admin", "read"],
            "no_expiry": True,
        },
        {"operation": "set", "expected_access_epoch": 1, "permissions": ["read"]},
        {
            "operation": "set",
            "expected_access_epoch": 1,
            "permissions": ["read"],
            "expires_at": "2100-01-01T00:00:00Z",
            "no_expiry": True,
        },
        {
            "operation": "set",
            "expected_access_epoch": 1,
            "permissions": ["read"],
            "expires_at": "2100-01-01T00:00:00",
        },
        {"operation": "revoke", "expected_access_epoch": 0},
        {"operation": "revoke", "expected_access_epoch": MAX_EPOCH + 1},
        {"operation": "revoke", "expected_access_epoch": True},
    ],
)
def test_invalid_access_contract(changes):
    with pytest.raises(ValidationError):
        ScopeAccessRequest.model_validate(
            {
                "operation": "get",
                "tenant_id": uuid4(),
                "scope_id": uuid4(),
                "principal_id": uuid4(),
                **changes,
            }
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ["set"],
        ["get", "--tenant-id", "DO_NOT_ECHO"],
        ["get", "--subject", "DO_NOT_ECHO"],
        ["get", "--once"],
        ["get", "--permissions", "DO_NOT_ECHO"],
        ["get", "--expires-at", "DO_NOT_ECHO"],
        ["unknown-operation-DO_NOT_ECHO"],
    ],
)
def test_cli_argument_errors_do_not_echo_input(arguments):
    completed = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "scope-access", *arguments],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 2 and completed.stdout == ""
    assert "invalid_scope_access_arguments" in completed.stderr
    assert "DO_NOT_ECHO" not in completed.stderr and "Traceback" not in completed.stderr


@pytest.mark.integration
def test_get_set_revoke_cas_and_minimal_durable_audit(env):
    initial = execute(env)
    assert initial.access_epoch == 1 and not initial.changed and initial.membership_exists
    assert initial.permissions == initial.effective_permissions == ["read", "write", "delete"]
    assert not audit_rows(env)
    changed = execute(env, "set", expected_access_epoch=1, permissions=("read",), no_expiry=True)
    assert changed.changed and changed.access_epoch == 2 and changed.permissions == ["read"]
    with pytest.raises(ScopeAccessError, match="access_epoch_conflict"):
        execute(env, "set", expected_access_epoch=1, permissions=("read",), no_expiry=True)
    noop = execute(env, "set", expected_access_epoch=2, permissions=("read",), no_expiry=True)
    assert not noop.changed and noop.access_epoch == 2
    assert len(audit_rows(env)) == 1
    revoked = execute(env, "revoke", expected_access_epoch=2)
    assert revoked.changed and revoked.access_epoch == 3 and not revoked.membership_exists
    assert revoked.permissions == revoked.effective_permissions == [] and revoked.expires_at is None
    assert not execute(env, "revoke", expected_access_epoch=3).changed
    rows = audit_rows(env)
    assert [(row[0], row[1], row[3], row[5]) for row in rows] == [
        (2, "set", ["read", "write", "delete"], ["read"]),
        (3, "revoke", ["read"], None),
    ]
    with psycopg.connect(env.admin_url) as conn:
        actor = conn.execute("SELECT current_user").fetchone()[0]
    assert all(row[2] == actor for row in rows)
    assert execute(env).access_epoch == 3
    assert execute(env, index=1).access_epoch == 1


@pytest.mark.integration
def test_permission_order_admin_and_expiry_are_explicit(env):
    assert not execute(
        env, "set", expected_access_epoch=1, permissions=("delete", "write", "read"), no_expiry=True
    ).changed
    admin = execute(env, "set", expected_access_epoch=1, permissions=("admin",), no_expiry=True)
    assert admin.permissions == ["admin"]
    assert admin.effective_permissions == ["read", "write", "delete", "admin"]
    expiry = datetime.now(UTC) + timedelta(seconds=30)
    result = execute(env, "set", expected_access_epoch=2, permissions=("admin",), expires_at=expiry)
    assert result.access_epoch == 3 and result.expires_at == expiry
    same_instant = expiry.astimezone(datetime.now().astimezone().tzinfo)
    assert not execute(
        env, "set", expected_access_epoch=3, permissions=("admin",), expires_at=same_instant
    ).changed
    assert (
        execute(
            env, "set", expected_access_epoch=3, permissions=("admin",), no_expiry=True
        ).access_epoch
        == 4
    )
    with pytest.raises(ScopeAccessError, match="invalid_expiration"):
        execute(
            env,
            "set",
            expected_access_epoch=4,
            permissions=("read",),
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
    assert execute(env).access_epoch == 4 and len(audit_rows(env)) == 3


@pytest.mark.integration
def test_expired_membership_is_not_a_purge_or_an_epoch_transition(env):
    source = env.observe().json()["memory_id"]
    expiry = datetime.now(UTC) + timedelta(seconds=0.5)
    changed = execute(env, "set", expected_access_epoch=1, permissions=("read",), expires_at=expiry)
    assert changed.effective_permissions == ["read"]
    time.sleep(0.6)
    expired = execute(env)
    assert expired.membership_exists and expired.permissions == ["read"]
    assert expired.effective_permissions == [] and expired.access_epoch == 2
    assert env.recall().json()["items"] == []
    assert len(audit_rows(env)) == 1
    execute(env, "set", expected_access_epoch=2, permissions=("read",), no_expiry=True)
    assert env.recall().json()["items"][0]["memory_id"] == source


@pytest.mark.integration
def test_add_existing_principal_only_within_tenant_and_grants_do_not_restore_purges(env):
    source = env.observe().json()["memory_id"]
    changes = {"principal_id": env.principals[2]}
    base = request(env).model_dump() | changes
    with scope_access(env.admin_url, ScopeAccessRequest(**base)) as result:
        assert not result.membership_exists and result.effective_permissions == []
    with scope_access(
        env.admin_url,
        ScopeAccessRequest(
            **(
                base
                | {
                    "operation": "set",
                    "expected_access_epoch": 1,
                    "permissions": ("read",),
                    "no_expiry": True,
                }
            )
        ),
    ) as result:
        assert result.changed and result.access_epoch == 2
    assert env.recall(2, scope_ids=[str(env.scopes[0])]).json()["items"][0]["memory_id"] == source
    assert (
        env.client.post(
            "/v1/forget", json={"memory_ids": [source], "reason": "test"}, headers=env.headers()
        ).status_code
        == 202
    )
    execute(env, "revoke", expected_access_epoch=2)
    execute(
        env, "set", expected_access_epoch=3, permissions=("read", "write", "delete"), no_expiry=True
    )
    assert not env.recall().json()["items"]
    assert env.observe(source_event_id="new-event").status_code == 201
    for target in [
        base | {"tenant_id": env.tenants[1]},
        base | {"scope_id": env.scopes[1]},
        base | {"principal_id": env.principals[1]},
        base | {"principal_id": uuid4()},
        base | {"scope_id": uuid4()},
    ]:
        with pytest.raises(ScopeAccessError, match="not_found"):
            with scope_access(env.admin_url, ScopeAccessRequest(**target)):
                pytest.fail("Invalid target accepted")


@pytest.mark.integration
def test_current_authorization_replay_and_stale_worker_publication(env):
    body = {
        "scope_id": str(env.scopes[0]),
        "subject": "ACME",
        "predicate": "tier",
        "value": "Gold",
        "evidence": [{"memory_id": env.observe().json()["memory_id"], "quote": "Gold"}],
        "explicit_intent": True,
    }
    headers = env.headers()
    saved = env.client.post("/v1/remember", json=body, headers=headers)
    assert saved.status_code == 201
    job = env.client.post(
        "/v1/jobs", json={"kind": "structured_remember", "memory": body}, headers=env.headers()
    ).json()

    async def claim():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            return await jobs.claim()

    lease = asyncio.run(claim())
    execute(env, "set", expected_access_epoch=1, permissions=("read",), no_expiry=True)
    assert env.client.post("/v1/remember", json=body, headers=headers).status_code == 404
    assert env.recall().json()["items"]
    execute(
        env, "set", expected_access_epoch=2, permissions=("read", "write", "delete"), no_expiry=True
    )

    async def publish():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            await jobs.publish(lease["job_id"], lease["lease_token"], Remember.model_validate(body))

    with pytest.raises(MemoryError, match="stale_context"):
        asyncio.run(publish())
    assert (
        env.client.get("/v1/jobs/" + job["job_id"], headers=env.headers()).json()["state"]
        == "running"
    )
    execute(env, "revoke", expected_access_epoch=3)
    assert env.recall(known_at="2100-01-01T00:00:00Z").json()["items"] == []
    assert (
        env.client.post(
            "/v1/explain", json={"memory_id": saved.json()["memory_id"]}, headers=env.headers()
        ).status_code
        == 404
    )


@pytest.mark.integration
def test_concurrent_mutations_have_single_epoch_cas_winner(env):
    def change(permissions):
        try:
            return execute(
                env, "set", expected_access_epoch=1, permissions=permissions, no_expiry=True
            )
        except ScopeAccessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [("read",), ("read", "write")]))
    assert results.count("access_epoch_conflict") == 1
    assert execute(env).access_epoch == 2 and len(audit_rows(env)) == 1


@pytest.mark.integration
def test_audit_insert_failure_rolls_back_membership_epoch_and_releases_lock(env, monkeypatch):
    original = psycopg.Connection.execute

    def failed(conn, query, *args, **kwargs):
        if isinstance(query, str) and "INSERT INTO memory_ops.scope_access_event" in query:
            raise psycopg.IntegrityError("DO_NOT_ECHO")
        return original(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", failed)
        with pytest.raises(ScopeAccessError, match="admin_database_error") as error:
            execute(env, "revoke", expected_access_epoch=1)
        assert not error.value.outcome_unknown
    assert execute(env).access_epoch == 1 and execute(env).membership_exists
    assert not audit_rows(env)
    assert execute(env, "revoke", expected_access_epoch=1).access_epoch == 2


@pytest.mark.integration
def test_uncertain_commit_is_not_reported_as_rollback(env, monkeypatch):
    from contextlib import contextmanager

    original = psycopg.Connection.transaction

    @contextmanager
    def lost_commit_response(conn, *args, **kwargs):
        with original(conn, *args, **kwargs) as transaction:
            yield transaction
        raise psycopg.OperationalError("DO_NOT_ECHO after actual commit")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "transaction", lost_commit_response)
        with pytest.raises(ScopeAccessError, match="admin_database_unavailable") as error:
            execute(env, "revoke", expected_access_epoch=1)
        assert error.value.outcome_unknown
    current = execute(env)
    assert not current.membership_exists and current.access_epoch == 2
    assert len(audit_rows(env)) == 1
    with pytest.raises(ScopeAccessError, match="access_epoch_conflict"):
        execute(env, "revoke", expected_access_epoch=1)


@pytest.mark.integration
def test_role_schema_extension_and_epoch_guards(env):
    with pytest.raises(ScopeAccessError, match="admin_role_required"):
        with scope_access(env.settings.database_url, request(env)):
            pytest.fail("Runtime credentials accepted")
    with psycopg.connect(env.settings.database_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM memory_ops.scope_access_event")
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        conn.execute("DELETE FROM public.pgag_schema_migration WHERE version=9")
        try:
            with pytest.raises(ScopeAccessError, match="schema_version_mismatch"):
                execute(env)
        finally:
            conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (9)")
        conn.execute("UPDATE pg_extension SET extversion='0.0.0' WHERE extname='vector'")
        try:
            with pytest.raises(ScopeAccessError, match="extension_version_mismatch"):
                execute(env)
        finally:
            conn.execute("UPDATE pg_extension SET extversion='0.8.6' WHERE extname='vector'")
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s", (MAX_EPOCH, env.tenants[0])
        )
    with pytest.raises(ScopeAccessError, match="access_epoch_exhausted"):
        execute(env, "revoke", expected_access_epoch=MAX_EPOCH)
    assert execute(env).membership_exists and not audit_rows(env)
    assert not execute(
        env,
        "set",
        expected_access_epoch=MAX_EPOCH,
        permissions=("read", "write", "delete"),
        no_expiry=True,
    ).changed


@pytest.mark.integration
def test_nonowner_bypass_role_with_only_select_can_inspect_not_change(env):
    role = "access_read_" + uuid4().hex
    password = uuid4().hex
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        conn.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN BYPASSRLS PASSWORD {}").format(
                psycopg.sql.Identifier(role), psycopg.sql.Literal(password)
            )
        )
        conn.execute(
            psycopg.sql.SQL("GRANT USAGE ON SCHEMA memory TO {}").format(
                psycopg.sql.Identifier(role)
            )
        )
        conn.execute(
            psycopg.sql.SQL(
                "GRANT SELECT ON public.pgag_schema_migration,memory.tenant,memory.scope,"
                "memory.principal,memory.scope_member TO {}"
            ).format(psycopg.sql.Identifier(role))
        )
    url = make_conninfo(**(conninfo_to_dict(env.admin_url) | {"user": role, "password": password}))
    try:
        with scope_access(url, request(env)) as result:
            assert result.access_epoch == 1
        with pytest.raises(ScopeAccessError, match="admin_privilege_required"):
            with scope_access(url, request(env, "revoke", expected_access_epoch=1)):
                pytest.fail("Read-only role changed access")
    finally:
        with psycopg.connect(env.admin_url, autocommit=True) as conn:
            conn.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(role)))
            conn.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(role)))
    assert execute(env).membership_exists and not audit_rows(env)


@pytest.mark.integration
def test_actual_cli_roundtrip_and_sanitized_errors(env):
    initial = cli(env, "get")
    assert initial.returncode == 0 and initial.stderr == ""
    assert json.loads(initial.stdout)["access_epoch"] == 1
    changed = cli(
        env, "set", "--expected-access-epoch", "1", "--permissions", "read", "--no-expiry"
    )
    assert changed.returncode == 0 and json.loads(changed.stdout)["access_epoch"] == 2
    conflict = cli(env, "revoke", "--expected-access-epoch", "1")
    assert conflict.returncode == 1 and conflict.stderr == ""
    assert json.loads(conflict.stdout)["error"] == {
        "code": "access_epoch_conflict",
        "outcome_unknown": False,
    }
    assert cli(env, "revoke", "--expected-access-epoch", "2").returncode == 0
    runtime = cli(env, "get", url=env.settings.database_url)
    assert (
        runtime.returncode == 1
        and json.loads(runtime.stdout)["error"]["code"] == "admin_role_required"
    )
    invalid = cli(env, "get", url="postgresql://DO_NOT_ECHO:private@127.0.0.1:1/absent")
    assert (
        invalid.returncode == 1
        and json.loads(invalid.stdout)["error"]["code"] == "admin_database_unavailable"
    )
    assert "DO_NOT_ECHO" not in invalid.stdout + invalid.stderr
    assert "private" not in invalid.stdout + invalid.stderr and "Traceback" not in invalid.stderr


@pytest.mark.integration
def test_admin_revoke_waits_for_real_transaction_boundary_response_delivery(env):
    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"synthetic evidence"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                reached.set()
                await release.wait()

        scope = {
            "type": "http",
            "path": "/v1/recall",
            "headers": [(b"authorization", f"Bearer {env.token()}".encode())],
        }
        reading = asyncio.create_task(TransactionBoundary(app, env.settings)(scope, receive, send))
        await asyncio.wait_for(reached.wait(), 5)
        revoking = asyncio.create_task(
            asyncio.to_thread(execute, env, "revoke", expected_access_epoch=1)
        )
        try:
            for _ in range(100):
                with psycopg.connect(env.admin_url) as conn:
                    waiting = conn.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE wait_event='advisory' "
                        "AND query LIKE 'SELECT pg_advisory_lock(hashtextextended%'"
                    ).fetchone()[0]
                if waiting:
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Administrative revoke did not wait for API response")
            assert not revoking.done()
        finally:
            release.set()
            await reading
        assert (await revoking).access_epoch == 2

    asyncio.run(scenario())
    assert not env.recall().json()["items"]


@pytest.mark.integration
def test_admin_output_barrier_survives_commit_and_closes_after_consumer_error(env):
    with pytest.raises(RuntimeError, match="simulated lost stdout"):
        with scope_access(env.admin_url, request(env, "revoke", expected_access_epoch=1)) as result:
            assert result.changed and result.access_epoch == 2
            with psycopg.connect(env.admin_url, autocommit=True) as conn:
                assert (
                    conn.execute(
                        "SELECT access_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],)
                    ).fetchone()[0]
                    == 2
                )
                assert not conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),)
                ).fetchone()[0]
                assert conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[1]),)
                ).fetchone()[0]
            raise RuntimeError("simulated lost stdout")
    assert execute(env).access_epoch == 2 and not execute(env).membership_exists
    assert len(audit_rows(env)) == 1


@pytest.mark.integration
def test_lock_timeout_is_explicit_and_leaves_no_acl_change(env):
    with psycopg.connect(env.admin_url, autocommit=True) as blocker:
        blocker.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        started = time.monotonic()
        with pytest.raises(ScopeAccessError, match="admin_database_unavailable") as failure:
            execute(env, "revoke", expected_access_epoch=1)
        elapsed = time.monotonic() - started
        assert 4.5 <= elapsed < 10
        assert not failure.value.outcome_unknown
        assert not audit_rows(env)
    assert execute(env).access_epoch == 1 and execute(env).membership_exists


@pytest.mark.integration
def test_epoch_cas_covers_other_scopes_and_distinguishes_empty_legacy_membership(env):
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions) "
            "VALUES (%s,%s,%s,ARRAY[]::text[])",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    target = request(env).model_dump() | {"scope_id": env.scopes[2]}
    with scope_access(env.admin_url, ScopeAccessRequest(**target)) as result:
        assert result.membership_exists and not result.permissions and result.access_epoch == 1
    with scope_access(
        env.admin_url,
        ScopeAccessRequest(
            **(
                target
                | {
                    "operation": "revoke",
                    "expected_access_epoch": 1,
                }
            )
        ),
    ) as result:
        assert result.changed and result.access_epoch == 2 and not result.membership_exists
    with pytest.raises(ScopeAccessError, match="access_epoch_conflict"):
        execute(env, "set", expected_access_epoch=1, permissions=("read",), no_expiry=True)
    assert execute(env).permissions == ["read", "write", "delete"]


@pytest.mark.integration
def test_schema9_migration_preserves_existing_memberships(env, database):
    _, _, legacy = database
    with psycopg.connect(env.admin_url) as conn:
        for record in legacy:
            row = conn.execute(
                "SELECT permissions,expires_at FROM memory.scope_member "
                "WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s",
                (record["tenant"], record["scope"], record["principal"]),
            ).fetchone()
            assert row == (["read", "write", "delete"], None)
            assert (
                conn.execute(
                    "SELECT count(*) FROM memory_ops.scope_access_event WHERE tenant_id=%s",
                    (record["tenant"],),
                ).fetchone()[0]
                == 0
            )
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='memory_ops.scope_access_event'::regclass"
        ).fetchone() == (True, True)
    caps = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert caps["schema_version"] == 10 and caps["stage"] == "m2-job-cancellation"
    assert caps["scope_access_administration"]["transport"] == "admin-cli"
