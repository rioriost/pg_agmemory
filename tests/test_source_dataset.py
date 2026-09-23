import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory import source_dataset as coordinator
from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access
from pg_agmemory.source_access import (
    SourceAccessRequest,
    SourceDatasetIdentity,
    SourceIdentity,
    SourceNotice,
    source_access,
)
from pg_agmemory.source_dataset import (
    MAX_DATASET_TARGETS,
    SourceDatasetRequest,
    source_dataset,
)

DATASET = SourceDatasetIdentity(source_system="synthetic-warehouse", dataset_id="contracts")
UNREACHABLE_URL = (
    "host=127.0.0.1 port=1 dbname=DO_NOT_ECHO user=DO_NOT_ECHO password=DO_NOT_ECHO"
)


@dataclass(frozen=True)
class Reader:
    tenant_id: UUID
    scope_id: UUID
    principal_id: UUID
    source: SourceIdentity


def request(env, operation="get", **changes):
    return SourceDatasetRequest.model_validate(
        {
            "operation": operation,
            "tenant_id": env.tenants[0],
            "dataset": DATASET,
            **changes,
        }
    )


def execute(env, operation="get", **changes):
    with source_dataset(env.admin_url, request(env, operation, **changes)) as result:
        return result


def revoke(env, discovered, **changes):
    return execute(
        env,
        "revoke",
        **(
            {
                "tenant_id": discovered.tenant_id,
                "dataset": discovered.dataset,
                "expected_access_epoch": discovered.access_epoch,
                "expected_target_digest": discovered.target_digest,
            }
            | changes
        ),
    )


def epoch(env, tenant_id=None):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute(
            "SELECT access_epoch FROM memory.tenant WHERE id=%s",
            (tenant_id or env.tenants[0],),
        ).fetchone()[0]


def source_command(env, reader, operation="get", **changes):
    value = SourceAccessRequest(
        operation=operation,
        tenant_id=reader.tenant_id,
        scope_id=reader.scope_id,
        principal_id=reader.principal_id,
        **changes,
    )
    with source_access(env.admin_url, value) as result:
        return result


def scope_command(env, reader, operation="get", **changes):
    value = ScopeAccessRequest(
        operation=operation,
        tenant_id=reader.tenant_id,
        scope_id=reader.scope_id,
        principal_id=reader.principal_id,
        **changes,
    )
    with scope_access(env.admin_url, value) as result:
        return result


def bind_reader(
    env, *, tenant_id=None, scope_id=None, principal_id=None, dataset=DATASET, subject=None
):
    tenant_id = tenant_id or env.tenants[0]
    with psycopg.connect(env.admin_url) as conn:
        if scope_id is None:
            scope_id = uuid4()
            conn.execute(
                "INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)", (tenant_id, scope_id)
            )
        if principal_id is None:
            principal_id = uuid4()
            conn.execute(
                "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
                (tenant_id, principal_id, str(uuid4())),
            )
    reader = Reader(
        tenant_id,
        scope_id,
        principal_id,
        SourceIdentity(**dataset.model_dump(), source_subject=subject or str(uuid4())),
    )
    source_command(
        env,
        reader,
        "bind",
        source=reader.source,
        expected_access_epoch=epoch(env, tenant_id),
    )
    return reader


def allow_reader(env, reader, sequence=1):
    with psycopg.connect(env.admin_url) as conn:
        now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    notice = SourceNotice(
        source=reader.source,
        sequence=sequence,
        decision="allow",
        reason="authorized",
        acl_version=f"acl-{sequence}",
        verified_at=now - timedelta(seconds=1),
        valid_until=now + timedelta(seconds=240),
    )
    source_command(
        env, reader, "apply", notice=notice, expected_access_epoch=epoch(env, reader.tenant_id)
    )
    return notice


def allowed_reader(env, **changes):
    reader = bind_reader(env, **changes)
    allow_reader(env, reader)
    return reader


def fingerprint(env):
    with psycopg.connect(env.admin_url) as conn:
        result = {
            "epochs": conn.execute(
                "SELECT id,access_epoch FROM memory.tenant WHERE id=ANY(%s) ORDER BY id",
                (env.tenants,),
            ).fetchall()
        }
        for table in (
            "memory.scope_member",
            "memory_ops.scope_access_event",
            "memory_ops.source_access_state",
            "memory_ops.source_access_event",
        ):
            result[table] = conn.execute(
                psycopg.sql.SQL(
                    "SELECT to_jsonb(t) FROM {} t "
                    "WHERE tenant_id=ANY(%s) ORDER BY to_jsonb(t)::text"
                ).format(psycopg.sql.Identifier(*table.split("."))),
                (env.tenants,),
            ).fetchall()
        return result


def audit_rows(env):
    with psycopg.connect(env.admin_url) as conn:
        return [
            row[0]
            for row in conn.execute(
                """SELECT to_jsonb(e) FROM memory_ops.scope_access_event e
                   WHERE tenant_id=%s ORDER BY access_epoch""",
                (env.tenants[0],),
            ).fetchall()
        ]


def target_key(target):
    return (target.scope_id, target.principal_id, target.source_subject)


def reader_key(reader):
    return (reader.scope_id, reader.principal_id, reader.source.source_subject)


def expected_digest(tenant_id, dataset, readers):
    payload = {
        "format": "pgag-source-dataset-targets-v1",
        "tenant_id": str(tenant_id),
        "dataset": dataset.model_dump(mode="json"),
        "targets": [
            {"scope_id": str(scope), "principal_id": str(principal), "source_subject": subject}
            for scope, principal, subject in sorted(reader_key(reader) for reader in readers)
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def assert_limitations(result):
    assert result.coverage == "registered_readers_only"
    assert result.source_authorization_verified is False
    assert result.source_notices_changed is False
    assert result.physical_purge is False
    assert result.durable_dataset_block is False


def forbid_revoke(monkeypatch):
    original = coordinator.apply_scope_access

    def guarded(conn, value, **kwargs):
        assert value.operation != "revoke", "Batch reached a write before checking expectations"
        return original(conn, value, **kwargs)

    monkeypatch.setattr(coordinator, "apply_scope_access", guarded)


def cli_arguments(operation="get", *, tenant_id=None):
    return [
        operation,
        "--tenant-id",
        str(tenant_id or uuid4()),
        "--source-system",
        DATASET.source_system,
        "--dataset-id",
        DATASET.dataset_id,
    ]


def cli(*arguments, url=None):
    variables = {"PATH": os.environ["PATH"]}
    if url is not None:
        variables["PGAG_ADMIN_DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "source-dataset", *arguments],
        env=variables,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("field", ["source_system", "dataset_id"])
@pytest.mark.parametrize(
    "value", ["", " \t\r\n ", "x" * 257, None, 1, True, "bad\x00text", "bad\ud800text"]
)
def test_dataset_identity_rejects_unbounded_or_non_database_text(field, value):
    with pytest.raises(ValidationError):
        SourceDatasetIdentity.model_validate(DATASET.model_dump() | {field: value})


def test_dataset_identity_is_shared_normalized_and_frozen():
    value = SourceDatasetIdentity(
        source_system=" \twarehouse / 東京 ", dataset_id="\n dataset \r"
    )
    assert value.model_dump() == {"source_system": "warehouse / 東京", "dataset_id": "dataset"}
    assert SourceDatasetIdentity(source_system="x" * 256, dataset_id="y" * 256)
    source = SourceIdentity(**value.model_dump(), source_subject=" \treader\r\n ")
    assert isinstance(source, SourceDatasetIdentity)
    assert source.source_subject == "reader"
    for model in (value, source):
        with pytest.raises(ValidationError):
            model.dataset_id = "different"
    with pytest.raises(ValidationError):
        SourceDatasetIdentity(**DATASET.model_dump(), source_subject="not-a-dataset-field")


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "bind"},
        {"operation": "revoke"},
        {"operation": "revoke", "expected_access_epoch": 1},
        {"operation": "revoke", "expected_target_digest": "a" * 64},
        {"expected_access_epoch": 1},
        {"expected_target_digest": "a" * 64},
        {"dataset": None},
        {"dataset": DATASET.model_dump() | {"source_subject": "DO_NOT_ECHO"}},
        {"tenant_id": "DO_NOT_ECHO"},
        {"scope_id": str(uuid4())},
        {"permissions": ["admin"]},
        {"source_authorization_verified": True},
        {"physical_purge": True},
    ],
)
def test_request_requires_exact_operation_arguments(changes):
    with pytest.raises(ValidationError):
        SourceDatasetRequest.model_validate(
            {"operation": "get", "tenant_id": uuid4(), "dataset": DATASET, **changes}
        )


@pytest.mark.parametrize("value", [0, -1, MAX_EPOCH + 1, True, "1", 1.0])
def test_revoke_epoch_is_strict_and_bounded(value):
    with pytest.raises(ValidationError):
        SourceDatasetRequest(
            operation="revoke",
            tenant_id=uuid4(),
            dataset=DATASET,
            expected_access_epoch=value,
            expected_target_digest="a" * 64,
        )


@pytest.mark.parametrize(
    "value",
    ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 64, "a" * 64 + "\n",
     b"a" * 64, 1, True],
)
def test_revoke_digest_is_exact_strict_lowercase_sha256(value):
    with pytest.raises(ValidationError):
        SourceDatasetRequest(
            operation="revoke",
            tenant_id=uuid4(),
            dataset=DATASET,
            expected_access_epoch=1,
            expected_target_digest=value,
        )


@pytest.mark.parametrize(
    "mutation", ["operation", "epoch", "digest", "tenant", "nested", "mapping", "get-expectations"]
)
def test_constructed_and_mutated_models_are_redacted_before_database_access(monkeypatch, mutation):
    value = SourceDatasetRequest(
        operation="revoke",
        tenant_id=uuid4(),
        dataset=DATASET,
        expected_access_epoch=1,
        expected_target_digest="a" * 64,
    )
    changes = {
        "operation": {"operation": "DO_NOT_ECHO"},
        "epoch": {"expected_access_epoch": True},
        "digest": {"expected_target_digest": "DO_NOT_ECHO"},
        "tenant": {"tenant_id": "DO_NOT_ECHO"},
        "nested": {
            "dataset": SourceDatasetIdentity.model_construct(
                source_system="DO_NOT_ECHO\x00", dataset_id=DATASET.dataset_id
            )
        },
        "mapping": {"dataset": {"source_system": "DO_NOT_ECHO"}},
        "get-expectations": {"operation": "get"},
    }[mutation]
    if mutation in ("tenant", "nested", "mapping"):
        value = SourceDatasetRequest.model_construct(**(value.model_dump() | changes))
    else:
        value = value.model_copy(update=changes)

    def no_database(*args, **kwargs):
        pytest.fail("Invalid dataset request attempted a database connection")

    monkeypatch.setattr(psycopg, "connect", no_database)
    with pytest.raises(AdminError, match="^invalid_source_dataset_request$") as failure:
        with source_dataset(UNREACHABLE_URL, value):
            pytest.fail("Unvalidated dataset request was accepted")
    assert not failure.value.outcome_unknown and "DO_NOT_ECHO" not in str(failure.value)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["unknown-DO_NOT_ECHO"],
        ["get", "--tenant-id", "DO_NOT_ECHO"],
        cli_arguments() + ["--source-subject", "DO_NOT_ECHO"],
        cli_arguments() + ["--expected-access-epoch", "1"],
        cli_arguments() + ["--expected-target-digest", "a" * 64],
        cli_arguments("revoke"),
        cli_arguments("revoke") + ["--expected-access-epoch", "1"],
        cli_arguments("revoke") + ["--expected-target-digest", "a" * 64],
        cli_arguments("revoke")
        + ["--expected-access-epoch", "1", "--expected-target-digest", "DO_NOT_ECHO"],
        cli_arguments() + ["--dataset-id", "DO_NOT_ECHO" * 100],
    ],
)
def test_cli_argument_errors_are_redacted_without_opening_database(arguments):
    result = cli(*arguments, url=UNREACHABLE_URL)
    assert result.returncode == 2 and result.stdout == ""
    assert "invalid_source_dataset_arguments" in result.stderr
    assert "DO_NOT_ECHO" not in result.stderr and "Traceback" not in result.stderr
    assert "admin_database_unavailable" not in result.stderr


@pytest.mark.parametrize("url", [None, "", " \t "])
def test_cli_requires_explicit_admin_database_url(url):
    result = cli(*cli_arguments(), url=url)
    assert result.returncode == 2 and result.stdout == ""
    assert "PGAG_ADMIN_DATABASE_URL is required" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_help_does_not_import_optional_sdk_or_httpx():
    script = """
import importlib.abc
import sys

class NoOptionalClient(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith(("httpx.", "pg_agmemory.sdk")):
            raise AssertionError("dataset administration imported an optional client")

sys.meta_path.insert(0, NoOptionalClient())
from pg_agmemory.cli import main
sys.argv = ["pg-agmemory", "source-dataset", "--help"]
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "--expected-target-digest" in result.stdout
    assert "--expected-access-epoch" in result.stdout


@pytest.mark.parametrize("outcome_unknown", [False, True])
def test_cli_preserves_admin_error_outcome_and_never_retries(monkeypatch, capsys, outcome_unknown):
    attempts = []

    def failed(url, value):
        attempts.append(value)
        raise AdminError("admin_database_unavailable", outcome_unknown=outcome_unknown)

    monkeypatch.setattr(coordinator, "source_dataset", failed)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", UNREACHABLE_URL)
    with pytest.raises(SystemExit) as failure:
        coordinator.main(
            cli_arguments("revoke")
            + ["--expected-access-epoch", "1", "--expected-target-digest", "a" * 64]
        )
    output = capsys.readouterr()
    assert failure.value.code == 1 and len(attempts) == 1 and output.err == ""
    assert json.loads(output.out) == {
        "error": {"code": "admin_database_unavailable", "outcome_unknown": outcome_unknown}
    }
    assert "DO_NOT_ECHO" not in output.out


@pytest.mark.integration
def test_discovery_is_complete_sorted_exact_and_revoke_isolated(env):
    first = allowed_reader(env, scope_id=env.scopes[0], principal_id=env.principals[2])
    second = allowed_reader(env, scope_id=first.scope_id, subject=first.source.source_subject)
    third = bind_reader(env, principal_id=first.principal_id)
    fourth = bind_reader(env, scope_id=third.scope_id)
    source_command(
        env,
        fourth,
        "apply",
        expected_access_epoch=epoch(env),
        notice=SourceNotice(
            source=fourth.source, sequence=1, decision="deny", reason="unavailable"
        ),
    )
    unrelated = [
        allowed_reader(
            env,
            dataset=SourceDatasetIdentity(**(DATASET.model_dump() | {"dataset_id": "contracts2"})),
        ),
        allowed_reader(
            env,
            dataset=SourceDatasetIdentity(
                **(DATASET.model_dump() | {"source_system": "synthetic-warehouse2"})
            ),
        ),
        allowed_reader(env, tenant_id=env.tenants[1]),
    ]
    before = fingerprint(env)
    discovered = execute(env)
    assert discovered.operation == "get" and discovered.tenant_id == env.tenants[0]
    assert discovered.dataset == DATASET and discovered.access_epoch == epoch(env)
    assert discovered.matched_targets == 4 and discovered.changed_targets == 0
    assert [target_key(target) for target in discovered.targets] == sorted(
        reader_key(reader) for reader in (first, second, third, fourth)
    )
    assert re.fullmatch("[0-9a-f]{64}", discovered.target_digest)
    assert discovered.target_digest == expected_digest(
        env.tenants[0], DATASET, [first, second, third, fourth]
    )
    assert_limitations(discovered)
    by_key = {target_key(target): target for target in discovered.targets}
    for reader, sequence, decision, reason, membership in (
        (first, 1, "allow", "authorized", True),
        (second, 1, "allow", "authorized", True),
        (third, 0, "deny", "bound", False),
        (fourth, 1, "deny", "unavailable", False),
    ):
        target = by_key[reader_key(reader)]
        assert (target.sequence, target.decision, target.reason) == (sequence, decision, reason)
        assert target.membership_exists is membership and not target.changed
        assert target.permissions == target.effective_permissions == (
            ["read"] if membership else []
        )
        assert (target.expires_at is not None) is membership
        assert target.evaluated_at.tzinfo is not None
        assert "source" not in target.model_dump()
    assert fingerprint(env) == before

    changed = revoke(env, discovered)
    assert changed.operation == "revoke" and changed.changed_targets == 2
    assert changed.matched_targets == 4 and changed.access_epoch == discovered.access_epoch + 2
    assert changed.target_digest == discovered.target_digest
    assert [target_key(target) for target in changed.targets] == list(by_key)
    assert {target_key(target) for target in changed.targets if target.changed} == {
        reader_key(first), reader_key(second)
    }
    for target in changed.targets:
        assert not target.membership_exists
        assert target.permissions == target.effective_permissions == []
        assert target.expires_at is None
        previous = by_key[target_key(target)]
        assert (target.sequence, target.decision, target.reason) == (
            previous.sequence, previous.decision, previous.reason
        )
    assert_limitations(changed)
    after = fingerprint(env)
    for table in ("memory_ops.source_access_state", "memory_ops.source_access_event"):
        assert after[table] == before[table]
    assert dict(after["epochs"])[env.tenants[1]] == dict(before["epochs"])[env.tenants[1]]
    deleted = {
        (str(reader.tenant_id), str(reader.scope_id), str(reader.principal_id))
        for reader in (first, second)
    }
    assert after["memory.scope_member"] == [
        row for row in before["memory.scope_member"]
        if (row[0]["tenant_id"], row[0]["scope_id"], row[0]["principal_id"]) not in deleted
    ]
    events = audit_rows(env)[-2:]
    assert [event["access_epoch"] for event in events] == [
        discovered.access_epoch + 1, discovered.access_epoch + 2
    ]
    assert {(event["scope_id"], event["principal_id"]) for event in events} == {
        (str(first.scope_id), str(first.principal_id)),
        (str(second.scope_id), str(second.principal_id)),
    }
    with psycopg.connect(env.admin_url) as conn:
        actor = conn.execute("SELECT current_user").fetchone()[0]
    assert all(
        event["operation"] == "revoke"
        and event["database_role"] == actor
        and event["previous_permissions"] == ["read"]
        and event["previous_expires_at"] is not None
        and event["permissions"] is None
        and event["expires_at"] is None
        for event in events
    )
    for reader in unrelated:
        assert source_command(env, reader).effective_permissions == ["read"]
    assert len(after["memory_ops.scope_access_event"]) == (
        len(before["memory_ops.scope_access_event"]) + 2
    )


@pytest.mark.integration
def test_unknown_tenant_and_unbound_dataset_are_distinct_and_read_only(env):
    before = fingerprint(env)
    for operation in ("get", "revoke"):
        arguments = (
            {"expected_access_epoch": 1, "expected_target_digest": "a" * 64}
            if operation == "revoke" else {}
        )
        with pytest.raises(AdminError, match="^source_dataset_not_bound$"):
            execute(env, operation, **arguments)
        with pytest.raises(AdminError, match="^not_found$"):
            execute(env, operation, tenant_id=uuid4(), **arguments)
    assert fingerprint(env) == before


@pytest.mark.integration
def test_normalized_constructed_request_is_revalidated_before_lookup(env):
    bind_reader(env)
    discovered = execute(env)
    constructed = SourceDatasetRequest.model_construct(
        operation="get",
        tenant_id=env.tenants[0],
        dataset=SourceDatasetIdentity.model_construct(
            source_system=f" \t{DATASET.source_system}\r\n",
            dataset_id=f"\n{DATASET.dataset_id} ",
        ),
    )
    with source_dataset(env.admin_url, constructed) as found:
        assert found.dataset == DATASET
        assert found.matched_targets == 1
        assert found.target_digest == discovered.target_digest


@pytest.mark.integration
def test_digest_uses_canonical_domain_tenant_dataset_and_all_target_identity_fields(env):
    dataset = SourceDatasetIdentity(
        source_system="warehouse / 東京", dataset_id='contracts"\n版'
    )
    readers = [
        bind_reader(env, dataset=dataset, subject=" lecteur / 日本 "),
        bind_reader(env, dataset=dataset, subject='a"b\\c'),
    ]
    found = execute(env, dataset=dataset)
    assert found.target_digest == expected_digest(env.tenants[0], dataset, readers)
    assert {target.source_subject for target in found.targets} == {"lecteur / 日本", 'a"b\\c'}
    with psycopg.connect(env.admin_url) as conn:
        for reader in readers:
            conn.execute(
                "INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)",
                (env.tenants[1], reader.scope_id),
            )
            conn.execute(
                "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
                (env.tenants[1], reader.principal_id, str(uuid4())),
            )
    for reader in readers:
        bind_reader(
            env,
            tenant_id=env.tenants[1],
            scope_id=reader.scope_id,
            principal_id=reader.principal_id,
            dataset=dataset,
            subject=reader.source.source_subject,
        )
    other_tenant = execute(env, tenant_id=env.tenants[1], dataset=dataset)
    assert [target_key(target) for target in other_tenant.targets] == [
        target_key(target) for target in found.targets
    ]
    assert other_tenant.access_epoch == found.access_epoch
    assert other_tenant.target_digest == expected_digest(env.tenants[1], dataset, readers)
    assert other_tenant.target_digest != found.target_digest


@pytest.mark.integration
def test_digest_survives_notice_cursor_grant_epoch_and_clock_changes(env):
    reader = bind_reader(env)
    initial = execute(env)
    source_command(
        env,
        reader,
        "apply",
        expected_access_epoch=initial.access_epoch,
        notice=SourceNotice(source=reader.source, sequence=1, decision="deny", reason="revoked"),
    )
    denied = execute(env)
    assert denied.access_epoch == initial.access_epoch
    assert denied.targets[0].sequence == 1
    allow_reader(env, reader, sequence=2)
    allowed = execute(env)
    assert allowed.access_epoch == initial.access_epoch + 1
    assert allowed.targets[0].effective_permissions == ["read"]
    assert allowed.targets[0].evaluated_at > initial.targets[0].evaluated_at
    scope_command(env, reader, "revoke", expected_access_epoch=allowed.access_epoch)
    manual = execute(env)
    assert manual.access_epoch == allowed.access_epoch + 1
    assert not manual.targets[0].membership_exists
    assert {value.target_digest for value in (initial, denied, allowed, manual)} == {
        initial.target_digest
    }
    assert manual.targets[0].decision == "allow"


@pytest.mark.integration
def test_stale_epoch_is_checked_before_noop_or_any_batch_write(env, monkeypatch):
    reader = bind_reader(env)
    discovered = execute(env)
    maintenance = Reader(env.tenants[0], env.scopes[0], env.principals[0], reader.source)
    scope_command(
        env,
        maintenance,
        "set",
        expected_access_epoch=discovered.access_epoch,
        permissions=("admin",),
        no_expiry=True,
    )
    assert execute(env).target_digest == discovered.target_digest
    before = fingerprint(env)
    forbid_revoke(monkeypatch)
    with pytest.raises(AdminError, match="^access_epoch_conflict$") as failure:
        revoke(env, discovered)
    assert not failure.value.outcome_unknown and fingerprint(env) == before


@pytest.mark.integration
def test_new_denied_binding_changes_digest_without_epoch_and_prevents_partial_revoke(
    env, monkeypatch
):
    allowed_reader(env)
    discovered = execute(env)
    new_reader = bind_reader(env)
    current = execute(env)
    assert current.access_epoch == discovered.access_epoch
    assert current.matched_targets == discovered.matched_targets + 1
    assert current.target_digest != discovered.target_digest
    added = next(
        target for target in current.targets if target_key(target) == reader_key(new_reader)
    )
    assert added.decision == "deny" and not added.membership_exists
    before = fingerprint(env)
    with monkeypatch.context() as patch:
        forbid_revoke(patch)
        with pytest.raises(AdminError, match="^source_dataset_target_conflict$") as failure:
            revoke(env, discovered)
    assert not failure.value.outcome_unknown and fingerprint(env) == before
    final = revoke(env, current)
    assert final.changed_targets == 1 and final.access_epoch == current.access_epoch + 1


@pytest.mark.integration
@pytest.mark.parametrize("bad_digest", ["0" * 64, "f" * 64])
def test_wrong_target_digest_is_checked_even_when_all_memberships_are_absent(
    env, monkeypatch, bad_digest
):
    bind_reader(env)
    discovered = execute(env)
    assert discovered.target_digest != bad_digest
    before = fingerprint(env)
    forbid_revoke(monkeypatch)
    with pytest.raises(AdminError, match="^source_dataset_target_conflict$"):
        revoke(env, discovered, expected_target_digest=bad_digest)
    assert fingerprint(env) == before


def seed_bounded_readers(env, count):
    readers = [
        Reader(
            env.tenants[0],
            env.scopes[0],
            uuid4(),
            SourceIdentity(**DATASET.model_dump(), source_subject=f"cap-reader-{index}"),
        )
        for index in range(count)
    ]
    with psycopg.connect(env.admin_url) as conn, conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
            [(reader.tenant_id, reader.principal_id, str(uuid4())) for reader in readers],
        )
        cursor.executemany(
            """INSERT INTO memory_ops.source_access_state
               (tenant_id,scope_id,principal_id,source_system,dataset_id,source_subject,
                sequence,decision,reason)
               VALUES (%s,%s,%s,%s,%s,%s,0,'deny','bound')""",
            [
                (
                    reader.tenant_id, reader.scope_id, reader.principal_id,
                    reader.source.source_system, reader.source.dataset_id,
                    reader.source.source_subject,
                )
                for reader in readers
            ],
        )
        cursor.executemany(
            """INSERT INTO memory_ops.source_access_event
               (tenant_id,scope_id,principal_id,sequence,decision,reason,access_epoch)
               VALUES (%s,%s,%s,0,'deny','bound',1)""",
            [(reader.tenant_id, reader.scope_id, reader.principal_id) for reader in readers],
        )
        cursor.executemany(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read'])""",
            [(reader.tenant_id, reader.scope_id, reader.principal_id) for reader in readers],
        )
    return readers


@pytest.mark.integration
@pytest.mark.parametrize("count", [100, 101, 107])
def test_discovery_cap_is_measurably_bounded_and_never_applies_a_partial_batch(
    env, monkeypatch, count
):
    assert MAX_DATASET_TARGETS == 100
    readers = seed_bounded_readers(env, count)
    before = fingerprint(env)
    original_execute = psycopg.Connection.execute
    original_apply = coordinator.apply_scope_access
    selected_counts, applied = [], []

    def measured(conn, query, *args, **kwargs):
        cursor = original_execute(conn, query, *args, **kwargs)
        columns = {column.name for column in cursor.description or ()}
        if {"scope_id", "principal_id", "source_subject"} <= columns:
            selected_counts.append(cursor.rowcount)
        return cursor

    def measured_apply(conn, value, **kwargs):
        applied.append(value.operation)
        return original_apply(conn, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", measured)
        patch.setattr(coordinator, "apply_scope_access", measured_apply)
        if count == MAX_DATASET_TARGETS:
            discovered = execute(env)
            assert discovered.matched_targets == len(discovered.targets) == 100
            assert [target_key(target) for target in discovered.targets] == sorted(
                reader_key(reader) for reader in readers
            )
            changed = revoke(env, discovered)
            assert changed.matched_targets == changed.changed_targets == 100
            assert changed.access_epoch == discovered.access_epoch + 100
            assert all(
                target.changed and not target.membership_exists for target in changed.targets
            )
        else:
            for operation in ("get", "revoke"):
                arguments = (
                    {"expected_access_epoch": 1, "expected_target_digest": "a" * 64}
                    if operation == "revoke" else {}
                )
                with pytest.raises(AdminError, match="^source_dataset_target_limit$") as failure:
                    execute(env, operation, **arguments)
                assert not failure.value.outcome_unknown
            assert applied == []
    assert selected_counts == [min(count, 101), min(count, 101)]
    after = fingerprint(env)
    if count > MAX_DATASET_TARGETS:
        assert after == before
    else:
        assert len(audit_rows(env)) == 100
        for table in ("memory_ops.source_access_state", "memory_ops.source_access_event"):
            assert after[table] == before[table]


@pytest.mark.integration
@pytest.mark.parametrize("failure_mode", ["later-database-error", "epoch-exhaustion"])
def test_later_target_failure_rolls_back_every_membership_audit_and_epoch(
    env, monkeypatch, failure_mode
):
    readers = [allowed_reader(env), allowed_reader(env)]
    if failure_mode == "epoch-exhaustion":
        with psycopg.connect(env.admin_url) as conn:
            conn.execute(
                "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s",
                (MAX_EPOCH - 1, env.tenants[0]),
            )
    discovered = execute(env)
    before = fingerprint(env)
    original = coordinator.apply_scope_access
    attempts = []

    def failed(conn, value, **kwargs):
        if value.operation == "revoke":
            attempts.append(value)
            if len(attempts) == 2 and failure_mode == "later-database-error":
                first = attempts[0]
                assert conn.execute(
                    """SELECT count(*) AS remaining FROM memory.scope_member
                       WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                    (first.tenant_id, first.scope_id, first.principal_id),
                ).fetchone()["remaining"] == 0
                raise psycopg.IntegrityError("DO_NOT_ECHO later target failure")
        return original(conn, value, **kwargs)

    code = (
        "admin_database_error"
        if failure_mode == "later-database-error" else "access_epoch_exhausted"
    )
    with monkeypatch.context() as patch:
        patch.setattr(coordinator, "apply_scope_access", failed)
        with pytest.raises(AdminError, match=f"^{code}$") as failure:
            revoke(env, discovered)
    assert not failure.value.outcome_unknown and "DO_NOT_ECHO" not in str(failure.value)
    assert [value.expected_access_epoch for value in attempts] == [
        discovered.access_epoch, discovered.access_epoch + 1
    ]
    assert [(value.scope_id, value.principal_id) for value in attempts] == sorted(
        (reader.scope_id, reader.principal_id) for reader in readers
    )
    assert fingerprint(env) == before
    assert all(target.membership_exists for target in execute(env).targets)
    if failure_mode == "later-database-error":
        assert revoke(env, discovered).changed_targets == 2


@pytest.mark.integration
def test_manual_and_expired_memberships_are_removed_but_absent_targets_are_noops(env):
    manual, expired, absent = [bind_reader(env) for _ in range(3)]
    with psycopg.connect(env.admin_url) as conn:
        for reader, expiry in ((manual, None), (expired, "2000-01-01T00:00:00Z")):
            conn.execute(
                """INSERT INTO memory.scope_member
                   (tenant_id,scope_id,principal_id,permissions,expires_at)
                   VALUES (%s,%s,%s,ARRAY['read'],%s)""",
                (reader.tenant_id, reader.scope_id, reader.principal_id, expiry),
            )
    discovered = execute(env)
    by_key = {target_key(target): target for target in discovered.targets}
    assert by_key[reader_key(manual)].effective_permissions == ["read"]
    expired_target = by_key[reader_key(expired)]
    assert expired_target.membership_exists and expired_target.permissions == ["read"]
    assert expired_target.effective_permissions == []
    assert expired_target.expires_at < expired_target.evaluated_at
    assert not by_key[reader_key(absent)].membership_exists
    changed = revoke(env, discovered)
    assert changed.changed_targets == 2 and changed.access_epoch == discovered.access_epoch + 2
    assert {target_key(target) for target in changed.targets if target.changed} == {
        reader_key(manual), reader_key(expired)
    }
    assert all(target.decision == "deny" and target.reason == "bound" for target in changed.targets)
    assert len(audit_rows(env)) == 2
    before = fingerprint(env)
    noop = revoke(env, execute(env))
    assert noop.changed_targets == 0 and noop.access_epoch == changed.access_epoch
    assert all(not target.changed for target in noop.targets)
    assert fingerprint(env) == before
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s", (MAX_EPOCH, env.tenants[0])
        )
    before = fingerprint(env)
    exhausted_noop = revoke(env, execute(env))
    assert exhausted_noop.access_epoch == MAX_EPOCH and exhausted_noop.changed_targets == 0
    assert fingerprint(env) == before


@pytest.mark.integration
def test_runtime_role_cannot_discover_or_revoke_dataset(env):
    allowed_reader(env)
    discovered = execute(env)
    before = fingerprint(env)
    for operation in ("get", "revoke"):
        changes = (
            {
                "expected_access_epoch": discovered.access_epoch,
                "expected_target_digest": discovered.target_digest,
            }
            if operation == "revoke" else {}
        )
        with pytest.raises(AdminError, match="^admin_role_required$"):
            with source_dataset(env.settings.database_url, request(env, operation, **changes)):
                pytest.fail("Runtime role accepted for dataset administration")
    assert fingerprint(env) == before


@pytest.mark.integration
@pytest.mark.parametrize("competitor", ["batch", "single-target"])
def test_competing_batch_and_reference_have_one_winner_without_retry(env, competitor):
    first, second = allowed_reader(env), allowed_reader(env)
    discovered = execute(env)
    barrier = Barrier(2)

    def competing(operation):
        barrier.wait(timeout=5)
        try:
            if operation == "batch":
                return revoke(env, discovered)
            return scope_command(
                env, first, "revoke", expected_access_epoch=discovered.access_epoch
            )
        except AdminError as exc:
            return exc.code

    before = len(audit_rows(env))
    with ThreadPoolExecutor(max_workers=2) as pool:
        batch, single = list(pool.map(competing, ["batch", competitor]))
    assert [batch, single].count("access_epoch_conflict") == 1
    current = execute(env)
    targets = {target_key(target): target for target in current.targets}
    assert not targets[reader_key(first)].membership_exists
    if competitor == "batch":
        winner = single if isinstance(batch, str) else batch
        assert winner.changed_targets == 2
        assert not targets[reader_key(second)].membership_exists
        changed = 2
    elif isinstance(batch, str):
        assert single.changed and targets[reader_key(second)].membership_exists
        changed = 1
    else:
        assert batch.changed_targets == 2 and not targets[reader_key(second)].membership_exists
        changed = 2
    assert current.access_epoch == discovered.access_epoch + changed
    assert len(audit_rows(env)) == before + changed


@pytest.mark.integration
def test_output_delivery_holds_committed_barrier_against_get_and_native_reads(env):
    memory = env.observe().json()["memory_id"]
    allowed_reader(env, scope_id=env.scopes[0], principal_id=env.principals[2])
    discovered = execute(env)
    value = request(
        env,
        "revoke",
        expected_access_epoch=discovered.access_epoch,
        expected_target_digest=discovered.target_digest,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        with pytest.raises(RuntimeError, match="consumer lost output"):
            with source_dataset(env.admin_url, value) as result:
                assert result.changed_targets == 1
                with psycopg.connect(env.admin_url, autocommit=True) as conn:
                    assert conn.execute(
                        "SELECT access_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],)
                    ).fetchone()[0] == result.access_epoch
                    assert not conn.execute(
                        "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                        (str(env.tenants[0]),),
                    ).fetchone()[0]
                    assert conn.execute(
                        "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                        (str(env.tenants[1]),),
                    ).fetchone()[0]
                getting = pool.submit(execute, env)
                reading = pool.submit(env.recall, 2, scope_ids=[str(env.scopes[0])])
                deadline = time.monotonic() + 3
                while True:
                    with psycopg.connect(env.admin_url) as conn:
                        waiting = conn.execute(
                            """SELECT count(*) FROM pg_stat_activity
                               WHERE datname=current_database() AND wait_event='advisory'"""
                        ).fetchone()[0]
                    if waiting >= 2:
                        break
                    assert time.monotonic() < deadline, "Get/API did not wait for result delivery"
                    time.sleep(0.02)
                assert not getting.done() and not reading.done()
                raise RuntimeError("consumer lost output")
        assert getting.result(timeout=5).access_epoch == discovered.access_epoch + 1
        hidden = reading.result(timeout=5)
    assert hidden.status_code == 200 and hidden.json()["items"] == []
    assert env.recall().json()["items"][0]["memory_id"] == memory


@pytest.mark.integration
def test_uncertain_batch_commit_is_redacted_not_retried_and_durably_all_or_nothing(
    env, monkeypatch
):
    allowed_reader(env)
    allowed_reader(env)
    discovered = execute(env)
    before = fingerprint(env)
    original = psycopg.Connection.transaction
    commits = []

    @contextmanager
    def lost_response(conn, *args, **kwargs):
        with original(conn, *args, **kwargs) as transaction:
            yield transaction
        commits.append(True)
        raise psycopg.OperationalError("DO_NOT_ECHO after actual dataset commit")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "transaction", lost_response)
        with pytest.raises(AdminError, match="^commit_outcome_unknown$") as failure:
            revoke(env, discovered)
    assert failure.value.outcome_unknown and "DO_NOT_ECHO" not in str(failure.value)
    assert len(commits) == 1
    durable = execute(env)
    assert durable.access_epoch == discovered.access_epoch + 2
    assert all(not target.membership_exists for target in durable.targets)
    after = fingerprint(env)
    assert len(after["memory_ops.scope_access_event"]) == (
        len(before["memory_ops.scope_access_event"]) + 2
    )
    for table in ("memory_ops.source_access_state", "memory_ops.source_access_event"):
        assert after[table] == before[table]
    with pytest.raises(AdminError, match="^access_epoch_conflict$"):
        revoke(env, discovered)
    assert fingerprint(env) == after


@pytest.mark.integration
def test_real_http_multitarget_revoke_hides_native_data_without_purge_or_durable_block(
    env, api_process
):
    first = bind_reader(env, scope_id=env.scopes[0], principal_id=env.principals[2])
    second = bind_reader(env, principal_id=env.principals[2])
    maintenance = Reader(env.tenants[0], second.scope_id, env.principals[0], second.source)
    scope_command(
        env,
        maintenance,
        "set",
        expected_access_epoch=epoch(env),
        permissions=("read", "write", "delete"),
        no_expiry=True,
    )
    memories = [
        env.observe(content="PRIVATE_DATASET_FIRST Gold").json()["memory_id"],
        env.observe(
            scope_id=str(second.scope_id), content="PRIVATE_DATASET_SECOND Gold"
        ).json()["memory_id"],
    ]
    own = env.observe(index=2, content="Independent task remains writable").json()["memory_id"]
    checkpoint = env.client.post(
        "/v1/checkpoints",
        headers=env.headers(),
        json={
            "scope_id": str(first.scope_id),
            "run_id": str(uuid4()),
            "branch_id": str(uuid4()),
            "expected_head": None,
            "harness_id": "source-dataset-test",
            "harness_version": "1",
            "event_watermark": 1,
            "state": {"goal": "PRIVATE_DATASET_FIRST"},
            "memory_refs": [{"memory_id": memories[0]}],
        },
    )
    assert checkpoint.status_code == 201, checkpoint.text
    checkpoint_path = "/v1/checkpoints/" + checkpoint.json()["checkpoint_id"]
    notices = [allow_reader(env, reader) for reader in (first, second)]
    body = {
        "scope_ids": [str(first.scope_id), str(second.scope_id), str(env.scopes[2])],
        "purpose": "source-dataset-test",
        "query": "",
        "token_budget": 8000,
    }
    with api_process("source-dataset-http.log") as (http, _):
        before_read = http.post("/v1/recall", json=body, headers=env.headers(2))
        assert before_read.status_code == 200, before_read.text
        assert {item["memory_id"] for item in before_read.json()["items"]} == {*memories, own}
        assert http.get(checkpoint_path, headers=env.headers(2)).status_code == 200
        discovered = execute(env)
        source_before = fingerprint(env)
        revoked = revoke(env, discovered)
        assert revoked.changed_targets == 2 and revoked.access_epoch == discovered.access_epoch + 2
        assert all(
            target.decision == "allow" and target.reason == "authorized"
            for target in revoked.targets
        )
        assert_limitations(revoked)
        hidden = http.post("/v1/recall", json=body, headers=env.headers(2))
        assert hidden.status_code == 200
        assert {item["memory_id"] for item in hidden.json()["items"]} == {own}
        assert "PRIVATE_DATASET" not in hidden.text
        assert http.get(checkpoint_path, headers=env.headers(2)).status_code == 404
        assert http.get(checkpoint_path, headers=env.headers()).status_code == 200
        maintenance_read = http.post("/v1/recall", json=body, headers=env.headers())
        assert {item["memory_id"] for item in maintenance_read.json()["items"]} == set(memories)
        for memory in memories:
            assert http.post(
                "/v1/explain", json={"memory_id": memory}, headers=env.headers(2)
            ).status_code == 404
            assert http.post(
                "/v1/explain", json={"memory_id": memory}, headers=env.headers()
            ).status_code == 200
        for path in ("/v1/source-dataset", "/v1/source-dataset/revoke"):
            assert http.post(path, json={}, headers=env.headers(2)).status_code == 404
        fresh = http.post(
            "/v1/observe",
            headers=env.headers(2),
            json={
                "scope_id": str(env.scopes[2]),
                "source_namespace": "source-dataset-test",
                "source_event_id": str(uuid4()),
                "occurred_at": "2026-09-23T00:00:00Z",
                "content": "Separate task still accepts writes",
                "consent_reference": "synthetic-consent",
            },
        )
        assert fresh.status_code == 201, fresh.text
        after = fingerprint(env)
        for table in ("memory_ops.source_access_state", "memory_ops.source_access_event"):
            assert after[table] == source_before[table]
        for reader, notice in zip((first, second), notices, strict=True):
            replay = source_command(
                env, reader, "apply", notice=notice,
                expected_access_epoch=discovered.access_epoch,
            )
            assert replay.replayed and not replay.changed
            assert replay.sequence == 1 and replay.effective_permissions == []
        assert fingerprint(env) == after
        still_hidden = http.post("/v1/recall", json=body, headers=env.headers(2))
        assert "PRIVATE_DATASET" not in still_hidden.text
        allow_reader(env, first, sequence=2)
        reopened = http.post("/v1/recall", json=body, headers=env.headers(2))
        reopened_ids = {item["memory_id"] for item in reopened.json()["items"]}
        assert memories[0] in reopened_ids and memories[1] not in reopened_ids
        assert own in reopened_ids
        assert http.get(checkpoint_path, headers=env.headers(2)).status_code == 200
        assert execute(env).target_digest == discovered.target_digest


@pytest.mark.integration
def test_actual_cli_get_revoke_conflicts_runtime_and_database_errors(env):
    allowed_reader(env)
    allowed_reader(env)
    arguments = cli_arguments(tenant_id=env.tenants[0])
    current = cli(*arguments, url=env.admin_url)
    assert current.returncode == 0 and current.stderr == "", current.stdout + current.stderr
    discovered = json.loads(current.stdout)
    assert discovered["matched_targets"] == 2 and discovered["changed_targets"] == 0
    assert discovered["dataset"] == DATASET.model_dump()
    assert discovered["coverage"] == "registered_readers_only"
    arguments[0] = "revoke"
    mutation = arguments + [
        "--expected-access-epoch", str(discovered["access_epoch"]),
        "--expected-target-digest", discovered["target_digest"],
    ]
    changed = cli(*mutation, url=env.admin_url)
    assert changed.returncode == 0 and changed.stderr == "", changed.stdout + changed.stderr
    payload = json.loads(changed.stdout)
    assert payload["changed_targets"] == 2
    assert payload["access_epoch"] == discovered["access_epoch"] + 2
    assert payload["target_digest"] == discovered["target_digest"]
    assert all(not target["membership_exists"] for target in payload["targets"])
    for field in (
        "source_authorization_verified", "source_notices_changed", "physical_purge",
        "durable_dataset_block",
    ):
        assert payload[field] is False
    conflict = cli(*mutation, url=env.admin_url)
    assert conflict.returncode == 1 and conflict.stderr == ""
    assert json.loads(conflict.stdout) == {
        "error": {"code": "access_epoch_conflict", "outcome_unknown": False}
    }
    arguments[0] = "get"
    for url, code in (
        (env.settings.database_url, "admin_role_required"),
        (UNREACHABLE_URL, "admin_database_unavailable"),
    ):
        failed = cli(*arguments, url=url)
        assert failed.returncode == 1 and failed.stderr == ""
        assert json.loads(failed.stdout) == {"error": {"code": code, "outcome_unknown": False}}
        assert "DO_NOT_ECHO" not in failed.stdout + failed.stderr
