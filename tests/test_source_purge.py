import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError
from test_external_source import (
    PRIVATE_TEXT,
    binding,
    capture_bundle,
    content,
    envelope,
    snapshot,
)
from test_external_source import native_transport as native_transport
from test_source_dataset import (
    DATASET,
    allow_reader,
    bind_reader,
    epoch,
    source_command,
)
from test_source_notice import public_pem, signed
from test_source_notice import signing_key as signing_key

from pg_agmemory import source_purge as coordinator
from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.capture_policy import CapturePolicy, CapturePolicyRequest, capture_policy
from pg_agmemory.external_source import SOURCE_NAMESPACE, ExternalSourceMemory
from pg_agmemory.models import (
    EnqueueJob,
    Evidence,
    Forget,
    MemoryReference,
    PlanToolEffect,
    Remember,
)
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.sdk import AsyncMemoryClient
from pg_agmemory.service import MemoryService
from pg_agmemory.source_access import SourceDatasetIdentity, SourceNotice
from pg_agmemory.source_notice import SourceNoticeProfile, receive_source_notice
from pg_agmemory.source_purge import (
    MAX_SOURCE_PURGE_BINDINGS,
    MAX_SOURCE_PURGE_PLAN_BYTES,
    MAX_SOURCE_PURGE_ROOTS,
    MAX_SOURCE_PURGE_SCOPES,
    SourcePurgeBinding,
    SourcePurgePlan,
    SourcePurgeRequest,
    SourcePurgeRoot,
    source_purge,
)

PRIVATE = "DO_NOT_ECHO_SOURCE_PURGE"
UNAVAILABLE_URL = "host=127.0.0.1 port=1 dbname=DO_NOT_ECHO_SOURCE_PURGE"
AUTHORITY_TABLES = (
    "memory_ops.source_access_state",
    "memory_ops.source_access_event",
    "memory_ops.scope_access_event",
    "memory.scope_member",
    "memory.scope_capture_policy",
    "memory_ops.capture_policy_event",
)
STATE_TABLES = (
    *AUTHORITY_TABLES,
    "memory.object",
    "memory.episode",
    "memory.assertion",
    "memory.assertion_revision",
    "memory.provenance_edge",
    "memory.checkpoint",
    "memory.checkpoint_reference",
    "memory.tool_effect",
    "memory.tool_effect_revision",
    "memory.tool_effect_reference",
    "memory_ops.job",
    "memory_ops.job_input",
    "memory_ops.object_tombstone",
    "memory_ops.deletion_request",
    "memory_ops.deletion_target",
    "memory_ops.idempotency",
    "memory_ops.audit_event",
)


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def plan_payload(**changes):
    scope = uuid4()
    payload = {
        "format": "pgag-source-purge-plan-v1",
        "tenant_id": str(uuid4()),
        "dataset": DATASET.model_dump(mode="json"),
        "maintenance_principal_id": str(uuid4()),
        "access_epoch": 7,
        "deletion_epoch": 3,
        "bindings": [{
            "scope_id": str(scope),
            "principal_id": str(uuid4()),
            "source_subject": "synthetic-reader",
            "sequence": 1,
        }],
        "roots": [{
            "memory_id": str(uuid4()),
            "scope_id": str(scope),
            "content_digest": "b" * 64,
        }],
        **changes,
    }
    payload["plan_digest"] = canonical_digest(payload)
    return payload


def request(env, operation="plan", **changes):
    return SourcePurgeRequest.model_validate({
        "operation": operation,
        "tenant_id": env.tenants[0],
        "dataset": DATASET,
        "maintenance_principal_id": env.principals[0],
        **changes,
    })


def execute(env, operation="plan", *, url=None, **changes):
    async def run():
        async with source_purge(url or env.admin_url, request(env, operation, **changes)) as result:
            return result

    return asyncio.run(run())


def apply(env, plan, **changes):
    return execute(env, "apply", expected_plan=plan, **changes)


def state(env, tables=STATE_TABLES):
    with psycopg.connect(env.admin_url) as conn:
        result = {
            "epochs": conn.execute(
                "SELECT access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s",
                (env.tenants[0],),
            ).fetchone(),
        }
        for table in tables:
            result[table] = tuple(
                row[0].encode("utf-8")
                for row in conn.execute(
                    psycopg.sql.SQL(
                        "SELECT to_jsonb(t)::text FROM {} t "
                        "WHERE tenant_id=%s ORDER BY to_jsonb(t)::text"
                    ).format(psycopg.sql.Identifier(*table.split("."))),
                    (env.tenants[0],),
                ).fetchall()
            )
        return result


def stop_capture(env, scope=None):
    with capture_policy(env.admin_url, CapturePolicyRequest(
        operation="set",
        tenant_id=env.tenants[0],
        scope_id=scope or env.scopes[0],
        expected_access_epoch=epoch(env),
        policy=CapturePolicy.model_validate(CapturePolicy.legacy().model_dump() | {
            "enabled": False,
        }),
    )) as result:
        assert result.configured and not result.policy.enabled
        return result


def terminal(env, reader, *, sequence=1, reason="deleted"):
    return source_command(
        env, reader, "apply", expected_access_epoch=epoch(env),
        notice=SourceNotice(
            source=reader.source, sequence=sequence, decision="deny", reason=reason,
        ),
    )


def capture_episode(env, *, source=None, value=None, **changes):
    source = source or binding(env.scopes[0])
    value = value or snapshot(query_id=str(uuid4()))
    body = {
        "scope_id": str(source.scope_id),
        "source_namespace": SOURCE_NAMESPACE,
        "source_event_id": hashlib.sha256(json.dumps({
            "source_system": source.source_system,
            "dataset_id": source.dataset_id,
            "source_subject": source.source_subject,
            "query_id": value.query_id,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
        "occurred_at": value.observed_at.isoformat(),
        "content": content(envelope(source, value)),
        "consent_reference": "synthetic-consent",
        **changes,
    }
    headers = env.headers()
    response = env.client.post("/v1/observe", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return UUID(response.json()["memory_id"]), body, headers


def ready(env, *, roots=1):
    reader = bind_reader(
        env, scope_id=env.scopes[0], principal_id=env.principals[2],
        subject="synthetic-reader",
    )
    captured = [capture_episode(env) for _ in range(roots)]
    terminal(env, reader)
    stop_capture(env)
    return reader, captured


def assert_safe_result(result, *, replayed=False):
    assert not result.capture_reenabled
    assert not result.source_authorization_verified
    assert result.backup_status == "operator_managed"
    assert result.replayed is replayed
    output = result.model_dump_json()
    assert PRIVATE_TEXT not in output and "PRIVATE_SOURCE_RESULT" not in output
    assert "snapshot_text" not in output and "BEGIN " not in output
    assert set(json.loads(output)["plan"]) == {
        "format", "tenant_id", "dataset", "maintenance_principal_id",
        "access_epoch", "deletion_epoch", "bindings", "roots", "plan_digest",
    }


def cli_arguments(operation="plan", *, tenant_id=None, maintenance_principal_id=None):
    return [
        operation,
        "--tenant-id", str(tenant_id or uuid4()),
        "--source-system", DATASET.source_system,
        "--dataset-id", DATASET.dataset_id,
        "--maintenance-principal-id", str(maintenance_principal_id or uuid4()),
    ]


def cli(*arguments, url=None):
    variables = {"PATH": os.environ["PATH"]}
    if url is not None:
        variables["PGAG_ADMIN_DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "source-purge", *arguments],
        env=variables, capture_output=True, text=True, timeout=15,
    )


@pytest.fixture
def plan_file():
    path = Path(f".source-purge-plan-{uuid4().hex}.json")
    try:
        yield path
    finally:
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink(missing_ok=True)
        path.with_suffix(".target.json").unlink(missing_ok=True)


def test_frozen_limits_and_independent_plan_digest():
    assert MAX_SOURCE_PURGE_BINDINGS == MAX_SOURCE_PURGE_ROOTS == 100
    assert MAX_SOURCE_PURGE_SCOPES == 32
    assert MAX_SOURCE_PURGE_PLAN_BYTES == 262144
    payload = plan_payload()
    plan = SourcePurgePlan.model_validate(payload)
    assert plan.model_dump(mode="json") == payload
    assert plan.plan_digest == canonical_digest(plan.model_dump(
        mode="json", exclude={"plan_digest"},
    ))
    assert isinstance(plan.bindings, tuple) and isinstance(plan.roots, tuple)
    for value, field in (
        (plan, "access_epoch"),
        (plan.bindings[0], "sequence"),
        (plan.roots[0], "content_digest"),
    ):
        with pytest.raises(ValidationError):
            setattr(value, field, 999)


@pytest.mark.parametrize("changes", [
    {"format": "other"},
    {"access_epoch": 0},
    {"access_epoch": True},
    {"access_epoch": "7"},
    {"deletion_epoch": MAX_EPOCH + 1},
    {"deletion_epoch": 0},
    {"deletion_epoch": True},
    {"bindings": []},
    {"extra": PRIVATE},
])
def test_plan_rejects_invalid_metadata_even_with_recomputed_digest(changes):
    with pytest.raises(ValidationError):
        SourcePurgePlan.model_validate(plan_payload(**changes))


@pytest.mark.parametrize("mutation", [
    "digest", "root-digest", "root-route", "duplicate-root", "duplicate-binding",
    "unordered-roots", "unordered-bindings", "binding-sequence", "binding-extra",
    "root-extra", "root-limit", "binding-limit", "dataset-extra",
])
def test_plan_rejects_forged_noncanonical_or_unbounded_input(mutation):
    payload = plan_payload()
    if mutation == "digest":
        payload["access_epoch"] += 1
    elif mutation == "root-digest":
        payload["roots"][0]["content_digest"] = "A" * 64
    elif mutation == "root-route":
        payload["roots"][0]["scope_id"] = str(uuid4())
    elif mutation == "duplicate-root":
        payload["roots"] *= 2
    elif mutation == "duplicate-binding":
        payload["bindings"] *= 2
    elif mutation == "unordered-roots":
        payload["roots"] = [
            payload["roots"][0] | {"memory_id": str(UUID(int=index))} for index in (2, 1)
        ]
    elif mutation == "unordered-bindings":
        payload["bindings"] = [
            payload["bindings"][0] | {"principal_id": str(UUID(int=index))} for index in (2, 1)
        ]
    elif mutation == "binding-sequence":
        payload["bindings"][0]["sequence"] = True
    elif mutation == "binding-extra":
        payload["bindings"][0]["notice"] = PRIVATE
    elif mutation == "root-extra":
        payload["roots"][0]["content"] = PRIVATE
    elif mutation == "root-limit":
        payload["roots"] = [
            payload["roots"][0] | {"memory_id": str(UUID(int=index + 1))}
            for index in range(101)
        ]
    elif mutation == "binding-limit":
        payload["bindings"] = [
            payload["bindings"][0] | {"principal_id": str(UUID(int=index + 1))}
            for index in range(101)
        ]
    else:
        payload["dataset"]["source_subject"] = PRIVATE
    if mutation != "digest":
        payload["plan_digest"] = canonical_digest({
            key: value for key, value in payload.items() if key != "plan_digest"
        })
    with pytest.raises(ValidationError):
        SourcePurgePlan.model_validate(payload)


@pytest.mark.parametrize("mutation", [
    "apply-without-plan", "plan-with-plan", "tenant", "dataset", "principal", "extra",
])
def test_request_requires_exact_plan_routing(mutation):
    plan = SourcePurgePlan.model_validate(plan_payload())
    payload = {
        "operation": "apply",
        "tenant_id": plan.tenant_id,
        "dataset": plan.dataset,
        "maintenance_principal_id": plan.maintenance_principal_id,
        "expected_plan": plan,
    }
    if mutation == "apply-without-plan":
        payload.pop("expected_plan")
    elif mutation == "plan-with-plan":
        payload["operation"] = "plan"
    elif mutation == "tenant":
        payload["tenant_id"] = uuid4()
    elif mutation == "principal":
        payload["maintenance_principal_id"] = uuid4()
    elif mutation == "dataset":
        payload["dataset"] = SourceDatasetIdentity(source_system="other", dataset_id="contracts")
    else:
        payload["upstream_token"] = PRIVATE
    with pytest.raises(ValidationError):
        SourcePurgeRequest.model_validate(payload)


@pytest.mark.parametrize("mutation", ["request", "plan", "binding", "root", "dataset"])
def test_constructed_nested_models_are_redacted_before_database_access(monkeypatch, mutation):
    plan = SourcePurgePlan.model_validate(plan_payload())
    if mutation == "plan":
        plan = plan.model_copy(update={"plan_digest": PRIVATE})
    elif mutation == "binding":
        bad = SourcePurgeBinding.model_construct(
            **(plan.bindings[0].model_dump() | {"source_subject": "\x00" + PRIVATE})
        )
        plan = plan.model_copy(update={"bindings": (bad,)})
    elif mutation == "root":
        bad = SourcePurgeRoot.model_construct(
            **(plan.roots[0].model_dump() | {"content_digest": PRIVATE})
        )
        plan = plan.model_copy(update={"roots": (bad,)})
    elif mutation == "dataset":
        plan = plan.model_copy(update={
            "dataset": SourceDatasetIdentity.model_construct(
                source_system=PRIVATE, dataset_id="\x00",
            ),
        })
    value = SourcePurgeRequest.model_construct(
        operation="apply" if mutation != "request" else PRIVATE,
        tenant_id=plan.tenant_id,
        dataset=plan.dataset,
        maintenance_principal_id=plan.maintenance_principal_id,
        expected_plan=plan,
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid constructed request reached the database")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", forbidden)

    async def run():
        with pytest.raises(AdminError, match="^invalid_source_purge_request$") as failure:
            async with source_purge(UNAVAILABLE_URL, value):
                pytest.fail("Invalid request was accepted")
        assert not failure.value.outcome_unknown
        assert PRIVATE not in str(failure.value)

    asyncio.run(run())


def test_admin_core_imports_do_not_require_sdk_or_httpx():
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys
class NoAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'httpx' or fullname.startswith('httpx.') or fullname in (
            'pg_agmemory.sdk', 'pg_agmemory.external_source', 'pg_agmemory.native_client'
        ):
            raise ImportError('optional adapter dependency imported')
sys.meta_path.insert(0, NoAdapters())
import pg_agmemory.source_purge
import pg_agmemory.source_snapshot
assert 'httpx' not in sys.modules
"""],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_plan_is_read_only_exact_sorted_and_contains_no_snapshot_content(env):
    reader, captured = ready(env, roots=3)
    before = state(env)
    result = execute(env)
    assert state(env) == before
    assert result.operation == "plan" and not result.changed
    assert result.receipt is None and not result.replayed
    assert result.coverage == "planned_source_snapshot_roots"
    assert_safe_result(result)
    plan = result.plan
    assert plan.tenant_id == env.tenants[0] and plan.dataset == DATASET
    assert plan.maintenance_principal_id == env.principals[0]
    assert (plan.access_epoch, plan.deletion_epoch) == before["epochs"]
    assert [(row.scope_id, row.principal_id, row.source_subject, row.sequence)
            for row in plan.bindings] == [
        (reader.scope_id, reader.principal_id, reader.source.source_subject, 1),
    ]
    assert [(row.memory_id, row.scope_id, row.content_digest) for row in plan.roots] == sorted(
        (memory_id, env.scopes[0], hashlib.sha256(body["content"].encode()).hexdigest())
        for memory_id, body, _ in captured
    )
    assert plan.plan_digest == canonical_digest(plan.model_dump(
        mode="json", exclude={"plan_digest"},
    ))
    assert execute(env).plan == plan


@pytest.mark.integration
def test_signed_terminal_deletion_purges_native_closure_as_runtime_and_preserves_history(
    env, native_transport, signing_key, monkeypatch,
):
    source = binding(env.scopes[0])
    reader = bind_reader(
        env, scope_id=source.scope_id, principal_id=env.principals[2],
        subject=source.source_subject,
    )
    allow_reader(env, reader)

    async def capture():
        async with AsyncMemoryClient("http://localhost", env.token()) as client:
            saved = await capture_bundle(client, source)
            effect = await client.plan_tool_effect(
                PlanToolEffect(
                    scope_id=source.scope_id, run_id=saved.branch.run_id,
                    operation_id=uuid4(), tool_name="synthetic.send", action_hash="a" * 64,
                    memory_refs=[MemoryReference(memory_id=saved.derived)],
                ),
                idempotency_key="source-purge-effect",
            )
            job = await client.enqueue_job(
                EnqueueJob(kind="structured_remember", memory=Remember(
                    scope_id=source.scope_id, subject="ACME", predicate="tier",
                    value="Gold", explicit_intent=True,
                    evidence=[Evidence(memory_id=saved.source, quote="Gold")],
                )),
                idempotency_key="source-purge-job",
            )
            assert await ExternalSourceMemory(client, binding=source).read_snapshot(
                saved.source
            ) == envelope(source)
            return saved, effect.memory_id, job.job_id

    saved, effect_id, job_id = asyncio.run(capture())
    profile = SourceNoticeProfile(
        issuer="synthetic-local-notifier", audience="synthetic-memory-ingress",
        subject="synthetic-source-administrator", key_id="synthetic-key-1",
        public_key=public_pem(signing_key), tenant_id=reader.tenant_id,
        scope_id=reader.scope_id, principal_id=reader.principal_id, source=reader.source,
    )
    delivery = signed(signing_key, profile, SourceNotice(
        source=reader.source, sequence=2, decision="deny", reason="deleted",
    ))
    with receive_source_notice(
        env.admin_url, profile, delivery, expected_access_epoch=epoch(env),
    ) as deleted:
        assert deleted.notification_signature_verified
        assert deleted.access.reason == "deleted" and deleted.access.effective_permissions == []
    stop_capture(env)
    before = state(env)
    plan = execute(env).plan
    original = MemoryService.forget
    calls = []

    async def checked(memory, data, key):
        row = await (await memory.conn.execute(
            """SELECT current_user AS actor,session_user AS administrator,
                      memory.current_tenant() AS tenant,
                      memory.current_principal() AS principal,
                      current_setting('pgag.subject') AS subject,
                      pg_backend_pid() AS pid"""
        )).fetchone()
        assert row["actor"] == "pgag_runtime"
        assert row["administrator"] != "pgag_runtime"
        assert row["tenant"] == env.tenants[0] and row["principal"] == env.principals[0]
        assert row["subject"] == env.subjects[0]
        with psycopg.connect(env.admin_url) as conn:
            assert conn.execute(
                """SELECT count(*) FROM pg_locks
                   WHERE pid=%s AND locktype='advisory' AND granted""", (row["pid"],),
            ).fetchone()[0] >= 1
        assert data == Forget(
            memory_ids=[saved.source], mode="purge", reason="external_source_deleted",
        )
        assert key == "source-purge-v1:" + plan.plan_digest
        calls.append((data, key))
        return await original(memory, data, key)

    with monkeypatch.context() as patch:
        patch.setattr(MemoryService, "forget", checked)
        result = apply(env, plan)
    assert len(calls) == 1
    assert result.changed and result.receipt.object_count == 5
    assert result.receipt.state == "active_store_purged"
    assert result.receipt.deletion_epoch == plan.deletion_epoch + 1
    assert result.plan == plan
    assert_safe_result(result)
    after = state(env)
    assert after["epochs"] == (plan.access_epoch, plan.deletion_epoch + 1)
    for table in AUTHORITY_TABLES:
        assert after[table] == before[table]
    for table in (
        "memory.episode", "memory.assertion", "memory.assertion_revision",
        "memory.provenance_edge", "memory.checkpoint", "memory.checkpoint_reference",
        "memory.tool_effect", "memory.tool_effect_revision", "memory.tool_effect_reference",
        "memory_ops.job", "memory_ops.job_input",
    ):
        assert after[table] == ()
    with psycopg.connect(env.admin_url) as conn:
        assert {row[0] for row in conn.execute(
            "SELECT object_id FROM memory_ops.object_tombstone WHERE tenant_id=%s",
            (env.tenants[0],),
        )} == {saved.source, saved.derived, saved.checkpoint, effect_id, job_id}
    replay = apply(env, plan)
    assert replay.receipt == result.receipt and replay.plan == plan
    assert replay.replayed and not replay.changed
    assert_safe_result(replay, replayed=True)
    assert state(env) == after
    with pytest.raises(AdminError, match="^source_deleted$"):
        allow_reader(env, reader, sequence=3)
    assert state(env) == after


@pytest.mark.integration
@pytest.mark.parametrize("roots", [0, 1])
def test_registered_empty_scope_is_valid_but_unbound_dataset_is_not(env, roots):
    if roots == 0:
        with pytest.raises(AdminError, match="^source_dataset_not_bound$"):
            execute(env)
    ready(env, roots=roots)
    before = state(env)
    plan = execute(env).plan
    assert len(plan.roots) == roots
    result = apply(env, plan)
    if roots:
        assert result.receipt.object_count == 1 and result.changed
    else:
        assert result.receipt is None and not result.changed and not result.replayed
        assert state(env) == before
        second = apply(env, plan)
        assert second.receipt is None and not second.changed and not second.replayed
        assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("reason", ["bound", "allow", "revoked", "unavailable", "expired"])
def test_emergency_revocation_or_expired_allow_is_not_terminal_source_deletion(env, reason):
    reader = bind_reader(env, scope_id=env.scopes[0], subject="synthetic-reader")
    capture_episode(env)
    if reason in ("allow", "expired"):
        allow_reader(env, reader)
        if reason == "expired":
            with psycopg.connect(env.admin_url) as conn:
                conn.execute(
                    """UPDATE memory.scope_member SET expires_at='2000-01-01'
                       WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                    (reader.tenant_id, reader.scope_id, reader.principal_id),
                )
    elif reason != "bound":
        terminal(env, reader, reason=reason)
    stop_capture(env)
    before = state(env)
    with pytest.raises(AdminError, match="^source_purge_source_not_deleted$"):
        execute(env)
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("configured", [False, True])
def test_capture_must_be_explicitly_disabled_before_planning(env, configured):
    reader = bind_reader(env, scope_id=env.scopes[0], subject="synthetic-reader")
    capture_episode(env)
    terminal(env, reader)
    if configured:
        with capture_policy(env.admin_url, CapturePolicyRequest(
            operation="set", tenant_id=env.tenants[0], scope_id=env.scopes[0],
            expected_access_epoch=epoch(env),
            policy=CapturePolicy(
                enabled=True, source_namespaces=[SOURCE_NAMESPACE],
                consent_references=None, max_content_bytes=262144,
            ),
        )):
            pass
    before = state(env)
    with pytest.raises(AdminError, match="^source_purge_capture_enabled$"):
        execute(env)
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("mutation", [
    "ordinary-episode", "invalid-json", "duplicate-json-key", "forged-digest",
    "foreign-scope", "other-dataset", "other-system", "other-subject",
    "unknown-format", "wrong-namespace", "wrong-occurrence", "envelope-extra",
])
def test_every_episode_must_prove_exact_source_scope_identity_and_timestamp(env, mutation):
    reader = bind_reader(env, scope_id=env.scopes[0], subject="synthetic-reader")
    capture_episode(env)
    source = binding(env.scopes[0])
    value = snapshot(query_id=str(uuid4()))
    payload = envelope(source, value).model_dump(mode="json")
    overrides = {}
    if mutation == "ordinary-episode":
        overrides = {"content": PRIVATE, "source_namespace": "ordinary"}
    elif mutation == "invalid-json":
        overrides["content"] = "{" + PRIVATE
    elif mutation == "duplicate-json-key":
        overrides["content"] = (
            '{"format":"untrusted",' + content(envelope(source, value))[1:]
        )
    elif mutation == "forged-digest":
        payload["snapshot"]["result_digest"] = "0" * 64
    elif mutation == "foreign-scope":
        payload["source"]["scope_id"] = str(env.scopes[2])
    elif mutation.startswith("other-"):
        field = {
            "other-dataset": "dataset_id", "other-system": "source_system",
            "other-subject": "source_subject",
        }[mutation]
        payload["source"][field] = "unrelated"
    elif mutation == "unknown-format":
        payload["format"] = "unknown"
    elif mutation == "wrong-namespace":
        overrides["source_namespace"] = "other"
    elif mutation == "wrong-occurrence":
        overrides["occurred_at"] = "2026-09-02T00:00:00Z"
    else:
        payload["credential"] = PRIVATE
    overrides.setdefault("content", json.dumps(payload))
    capture_episode(env, source=source, value=value, **overrides)
    terminal(env, reader)
    stop_capture(env)
    before = state(env)
    with pytest.raises(AdminError, match="^source_purge_scope_mixed$") as failure:
        execute(env)
    assert not failure.value.outcome_unknown and PRIVATE not in str(failure.value)
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("other", ["subject", "dataset", "system"])
def test_all_bindings_in_scope_must_be_homogeneous_including_other_datasets(env, other):
    ready(env)
    identity = DATASET
    subject = "synthetic-reader"
    if other == "subject":
        subject = "another-reader"
    else:
        identity = SourceDatasetIdentity.model_validate(DATASET.model_dump() | {
            "dataset_id" if other == "dataset" else "source_system": "other",
        })
    reader = bind_reader(
        env, scope_id=env.scopes[0], dataset=identity, subject=subject,
    )
    terminal(env, reader)
    before = state(env)
    with pytest.raises(AdminError, match="^source_purge_scope_mixed$"):
        execute(env)
    assert state(env) == before


@pytest.mark.integration
def test_all_matching_readers_must_be_deleted_not_only_first(env):
    ready(env)
    bind_reader(env, scope_id=env.scopes[0], subject="synthetic-reader")
    before = state(env)
    with pytest.raises(AdminError, match="^source_purge_source_not_deleted$"):
        execute(env)
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("changed", ["access", "deletion", "binding", "notice", "content"])
def test_stale_plan_checks_every_precondition_before_any_native_write(env, monkeypatch, changed):
    reader, _ = ready(env)
    plan = execute(env).plan
    if changed == "binding":
        reader = bind_reader(env, scope_id=env.scopes[0], subject="synthetic-reader")
        terminal(env, reader)
        assert epoch(env) == plan.access_epoch
    elif changed == "notice":
        terminal(env, reader, sequence=2, reason="revoked")
        assert epoch(env) == plan.access_epoch
    elif changed == "content":
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(
                "UPDATE memory.episode SET content=content || ' ' WHERE tenant_id=%s",
                (env.tenants[0],),
            )
        assert epoch(env) == plan.access_epoch
    else:
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(
                psycopg.sql.SQL("UPDATE memory.tenant SET {}={}+1 WHERE id=%s").format(
                    psycopg.sql.Identifier(changed + "_epoch"),
                    psycopg.sql.Identifier(changed + "_epoch"),
                ), (env.tenants[0],),
            )
    before = state(env)

    async def forbidden(*args, **kwargs):
        pytest.fail("Stale plan reached Native forget")

    monkeypatch.setattr(MemoryService, "forget", forbidden)
    code = changed + "_epoch_conflict" if changed in ("access", "deletion") else (
        "source_purge_plan_conflict"
    )
    with pytest.raises(AdminError, match=f"^{code}$"):
        apply(env, plan)
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("roots", [0, 1])
@pytest.mark.parametrize("permissions", [[], ["read"], ["delete"]])
def test_privileged_admin_does_not_bypass_maintenance_scope_read_and_delete(
    env, roots, permissions,
):
    ready(env, roots=roots)
    plan = execute(env).plan
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET permissions=%s
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (permissions, env.tenants[0], env.scopes[0], env.principals[0]),
        )
    before = state(env)
    for operation in ("plan", "apply"):
        with pytest.raises(AdminError, match="^not_found$"):
            execute(env, operation, **({"expected_plan": plan} if operation == "apply" else {}))
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("identity", ["runtime-role", "missing", "other-tenant", "expired"])
def test_role_and_identity_guards_apply_before_purge_even_when_empty(env, identity):
    ready(env, roots=0)
    plan = execute(env).plan
    changes = {}
    url = env.admin_url
    if identity == "runtime-role":
        url = env.settings.database_url
    elif identity == "missing":
        changes["maintenance_principal_id"] = uuid4()
    elif identity == "other-tenant":
        changes["maintenance_principal_id"] = env.principals[1]
    else:
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(
                """UPDATE memory.scope_member SET expires_at='2000-01-01'
                   WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                (env.tenants[0], env.scopes[0], env.principals[0]),
            )
    before = state(env)
    code = "admin_role_required" if identity == "runtime-role" else "not_found"
    with pytest.raises(AdminError, match=f"^{code}$"):
        execute(env, url=url, **changes)
    if identity == "runtime-role":
        with pytest.raises(AdminError, match="^admin_role_required$"):
            execute(env, "apply", url=url, expected_plan=plan)
    assert state(env) == before


@pytest.mark.integration
def test_expired_maintenance_lease_rejects_apply_without_epoch_change(env):
    ready(env)
    expiry = None
    with scope_access(env.admin_url, ScopeAccessRequest(
        operation="get", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=env.principals[0],
    )) as current:
        expiry = current.evaluated_at + timedelta(seconds=2)
    with scope_access(env.admin_url, ScopeAccessRequest(
        operation="set", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=env.principals[0], expected_access_epoch=epoch(env),
        permissions=("read", "delete"), expires_at=expiry,
    )):
        pass
    plan = execute(env).plan
    deadline = time.monotonic() + 10
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        while conn.execute("SELECT clock_timestamp()").fetchone()[0] < expiry:
            assert time.monotonic() < deadline
            time.sleep(0.02)
    before = state(env)
    with pytest.raises(AdminError, match="^not_found$"):
        apply(env, plan)
    assert state(env) == before and before["epochs"][0] == plan.access_epoch


@pytest.mark.integration
def test_stop_capture_remains_disabled_for_exact_retry_and_new_events(env):
    _, captured = ready(env)
    plan = execute(env).plan
    before = state(env)
    body, headers = captured[0][1:]
    for key in (headers, env.headers()):
        response = env.client.post("/v1/observe", json=body, headers=key)
        assert response.status_code == 403 and response.json()["code"] == "capture_policy_denied"
    assert state(env) == before
    apply(env, plan)
    after = state(env)
    for key in (headers, env.headers()):
        response = env.client.post("/v1/observe", json=body, headers=key)
        assert response.status_code == 403 and response.json()["code"] == "capture_policy_denied"
    response = env.client.post(
        "/v1/observe", json=body | {"source_event_id": str(uuid4())}, headers=env.headers(),
    )
    assert response.status_code == 403 and response.json()["code"] == "capture_policy_denied"
    assert state(env) == after


@pytest.mark.integration
def test_output_delivery_holds_barrier_after_commit_and_releases_on_consumer_error(env):
    ready(env)
    plan = execute(env).plan

    async def run():
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(RuntimeError, match="consumer lost output"):
                async with source_purge(
                    env.admin_url, request(env, "apply", expected_plan=plan),
                ) as result:
                    assert result.changed
                    with psycopg.connect(env.admin_url, autocommit=True) as conn:
                        assert conn.execute(
                            "SELECT deletion_epoch FROM memory.tenant WHERE id=%s",
                            (env.tenants[0],),
                        ).fetchone()[0] == plan.deletion_epoch + 1
                        assert not conn.execute(
                            "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                            (str(env.tenants[0]),),
                        ).fetchone()[0]
                        assert conn.execute(
                            "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                            (str(env.tenants[1]),),
                        ).fetchone()[0]
                    reading = pool.submit(env.recall)
                    deadline = time.monotonic() + 3
                    while True:
                        with psycopg.connect(env.admin_url) as conn:
                            waiting = conn.execute(
                                """SELECT count(*) FROM pg_stat_activity
                                   WHERE datname=current_database() AND wait_event='advisory'"""
                            ).fetchone()[0]
                        if waiting:
                            break
                        assert time.monotonic() < deadline, "Native read did not wait for output"
                        await asyncio.sleep(0.02)
                    assert not reading.done()
                    raise RuntimeError("consumer lost output")
            response = reading.result(timeout=5)
            assert response.status_code == 200 and response.json()["items"] == []

    asyncio.run(run())
    assert apply(env, plan).replayed


@pytest.mark.integration
def test_lost_commit_response_uses_native_idempotent_receipt_without_new_writes(env, monkeypatch):
    ready(env)
    plan = execute(env).plan
    original = psycopg.AsyncConnection.transaction
    commits = []

    @asynccontextmanager
    async def lost_response(conn, *args, **kwargs):
        async with original(conn, *args, **kwargs) as transaction:
            yield transaction
        commits.append(True)
        raise psycopg.OperationalError(PRIVATE + " after real commit")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.AsyncConnection, "transaction", lost_response)
        with pytest.raises(AdminError, match="^commit_outcome_unknown$") as failure:
            apply(env, plan)
    assert failure.value.outcome_unknown and PRIVATE not in str(failure.value)
    assert len(commits) == 1
    after = state(env)
    assert after["epochs"] == (plan.access_epoch, plan.deletion_epoch + 1)
    assert after["memory.episode"] == ()
    assert len(after["memory_ops.deletion_request"]) == 1
    replay = apply(env, plan)
    assert replay.replayed and not replay.changed
    assert replay.receipt.deletion_epoch == plan.deletion_epoch + 1
    assert replay.plan.roots == plan.roots
    assert_safe_result(replay, replayed=True)
    assert state(env) == after


@pytest.mark.integration
def test_receipt_replay_still_requires_authorized_maintenance_principal(env):
    ready(env)
    plan = execute(env).plan
    assert apply(env, plan).changed
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """DELETE FROM memory.scope_member
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (env.tenants[0], env.scopes[0], env.principals[0]),
        )
    before = state(env)
    with pytest.raises(AdminError, match="^not_found$"):
        apply(env, plan)
    assert state(env) == before


def seed_deleted_bindings(env, count, *, scopes=1):
    scope_ids = [env.scopes[0], *(uuid4() for _ in range(scopes - 1))]
    principals = [uuid4() for _ in range(count)]
    notice = SourceNotice(
        source={**DATASET.model_dump(), "source_subject": "synthetic-reader"},
        sequence=1, decision="deny", reason="deleted",
    )
    digest = canonical_digest(notice.model_dump(mode="json"))
    with psycopg.connect(env.admin_url) as conn, conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)",
            [(env.tenants[0], scope) for scope in scope_ids[1:]],
        )
        cursor.executemany(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write','delete'])""",
            [(env.tenants[0], scope, env.principals[0]) for scope in scope_ids[1:]],
        )
        cursor.executemany(
            "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
            [(env.tenants[0], principal, str(uuid4())) for principal in principals],
        )
        keys = [
            (env.tenants[0], scope_ids[index % scopes], principal)
            for index, principal in enumerate(principals)
        ]
        cursor.executemany(
            """INSERT INTO memory_ops.source_access_state
               (tenant_id,scope_id,principal_id,source_system,dataset_id,source_subject,
                sequence,decision,reason)
               VALUES (%s,%s,%s,%s,%s,'synthetic-reader',0,'deny','bound')""",
            [(*key, DATASET.source_system, DATASET.dataset_id) for key in keys],
        )
        cursor.executemany(
            """INSERT INTO memory_ops.source_access_event
               (tenant_id,scope_id,principal_id,sequence,decision,reason,access_epoch)
               VALUES (%s,%s,%s,0,'deny','bound',1)""",
            keys,
        )
        cursor.executemany(
            """UPDATE memory_ops.source_access_state
               SET sequence=1,decision='deny',reason='deleted',notice_digest=%s
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            [(digest, *key) for key in keys],
        )
        cursor.executemany(
            """INSERT INTO memory_ops.source_access_event
               (tenant_id,scope_id,principal_id,sequence,notice_digest,
                decision,reason,access_epoch)
               VALUES (%s,%s,%s,1,%s,'deny','deleted',1)""",
            [(*key, digest) for key in keys],
        )
    for scope in scope_ids:
        stop_capture(env, scope)
    return scope_ids


@pytest.mark.integration
@pytest.mark.parametrize("count", [100, 101, 107])
def test_binding_limit_is_measured_limit_plus_one_without_partial_work(env, monkeypatch, count):
    seed_deleted_bindings(env, count)
    before = state(env)
    original = psycopg.AsyncConnection.execute
    selected = []

    async def measured(conn, query, *args, **kwargs):
        cursor = await original(conn, query, *args, **kwargs)
        columns = {column.name for column in cursor.description or ()}
        if {"scope_id", "principal_id", "source_subject", "sequence", "decision"} <= columns:
            assert "LIMIT" in str(query).upper()
            assert args[0][-1] == 101
            selected.append(cursor.rowcount)
        return cursor

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.AsyncConnection, "execute", measured)
        if count == 100:
            result = execute(env)
            assert len(result.plan.bindings) == 100 and not result.plan.roots
            applied = apply(env, result.plan)
            assert not applied.changed and applied.receipt is None
            assert selected == [100, 100]
        else:
            with pytest.raises(AdminError, match="^source_dataset_target_limit$"):
                execute(env)
            assert selected == [101]
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("count", [32, 33])
def test_scope_limit_uses_unique_scopes_and_checks_all_empty_scope_permissions(env, count):
    scope_ids = seed_deleted_bindings(env, count, scopes=count)
    before = state(env)
    if count == 33:
        with pytest.raises(AdminError, match="^source_purge_scope_limit$"):
            execute(env)
        assert state(env) == before
        return
    result = execute(env)
    assert {row.scope_id for row in result.plan.bindings} == set(scope_ids)
    assert result.plan.roots == ()
    assert apply(env, result.plan).receipt is None
    assert state(env) == before
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """DELETE FROM memory.scope_member
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (env.tenants[0], sorted(scope_ids)[-1], env.principals[0]),
        )
    before = state(env)
    with pytest.raises(AdminError, match="^not_found$"):
        apply(env, result.plan)
    assert state(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("count", [100, 101, 107])
def test_root_limit_measures_all_rows_and_one_hundred_roots_really_purge(env, monkeypatch, count):
    _, captured = ready(env, roots=count)
    before = state(env)
    original = psycopg.AsyncConnection.execute
    selected = []

    async def measured(conn, query, *args, **kwargs):
        cursor = await original(conn, query, *args, **kwargs)
        columns = {column.name for column in cursor.description or ()}
        if {"id", "scope_id", "source_namespace", "occurred_at", "content"} <= columns:
            assert "LIMIT" in str(query).upper()
            assert args[0][-1] == 101
            selected.append(cursor.rowcount)
        return cursor

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.AsyncConnection, "execute", measured)
        if count == 100:
            plan = execute(env).plan
            assert {root.memory_id for root in plan.roots} == {
                memory_id for memory_id, _, _ in captured
            }
            result = apply(env, plan)
            assert result.changed and result.receipt.object_count == 100
            assert selected == [100, 100]
        else:
            with pytest.raises(AdminError, match="^source_purge_root_limit$"):
                execute(env)
            assert selected == [101]
    after = state(env)
    if count == 100:
        assert after["memory.episode"] == ()
        assert len(after["memory_ops.object_tombstone"]) == 100
        assert len(after["memory_ops.deletion_request"]) == 1
        assert after["epochs"] == (before["epochs"][0], before["epochs"][1] + 1)
        for table in AUTHORITY_TABLES:
            assert after[table] == before[table]
    else:
        assert after == before


@pytest.mark.integration
def test_failure_after_native_delete_rolls_back_payload_receipt_audit_and_epoch(env, monkeypatch):
    ready(env)
    plan = execute(env).plan
    before = state(env)
    original = MemoryService.forget
    calls = []

    async def failed(memory, data, key):
        result = await original(memory, data, key)
        calls.append(result)
        assert result["object_count"] == 1
        assert not await (await memory.conn.execute(
            "SELECT id FROM memory.episode WHERE tenant_id=%s", (env.tenants[0],),
        )).fetchall()
        raise psycopg.IntegrityError(PRIVATE + " after Native writes before commit")

    with monkeypatch.context() as patch:
        patch.setattr(MemoryService, "forget", failed)
        with pytest.raises(AdminError, match="^admin_database_error$") as failure:
            apply(env, plan)
    assert not failure.value.outcome_unknown and PRIVATE not in str(failure.value)
    assert len(calls) == 1 and state(env) == before
    assert apply(env, plan).changed


@pytest.mark.integration
def test_historical_receipt_replay_does_not_claim_or_delete_new_current_inventory(env):
    ready(env)
    plan = execute(env).plan
    first = apply(env, plan)
    with capture_policy(env.admin_url, CapturePolicyRequest(
        operation="set", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        expected_access_epoch=epoch(env), policy=CapturePolicy.legacy(),
    )):
        pass
    memory_id, _, _ = capture_episode(env)
    stop_capture(env)
    before = state(env)
    replay = apply(env, plan)
    assert replay.replayed and not replay.changed and replay.receipt == first.receipt
    assert replay.plan == plan and memory_id not in {root.memory_id for root in replay.plan.roots}
    assert state(env) == before
    current = execute(env)
    assert [root.memory_id for root in current.plan.roots] == [memory_id]
    assert current.plan.plan_digest != plan.plan_digest


@pytest.mark.integration
def test_multiscope_purge_allows_distinct_subjects_but_never_touches_other_tenants(env):
    _, first = ready(env)
    other = env.observe("unrelated tenant must survive", index=1)
    assert other.status_code == 201
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write','delete'])""",
            (env.tenants[0], env.scopes[2], env.principals[0]),
        )
    source = binding(env.scopes[2], source_subject="second-source-reader")
    reader = bind_reader(env, scope_id=source.scope_id, subject=source.source_subject)
    second, _, _ = capture_episode(env, source=source)
    terminal(env, reader)
    stop_capture(env, source.scope_id)
    before = state(env)
    plan = execute(env).plan
    assert {root.memory_id for root in plan.roots} == {first[0][0], second}
    result = apply(env, plan)
    assert result.receipt.object_count == 2
    assert set(result.receipt.scope_ids) == {env.scopes[0], env.scopes[2]}
    after = state(env)
    for table in AUTHORITY_TABLES:
        assert after[table] == before[table]
    response = env.client.post(
        "/v1/explain", json={"memory_id": other.json()["memory_id"]}, headers=env.headers(1),
    )
    assert response.status_code == 200
    assert response.json()["source"]["content"] == "unrelated tenant must survive"


@pytest.mark.parametrize("arguments", [
    ["apply"],
    ["plan", "--tenant-id", PRIVATE],
    ["unknown-" + PRIVATE],
    [*cli_arguments(), "--source-subject", PRIVATE],
    [*cli_arguments("apply")],
])
def test_cli_argument_errors_never_echo_sensitive_input(arguments):
    result = cli(*arguments)
    assert result.returncode == 2 and result.stdout == ""
    assert "invalid_source_purge_arguments" in result.stderr
    assert PRIVATE not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize("invalid", [
    "missing", "directory", "symlink", "fifo", "malformed", "duplicate", "constant",
    "too-large", "invalid-utf8", "deep", "wrapper",
])
def test_cli_plan_file_is_strict_bounded_regular_json(plan_file, invalid):
    payload = plan_payload()
    raw = json.dumps(payload)
    if invalid == "directory":
        plan_file.mkdir()
    elif invalid == "symlink":
        target = plan_file.with_suffix(".target.json")
        target.write_text(raw, encoding="utf-8")
        plan_file.symlink_to(target.absolute())
    elif invalid == "fifo":
        os.mkfifo(plan_file)
    elif invalid == "malformed":
        plan_file.write_text("{" + PRIVATE, encoding="utf-8")
    elif invalid == "duplicate":
        plan_file.write_text(
            '{"format":"untrusted",' + raw[1:],
            encoding="utf-8",
        )
    elif invalid == "constant":
        plan_file.write_text('{"access_epoch":NaN}', encoding="utf-8")
    elif invalid == "too-large":
        plan_file.write_bytes(
            raw.encode() + b" " * (MAX_SOURCE_PURGE_PLAN_BYTES + 1 - len(raw.encode()))
        )
    elif invalid == "invalid-utf8":
        plan_file.write_bytes(b'{"private":"\xff"}')
    elif invalid == "deep":
        plan_file.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    elif invalid == "wrapper":
        plan_file.write_text(json.dumps({"plan": payload}), encoding="utf-8")
    result = cli(
        *cli_arguments(
            "apply", tenant_id=payload["tenant_id"],
            maintenance_principal_id=payload["maintenance_principal_id"],
        ),
        "--plan-file", str(plan_file), url=UNAVAILABLE_URL,
    )
    assert result.returncode == 2 and result.stdout == ""
    assert "invalid_source_purge_arguments" in result.stderr
    assert PRIVATE not in result.stderr and "Traceback" not in result.stderr


def test_cli_help_describes_admin_plan_apply_without_enabling_capture():
    result = cli("--help")
    assert result.returncode == 0 and result.stderr == ""
    output = result.stdout.lower()
    for word in ("plan", "apply", "maintenance-principal-id", "plan-file", "admin", "capture"):
        assert word in output


@pytest.mark.parametrize("outcome_unknown", [False, True])
def test_cli_preserves_redacted_admin_error_and_never_retries(monkeypatch, capsys, outcome_unknown):
    calls = []

    @asynccontextmanager
    async def unavailable(url, value):
        calls.append((url, value))
        raise AdminError("admin_database_unavailable", outcome_unknown=outcome_unknown)
        yield

    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", UNAVAILABLE_URL)
    monkeypatch.setattr(coordinator, "source_purge", unavailable)
    with pytest.raises(SystemExit) as failure:
        coordinator.main(cli_arguments())
    assert failure.value.code == 1 and len(calls) == 1
    output = capsys.readouterr()
    assert output.err == "" and PRIVATE not in output.out
    assert json.loads(output.out) == {
        "error": {"code": "admin_database_unavailable", "outcome_unknown": outcome_unknown},
    }


@pytest.mark.integration
def test_actual_cli_plan_file_apply_and_replay_emit_only_redacted_receipts(env, plan_file):
    ready(env)
    arguments = {
        "tenant_id": env.tenants[0], "maintenance_principal_id": env.principals[0],
    }
    planned = cli(*cli_arguments(**arguments), url=env.admin_url)
    assert planned.returncode == 0 and planned.stderr == ""
    payload = json.loads(planned.stdout)
    plan = SourcePurgePlan.model_validate(payload["plan"])
    assert not payload["changed"] and payload["receipt"] is None
    assert "PRIVATE_SOURCE_RESULT" not in planned.stdout
    plan_file.write_text(plan.model_dump_json(), encoding="utf-8")
    applied = cli(
        *cli_arguments("apply", **arguments), "--plan-file", str(plan_file), url=env.admin_url,
    )
    assert applied.returncode == 0 and applied.stderr == ""
    result = json.loads(applied.stdout)
    assert result["changed"] and not result["replayed"]
    assert result["receipt"]["object_count"] == 1
    before = state(env)
    replayed = cli(
        *cli_arguments("apply", **arguments), "--plan-file", str(plan_file), url=env.admin_url,
    )
    assert replayed.returncode == 0 and replayed.stderr == ""
    replay = json.loads(replayed.stdout)
    assert replay["replayed"] and not replay["changed"] and replay["receipt"] == result["receipt"]
    assert "PRIVATE_SOURCE_RESULT" not in applied.stdout + replayed.stdout
    assert state(env) == before
