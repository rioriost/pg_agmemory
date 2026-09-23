import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory import source_access as coordinator
from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.source_access import (
    MAX_SOURCE_LEASE_SECONDS,
    SourceAccessRequest,
    SourceIdentity,
    SourceNotice,
    source_access,
)

SOURCE = {
    "source_system": "synthetic-warehouse",
    "dataset_id": "contracts",
    "source_subject": "source-reader",
}
IDENTITY = SourceIdentity(**SOURCE)
INSTANT = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)


def notice_data(**changes):
    return {
        "format": "pgag-source-access-notice-v1",
        "source": SOURCE,
        "sequence": 1,
        "decision": "allow",
        "reason": "authorized",
        "acl_version": "acl-1",
        "verified_at": INSTANT,
        "valid_until": INSTANT + timedelta(seconds=60),
        **changes,
    }


def request(env, operation="get", **changes):
    return SourceAccessRequest(
        **{
            "operation": operation,
            "tenant_id": env.tenants[0],
            "scope_id": env.scopes[0],
            "principal_id": env.principals[2],
            **changes,
        }
    )


def execute(env, operation="get", **changes):
    with source_access(env.admin_url, request(env, operation, **changes)) as result:
        return result


def regular_access(env, operation="get", **changes):
    body = request(env).model_dump(include={"tenant_id", "scope_id", "principal_id"})
    with scope_access(
        env.admin_url, ScopeAccessRequest(**(body | {"operation": operation} | changes))
    ) as result:
        return result


def bind(env, **changes):
    return execute(
        env,
        "bind",
        **{
            "source": IDENTITY,
            "expected_access_epoch": regular_access(env).access_epoch,
            **changes,
        },
    )


def database_now(env):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute("SELECT clock_timestamp()").fetchone()[0]


def allow_notice(env, sequence=1, **changes):
    now = database_now(env)
    return SourceNotice.model_validate(
        notice_data(
            sequence=sequence,
            verified_at=now - timedelta(seconds=1),
            valid_until=now + timedelta(seconds=60),
            **changes,
        )
    )


def deny_notice(sequence=2, reason="revoked", **changes):
    return SourceNotice(
        source=IDENTITY,
        sequence=sequence,
        decision="deny",
        reason=reason,
        **changes,
    )


def apply_notice(env, notice, epoch=None):
    return execute(
        env,
        "apply",
        notice=notice,
        expected_access_epoch=execute(env).access_epoch if epoch is None else epoch,
    )


def fingerprint(env):
    tables = (
        "memory.scope_member",
        "memory_ops.scope_access_event",
        "memory_ops.source_access_state",
        "memory_ops.source_access_event",
    )
    with psycopg.connect(env.admin_url) as conn:
        result = {
            "epoch": conn.execute(
                "SELECT access_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],)
            ).fetchone()[0]
        }
        for table in tables:
            result[table] = conn.execute(
                psycopg.sql.SQL(
                    "SELECT to_jsonb(t) FROM {} t WHERE tenant_id=%s ORDER BY to_jsonb(t)::text"
                ).format(psycopg.sql.Identifier(*table.split("."))),
                (env.tenants[0],),
            ).fetchall()
    return result


def event_rows(env):
    with psycopg.connect(env.admin_url) as conn:
        return [
            row[0]
            for row in conn.execute(
                """SELECT to_jsonb(e) FROM memory_ops.source_access_event e
                   WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s AND sequence>0
                   ORDER BY sequence""",
                (env.tenants[0], env.scopes[0], env.principals[2]),
            ).fetchall()
        ]


def cli(operation, *arguments, env=None, url=None):
    target = (
        [
            "--tenant-id",
            str(env.tenants[0]),
            "--scope-id",
            str(env.scopes[0]),
            "--principal-id",
            str(env.principals[2]),
        ]
        if env is not None
        else []
    )
    variables = {"PATH": os.environ["PATH"]}
    if url is not None or env is not None:
        variables["PGAG_ADMIN_DATABASE_URL"] = url or env.admin_url
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pg_agmemory.cli",
            "source-access",
            operation,
            *target,
            *arguments,
        ],
        env=variables,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def notice_file():
    path = Path(f".source-access-notice-{uuid4().hex}.json")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("field", ["source_system", "dataset_id", "source_subject"])
@pytest.mark.parametrize("value", ["", " \t\r\n ", "x" * 257, None, 17, True])
def test_source_identity_has_bounded_opaque_text_fields(field, value):
    with pytest.raises(ValidationError):
        SourceIdentity.model_validate(SOURCE | {field: value})


def test_source_identity_and_acl_version_trim_surrounding_whitespace():
    wrapped = {key: f" \t{value}\r\n " for key, value in SOURCE.items()}
    assert SourceIdentity.model_validate(wrapped) == IDENTITY
    notice = SourceNotice.model_validate(notice_data(source=wrapped, acl_version=" \tacl-1\r\n "))
    assert notice.source == IDENTITY and notice.acl_version == "acl-1"


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "untrusted-format"},
        {"source": SOURCE | {"password": "DO_NOT_ECHO"}},
        {"sequence": 0},
        {"sequence": -1},
        {"sequence": MAX_EPOCH + 1},
        {"sequence": True},
        {"sequence": "1"},
        {"sequence": 1.0},
        {"decision": "permit"},
        {"reason": "bound"},
        {"reason": "revoked"},
        {"acl_version": None},
        {"acl_version": ""},
        {"acl_version": " \t\r\n "},
        {"acl_version": "x" * 257},
        {"verified_at": None},
        {"valid_until": None},
        {"verified_at": "2026-09-23T00:00:00"},
        {"valid_until": "2026-09-23T00:01:00"},
        {"decision": "deny", "reason": "authorized"},
        {"decision": "deny", "reason": "revoked"},
        {"decision": "deny", "reason": "unavailable", "verified_at": None},
        {"decision": "deny", "reason": "deleted", "valid_until": None},
        {"source_authorization_verified": True},
        {"tenant_id": str(uuid4())},
    ],
)
def test_notice_contract_rejects_invalid_or_authority_expanding_fields(changes):
    with pytest.raises(ValidationError):
        SourceNotice.model_validate(notice_data(**changes))


@pytest.mark.parametrize("reason", ["revoked", "unavailable", "deleted"])
def test_deny_notice_accepts_optional_acl_but_never_a_lease(reason):
    for acl_version in (None, "acl-revoked"):
        value = deny_notice(reason=reason, acl_version=acl_version)
        assert value.verified_at is None and value.valid_until is None
    assert SourceNotice.model_validate(notice_data(sequence=MAX_EPOCH)).sequence == MAX_EPOCH
    assert MAX_SOURCE_LEASE_SECONDS == 300


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "set"},
        {"operation": "bind"},
        {"operation": "apply"},
        {"expected_access_epoch": 1},
        {"source": SOURCE},
        {"notice": notice_data()},
        {"operation": "bind", "expected_access_epoch": 1},
        {"operation": "bind", "expected_access_epoch": 1, "notice": notice_data()},
        {"operation": "apply", "expected_access_epoch": 1},
        {"operation": "apply", "expected_access_epoch": 1, "source": SOURCE},
        {
            "operation": "bind",
            "expected_access_epoch": 1,
            "source": SOURCE,
            "notice": notice_data(),
        },
        {
            "operation": "apply",
            "expected_access_epoch": 1,
            "source": SOURCE,
            "notice": notice_data(),
        },
        {"operation": "bind", "expected_access_epoch": 0, "source": SOURCE},
        {"operation": "bind", "expected_access_epoch": True, "source": SOURCE},
        {"operation": "bind", "expected_access_epoch": "1", "source": SOURCE},
        {"operation": "bind", "expected_access_epoch": MAX_EPOCH + 1, "source": SOURCE},
        {"permissions": ["admin"]},
        {"subject": "DO_NOT_ECHO"},
    ],
)
def test_request_contract_requires_explicit_operation_arguments(changes):
    with pytest.raises(ValidationError):
        SourceAccessRequest.model_validate(
            {
                "operation": "get",
                "tenant_id": uuid4(),
                "scope_id": uuid4(),
                "principal_id": uuid4(),
                **changes,
            }
        )


@pytest.mark.parametrize("mutation", ["operation", "epoch", "notice", "source", "constructed"])
def test_mutated_models_are_revalidated_and_redacted_before_database_access(monkeypatch, mutation):
    value = SourceAccessRequest(
        operation="apply",
        tenant_id=uuid4(),
        scope_id=uuid4(),
        principal_id=uuid4(),
        expected_access_epoch=1,
        notice=SourceNotice.model_validate(notice_data()),
    )
    if mutation == "operation":
        value = value.model_copy(update={"operation": "DO_NOT_ECHO"})
    elif mutation == "epoch":
        value = value.model_copy(update={"expected_access_epoch": True})
    elif mutation == "notice":
        value = value.model_copy(
            update={"notice": value.notice.model_copy(update={"sequence": True})}
        )
    elif mutation == "source":
        source = value.notice.source.model_copy(update={"source_subject": "DO_NOT_ECHO" * 300})
        value = value.model_copy(
            update={"notice": value.notice.model_copy(update={"source": source})}
        )
    else:
        value = SourceAccessRequest.model_construct(**(value.model_dump() | {"tenant_id": "bad"}))

    def no_database(*args, **kwargs):
        pytest.fail("Invalid source request attempted a database connection")

    monkeypatch.setattr(psycopg, "connect", no_database)
    with pytest.raises(AdminError, match="^invalid_source_access_request$") as failure:
        with source_access("DO_NOT_ECHO_DSN", value):
            pytest.fail("Mutated request was accepted")
    assert not failure.value.outcome_unknown
    assert "DO_NOT_ECHO" not in str(failure.value)


@pytest.mark.parametrize(
    "arguments",
    [
        ["get"],
        ["get", "--tenant-id", "DO_NOT_ECHO"],
        ["get", "--subject", "DO_NOT_ECHO"],
        ["bind", "--source-system", "DO_NOT_ECHO"],
        ["apply", "--permissions", "DO_NOT_ECHO"],
        ["unknown-DO_NOT_ECHO"],
    ],
)
def test_cli_argument_failures_never_echo_untrusted_values(arguments):
    result = cli(*arguments)
    assert result.returncode == 2 and result.stdout == ""
    assert "invalid_source_access_arguments" in result.stderr
    assert "DO_NOT_ECHO" not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize("kind", ["oversized", "invalid-json", "invalid-utf8", "missing"])
def test_cli_notice_file_failures_are_bounded_and_redacted(notice_file, kind):
    if kind == "oversized":
        data = json.dumps(notice_data(acl_version="DO_NOT_ECHO"), default=str).encode()
        notice_file.write_bytes(data + b" " * (32769 - len(data)))
        assert notice_file.stat().st_size == 32769
    elif kind == "invalid-json":
        notice_file.write_text('{"source":"DO_NOT_ECHO"')
    elif kind == "invalid-utf8":
        notice_file.write_bytes(b"\xffDO_NOT_ECHO")
    result = cli(
        "apply",
        "--tenant-id",
        str(uuid4()),
        "--scope-id",
        str(uuid4()),
        "--principal-id",
        str(uuid4()),
        "--expected-access-epoch",
        "1",
        "--notice-file",
        str(notice_file),
        url="postgresql://DO_NOT_ECHO:DO_NOT_ECHO@127.0.0.1:1/DO_NOT_ECHO",
    )
    assert result.returncode != 0
    assert "DO_NOT_ECHO" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "admin_database_unavailable" not in result.stdout + result.stderr


def test_source_cli_help_does_not_import_optional_httpx():
    script = """
import importlib.abc
import sys

class NoHTTPX(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith("httpx."):
            raise AssertionError("source administration imported optional httpx")

sys.meta_path.insert(0, NoHTTPX())
from pg_agmemory.cli import main
sys.argv = ["pg-agmemory", "source-access", "--help"]
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "notice-file" in result.stdout


def test_cli_unknown_outcome_is_redacted_and_never_automatically_retried(monkeypatch, capsys):
    attempts = []

    def failed(url, value):
        attempts.append(value)
        raise AdminError("admin_database_unavailable", outcome_unknown=True)

    monkeypatch.setattr(coordinator, "source_access", failed)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "DO_NOT_ECHO")
    with pytest.raises(SystemExit) as exited:
        coordinator.main(
            [
                "get",
                "--tenant-id",
                str(uuid4()),
                "--scope-id",
                str(uuid4()),
                "--principal-id",
                str(uuid4()),
            ]
        )
    output = capsys.readouterr()
    assert exited.value.code == 1 and len(attempts) == 1 and output.err == ""
    assert json.loads(output.out)["error"] == {
        "code": "admin_database_unavailable",
        "outcome_unknown": True,
    }
    assert "DO_NOT_ECHO" not in output.out


@pytest.mark.integration
@pytest.mark.parametrize("existing", [False, True])
def test_binding_revokes_old_read_only_membership_and_records_durable_identity(env, existing):
    if existing:
        regular_access(
            env, "set", expected_access_epoch=1, permissions=("read",), no_expiry=True
        )
    before = regular_access(env)
    result = bind(env)
    assert result.operation == "bind" and result.changed and not result.replayed
    assert result.source == IDENTITY and result.sequence == 0
    assert result.decision == "deny" and result.reason == "bound"
    assert result.permissions == result.effective_permissions == []
    assert result.expires_at is None and not result.source_authorization_verified
    assert result.access_epoch == before.access_epoch + int(existing)
    assert (result.tenant_id, result.scope_id, result.principal_id) == (
        env.tenants[0],
        env.scopes[0],
        env.principals[2],
    )
    assert not regular_access(env).membership_exists
    current = execute(env)
    assert current.operation == "get" and not current.changed and not current.replayed
    assert current.source == IDENTITY and current.sequence == 0
    with psycopg.connect(env.admin_url) as conn:
        stored = conn.execute(
            """SELECT source_system,dataset_id,source_subject,sequence,decision,reason,
                      database_role::text FROM memory_ops.source_access_state
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (env.tenants[0], env.scopes[0], env.principals[2]),
        ).fetchone()
        role = conn.execute("SELECT current_user").fetchone()[0]
    assert stored == (*SOURCE.values(), 0, "deny", "bound", role)


@pytest.mark.integration
@pytest.mark.parametrize("permissions", [("write",), ("delete",), ("admin",), ("read", "write")])
def test_binding_rejects_write_delete_or_admin_targets_without_changes(env, permissions):
    granted = regular_access(
        env, "set", expected_access_epoch=1, permissions=permissions, no_expiry=True
    )
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^source_binding_permission_conflict$"):
        bind(env, expected_access_epoch=granted.access_epoch)
    assert fingerprint(env) == before


@pytest.mark.integration
def test_binding_replay_never_resets_an_allowed_source_or_accepts_a_different_identity(env):
    bound = bind(env)
    first = fingerprint(env)
    replay = bind(env)
    assert replay.replayed and not replay.changed and replay.access_epoch == bound.access_epoch
    assert fingerprint(env) == first
    allowed = apply_notice(env, allow_notice(env))
    before = fingerprint(env)
    replay = bind(env)
    assert replay.replayed and not replay.changed and replay.sequence == 1
    assert replay.access_epoch == allowed.access_epoch and replay.effective_permissions == ["read"]
    assert fingerprint(env) == before
    for field in SOURCE:
        wrong = SourceIdentity(**(SOURCE | {field: "other-source"}))
        with pytest.raises(AdminError, match="^source_binding_conflict$"):
            bind(env, source=wrong)
        assert fingerprint(env) == before


@pytest.mark.integration
def test_binding_and_notice_cannot_cross_tenant_principal_scope_or_source_identity(env):
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^not_found$"):
        apply_notice(env, allow_notice(env), epoch=1)
    assert fingerprint(env) == before
    for changes in (
        {"tenant_id": env.tenants[1]},
        {"scope_id": env.scopes[1]},
        {"principal_id": env.principals[1]},
        {"scope_id": uuid4()},
        {"principal_id": uuid4()},
    ):
        before = fingerprint(env)
        with pytest.raises(AdminError, match="^not_found$"):
            execute(env, "bind", source=IDENTITY, expected_access_epoch=1, **changes)
        assert fingerprint(env) == before
    bind(env)
    for field in SOURCE:
        wrong = SourceIdentity(**(SOURCE | {field: "other-source"}))
        before = fingerprint(env)
        with pytest.raises(AdminError, match="^source_binding_conflict$"):
            apply_notice(env, allow_notice(env, source=wrong))
        assert fingerprint(env) == before


@pytest.mark.integration
def test_contiguous_authorized_notice_grants_only_read_and_exact_database_clock_lease(env):
    bound = bind(env)
    notice = allow_notice(env)
    result = apply_notice(env, notice)
    assert result.operation == "apply" and result.changed and not result.replayed
    assert result.source == IDENTITY and result.sequence == 1
    assert result.decision == "allow" and result.reason == "authorized"
    assert result.permissions == result.effective_permissions == ["read"]
    assert result.expires_at == notice.valid_until
    assert result.access_epoch == bound.access_epoch + 1
    assert notice.verified_at <= result.evaluated_at < result.expires_at
    assert result.expires_at <= notice.verified_at + timedelta(seconds=300)
    assert result.source_authorization_verified is False
    regular = regular_access(env)
    assert regular.permissions == ["read"] and regular.expires_at == notice.valid_until
    events = event_rows(env)
    assert len(events) == 1 and events[0]["sequence"] == 1
    assert events[0]["decision"] == "allow" and events[0]["reason"] == "authorized"
    assert events[0]["access_epoch"] == result.access_epoch
    snapshot = fingerprint(env)
    assert execute(env).expires_at == result.expires_at and fingerprint(env) == snapshot


@pytest.mark.integration
def test_normalized_latest_notice_replay_precedes_epoch_cas_and_never_extends_lease(env):
    bound = bind(env)
    notice = allow_notice(env)
    first = apply_notice(env, notice)
    before = fingerprint(env)
    offset = timezone(timedelta(hours=9))
    normalized = notice.model_copy(
        update={
            "verified_at": notice.verified_at.astimezone(offset),
            "valid_until": notice.valid_until.astimezone(offset),
        }
    )
    replay = apply_notice(env, normalized, epoch=bound.access_epoch)
    assert replay.replayed and not replay.changed
    assert replay.access_epoch == first.access_epoch and replay.expires_at == first.expires_at
    assert fingerprint(env) == before
    for changed in (
        notice.model_copy(update={"acl_version": "acl-changed"}),
        notice.model_copy(update={"valid_until": notice.valid_until + timedelta(seconds=1)}),
        deny_notice(sequence=1),
    ):
        with pytest.raises(AdminError, match="^source_notice_conflict$"):
            apply_notice(env, changed, epoch=bound.access_epoch)
        assert fingerprint(env) == before
    denied = apply_notice(env, deny_notice())
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^source_sequence_conflict$"):
        apply_notice(env, notice, epoch=denied.access_epoch)
    assert fingerprint(env) == before


@pytest.mark.integration
def test_new_notice_uses_tenant_wide_epoch_cas_without_silent_retry(env):
    bound = bind(env)
    own = regular_access(
        env,
        "set",
        scope_id=env.scopes[2],
        expected_access_epoch=bound.access_epoch,
        permissions=("read", "write"),
        no_expiry=True,
    )
    before = fingerprint(env)
    notice = allow_notice(env)
    with pytest.raises(AdminError, match="^access_epoch_conflict$"):
        apply_notice(env, notice, epoch=bound.access_epoch)
    assert fingerprint(env) == before
    accepted = apply_notice(env, notice, epoch=own.access_epoch)
    assert accepted.effective_permissions == ["read"] and accepted.sequence == 1
    assert len(event_rows(env)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("verified_offset", "until_offset"),
    [(60, 120), (-120, -60), (-60, -60), (-1, 300), (-1, -2)],
    ids=["future-verification", "expired", "empty-lease", "over-300-seconds", "reversed"],
)
def test_invalid_lease_fails_closed_revokes_atomically_and_advances_cursor(
    env, verified_offset, until_offset
):
    bind(env)
    previous = apply_notice(env, allow_notice(env))
    now = database_now(env)
    notice = SourceNotice.model_validate(
        notice_data(
            sequence=2,
            verified_at=now + timedelta(seconds=verified_offset),
            valid_until=now + timedelta(seconds=until_offset),
        )
    )
    denied = apply_notice(env, notice)
    assert denied.changed and not denied.replayed and denied.sequence == 2
    assert denied.decision == "deny" and denied.reason == "invalid_lease"
    assert denied.permissions == denied.effective_permissions == []
    assert denied.expires_at is None and denied.access_epoch == previous.access_epoch + 1
    assert not regular_access(env).membership_exists
    before = fingerprint(env)
    replay = apply_notice(env, notice, epoch=previous.access_epoch)
    assert replay.replayed and replay.reason == "invalid_lease"
    assert fingerprint(env) == before
    fresh = apply_notice(env, allow_notice(env, sequence=3))
    assert fresh.sequence == 3 and fresh.effective_permissions == ["read"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "extra_microseconds", [0, 1], ids=["exactly-300", "300-plus-one-microsecond"]
)
def test_exact_lease_limit_is_inclusive_and_not_an_expiration_proxy(env, extra_microseconds):
    bind(env)
    now = database_now(env)
    verified_at = now - timedelta(seconds=295)
    valid_until = now + timedelta(seconds=5, microseconds=extra_microseconds)
    notice = SourceNotice.model_validate(
        notice_data(verified_at=verified_at, valid_until=valid_until)
    )
    assert notice.valid_until - notice.verified_at == timedelta(
        seconds=300, microseconds=extra_microseconds
    )
    result = apply_notice(env, notice)
    assert notice.verified_at <= result.evaluated_at < notice.valid_until
    assert result.changed and result.sequence == 1
    if extra_microseconds:
        assert result.decision == "deny" and result.reason == "invalid_lease"
        assert result.effective_permissions == [] and result.expires_at is None
        assert not regular_access(env).membership_exists
    else:
        assert result.decision == "allow" and result.reason == "authorized"
        assert result.permissions == result.effective_permissions == ["read"]
        assert result.expires_at == notice.valid_until
    assert not result.source_authorization_verified


@pytest.mark.integration
def test_sequence_gap_is_a_durable_denial_until_an_explicit_contiguous_fresh_notice(env):
    bind(env)
    first = apply_notice(env, allow_notice(env))
    skipped = allow_notice(env, sequence=3)
    denied = apply_notice(env, skipped)
    assert denied.changed and denied.sequence == 3
    assert denied.decision == "deny" and denied.reason == "sequence_gap"
    assert denied.effective_permissions == [] and not regular_access(env).membership_exists
    before = fingerprint(env)
    replay = apply_notice(env, skipped, epoch=first.access_epoch)
    assert replay.replayed and replay.reason == "sequence_gap"
    assert fingerprint(env) == before
    with pytest.raises(AdminError, match="^source_sequence_conflict$"):
        apply_notice(env, allow_notice(env, sequence=2))
    renewed = apply_notice(env, allow_notice(env, sequence=4))
    assert renewed.sequence == 4 and renewed.effective_permissions == ["read"]
    assert [(row["sequence"], row["reason"]) for row in event_rows(env)] == [
        (1, "authorized"),
        (3, "sequence_gap"),
        (4, "authorized"),
    ]


@pytest.mark.integration
@pytest.mark.parametrize("reason", ["revoked", "unavailable"])
def test_explicit_denial_revokes_existing_grant_and_requires_a_new_notice(env, reason):
    bind(env)
    apply_notice(env, allow_notice(env))
    denied = apply_notice(env, deny_notice(reason=reason, acl_version="acl-denied"))
    assert denied.sequence == 2 and denied.decision == "deny" and denied.reason == reason
    assert denied.permissions == denied.effective_permissions == []
    assert denied.expires_at is None and not regular_access(env).membership_exists
    replay = apply_notice(env, deny_notice(reason=reason, acl_version="acl-denied"))
    assert replay.replayed and not replay.changed
    restored = apply_notice(env, allow_notice(env, sequence=3))
    assert restored.effective_permissions == ["read"]


@pytest.mark.integration
@pytest.mark.parametrize("deleted_sequence", [2, 4], ids=["contiguous", "sequence-gap"])
def test_deleted_is_terminal_and_never_claims_native_payload_was_purged(env, deleted_sequence):
    memory = env.observe(content="Source payload remains until explicit Native purge").json()
    bind(env)
    apply_notice(env, allow_notice(env))
    deletion = deny_notice(sequence=deleted_sequence, reason="deleted")
    deleted = apply_notice(env, deletion)
    assert deleted.reason == "deleted" and deleted.decision == "deny"
    assert deleted.sequence == deleted_sequence
    assert deleted.effective_permissions == [] and not deleted.source_authorization_verified
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^source_deleted$"):
        apply_notice(env, allow_notice(env, sequence=deleted_sequence + 1))
    assert fingerprint(env) == before
    assert bind(env).replayed and fingerprint(env) == before
    assert apply_notice(env, deletion).replayed
    for sequence, reason in (
        (deleted_sequence + 1, "revoked"),
        (deleted_sequence + 3, "unavailable"),
    ):
        later = apply_notice(env, deny_notice(sequence=sequence, reason=reason))
        assert later.sequence == sequence and later.reason == "deleted"
        assert later.decision == "deny" and later.effective_permissions == []
        before = fingerprint(env)
        with pytest.raises(AdminError, match="^source_deleted$"):
            apply_notice(env, allow_notice(env, sequence=sequence + 1))
        assert fingerprint(env) == before
    assert all(row["reason"] == "deleted" for row in event_rows(env)[1:])
    assert not env.recall(2, scope_ids=[str(env.scopes[0])]).json()["items"]
    assert env.recall().json()["items"][0]["memory_id"] == memory["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM memory.episode WHERE tenant_id=%s AND id=%s",
            (env.tenants[0], memory["memory_id"]),
        ).fetchone()[0] == 1
    forgotten = env.client.post(
        "/v1/forget",
        json={"memory_ids": [memory["memory_id"]], "reason": "Separate Native maintenance"},
        headers=env.headers(),
    )
    assert forgotten.status_code == 202, forgotten.text
    assert not env.recall().json()["items"]
    assert execute(env).reason == "deleted"


@pytest.mark.integration
def test_managed_scope_set_is_blocked_and_replaying_source_allow_never_undoes_manual_revoke(env):
    bind(env)
    notice = allow_notice(env)
    allowed = apply_notice(env, notice)
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^source_access_managed$"):
        regular_access(
            env,
            "set",
            expected_access_epoch=allowed.access_epoch,
            permissions=("read",),
            expires_at=allowed.expires_at,
        )
    assert fingerprint(env) == before
    maintenance = regular_access(
        env,
        "set",
        principal_id=env.principals[0],
        expected_access_epoch=allowed.access_epoch,
        permissions=("read", "write", "delete"),
        no_expiry=True,
    )
    assert not maintenance.changed
    assert maintenance.effective_permissions == ["read", "write", "delete"]
    assert fingerprint(env) == before
    revoked = regular_access(env, "revoke", expected_access_epoch=allowed.access_epoch)
    assert revoked.changed and not revoked.membership_exists
    before = fingerprint(env)
    replay = apply_notice(env, notice, epoch=allowed.access_epoch)
    assert replay.replayed and not replay.changed and replay.sequence == 1
    assert replay.access_epoch == revoked.access_epoch
    assert replay.permissions == replay.effective_permissions == []
    assert replay.expires_at is None and fingerprint(env) == before
    own = regular_access(
        env,
        "set",
        scope_id=env.scopes[2],
        expected_access_epoch=revoked.access_epoch,
        permissions=("read", "write"),
        no_expiry=True,
    )
    assert own.permissions == ["read", "write"]
    renewed = apply_notice(env, allow_notice(env, sequence=2), epoch=own.access_epoch)
    assert renewed.effective_permissions == ["read"]


@pytest.mark.integration
def test_event_insert_failure_rolls_back_state_audit_epoch_and_membership(env, monkeypatch):
    bind(env)
    first = apply_notice(env, allow_notice(env))
    before = fingerprint(env)
    original = psycopg.Connection.execute
    attempts = []

    def failed(conn, query, *args, **kwargs):
        if isinstance(query, str) and "INSERT INTO memory_ops.source_access_event" in query:
            attempts.append(query)
            raise psycopg.IntegrityError("DO_NOT_ECHO event failure")
        return original(conn, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", failed)
        with pytest.raises(AdminError, match="^admin_database_error$") as failure:
            apply_notice(env, deny_notice(), epoch=first.access_epoch)
        assert not failure.value.outcome_unknown and "DO_NOT_ECHO" not in str(failure.value)
    assert len(attempts) == 1 and fingerprint(env) == before
    assert execute(env).effective_permissions == ["read"]
    assert apply_notice(env, deny_notice()).reason == "revoked"


@pytest.mark.integration
def test_uncertain_commit_is_not_retried_or_misreported_as_a_rollback(env, monkeypatch):
    bound = bind(env)
    notice = allow_notice(env)
    original = psycopg.Connection.transaction
    commits = []

    @contextmanager
    def lost_commit_response(conn, *args, **kwargs):
        with original(conn, *args, **kwargs) as transaction:
            yield transaction
        commits.append(True)
        raise psycopg.OperationalError("DO_NOT_ECHO after actual commit")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "transaction", lost_commit_response)
        with pytest.raises(AdminError, match="^commit_outcome_unknown$") as failure:
            apply_notice(env, notice, epoch=bound.access_epoch)
        assert failure.value.outcome_unknown
        assert "DO_NOT_ECHO" not in str(failure.value)
    assert len(commits) == 1 and len(event_rows(env)) == 1
    durable = execute(env)
    assert durable.sequence == 1 and durable.effective_permissions == ["read"]
    before = fingerprint(env)
    replay = apply_notice(env, notice, epoch=bound.access_epoch)
    assert replay.replayed and not replay.changed and fingerprint(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("identical", [True, False])
def test_concurrent_same_sequence_has_one_committed_transition_and_safe_replay_or_conflict(
    env, identical
):
    bound = bind(env)
    notice = allow_notice(env)
    other = notice if identical else notice.model_copy(update={"acl_version": "other-acl"})
    barrier = Barrier(2)

    def change(value):
        barrier.wait(timeout=5)
        try:
            return apply_notice(env, value, epoch=bound.access_epoch)
        except AdminError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [notice, other]))
    success = [result for result in results if not isinstance(result, str)]
    assert sum(result.changed for result in success) == 1
    if identical:
        assert len(success) == 2 and sum(result.replayed for result in success) == 1
    else:
        assert len(success) == 1 and results.count("source_notice_conflict") == 1
    current = execute(env)
    assert current.sequence == 1 and current.access_epoch == bound.access_epoch + 1
    assert current.effective_permissions == ["read"] and len(event_rows(env)) == 1


@pytest.mark.integration
def test_source_state_and_events_are_private_forced_rls_tables(env):
    bind(env)
    for operation, changes in (
        ("get", {}),
        ("bind", {"source": IDENTITY, "expected_access_epoch": execute(env).access_epoch}),
        (
            "apply",
            {"notice": allow_notice(env), "expected_access_epoch": execute(env).access_epoch},
        ),
    ):
        with pytest.raises(AdminError, match="^admin_role_required$"):
            with source_access(env.settings.database_url, request(env, operation, **changes)):
                pytest.fail("Runtime role was accepted by source administration")
    for table in ("source_access_state", "source_access_event"):
        with psycopg.connect(env.admin_url) as conn:
            assert conn.execute(
                """SELECT relrowsecurity,relforcerowsecurity FROM pg_class
                   WHERE oid=%s::regclass""",
                ("memory_ops." + table,),
            ).fetchone() == (True, True)
        with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
            for command in (
                f"SELECT * FROM memory_ops.{table}",
                f"INSERT INTO memory_ops.{table} DEFAULT VALUES",
                f"UPDATE memory_ops.{table} SET reason=reason",
                f"DELETE FROM memory_ops.{table}",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(command)


@pytest.mark.integration
def test_database_triggers_reject_identity_changes_cursor_rewind_deletion_and_event_edits(env):
    bind(env)
    apply_notice(env, allow_notice(env))
    before = fingerprint(env)
    statements = [
        "UPDATE memory_ops.source_access_state SET sequence=sequence-1 WHERE tenant_id=%s",
        "UPDATE memory_ops.source_access_state SET sequence=sequence+1,"
        "source_subject='other' WHERE tenant_id=%s",
        "DELETE FROM memory_ops.source_access_state WHERE tenant_id=%s",
        "UPDATE memory_ops.source_access_event SET reason='unavailable' WHERE tenant_id=%s",
        "DELETE FROM memory_ops.source_access_event WHERE tenant_id=%s",
    ]
    for statement in statements:
        with psycopg.connect(env.admin_url, autocommit=True) as conn:
            with pytest.raises(psycopg.Error):
                conn.execute(statement, (env.tenants[0],))
        assert fingerprint(env) == before
    apply_notice(env, deny_notice(reason="deleted"))
    before = fingerprint(env)
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute(
                """UPDATE memory_ops.source_access_state SET sequence=sequence+1,
                   decision='allow',reason='authorized',acl_version='new',
                   verified_at=clock_timestamp(),valid_until=clock_timestamp()+interval '1 minute'
                   WHERE tenant_id=%s""",
                (env.tenants[0],),
            )
    assert fingerprint(env) == before


@pytest.mark.integration
def test_source_result_is_delivered_after_commit_with_tenant_barrier_still_held(env):
    bound = bind(env)
    notice = allow_notice(env)
    with pytest.raises(RuntimeError, match="consumer lost output"):
        with source_access(
            env.admin_url,
            request(env, "apply", notice=notice, expected_access_epoch=bound.access_epoch),
        ) as result:
            assert result.changed and result.effective_permissions == ["read"]
            with psycopg.connect(env.admin_url, autocommit=True) as conn:
                assert conn.execute(
                    """SELECT sequence,decision FROM memory_ops.source_access_state
                       WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                    (env.tenants[0], env.scopes[0], env.principals[2]),
                ).fetchone() == (1, "allow")
                assert not conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),)
                ).fetchone()[0]
                assert conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[1]),)
                ).fetchone()[0]
            raise RuntimeError("consumer lost output")
    assert execute(env).effective_permissions == ["read"] and len(event_rows(env)) == 1


@pytest.mark.integration
def test_expired_source_lease_hides_native_payload_without_renewal_on_replay(env):
    memory = env.observe().json()["memory_id"]
    bind(env)
    now = database_now(env)
    notice = SourceNotice.model_validate(
        notice_data(verified_at=now, valid_until=now + timedelta(seconds=1))
    )
    allowed = apply_notice(env, notice)
    assert allowed.effective_permissions == ["read"]
    assert env.recall(2, scope_ids=[str(env.scopes[0])]).json()["items"][0]["memory_id"] == memory
    deadline = time.monotonic() + 5
    while database_now(env) < notice.valid_until:
        assert time.monotonic() < deadline, "Database lease clock did not advance"
        time.sleep(0.05)
    before = fingerprint(env)
    replay = apply_notice(env, notice, epoch=allowed.access_epoch - 1)
    assert replay.replayed and not replay.changed and replay.effective_permissions == []
    assert replay.expires_at == notice.valid_until and fingerprint(env) == before
    assert not env.recall(2, scope_ids=[str(env.scopes[0])]).json()["items"]
    assert env.recall().json()["items"][0]["memory_id"] == memory


@pytest.mark.integration
@pytest.mark.parametrize("transport", ["asgi", "http"])
def test_native_reader_lease_revocation_and_checkpoint_gating_preserve_separate_maintenance(
    env, api_process, transport
):
    source = env.observe(content="PRIVATE_SOURCE_RESULT Gold").json()["memory_id"]
    checkpoint = env.client.post(
        "/v1/checkpoints",
        headers=env.headers(),
        json={
            "scope_id": str(env.scopes[0]),
            "run_id": str(uuid4()),
            "branch_id": str(uuid4()),
            "expected_head": None,
            "harness_id": "source-access-test",
            "harness_version": "1",
            "event_watermark": 1,
            "state": {"goal": "PRIVATE_SOURCE_RESULT"},
            "memory_refs": [{"memory_id": source}],
        },
    )
    assert checkpoint.status_code == 201, checkpoint.text
    checkpoint_id = checkpoint.json()["checkpoint_id"]
    own = env.observe(index=2, content="Independent task context").json()["memory_id"]
    bind(env)
    context = (
        api_process("source-access-http.log")
        if transport == "http"
        else nullcontext((env.client, None))
    )
    with context as (http, _):
        recall_body = {
            "scope_ids": [str(env.scopes[0]), str(env.scopes[2])],
            "purpose": "source-access-test",
            "query": "",
            "token_budget": 8000,
        }
        allowed = apply_notice(env, allow_notice(env))
        read = http.post("/v1/recall", json=recall_body, headers=env.headers(2))
        assert read.status_code == 200, read.text
        assert {item["memory_id"] for item in read.json()["items"]} == {source, own}
        checkpoint_path = "/v1/checkpoints/" + checkpoint_id
        assert http.get(checkpoint_path, headers=env.headers(2)).status_code == 200
        denied = apply_notice(env, deny_notice(reason="unavailable"), epoch=allowed.access_epoch)
        assert denied.effective_permissions == []
        hidden = http.post("/v1/recall", json=recall_body, headers=env.headers(2))
        assert hidden.status_code == 200
        assert {item["memory_id"] for item in hidden.json()["items"]} == {own}
        assert "PRIVATE_SOURCE_RESULT" not in hidden.text
        assert http.get(checkpoint_path, headers=env.headers(2)).status_code == 404
        assert http.get(checkpoint_path, headers=env.headers()).status_code == 200
        assert http.post(
            "/v1/explain", json={"memory_id": source}, headers=env.headers()
        ).status_code == 200
        assert http.post(
            "/v1/explain", json={"memory_id": source}, headers=env.headers(2)
        ).status_code == 404
        assert http.post(
            "/v1/source-access", json={"decision": "allow"}, headers=env.headers(2)
        ).status_code == 404
        fresh = http.post(
            "/v1/observe",
            headers=env.headers(2),
            json={
                "scope_id": str(env.scopes[2]),
                "source_namespace": "source-access-test",
                "source_event_id": str(uuid4()),
                "occurred_at": "2026-09-23T00:00:00Z",
                "content": "Own task is still writable",
                "consent_reference": "synthetic-consent",
            },
        )
        assert fresh.status_code == 201, fresh.text


@pytest.mark.integration
def test_actual_source_cli_bind_get_apply_replay_conflict_and_redacted_database_error(
    env, notice_file
):
    bound = cli(
        "bind",
        "--expected-access-epoch",
        "1",
        "--source-system",
        SOURCE["source_system"],
        "--dataset-id",
        SOURCE["dataset_id"],
        "--source-subject",
        SOURCE["source_subject"],
        env=env,
    )
    assert bound.returncode == 0 and bound.stderr == "", bound.stdout + bound.stderr
    epoch = json.loads(bound.stdout)["access_epoch"]
    assert json.loads(bound.stdout)["reason"] == "bound"
    current = cli("get", env=env)
    assert current.returncode == 0 and current.stderr == ""
    assert json.loads(current.stdout)["source"] == SOURCE
    data = allow_notice(env).model_dump_json().encode()
    notice_file.write_bytes(data + b" " * (32768 - len(data)))
    assert notice_file.stat().st_size == 32768
    arguments = ["--expected-access-epoch", str(epoch), "--notice-file", str(notice_file)]
    allowed = cli("apply", *arguments, env=env)
    assert allowed.returncode == 0 and allowed.stderr == ""
    payload = json.loads(allowed.stdout)
    assert payload["effective_permissions"] == ["read"]
    assert payload["source_authorization_verified"] is False
    equivalent = json.loads(data)
    offset = timezone(timedelta(hours=9))
    for field in ("verified_at", "valid_until"):
        equivalent[field] = datetime.fromisoformat(equivalent[field]).astimezone(offset).isoformat()
        assert equivalent[field].endswith("+09:00")
    equivalent["source"] = {
        key: f" \t{value}\r\n " for key, value in equivalent["source"].items()
    }
    equivalent["acl_version"] = f" \t{equivalent['acl_version']}\r\n "
    notice_file.write_text(json.dumps(equivalent))
    before = fingerprint(env)
    replay = cli("apply", *arguments, env=env)
    assert replay.returncode == 0 and replay.stderr == ""
    duplicate = json.loads(replay.stdout)
    assert duplicate["replayed"] and not duplicate["changed"]
    assert duplicate["access_epoch"] == payload["access_epoch"]
    assert duplicate["expires_at"] == payload["expires_at"]
    assert fingerprint(env) == before
    notice_file.write_text(deny_notice().model_dump_json())
    conflict = cli("apply", *arguments, env=env)
    assert conflict.returncode == 1 and conflict.stderr == ""
    assert json.loads(conflict.stdout)["error"] == {
        "code": "access_epoch_conflict",
        "outcome_unknown": False,
    }
    runtime = cli("get", env=env, url=env.settings.database_url)
    assert runtime.returncode == 1 and runtime.stderr == ""
    assert json.loads(runtime.stdout)["error"]["code"] == "admin_role_required"
    unavailable = cli(
        "get",
        env=env,
        url="postgresql://DO_NOT_ECHO:DO_NOT_ECHO@127.0.0.1:1/DO_NOT_ECHO",
    )
    assert unavailable.returncode == 1
    assert json.loads(unavailable.stdout)["error"] == {
        "code": "admin_database_unavailable",
        "outcome_unknown": False,
    }
    assert "DO_NOT_ECHO" not in unavailable.stdout + unavailable.stderr
    assert "Traceback" not in unavailable.stderr
