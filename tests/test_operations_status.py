"""ADMIN metadata snapshots are observations, never maintenance or authority."""

import asyncio
import builtins
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import ValidationError
from test_deletion_history import forget, receipt, target, tombstone
from test_graph_generation import begin, record
from test_processing import configure, enqueue, install_extraction, run
from test_processing import profile as profile

from pg_agmemory import __version__
from pg_agmemory import admin as administration
from pg_agmemory import operations_status as status
from pg_agmemory.admin import AdminError
from pg_agmemory.database import SCHEMA_VERSION, VECTOR_QUERY
from pg_agmemory.jobs import job_transaction
from pg_agmemory.processing import Processing
from pg_agmemory.recovery_apply import export_bundle, secret_for, signature
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.source_access import (
    SourceAccessRequest,
    SourceIdentity,
    SourceNotice,
    source_access,
)

WARNING_ORDER = (
    "standby_snapshot",
    "job_lease_expired",
    "job_epoch_drift",
    "billing_unknown",
    "source_lease_expired",
    "source_grant_mismatch",
    "legacy_deletion_manifest",
    "graph_registry_stale",
)
TEXT = "Alice / preferred_editor: Vim — PRIVATE_STATUS_CONTENT"


def request(env, index=0):
    return status.OperationsStatusRequest(tenant_id=env.tenants[index])


def inspect(env, index=0):
    return status.operations_status(env.admin_url, request(env, index))


def database_now(env):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute("SELECT clock_timestamp()").fetchone()[0]


def canonical_state(env):
    with psycopg.connect(env.admin_url) as conn:
        tables = conn.execute(
            """SELECT n.nspname,c.relname
               FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='tenant_id'
               WHERE n.nspname IN ('memory','memory_ops') AND c.relkind IN ('r','p')
                 AND NOT a.attisdropped ORDER BY n.nspname,c.relname"""
        ).fetchall()
        result = {
            "tenant": conn.execute(
                "SELECT to_jsonb(t) FROM memory.tenant t WHERE id=%s", (env.tenants[0],),
            ).fetchall()
        }
        for schema, table in tables:
            result[f"{schema}.{table}"] = conn.execute(
                sql.SQL(
                    "SELECT to_jsonb(t) FROM {} t WHERE tenant_id=%s ORDER BY to_jsonb(t)::text"
                ).format(sql.Identifier(schema, table)),
                (env.tenants[0],),
            ).fetchall()
        return result


def add_job(env, kind="extract"):
    observed = env.observe(TEXT)
    assert observed.status_code == 201, observed.text
    source = observed.json()["memory_id"]
    queued = enqueue(env, source, kind=kind)
    assert queued.status_code == 202, queued.text
    return source, queued.json()["job_id"]


def reserve(env, profile):
    async def execute():
        async with job_transaction(env.settings.database_url, env.subjects[0]) as jobs:
            claim = await jobs.claim(lease_seconds=120, profile_digest=profile.digest)
            assert claim is not None
            await Processing(jobs.memory).prepare(
                claim["job_id"], claim["lease_token"], profile, reserve=True,
            )
            return claim
    return asyncio.run(execute())


def source_binding(env, *, decision="allow", reason="authorized", lifetime=120):
    principal = uuid4()
    identity = SourceIdentity(
        source_system="PRIVATE_STATUS_SOURCE", dataset_id="PRIVATE_STATUS_DATASET",
        source_subject="PRIVATE_STATUS_SUBJECT_" + principal.hex,
    )
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
            (env.tenants[0], principal, "PRIVATE_STATUS_PRINCIPAL_" + principal.hex),
        )
        epoch = conn.execute(
            "SELECT access_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],),
        ).fetchone()[0]
    values = {
        "tenant_id": env.tenants[0], "scope_id": env.scopes[0], "principal_id": principal,
    }
    with source_access(env.admin_url, SourceAccessRequest(
        operation="bind", source=identity, expected_access_epoch=epoch, **values,
    )) as bound:
        epoch = bound.access_epoch
    if reason == "bound":
        return bound
    lease = {}
    if decision == "allow":
        now = database_now(env)
        lease = {
            "acl_version": "PRIVATE_STATUS_ACL", "verified_at": now - timedelta(seconds=1),
            "valid_until": now + timedelta(seconds=lifetime),
        }
    with source_access(env.admin_url, SourceAccessRequest(
        operation="apply", expected_access_epoch=epoch, **values,
        notice=SourceNotice(
            source=identity, sequence=1, decision=decision, reason=reason, **lease,
        ),
    )) as applied:
        assert applied.decision == decision
        return applied


def register_graph(env, *, enabled=True):
    current = record(env, begin(env))
    generation = current.head
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory_ops.age_projection
               (tenant_id,generation_id,graph_name,artifact_digest,profile_digest,input_digest,
                captured_access_epoch,captured_deletion_epoch,age_commit,node_count,
                edge_revision_count,enabled)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                       '72707aab7ce982bf13cad3d102bd869dab07d64b',0,0,%s)""",
            (env.tenants[0], generation.id, "pgag_age_" + generation.id.hex,
             generation.artifact_digest, generation.profile_digest, generation.input_digest,
             generation.input_snapshot.access_epoch, generation.input_snapshot.deletion_epoch,
             enabled),
        )
    return generation


def cli(env, *, url=None):
    return subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "operations-status",
         "--tenant-id", str(env.tenants[0])],
        env={"PATH": os.environ["PATH"], "PGAG_ADMIN_DATABASE_URL": url or env.admin_url},
        capture_output=True, text=True, timeout=15,
    )


def assert_private(report, env, *secrets):
    serialized = report.model_dump_json()
    for value in (
        env.admin_url, TEXT, *env.principals, *env.scopes, *env.subjects, env.tenants[1],
        "PRIVATE_STATUS_SOURCE", "PRIVATE_STATUS_DATASET", "PRIVATE_STATUS_SUBJECT",
        "PRIVATE_STATUS_ACL", "PRIVATE_STATUS_PRINCIPAL", *secrets,
    ):
        assert str(value) not in serialized
    identifiers = re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", serialized,
    )
    assert identifiers == [str(env.tenants[0])]


@pytest.mark.parametrize(
    "value", [None, True, 1, b"PRIVATE_STATUS_INPUT", "not-a-uuid", str(uuid4())],
)
def test_request_accepts_only_native_uuid_in_python(value):
    with pytest.raises(ValidationError):
        status.OperationsStatusRequest(tenant_id=value)


def test_request_is_frozen_extra_forbidden_and_always_revalidated():
    value = status.OperationsStatusRequest(tenant_id=uuid4())
    assert value.model_config["frozen"] is True
    assert value.model_config["extra"] == "forbid"
    assert value.model_config["revalidate_instances"] == "always"
    assert status.OperationsStatusRequest.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        value.tenant_id = uuid4()
    with pytest.raises(ValidationError):
        status.OperationsStatusRequest(tenant_id=uuid4(), scope_id=uuid4())


@pytest.mark.parametrize("bad", [
    None,
    object(),
    {"tenant_id": "PRIVATE_STATUS_INPUT"},
    {"tenant_id": uuid4(), "principal_id": uuid4()},
    status.OperationsStatusRequest.model_construct(),
    status.OperationsStatusRequest.model_construct(tenant_id="PRIVATE_STATUS_INPUT"),
    status.OperationsStatusRequest(tenant_id=uuid4()).model_copy(update={"tenant_id": False}),
])
def test_forged_request_is_revalidated_before_connecting(monkeypatch, bad):
    def forbidden(*args, **kwargs):
        pytest.fail("An invalid request reached the database")

    monkeypatch.setattr(status, "read_admin_snapshot", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    with pytest.raises(AdminError, match="^invalid_operations_status_request$") as error:
        status.operations_status("PRIVATE_STATUS_DSN", bad)
    assert error.value.outcome_unknown is False
    assert "PRIVATE_STATUS" not in str(error.value)


def test_empty_snapshot_exact_contract_is_a_noop_not_authority(env):
    before = canonical_state(env)
    lower = database_now(env)
    result = inspect(env)
    upper = database_now(env)
    assert isinstance(result, status.OperationsStatus)
    assert not hasattr(result, "__enter__")
    assert result.evaluated_at.utcoffset() == timedelta(0)
    assert lower <= result.evaluated_at <= upper
    assert result.model_dump(exclude={"evaluated_at"}) == {
        "format": "pgag-operations-status-v1",
        "service_version": __version__, "api_version": "v1", "schema_version": 21,
        "tenant_id": env.tenants[0], "access_epoch": 1, "deletion_epoch": 1,
        "in_recovery": False, "primary_snapshot": True,
        "restore_authorized": False, "source_authorization_verified": False,
        "production_qualified": False,
        "jobs": {
            "total": 0, "pending": 0, "running": 0, "succeeded": 0, "failed": 0,
            "cancelled": 0, "due_pending": 0, "expired_running": 0, "stale_active": 0,
            "oldest_due_age_seconds": None,
        },
        "calls": {
            "total": 0, "unknown": 0, "succeeded": 0, "failed": 0, "billing_unknown": 0,
            "reserved_input_bytes": 0, "max_output_tokens_reserved": 0,
        },
        "source": {
            "total": 0, "allowed": 0, "denied": 0, "deleted": 0, "expired_allow": 0,
            "active_read_leases": 0, "unexpected_grants": 0,
        },
        "deletions": {
            "total": 0, "active_store_purged": 0, "blocked_for_reads": 0,
            "legacy_manifests": 0, "target_rows": 0, "backup_retention_verified": False,
        },
        "graph": {
            "registered": False, "enabled": False, "epoch_schema_match": None,
            "serving_verified": False,
        },
        "warnings": (),
    }
    assert result.schema_version == SCHEMA_VERSION
    assert status.OperationsStatus.model_validate_json(result.model_dump_json()) == result
    assert inspect(env).model_dump(exclude={"evaluated_at"}) == result.model_dump(
        exclude={"evaluated_at"},
    )
    assert canonical_state(env) == before
    assert_private(result, env)


def test_real_job_states_exact_counts_and_reservations_not_billed_usage(
    env, profile, monkeypatch,
):
    from pg_agmemory.providers import ProviderFailure

    expiring = source_binding(env, lifetime=5)
    denied = source_binding(env, decision="deny", reason="bound")
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read'])""",
            (env.tenants[0], env.scopes[0], denied.principal_id),
        )
    generation = register_graph(env)
    configure(env, profile)
    install_extraction(monkeypatch, profile)
    add_job(env)
    assert run(env, profile)["outcome"] == "succeeded"

    async def fail(data):
        raise ProviderFailure("provider_unavailable", retryable=False, unknown=False)

    monkeypatch.setattr(profile.provider, "extract", fail)
    add_job(env)
    assert run(env, profile)["outcome"] == "failed"
    _, cancelled = add_job(env)
    response = env.client.post(
        f"/v1/jobs/{cancelled}/cancel",
        json={"expected_state": "pending", "expected_attempt": 0}, headers=env.headers(),
    )
    assert response.status_code == 200, response.text
    _, expired = add_job(env)
    assert str(reserve(env, profile)["job_id"]) == expired
    _, running = add_job(env)
    assert str(reserve(env, profile)["job_id"]) == running
    _, stale = add_job(env)
    _, due = add_job(env)
    _, future = add_job(env)
    now = database_now(env)
    oldest = now - timedelta(minutes=5)
    # Reconstruct old captured epochs and deadlines through the existing recovery guard.
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SET LOCAL pgag.recovery_apply='on'")
        conn.execute(
            """UPDATE memory_ops.job SET lease_until=%s,captured_access_epoch=1
               WHERE tenant_id=%s AND id=%s""",
            (now - timedelta(seconds=1), env.tenants[0], expired),
        )
        conn.execute(
            """UPDATE memory_ops.job SET available_at=%s,captured_deletion_epoch=2
               WHERE tenant_id=%s AND id=%s""", (oldest, env.tenants[0], stale),
        )
        conn.execute(
            "UPDATE memory_ops.job SET available_at=%s WHERE tenant_id=%s AND id=%s",
            (now - timedelta(seconds=20), env.tenants[0], due),
        )
        conn.execute(
            "UPDATE memory_ops.job SET available_at=%s WHERE tenant_id=%s AND id=%s",
            (now + timedelta(days=1), env.tenants[0], future),
        )
        conn.execute(
            """UPDATE memory_ops.job SET captured_access_epoch=1
               WHERE tenant_id=%s AND state IN ('succeeded','failed','cancelled')""",
            (env.tenants[0],),
        )
        conn.execute(
            "SELECT pg_sleep(greatest(0,extract(epoch FROM (%s-clock_timestamp())))+0.02)",
            (expiring.expires_at,),
        )
    before = canonical_state(env)
    result = inspect(env)
    assert result.jobs.model_dump(exclude={"oldest_due_age_seconds"}) == {
        "total": 8, "pending": 3, "running": 2, "succeeded": 1, "failed": 1, "cancelled": 1,
        "due_pending": 2, "expired_running": 1, "stale_active": 2,
    }
    assert isinstance(result.jobs.oldest_due_age_seconds, float)
    assert result.jobs.oldest_due_age_seconds == pytest.approx(
        (result.evaluated_at - oldest).total_seconds(), abs=0.000001,
    )
    assert result.calls.model_dump() == {
        "total": 4, "unknown": 2, "succeeded": 1, "failed": 1, "billing_unknown": 2,
        "reserved_input_bytes": 4 * len(TEXT.encode()),
        "max_output_tokens_reserved": 4 * profile.settings.max_output_tokens,
    }
    assert result.source.model_dump() == {
        "total": 2, "allowed": 1, "denied": 1, "deleted": 0, "expired_allow": 1,
        "active_read_leases": 0, "unexpected_grants": 1,
    }
    assert result.graph.registered and result.graph.enabled
    assert result.graph.epoch_schema_match is False
    assert result.warnings == (
        "job_lease_expired", "job_epoch_drift", "billing_unknown",
        "source_lease_expired", "source_grant_mismatch", "graph_registry_stale",
    )
    assert canonical_state(env) == before
    assert_private(
        result, env, expired, running, stale, due, future, cancelled, profile.digest,
        expiring.principal_id, denied.principal_id, generation.id,
    )
    other = inspect(env, 1)
    assert other.jobs.total == other.calls.total == other.source.total == 0
    assert not other.graph.registered and other.warnings == ()
    completed = cli(env)
    assert completed.returncode == 0 and completed.stderr == ""
    output = json.loads(completed.stdout)
    assert output["warnings"] == list(result.warnings)
    assert output["jobs"]["total"] == 8 and output["calls"] == result.calls.model_dump()


def test_unknown_reservations_survive_purge_and_null_token_reservations_are_zero(
    env, profile,
):
    configure(env, profile)
    source, job = add_job(env)
    reserve(env, profile)
    deletion = forget(env, source)
    _, embedding = add_job(env, kind="embed")
    assert str(reserve(env, profile)["job_id"]) == embedding
    before = canonical_state(env)
    result = inspect(env)
    assert result.jobs.total == result.jobs.running == 1
    assert result.calls.model_dump() == {
        "total": 2, "unknown": 2, "succeeded": 0, "failed": 0, "billing_unknown": 2,
        "reserved_input_bytes": 2 * len(TEXT.encode()),
        "max_output_tokens_reserved": profile.settings.max_output_tokens,
    }
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM memory_ops.job WHERE tenant_id=%s AND id=%s",
            (env.tenants[0], job),
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT outcome,billing_unknown FROM memory_ops.model_call WHERE job_id=%s",
            (job,),
        ).fetchone() == ("unknown", True)
    assert result.deletions.total == result.deletions.active_store_purged == 1
    assert result.deletions.target_rows >= 2
    assert result.warnings == ("billing_unknown",)
    assert canonical_state(env) == before
    assert_private(result, env, source, job, embedding, deletion["deletion_id"])


@pytest.mark.parametrize("boundary", ["verified_at", "valid_until"])
def test_exact_database_deadlines_use_inclusive_due_expiry_and_half_open_source_lease(
    env, profile, monkeypatch, boundary,
):
    allowed = source_binding(env)
    configure(env, profile)
    _, running = add_job(env)
    reserve(env, profile)
    _, due = add_job(env)
    _, future = add_job(env)
    with psycopg.connect(env.admin_url) as conn:
        at = conn.execute(
            sql.SQL("SELECT {} FROM memory_ops.source_access_state "
                    "WHERE tenant_id=%s AND principal_id=%s").format(sql.Identifier(boundary)),
            (env.tenants[0], allowed.principal_id),
        ).fetchone()[0]
        conn.execute("SET LOCAL pgag.recovery_apply='on'")
        conn.execute(
            "UPDATE memory_ops.job SET lease_until=%s WHERE tenant_id=%s AND id=%s",
            (at, env.tenants[0], running),
        )
        conn.execute(
            "UPDATE memory_ops.job SET available_at=%s WHERE tenant_id=%s AND id=%s",
            (at, env.tenants[0], due),
        )
        conn.execute(
            "UPDATE memory_ops.job SET available_at=%s WHERE tenant_id=%s AND id=%s",
            (at + timedelta(microseconds=1), env.tenants[0], future),
        )
    original = psycopg.Connection.execute

    def execute(conn, query, *args, **kwargs):
        # Pin only this boundary test's clock to a stored database timestamp.
        if isinstance(query, str) and "clock_timestamp()" in query:
            query = query.replace("clock_timestamp()", sql.Literal(at).as_string(conn))
        return original(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", execute)
        result = inspect(env)
    assert result.evaluated_at == at
    assert result.jobs.pending == 2 and result.jobs.due_pending == 1
    assert result.jobs.expired_running == 1 and result.jobs.oldest_due_age_seconds == 0.0
    assert result.source.allowed == 1 and result.source.unexpected_grants == 0
    assert result.source.active_read_leases == int(boundary == "verified_at")
    assert result.source.expired_allow == int(boundary == "valid_until")


@pytest.mark.parametrize("membership,expected_active,expected_unexpected", [
    ("exact", 1, 0),
    ("absent", 0, 0),
    ("expired", 0, 0),
    ("write", 0, 1),
    ("read_write", 0, 1),
    ("admin", 0, 1),
    ("empty", 0, 1),
    ("no_expiry", 0, 1),
    ("different_expiry", 0, 1),
    ("shorter_expiry", 0, 1),
    ("duplicate_read", 0, 1),
])
def test_source_lease_counts_exact_read_grants_not_configured_permissions(
    env, membership, expected_active, expected_unexpected,
):
    allowed = source_binding(env)
    with psycopg.connect(env.admin_url) as conn:
        identity = (env.tenants[0], env.scopes[0], allowed.principal_id)
        if membership == "absent":
            conn.execute(
                """DELETE FROM memory.scope_member
                   WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""", identity,
            )
        elif membership != "exact":
            permissions = {
                "write": ["write"], "read_write": ["read", "write"], "admin": ["admin"],
                "empty": [], "duplicate_read": ["read", "read"],
            }.get(membership, ["read"])
            expiry = {
                "expired": database_now(env) - timedelta(seconds=1),
                "no_expiry": None,
                "different_expiry": allowed.expires_at + timedelta(seconds=30),
                "shorter_expiry": allowed.expires_at - timedelta(seconds=30),
            }.get(membership, allowed.expires_at)
            conn.execute(
                """UPDATE memory.scope_member SET permissions=%s,expires_at=%s
                   WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                (permissions, expiry, *identity),
            )
    before = export_bundle(env.admin_url, env.tenants[0])
    result = inspect(env)
    assert result.source.model_dump() == {
        "total": 1, "allowed": 1, "denied": 0, "deleted": 0, "expired_allow": 0,
        "active_read_leases": expected_active, "unexpected_grants": expected_unexpected,
    }
    assert result.warnings == (("source_grant_mismatch",) if expected_unexpected else ())
    assert export_bundle(env.admin_url, env.tenants[0]) == before
    assert_private(result, env, allowed.principal_id)


def test_future_verified_source_is_not_an_active_lease_even_with_matching_membership(env):
    allowed = source_binding(env)
    now = database_now(env)
    notice = SourceNotice(
        source=allowed.source, sequence=2, decision="allow", reason="authorized",
        acl_version="PRIVATE_STATUS_FUTURE_ACL", verified_at=now + timedelta(minutes=2),
        valid_until=now + timedelta(minutes=4),
    )
    digest = hashlib.sha256(json.dumps(
        notice.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    # A storage-valid restored lease can be ahead of the current database clock.
    with psycopg.connect(env.admin_url) as conn:
        identity = (env.tenants[0], env.scopes[0], allowed.principal_id)
        conn.execute(
            """UPDATE memory_ops.source_access_state
               SET sequence=2,notice_digest=%s,acl_version=%s,verified_at=%s,valid_until=%s
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (digest, notice.acl_version, notice.verified_at, notice.valid_until, *identity),
        )
        conn.execute(
            """INSERT INTO memory_ops.source_access_event
               (tenant_id,scope_id,principal_id,sequence,notice_digest,decision,reason,
                acl_version,verified_at,valid_until,access_epoch)
               SELECT s.tenant_id,s.scope_id,s.principal_id,s.sequence,s.notice_digest,
                      s.decision,s.reason,s.acl_version,s.verified_at,s.valid_until,t.access_epoch
               FROM memory_ops.source_access_state s JOIN memory.tenant t ON t.id=s.tenant_id
               WHERE s.tenant_id=%s AND s.scope_id=%s AND s.principal_id=%s""", identity,
        )
        conn.execute(
            """UPDATE memory.scope_member SET expires_at=%s
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (notice.valid_until, *identity),
        )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    before = export_bundle(env.admin_url, env.tenants[0])
    result = inspect(env)
    assert result.evaluated_at < notice.verified_at
    assert result.source.model_dump() == {
        "total": 1, "allowed": 1, "denied": 0, "deleted": 0, "expired_allow": 0,
        "active_read_leases": 0, "unexpected_grants": 1,
    }
    assert result.warnings == ("source_grant_mismatch",)
    assert export_bundle(env.admin_url, env.tenants[0]) == before
    assert_private(result, env, digest, notice.acl_version, allowed.principal_id)


def test_source_expiration_counts_stored_allow_without_refreshing_signed_ledger(env):
    source_binding(env, decision="deny", reason="bound")
    source_binding(env, decision="deny", reason="deleted")
    source_binding(env, decision="deny", reason="revoked")
    source_binding(env)
    expired = source_binding(env, lifetime=2)
    before = export_bundle(env.admin_url, env.tenants[0])
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        assert signature(before, secret_for(conn, env.tenants[0])) == before.signature
        conn.execute(
            "SELECT pg_sleep(greatest(0,extract(epoch FROM (%s-clock_timestamp())))+0.02)",
            (expired.expires_at,),
        )
        assert conn.execute(
            """SELECT count(*) AS states FROM memory_ops.source_access_state
               WHERE tenant_id=%s""", (env.tenants[0],),
        ).fetchone()["states"] == 5
        assert conn.execute(
            """SELECT count(*) AS events FROM memory_ops.source_access_event
               WHERE tenant_id=%s""", (env.tenants[0],),
        ).fetchone()["events"] == 9
    state = canonical_state(env)
    result = inspect(env)
    assert result.source.model_dump() == {
        "total": 5, "allowed": 2, "denied": 3, "deleted": 1, "expired_allow": 1,
        "active_read_leases": 1, "unexpected_grants": 0,
    }
    assert result.evaluated_at >= expired.expires_at
    assert result.warnings == ("source_lease_expired",)
    assert canonical_state(env) == state
    assert export_bundle(env.admin_url, env.tenants[0]) == before
    assert_private(result, env, before.signature, expired.principal_id)
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE memory_ops.source_access_event SET reason='revoked' WHERE tenant_id=%s",
                (env.tenants[0],),
            )
    assert export_bundle(env.admin_url, env.tenants[0]) == before
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET expires_at=NULL
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (env.tenants[0], env.scopes[0], expired.principal_id),
        )
    drifted = inspect(env)
    assert drifted.source.expired_allow == drifted.source.unexpected_grants == 1
    assert drifted.source.active_read_leases == 1
    assert drifted.warnings == ("source_lease_expired", "source_grant_mismatch")


@pytest.mark.parametrize("reason", ["bound", "revoked", "deleted"])
def test_denied_source_with_effective_membership_is_an_unexpected_grant(env, reason):
    denied = source_binding(env, decision="deny", reason=reason)
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read'])""",
            (env.tenants[0], env.scopes[0], denied.principal_id),
        )
    before = canonical_state(env)
    result = inspect(env)
    assert result.source.denied == result.source.unexpected_grants == 1
    assert result.source.active_read_leases == result.source.allowed == 0
    assert result.source.deleted == int(reason == "deleted")
    assert result.warnings == ("source_grant_mismatch",)
    assert canonical_state(env) == before


def test_deletion_counts_stored_modes_manifests_not_backup_removal(env):
    suppressed = env.observe().json()["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        deletion = receipt(conn, env, mode="suppress")
        target(conn, env, deletion, suppressed)
        tombstone(conn, env, suppressed)
        conn.execute("UPDATE memory.tenant SET deletion_epoch=2 WHERE id=%s", (env.tenants[0],))
    purged = env.observe().json()["memory_id"]
    forget(env, purged)
    before = canonical_state(env)
    result = inspect(env)
    assert result.deletions.model_dump() == {
        "total": 2, "active_store_purged": 1, "blocked_for_reads": 1,
        "legacy_manifests": 0, "target_rows": 2, "backup_retention_verified": False,
    }
    assert result.deletion_epoch == 3 and result.warnings == ()
    assert canonical_state(env) == before
    assert_private(result, env, suppressed, purged, deletion)
    assert inspect(env, 1).deletions.total == 0


def test_migrated_legacy_manifest_warns_without_guessing_missing_targets(database):
    url, _, legacy = database
    for tenant in (row["tenant"] for row in legacy):
        result = status.operations_status(url, status.OperationsStatusRequest(tenant_id=tenant))
        with psycopg.connect(url) as conn:
            legacy_count = conn.execute(
                """SELECT count(*) FROM memory_ops.deletion_request
                   WHERE tenant_id=%s AND target_manifest_version=0""", (tenant,),
            ).fetchone()[0]
            targets = conn.execute(
                "SELECT count(*) FROM memory_ops.deletion_target WHERE tenant_id=%s", (tenant,),
            ).fetchone()[0]
        assert result.deletions.legacy_manifests == legacy_count > 0
        assert result.deletions.target_rows == targets == 0
        assert "legacy_deletion_manifest" in result.warnings
        assert result.warnings == tuple(code for code in WARNING_ORDER if code in result.warnings)
        assert not result.deletions.backup_retention_verified


@pytest.mark.parametrize("drift", [None, "access_epoch", "deletion_epoch"])
def test_graph_registry_match_is_only_schema_and_epochs_without_age_serving(env, drift):
    generation = register_graph(env)
    if drift:
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(
                sql.SQL("UPDATE memory.tenant SET {}={}+1 WHERE id=%s").format(
                    sql.Identifier(drift), sql.Identifier(drift),
                ), (env.tenants[0],),
            )
    before = canonical_state(env)
    result = inspect(env)
    assert result.graph.model_dump() == {
        "registered": True, "enabled": True, "epoch_schema_match": drift is None,
        "serving_verified": False,
    }
    assert result.warnings == (() if drift is None else ("graph_registry_stale",))
    assert canonical_state(env) == before
    assert_private(result, env, generation.id, generation.artifact_digest, generation.input_digest)


def test_disabled_legacy_graph_registry_schema_mismatch_does_not_warn(env):
    generation = register_graph(env, enabled=False)
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT captured_schema_version FROM memory_ops.age_projection WHERE tenant_id=%s",
            (env.tenants[0],),
        ).fetchone() == (20,)
    result = inspect(env)
    assert result.graph.model_dump() == {
        "registered": True, "enabled": False, "epoch_schema_match": False,
        "serving_verified": False,
    }
    assert result.warnings == ()
    assert_private(result, env, generation.id)


def test_matching_graph_markers_do_not_claim_current_head_or_content_completeness(env):
    original = register_graph(env)
    assert env.observe(TEXT).status_code == 201
    replacement = record(env, begin(env))
    assert replacement.head.id != original.id
    result = inspect(env)
    assert result.graph.epoch_schema_match is True and result.graph.enabled
    assert result.graph.serving_verified is False and result.warnings == ()
    assert_private(result, env, original.id, replacement.head.id)


def test_snapshot_helper_is_readonly_repeatable_read_utc_and_bounded(env, monkeypatch):
    original = psycopg.connect
    connected = []

    def connect(*args, **kwargs):
        connected.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", connect)
    with administration.read_admin_snapshot(env.admin_url) as conn:
        settings = conn.execute(
            """SELECT current_setting('transaction_read_only') AS read_only,
                      current_setting('default_transaction_read_only') AS default_read_only,
                      current_setting('transaction_isolation') AS isolation,
                      current_setting('TimeZone') AS timezone,
                      current_setting('statement_timeout') AS statement_timeout,
                      current_setting('lock_timeout') AS lock_timeout"""
        ).fetchone()
        assert settings == {
            "read_only": "on", "default_read_only": "on", "isolation": "repeatable read",
            "timezone": "UTC", "statement_timeout": "5s", "lock_timeout": "5s",
        }
        assert conn.execute(
            "SELECT count(*) AS locks FROM pg_locks WHERE pid=pg_backend_pid() "
            "AND locktype='advisory'"
        ).fetchone()["locks"] == 0
    assert conn.closed
    assert len(connected) == 1 and connected[0]["connect_timeout"] == 5


def test_status_never_waits_for_tenant_barrier_or_row_locks_and_only_executes_reads(
    env, monkeypatch,
):
    original = psycopg.Connection.execute
    queries = []
    with psycopg.connect(env.admin_url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        with holder.transaction():
            holder.execute("SELECT id FROM memory.tenant WHERE id=%s FOR UPDATE", (env.tenants[0],))

            def execute(conn, query, *args, **kwargs):
                text = query.as_string(conn) if isinstance(query, sql.Composable) else query
                queries.append(text)
                assert not re.search(
                    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP)\b", text, re.I,
                )
                assert "pg_advisory" not in text.lower()
                assert not re.search(r"\bFOR\s+(SHARE|KEY|NO)\b", text, re.I)
                return original(conn, query, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(psycopg.Connection, "execute", execute)
                result = inspect(env)
            assert result.jobs.total == 0
    aggregates = [query for query in queries if "memory_ops.job" in query]
    assert aggregates and any(re.search(r"\bcount\s*\(\s*\*\s*\)", q, re.I) for q in aggregates)
    assert any(re.search(r"\bsum\s*\(", q, re.I) for q in queries)
    assert not any(re.search(r"\b(TABLESAMPLE|reltuples|LIMIT)\b", q, re.I) for q in queries)


def test_status_keeps_one_mvcc_snapshot_when_a_writer_commits_between_queries(env, monkeypatch):
    original = psycopg.Connection.execute
    written = []

    def execute(conn, query, *args, **kwargs):
        cursor = original(conn, query, *args, **kwargs)
        if (
            isinstance(query, str) and "pg_is_in_recovery()" in query
            and "memory.tenant" in query and not written
        ):
            written.append(True)
            with scope_access(env.admin_url, ScopeAccessRequest(
                operation="set", tenant_id=env.tenants[0], scope_id=env.scopes[2],
                principal_id=env.principals[2], expected_access_epoch=1,
                permissions=("read",), no_expiry=True,
            )) as changed:
                assert changed.access_epoch == 2
            source_binding(env, decision="deny", reason="bound")
        return cursor

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", execute)
        snapshot = inspect(env)
    assert written == [True]
    assert snapshot.access_epoch == 1 and snapshot.source.total == 0
    next_snapshot = inspect(env)
    assert next_snapshot.access_epoch == 2 and next_snapshot.source.total == 1


def test_evaluated_at_is_the_single_database_clock_not_python_time(env, monkeypatch):
    original = psycopg.Connection.execute
    clocks = []

    class NoPythonClock(datetime):
        @classmethod
        def now(cls, tz=None):
            pytest.fail("Status used the Python wall clock")

        @classmethod
        def utcnow(cls):
            pytest.fail("Status used the Python wall clock")

    class RememberClock:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            clocks.append(row["evaluated_at"])
            return row

    def execute(conn, query, *args, **kwargs):
        cursor = original(conn, query, *args, **kwargs)
        if isinstance(query, str) and re.search(r"\bclock_timestamp\s*\(", query):
            assert len(re.findall(r"\bclock_timestamp\s*\(", query)) == 1
            assert "evaluated_at" in query
            return RememberClock(cursor)
        return cursor

    with monkeypatch.context() as patch:
        patch.setattr(status, "datetime", NoPythonClock)
        patch.setattr(psycopg.Connection, "execute", execute)
        result = inspect(env)
    assert clocks == [result.evaluated_at]


def test_report_and_cli_serialization_happen_after_snapshot_connection_closes(
    env, monkeypatch, capsys,
):
    original = status.read_admin_snapshot
    connections = []

    @contextmanager
    def snapshot(url):
        with original(url) as conn:
            connections.append(conn)
            yield conn

    def print_after_close(*args, **kwargs):
        assert connections and all(conn.closed for conn in connections)
        builtins.print(*args, **kwargs)

    monkeypatch.setattr(status, "read_admin_snapshot", snapshot)
    result = inspect(env)
    assert connections[0].closed and result.tenant_id == env.tenants[0]
    monkeypatch.setattr(status, "print", print_after_close, raising=False)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    status.main(["--tenant-id", str(env.tenants[0])])
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["tenant_id"] == str(env.tenants[0])


def test_unknown_tenant_and_runtime_role_are_rejected_even_for_own_tenant(env):
    for url, tenant, expected in (
        (env.admin_url, uuid4(), "not_found"),
        (env.settings.database_url, env.tenants[0], "admin_role_required"),
        (env.settings.database_url, uuid4(), "admin_role_required"),
    ):
        with pytest.raises(AdminError, match=f"^{expected}$") as error:
            status.operations_status(url, status.OperationsStatusRequest(tenant_id=tenant))
        assert not error.value.outcome_unknown and url not in str(error.value)
    completed = cli(env, url=env.settings.database_url)
    assert completed.returncode == 1 and completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "error": {"code": "admin_role_required", "outcome_unknown": False},
    }


@pytest.mark.parametrize("case,code", [
    ("missing_schema", "schema_unavailable"),
    ("ledger_gap", "schema_version_mismatch"),
    ("vector_version", "extension_version_mismatch"),
    ("vector_schema", "extension_version_mismatch"),
])
def test_schema_ledger_and_vector_pin_are_enforced_without_partial_success(
    env, monkeypatch, capsys, case, code,
):
    original = psycopg.Connection.execute
    intercepted = []

    def execute(conn, query, *args, **kwargs):
        if isinstance(query, str) and "FROM public.pgag_schema_migration" in query:
            if case == "missing_schema":
                intercepted.append(case)
                return original(conn, "SELECT * FROM memory_ops.PRIVATE_STATUS_ABSENT_TABLE")
            if case == "ledger_gap":
                intercepted.append(case)
                return original(
                    conn, "SELECT version FROM public.pgag_schema_migration "
                    "WHERE version<>2 ORDER BY version",
                )
        if query == VECTOR_QUERY and case.startswith("vector_"):
            intercepted.append(case)
            if case == "vector_version":
                return original(
                    conn, "SELECT 'PRIVATE_STATUS_BAD_VERSION' AS extversion,'public' AS nspname",
                )
            return original(
                conn, "SELECT extversion,'PRIVATE_STATUS_BAD_SCHEMA' AS nspname "
                "FROM pg_extension WHERE extname='vector'",
            )
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", env.admin_url)
    with pytest.raises(SystemExit) as error:
        status.main(["--tenant-id", str(env.tenants[0])])
    assert error.value.code == 1 and intercepted == [case]
    output = capsys.readouterr()
    assert output.err == "" and "PRIVATE_STATUS" not in output.out
    assert json.loads(output.out) == {"error": {"code": code, "outcome_unknown": False}}


def test_database_statement_timeout_is_an_error_not_an_empty_healthy_report(env, monkeypatch):
    original = psycopg.Connection.execute
    delayed = []

    def execute(conn, query, *args, **kwargs):
        if isinstance(query, str) and "memory_ops.job" in query:
            delayed.append(True)
            original(conn, "SELECT pg_sleep(6)")
        return original(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", execute)
        with pytest.raises(AdminError, match="^admin_database_unavailable$") as error:
            inspect(env)
    assert delayed == [True] and error.value.outcome_unknown is False
    assert inspect(env).jobs.total == 0


@pytest.mark.parametrize("arguments", [
    [], ["--tenant-id", "PRIVATE_STATUS_BAD_UUID"],
    ["--tenant-id", str(uuid4()), "--source", "PRIVATE_STATUS_SOURCE"],
    ["--tenant-id", str(uuid4()), "--database-url", "PRIVATE_STATUS_DSN"],
    ["PRIVATE_STATUS_ARGUMENT"],
])
def test_cli_invalid_arguments_are_redacted_before_connection(arguments, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid CLI arguments reached the database")

    monkeypatch.setattr(status, "operations_status", forbidden)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_STATUS_DSN")
    with pytest.raises(SystemExit) as error:
        status.main(arguments)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "invalid_operations_status_arguments" in output.err
    assert "PRIVATE_STATUS" not in output.err and "Traceback" not in output.err


@pytest.mark.parametrize("value", [None, "", " \t"])
def test_cli_requires_admin_url_and_never_falls_back_to_runtime_url(
    monkeypatch, capsys, value,
):
    def forbidden(*args, **kwargs):
        pytest.fail("Missing ADMIN URL used the runtime connection")

    monkeypatch.setenv("PGAG_DATABASE_URL", "PRIVATE_STATUS_RUNTIME_URL")
    if value is None:
        monkeypatch.delenv("PGAG_ADMIN_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", value)
    monkeypatch.setattr(status, "operations_status", forbidden)
    with pytest.raises(SystemExit) as error:
        status.main(["--tenant-id", str(uuid4())])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "PGAG_ADMIN_DATABASE_URL is required" in output.err
    assert "PRIVATE_STATUS" not in output.err


@pytest.mark.parametrize("url", [
    "postgresql://PRIVATE_STATUS_USER:PRIVATE_STATUS_PASSWORD@127.0.0.1:1/PRIVATE_STATUS_DATABASE",
    "PRIVATE_STATUS_MALFORMED_DSN",
])
def test_cli_connection_failures_are_redacted_json_not_tracebacks(monkeypatch, capsys, url):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", url)
    with pytest.raises(SystemExit) as error:
        status.main(["--tenant-id", str(uuid4())])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.err == "" and "PRIVATE_STATUS" not in output.out
    value = json.loads(output.out)
    assert set(value) == {"error"}
    assert value["error"]["code"] in {"admin_database_unavailable", "admin_database_error"}
    assert value["error"]["outcome_unknown"] is False
    assert "Traceback" not in output.out


def test_standby_snapshot_is_explicit_non_authority_not_readiness(env, monkeypatch):
    original = psycopg.Connection.execute

    def execute(conn, query, *args, **kwargs):
        if isinstance(query, str) and "pg_is_in_recovery()" in query:
            query = query.replace("pg_is_in_recovery()", "true")
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    result = inspect(env)
    assert result.in_recovery is True and result.primary_snapshot is False
    assert result.warnings == ("standby_snapshot",)
    assert not result.restore_authorized
    assert not result.source_authorization_verified and not result.production_qualified
    assert list(result.warnings) == [code for code in WARNING_ORDER if code in result.warnings]
