import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError
from test_processing_chaos import wait_for_real_expiry

from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.api import TransactionBoundary
from pg_agmemory.capture_policy import CapturePolicy, CapturePolicyRequest, capture_policy
from pg_agmemory.jobs import job_transaction
from pg_agmemory.models import Observe, Remember
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError
from pg_agmemory.service import MemoryError
from pg_agmemory.worker import run_once


def policy(**changes):
    return CapturePolicy.model_validate(CapturePolicy.legacy().model_dump() | changes)


def request(env, operation="get", index=0, **changes):
    return CapturePolicyRequest.model_validate(
        {
            "operation": operation,
            "tenant_id": env.tenants[index],
            "scope_id": env.scopes[index],
            **changes,
        }
    )


def execute(env, operation="get", **changes):
    with capture_policy(env.admin_url, request(env, operation, **changes)) as result:
        return result


def set_policy(env, value, epoch=None, **changes):
    if epoch is None:
        epoch = execute(env).access_epoch
    return execute(env, "set", policy=value, expected_access_epoch=epoch, **changes)


def episode(env, **changes):
    return {
        "scope_id": str(env.scopes[0]),
        "source_namespace": "synthetic",
        "source_event_id": str(uuid4()),
        "occurred_at": "2026-09-01T00:00:00Z",
        "content": "Synthetic Gold",
        "consent_reference": "approved-test",
        **changes,
    }


def admission_body(route, source):
    memory = {
        "subject": "Test",
        "predicate": "tier",
        "value": "Gold",
        "evidence_quote": "Gold",
        "explicit_intent": True,
    }
    body = source
    if route == "captures":
        body = {"episode": source, "memory": memory}
    elif route == "captures/batch":
        body = {"episode": source, "memories": [memory, {**memory, "subject": "Other"}]}
    return body


def admission(env, route, source, headers):
    return env.client.post("/v1/" + route, json=admission_body(route, source), headers=headers)


def audit(env):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute(
            """SELECT access_epoch,database_role::text,previous_policy,policy
               FROM memory_ops.capture_policy_event WHERE tenant_id=%s ORDER BY access_epoch""",
            (env.tenants[0],),
        ).fetchall()


def counts(env):
    with psycopg.connect(env.admin_url) as conn:
        return [
            conn.execute(
                psycopg.sql.SQL("SELECT count(*) FROM {} WHERE tenant_id=%s").format(
                    psycopg.sql.Identifier(*name.split("."))
                ),
                (env.tenants[0],),
            ).fetchone()[0]
            for name in (
                "memory.object",
                "memory.episode",
                "memory.episode_lexical",
                "memory_ops.job",
                "memory_ops.job_input",
                "memory_ops.job_identity",
                "memory_ops.source_event",
                "memory_ops.idempotency",
                "memory_ops.audit_event",
            )
        ]


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": 1},
        {"enabled": "true"},
        {"enabled": None},
        {"extra": True},
        {"max_content_bytes": 0},
        {"max_content_bytes": 262145},
        {"max_content_bytes": True},
        {"max_content_bytes": "32"},
        {"source_namespaces": ["a", " a "]},
        {"consent_references": ["b", "b"]},
        {"source_namespaces": [""]},
        {"source_namespaces": [" "]},
        {"source_namespaces": ["a" * 257]},
        {"consent_references": ["\ud800"]},
        {"source_namespaces": ["a\x00b"]},
        {"consent_references": ["a\nb"]},
        {"source_namespaces": "synthetic"},
        {"source_namespaces": [None]},
        {"source_namespaces": [str(i) for i in range(65)]},
        {"consent_references": [str(i) for i in range(65)]},
    ],
)
def test_policy_rejects_invalid_closed_configuration(changes):
    with pytest.raises(ValidationError):
        policy(**changes)


@pytest.mark.parametrize("missing", list(CapturePolicy.model_fields))
def test_policy_requires_every_field(missing):
    body = CapturePolicy.legacy().model_dump()
    del body[missing]
    with pytest.raises(ValidationError):
        CapturePolicy.model_validate(body)


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "reset"},
        {"operation": "set"},
        {"operation": "set", "expected_access_epoch": 1},
        {"expected_access_epoch": 1},
        {"policy": CapturePolicy.legacy().model_dump()},
        {"operation": "set", "expected_access_epoch": True, "policy": CapturePolicy.legacy()},
        {"operation": "set", "expected_access_epoch": 0, "policy": CapturePolicy.legacy()},
        {
            "operation": "set",
            "expected_access_epoch": MAX_EPOCH + 1,
            "policy": CapturePolicy.legacy(),
        },
    ],
)
def test_admin_request_rejects_ambiguous_mutations(changes):
    with pytest.raises(ValidationError):
        CapturePolicyRequest.model_validate(
            {"operation": "get", "tenant_id": uuid4(), "scope_id": uuid4(), **changes}
        )


def test_policy_normalizes_lists_and_preserves_exact_limits():
    assert policy(source_namespaces=["z", " a "]).source_namespaces == ["a", "z"]
    assert len(policy(source_namespaces=[str(i) for i in range(64)]).source_namespaces) == 64
    assert policy(consent_references=["日" * 256]).consent_references == ["日" * 256]
    assert policy(max_content_bytes=1).max_content_bytes == 1
    assert policy(max_content_bytes=262144).max_content_bytes == 262144


@pytest.mark.parametrize("arguments", [["set"], ["bad-PRIVATE"], ["get", "--scope-id", "PRIVATE"]])
def test_cli_does_not_echo_invalid_arguments(arguments):
    result = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "scope-capture", *arguments],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2 and not result.stdout
    assert "invalid_capture_policy_arguments" in result.stderr
    assert "PRIVATE" not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "body", [b"PRIVATE", b"x" * 65537, b'{"enabled":true}', b'{"x":"\\ud800"}']
)
def test_cli_rejects_bad_policy_files_without_leaking_contents(tmp_path, body):
    path = tmp_path / "policy.json"
    path.write_bytes(body)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pg_agmemory.cli",
            "scope-capture",
            "set",
            "--tenant-id",
            str(uuid4()),
            "--scope-id",
            str(uuid4()),
            "--expected-access-epoch",
            "1",
            "--policy-file",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2 and not result.stdout
    assert "invalid_capture_policy_arguments" in result.stderr
    assert "PRIVATE" not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.integration
def test_admin_default_cas_noop_audit_and_restore(env):
    initial = execute(env)
    assert initial.policy == CapturePolicy.legacy()
    assert initial.access_epoch == 1 and initial.policy_access_epoch is None
    assert not initial.changed and not initial.configured
    assert not set_policy(env, CapturePolicy.legacy()).configured
    disabled = set_policy(env, policy(enabled=False))
    assert disabled.changed and disabled.configured
    assert disabled.access_epoch == disabled.policy_access_epoch == 2
    with pytest.raises(AdminError, match="access_epoch_conflict"):
        set_policy(env, policy(enabled=False), epoch=1)
    assert not set_policy(env, policy(enabled=False)).changed
    ordered = set_policy(env, policy(source_namespaces=["z", " a "]))
    assert ordered.access_epoch == 3
    assert not set_policy(env, policy(source_namespaces=["a", "z"])).changed
    restored = set_policy(env, CapturePolicy.legacy())
    assert restored.configured and restored.access_epoch == restored.policy_access_epoch == 4
    rows = audit(env)
    assert [row[0] for row in rows] == [2, 3, 4]
    assert rows[0][2] == CapturePolicy.legacy().model_dump()
    assert rows[-1][3] == CapturePolicy.legacy().model_dump()
    with psycopg.connect(env.admin_url) as conn:
        assert all(row[1] == conn.execute("SELECT current_user").fetchone()[0] for row in rows)
    assert execute(env, index=1).access_epoch == 1 and not execute(env, index=1).configured


@pytest.mark.integration
@pytest.mark.parametrize("route", ["observe", "captures", "captures/batch"])
def test_all_admission_replay_and_source_dedup_revalidate_current_policy(env, route):
    source, headers = episode(env), env.headers()
    created = admission(env, route, source, headers)
    assert created.status_code == 201
    saved = created.json()
    before = counts(env)
    set_policy(env, policy(enabled=False))
    for data, key in (
        (source, headers),
        (source, env.headers()),
        ({**source, "source_event_id": str(uuid4())}, env.headers()),
        ({**source, "content": "Changed Gold"}, headers),
    ):
        denied = admission(env, route, data, key)
        assert denied.status_code == 403 and denied.json()["code"] == "capture_policy_denied"
        assert (
            source["content"] not in denied.text and source["consent_reference"] not in denied.text
        )
        assert counts(env) == before
    assert env.recall().json()["items"]
    set_policy(env, CapturePolicy.legacy())
    assert admission(env, route, source, headers).json() == saved
    assert admission(env, route, source, env.headers()).json() == saved


@pytest.mark.integration
@pytest.mark.parametrize(
    ("changes", "source_changes", "accepted"),
    [
        ({"source_namespaces": []}, {}, False),
        ({"consent_references": []}, {}, False),
        ({"source_namespaces": ["synthetic"]}, {}, True),
        ({"source_namespaces": ["Synthetic"]}, {}, False),
        ({"consent_references": ["approved-test"]}, {}, True),
        ({"consent_references": ["different"]}, {}, False),
        (
            {"source_namespaces": ["synthetic"], "consent_references": ["approved-test"]},
            {"consent_reference": "different"},
            False,
        ),
        ({"max_content_bytes": 6}, {"content": "日本"}, True),
        ({"max_content_bytes": 5}, {"content": "日本"}, False),
        ({"max_content_bytes": 6}, {"content": " 日本 "}, True),
        ({"max_content_bytes": 1}, {"content": "a"}, True),
        ({"max_content_bytes": 1}, {"content": "ab"}, False),
    ],
)
def test_policy_exact_allowlists_and_utf8_byte_boundaries(env, changes, source_changes, accepted):
    set_policy(env, policy(**changes))
    response = admission(env, "observe", episode(env, **source_changes), env.headers())
    assert response.status_code == (201 if accepted else 403), response.text


@pytest.mark.integration
def test_scope_access_precedes_policy_and_other_scope_epoch_cas(env):
    set_policy(env, policy(enabled=False))
    for target in (env.scopes[1], env.scopes[2], uuid4()):
        assert (
            admission(env, "observe", episode(env, scope_id=str(target)), env.headers()).status_code
            == 404
        )
    with pytest.raises(AdminError, match="not_found"):
        execute(env, scope_id=env.scopes[1])
    other = set_policy(env, policy(enabled=False), scope_id=env.scopes[2], epoch=2)
    assert other.access_epoch == 3
    with pytest.raises(AdminError, match="access_epoch_conflict"):
        set_policy(env, CapturePolicy.legacy(), epoch=2)
    with scope_access(
        env.admin_url,
        ScopeAccessRequest(
            operation="set",
            tenant_id=env.tenants[0],
            scope_id=env.scopes[0],
            principal_id=env.principals[0],
            expected_access_epoch=3,
            permissions=("read",),
            no_expiry=True,
        ),
    ) as changed:
        assert changed.access_epoch == 4
    assert admission(env, "observe", episode(env), env.headers()).status_code == 404
    assert execute(env).policy_access_epoch == 2


@pytest.mark.integration
def test_runtime_policy_rls_is_read_only_and_audit_is_private(env):
    for index in (0, 1):
        set_policy(env, policy(enabled=False), index=index, epoch=1)
    with pytest.raises(AdminError, match="admin_role_required"):
        with capture_policy(env.settings.database_url, request(env)):
            pytest.fail("Runtime role accepted")
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT set_config('pgag.tenant_id',%s,true),"
                "set_config('pgag.principal_id',%s,true)",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            rows = conn.execute(
                "SELECT tenant_id,scope_id FROM memory.scope_capture_policy"
            ).fetchall()
            assert rows == [(env.tenants[0], env.scopes[0])]
        for statement in (
            "SELECT * FROM memory_ops.capture_policy_event",
            "UPDATE memory.scope_capture_policy SET enabled=true",
            "DELETE FROM memory.scope_capture_policy",
            "INSERT INTO memory.scope_capture_policy DEFAULT VALUES",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


@pytest.mark.integration
def test_policy_fences_running_job_but_does_not_purge_or_cancel_explicit_work(env):
    source = env.observe().json()["memory_id"]
    body = {
        "scope_id": str(env.scopes[0]),
        "subject": "Test",
        "predicate": "tier",
        "value": "Gold",
        "explicit_intent": True,
        "evidence": [{"memory_id": source, "quote": "Gold"}],
    }
    receipt = env.client.post(
        "/v1/jobs", json={"kind": "structured_remember", "memory": body}, headers=env.headers()
    ).json()

    async def claim():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            return await jobs.claim(lease_seconds=5)

    lease = asyncio.run(claim())
    set_policy(env, policy(enabled=False))

    async def publish():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            await jobs.publish(lease["job_id"], lease["lease_token"], Remember.model_validate(body))

    with pytest.raises(MemoryError, match="stale_context"):
        asyncio.run(publish())
    assert env.client.post("/v1/remember", json=body, headers=env.headers()).status_code == 201
    assert len(env.recall().json()["items"]) == 2
    wait_for_real_expiry(env, receipt["job_id"], timeout_seconds=6)
    recovered = asyncio.run(run_once(env.settings.database_url, env.subjects[0]))
    assert recovered["outcome"] == "succeeded" and recovered["job_id"] == receipt["job_id"]
    detail = env.client.get("/v1/jobs/" + receipt["job_id"], headers=env.headers()).json()
    assert detail["state"] == "succeeded" and detail["attempt"] == 2


@pytest.mark.integration
def test_invalid_persisted_policy_is_fail_closed_without_exposing_labels(env):
    set_policy(env, policy(enabled=True, source_namespaces=["synthetic"]))
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory.scope_capture_policy SET source_namespaces=ARRAY['PRIVATE','PRIVATE'] "
            "WHERE tenant_id=%s",
            (env.tenants[0],),
        )
    with pytest.raises(AdminError, match="capture_policy_invalid"):
        execute(env)
    response = env.observe()
    assert response.status_code == 503 and response.json()["code"] == "capture_policy_invalid"
    assert "PRIVATE" not in response.text


@pytest.mark.integration
@pytest.mark.parametrize("route", ["observe", "captures", "captures/batch"])
@pytest.mark.parametrize(
    "field", ["content", "source_namespace", "source_event_id", "consent_reference"]
)
def test_invalid_capture_utf8_is_explicit_and_atomic(env, route, field):
    before = counts(env)
    source = episode(env, **{field: "\ud800"})
    response = env.client.post(
        "/v1/" + route,
        content=json.dumps(admission_body(route, source)),
        headers={**env.headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    assert counts(env) == before


@pytest.mark.integration
def test_audit_failure_rolls_back_policy_and_epoch(env, monkeypatch):
    original = psycopg.Connection.execute

    def fail(conn, query, *args, **kwargs):
        if isinstance(query, str) and "INSERT INTO memory_ops.capture_policy_event" in query:
            raise psycopg.IntegrityError("PRIVATE")
        return original(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", fail)
        with pytest.raises(AdminError, match="admin_database_error") as failure:
            set_policy(env, policy(enabled=False))
        assert not failure.value.outcome_unknown and "PRIVATE" not in str(failure.value)
    assert execute(env).access_epoch == 1 and not execute(env).configured and not audit(env)


@pytest.mark.integration
def test_commit_response_loss_is_unknown_and_readback_resolves(env, monkeypatch):
    original = psycopg.Connection.transaction

    @contextmanager
    def lost(conn, *args, **kwargs):
        with original(conn, *args, **kwargs) as transaction:
            yield transaction
        raise psycopg.OperationalError("PRIVATE")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "transaction", lost)
        with pytest.raises(AdminError, match="admin_database_unavailable") as failure:
            set_policy(env, policy(enabled=False), epoch=1)
        assert failure.value.outcome_unknown
    assert execute(env).access_epoch == 2 and not execute(env).policy.enabled
    assert len(audit(env)) == 1


@pytest.mark.integration
def test_concurrent_policy_cas_has_single_winner(env):
    def change(size):
        try:
            return set_policy(env, policy(max_content_bytes=size), epoch=1).access_epoch
        except AdminError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, (1, 2)))
    assert sorted(map(str, results)) == ["2", "access_epoch_conflict"]
    assert len(audit(env)) == 1


@pytest.mark.integration
def test_policy_max_epoch_allows_noop_but_rejects_changes(env):
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s", (MAX_EPOCH, env.tenants[0])
        )
    assert not set_policy(env, CapturePolicy.legacy(), epoch=MAX_EPOCH).changed
    with pytest.raises(AdminError, match="access_epoch_exhausted"):
        set_policy(env, policy(enabled=False), epoch=MAX_EPOCH)
    assert not execute(env).configured and not audit(env)


@pytest.mark.integration
def test_policy_output_barrier_holds_through_commit_and_releases_on_error(env):
    with pytest.raises(RuntimeError, match="stdout lost"):
        with capture_policy(
            env.admin_url,
            request(env, "set", expected_access_epoch=1, policy=policy(enabled=False)),
        ) as changed:
            assert changed.access_epoch == 2
            with psycopg.connect(env.admin_url) as conn:
                assert not conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),)
                ).fetchone()[0]
            raise RuntimeError("stdout lost")
    assert execute(env).access_epoch == 2


@pytest.mark.integration
def test_policy_change_waits_for_api_buffered_response_delivery(env):
    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"synthetic"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                reached.set()
                await release.wait()

        scope = {
            "type": "http",
            "path": "/v1/observe",
            "headers": [(b"authorization", ("Bearer " + env.token()).encode())],
        }
        reading = asyncio.create_task(TransactionBoundary(app, env.settings)(scope, receive, send))
        await asyncio.wait_for(reached.wait(), 5)
        changing = asyncio.create_task(asyncio.to_thread(set_policy, env, policy(enabled=False), 1))
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
                pytest.fail("Policy mutation did not wait for response delivery")
            assert not changing.done()
        finally:
            release.set()
            await reading
        assert (await changing).access_epoch == 2

    asyncio.run(scenario())


@pytest.mark.integration
def test_cli_and_sdk_roundtrip_error_contract(env, tmp_path, api_process):
    path = tmp_path / "policy.json"
    path.write_text(policy(enabled=False).model_dump_json())
    args = [
        sys.executable,
        "-m",
        "pg_agmemory.cli",
        "scope-capture",
        "set",
        "--tenant-id",
        str(env.tenants[0]),
        "--scope-id",
        str(env.scopes[0]),
        "--expected-access-epoch",
        "1",
        "--policy-file",
        str(path),
    ]
    cli_env = {**os.environ, "PGAG_ADMIN_DATABASE_URL": env.admin_url}
    first = subprocess.run(args, env=cli_env, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0 and not first.stderr
    assert json.loads(first.stdout)["access_epoch"] == 2
    stale = subprocess.run(args, env=cli_env, capture_output=True, text=True, timeout=15)
    assert stale.returncode == 1 and not stale.stderr
    assert json.loads(stale.stdout)["error"] == {
        "code": "access_epoch_conflict",
        "outcome_unknown": False,
    }
    with api_process("capture-policy-sdk.log") as (http, _):

        async def denied():
            async with AsyncMemoryClient(str(http.base_url), env.token()) as sdk:
                with pytest.raises(MemoryClientError) as failure:
                    await sdk.observe(
                        Observe.model_validate(episode(env)), idempotency_key="policy"
                    )
                assert failure.value.error.code == "capture_policy_denied"
                assert not failure.value.error.outcome_unknown

        asyncio.run(denied())


@pytest.mark.integration
def test_migration_preserves_legacy_capture_and_caps_are_explicit(env, database):
    _, _, legacy = database
    with psycopg.connect(env.admin_url) as conn:
        for row in legacy:
            assert (
                conn.execute(
                    "SELECT count(*) FROM memory.scope_capture_policy WHERE tenant_id=%s",
                    (row["tenant"],),
                ).fetchone()[0]
                == 0
            )
        for name in ("memory.scope_capture_policy", "memory_ops.capture_policy_event"):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (name,),
            ).fetchone() == (True, True)
    caps = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert caps["schema_version"] == 21 and caps["stage"] == "m4-integration-pilot"
    assert caps["capture_policy"]["replay_revalidated"]
    assert not caps["capture_policy"]["secret_pii_detection"]
    assert not caps["capture_policy"]["provider_egress_control"]
    assert not caps["auto_synthesis"]
    schema = env.client.get("/openapi.json").json()
    for path in ("/v1/observe", "/v1/captures", "/v1/captures/batch"):
        assert schema["paths"][path]["post"]["responses"]["403"]["content"][
            "application/json"
        ]["schema"] == {"$ref": "#/components/schemas/ErrorBody"}
