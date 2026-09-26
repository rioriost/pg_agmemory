"""Exact, bounded, principal-scoped observations of independently retained spaces."""

import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError
from test_deletion_history import forget, receipt, target, tombstone
from test_graphs import create_entity, create_relation
from test_operations_status import canonical_state
from test_vectors import MODEL, ids, input_for, recall, upload, upload_body, vector

from pg_agmemory import embedding_migration as migration
from pg_agmemory.admin import AdminError
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access

TARGET = {**MODEL, "revision": "fixture-v2"}


def request(env=None, index=0, **changes):
    values = {
        "tenant_id": env.tenants[1 if index == 1 else 0] if env else uuid4(),
        "principal_id": env.principals[index] if env else uuid4(),
        "scope_ids": (env.scopes[index],) if env else (uuid4(),),
        "source": migration.EmbeddingSpace(**MODEL),
        "target": migration.EmbeddingSpace(**TARGET),
        **changes,
    }
    return migration.EmbeddingMigrationRequest(**values)


def inspect(env, index=0, **changes):
    return migration.embedding_migration(env.admin_url, request(env, index, **changes))


def cli_args(env=None):
    data = request(env)
    return [
        "--tenant-id", str(data.tenant_id), "--principal-id", str(data.principal_id),
        "--scope-id", str(data.scope_ids[0]),
        "--source-name", MODEL["name"], "--source-revision", MODEL["revision"],
        "--target-name", TARGET["name"], "--target-revision", TARGET["revision"],
    ]


def revise(env, memory_id, source):
    response = env.client.post(
        f"/v1/assertions/{memory_id}/revisions",
        json={
            "expected_revision": 1, "value": "Silver", "explicit_intent": True,
            "evidence": [{"memory_id": source, "quote": "Silver"}], "reason": "fixture revision",
        },
        headers=env.headers(),
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("changes", [
    {"max_revisions": 0}, {"max_revisions": 10001}, {"max_revisions": True},
    {"max_samples": -1}, {"max_samples": 101}, {"max_samples": True},
    {"scope_ids": ()}, {"scope_ids": tuple(uuid4() for _ in range(33))},
    {"scope_ids": [uuid4()]}, {"tenant_id": str(uuid4())},
    {"as_of": datetime(2026, 1, 1)}, {"known_at": datetime(2026, 1, 1)},
    {"expected_access_epoch": 1}, {"expected_deletion_epoch": 1},
    {"expected_access_epoch": True, "expected_deletion_epoch": 1},
    {"provider_url": "PRIVATE_ENDPOINT"},
])
def test_request_rejects_unbounded_or_ambiguous_input(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("changes", [
    {"dimensions": 1536}, {"dimensions": 768.0}, {"dimensions": True},
    {"distance_metric": "dot"}, {"normalization": "none"},
    {"name": " synthetic-basis"}, {"revision": "fixture-v1 "},
    {"name": "\x00"}, {"revision": "\ud800"}, {"input_format": ""},
    {"input_format": "private\nformat"}, {"input_format": "x" * 129},
])
def test_space_identity_is_exact_and_closed(changes):
    with pytest.raises(ValidationError):
        migration.EmbeddingSpace(**{**MODEL, **changes})


def test_frozen_revalidated_contract_roundtrips_and_requires_new_namespace():
    value = request()
    assert migration.EmbeddingMigrationRequest.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        value.max_samples = 100
    with pytest.raises(ValidationError):
        value.target.revision = "changed"
    with pytest.raises(ValidationError):
        request(scope_ids=(value.scope_ids[0], value.scope_ids[0]))
    with pytest.raises(ValidationError):
        request(target=migration.EmbeddingSpace(**MODEL, input_format="changed-input-v2"))
    tampered = value.target.model_construct(**{**value.target.model_dump(), "dimensions": 2})
    with pytest.raises(ValidationError):
        request(target=tampered)


@pytest.mark.parametrize("blockers,eligible,source_complete,target_complete,expected", [
    ((), 0, False, False, "empty"),
    ((), 1, False, False, "incomplete"),
    ((), 1, True, False, "incomplete"),
    ((), 1, False, True, "incomplete"),
    ((), 1, True, True, "ready"),
    (("standby_snapshot",), 1, True, True, "blocked"),
    (("unsupported_target_input_format",), 0, False, False, "blocked"),
])
def test_status_is_snapshot_only_and_zero_never_counts_as_ready(
    blockers, eligible, source_complete, target_complete, expected,
):
    assert migration._status(blockers, eligible, source_complete, target_complete) == expected


@pytest.mark.parametrize("bad", [
    None, object(), {}, migration.EmbeddingMigrationRequest.model_construct(),
    migration.EmbeddingMigrationRequest.model_construct(tenant_id="PRIVATE_ARGUMENT"),
])
def test_invalid_request_never_connects(monkeypatch, bad):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid request reached the database")

    monkeypatch.setattr(migration, "read_admin_snapshot", forbidden)
    with pytest.raises(AdminError, match="^invalid_embedding_migration_request$"):
        migration.embedding_migration("PRIVATE_DSN", bad)


@pytest.mark.integration
def test_empty_universe_is_not_positive_completeness_and_has_no_side_effect(env):
    before = canonical_state(env)
    result = inspect(env)
    assert result.schema_version == SCHEMA_VERSION == 23
    assert result.visible_revisions == result.eligible_revisions == 0
    assert result.status == "empty" and result.blockers == ()
    assert result.assessment_scope == "principal_scope_time_projection_snapshot"
    assert not result.source_projection_complete and not result.target_projection_complete
    assert result.issues == () and result.issues_total == 0 and not result.issues_truncated
    assert result.input_formats_supported
    assert result.differences == ("revision",)
    assert result.source_coverage.model_dump() == {
        "eligible_ready": 0, "eligible_missing": 0, "eligible_stale": 0,
        "ineligible_present": 0, "ineligible_stale": 0,
    }
    for key in (
        "input_profile_verified", "query_space_verified", "source_authorization_verified",
        "cutover_authorized", "rollback_authorized", "production_qualified",
    ):
        assert getattr(result, key) is False
    assert result.source_retention == "retain_for_explicit_caller_rollback"
    assert result.projection_retirement == "no_supported_projection_delete"
    assert result.deletion_contract == "canonical_forget_purges_all_model_spaces"
    assert canonical_state(env) == before
    decoded = migration.EmbeddingMigrationReport.model_validate_json(result.model_dump_json())
    assert decoded == result


@pytest.mark.integration
def test_real_uploads_exact_canonical_digests_and_explicit_model_isolation(env):
    episode = env.observe("  PRIVATE_東京 Gold  ").json()["memory_id"]
    assertion = env.remember(episode).json()["memory_id"]
    for memory_id in (episode, assertion):
        upload(env, memory_id)
    missing = inspect(env)
    assert missing.status == "incomplete" and missing.blockers == ()
    assert missing.source_projection_complete and not missing.target_projection_complete
    assert missing.visible_revisions == missing.eligible_revisions == 2
    assert missing.target_coverage.eligible_missing == 2
    assert {str(issue.memory_id) for issue in missing.issues} == {episode, assertion}
    assert all(issue.code == "target_missing" for issue in missing.issues)
    # Equal dimensions, name or revision independently are never enough.
    upload(env, episode, model={**TARGET, "name": "another-model"})
    upload(env, assertion, model={**TARGET, "revision": "other-revision"})
    assert inspect(env).target_coverage.eligible_missing == 2
    upload(env, episode, values=vector(-1), model=TARGET)
    partial = inspect(env)
    assert partial.target_coverage.eligible_ready == 1
    assert partial.target_coverage.eligible_missing == 1
    upload(env, assertion, values=vector(0, 1), model=TARGET)
    complete = inspect(env)
    assert complete.status == "ready" and complete.blockers == ()
    assert complete.source_projection_complete and complete.target_projection_complete
    assert complete.target_coverage.eligible_ready == 2 and complete.issues_total == 0
    target_recall = recall(env, model=TARGET)
    assert ids(target_recall) == [assertion, episode]
    assert not target_recall["coverage"]["vector_incomplete"]
    source_recall = recall(env)
    assert set(ids(source_recall)) == {episode, assertion}
    assert not source_recall["coverage"]["vector_incomplete"]
    for secret in (env.admin_url, env.subjects[0], "PRIVATE_東京", "Gold"):
        assert secret not in complete.model_dump_json()
    for memory_id in (episode, assertion):
        assert input_for(env, memory_id)["input_digest"] not in complete.model_dump_json()


@pytest.mark.integration
def test_equal_projection_counts_cannot_conceal_missing_and_digest_stale_revisions(env):
    first = env.observe().json()["memory_id"]
    second = env.observe().json()["memory_id"]
    future = env.observe(occurred_at="2100-01-01T00:00:00Z").json()["memory_id"]
    upload(env, first, model=TARGET)
    upload(env, future, model=TARGET)
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.episode_embedding SET input_digest=%s
               WHERE tenant_id=%s AND episode_id=%s""", ("0" * 64, env.tenants[0], first),
        )
        assert conn.execute(
            "SELECT count(*) FROM memory.episode_embedding WHERE tenant_id=%s",
            (env.tenants[0],),
        ).fetchone()[0] == 2
    result = inspect(env)
    assert result.visible_revisions == 3 and result.eligible_revisions == 2
    assert result.target_coverage.model_dump() == {
        "eligible_ready": 0, "eligible_missing": 1, "eligible_stale": 1,
        "ineligible_present": 1, "ineligible_stale": 0,
    }
    assert not result.target_projection_complete
    assert result.status == "blocked" and result.blockers == ("target_stale",)
    assert {(str(issue.memory_id), issue.code) for issue in result.issues} >= {
        (first, "target_stale"), (second, "target_missing"), (future, "target_ineligible"),
    }
    # Normal immutable upload will not silently repair a corrupt projection.
    response = env.client.post(
        "/v1/embeddings", json=upload_body(env, first, model=TARGET), headers=env.headers(),
    )
    assert response.status_code == 409 and response.json()["code"] == "embedding_conflict"


@pytest.mark.integration
def test_revisions_are_exact_and_historical_coverage_is_not_current_coverage(env):
    episode = env.observe("Gold Silver").json()["memory_id"]
    assertion = env.remember(episode).json()["memory_id"]
    old = env.client.post(
        "/v1/explain", json={"memory_id": assertion}, headers=env.headers(),
    ).json()["assertion"]["recorded_at"]
    for memory_id in (episode, assertion):
        upload(env, memory_id)
        upload(env, memory_id, model=TARGET)
    revise(env, assertion, episode)
    missing = inspect(env)
    assert missing.visible_revisions == 3 and missing.eligible_revisions == 2
    assert missing.target_coverage.eligible_missing == missing.source_coverage.eligible_missing == 1
    assert missing.target_coverage.ineligible_present == 1
    assert missing.target_coverage.ineligible_stale == 0
    assert any(
        str(issue.memory_id) == assertion and issue.revision == 2
        and issue.code == "target_missing" for issue in missing.issues
    )
    historical = inspect(env, known_at=datetime.fromisoformat(old))
    assert historical.target_projection_complete and historical.source_projection_complete
    upload(env, assertion, model=TARGET, revision=2)
    current = inspect(env)
    assert current.target_projection_complete and not current.source_projection_complete
    assert current.status == "incomplete"
    assert not recall(env, model=TARGET)["coverage"]["vector_incomplete"]
    assert recall(env)["coverage"]["vector_incomplete"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.assertion_embedding SET input_digest=%s
               WHERE tenant_id=%s AND assertion_id=%s AND revision=1
                 AND model_revision=%s""",
            ("f" * 64, env.tenants[0], assertion, TARGET["revision"]),
        )
    current = inspect(env)
    assert current.target_projection_complete and current.target_coverage.ineligible_stale == 1
    historical = inspect(env, known_at=datetime.fromisoformat(old))
    assert not historical.target_projection_complete
    assert historical.target_coverage.eligible_stale == 1


@pytest.mark.integration
def test_relation_assertions_use_the_same_canonical_input_not_entity_text(env):
    source = create_entity(env, "Source")
    destination = create_entity(env, "Destination")
    relation = create_relation(env, source, destination)
    memory_id = relation["memory_id"]
    upload(env, memory_id)
    upload(env, memory_id, model=TARGET)
    result = inspect(env)
    assert result.source_coverage.eligible_ready == result.target_coverage.eligible_ready == 1
    assert not any(issue.memory_id == UUID(memory_id) for issue in result.issues)
    assert result.visible_revisions == result.eligible_revisions
    assert all(issue.kind == "episode" for issue in result.issues)


@pytest.mark.integration
def test_full_rls_scope_tenant_and_lease_expiry_exclusion_precedes_coverage(env):
    visible = env.observe("VISIBLE").json()["memory_id"]
    hidden = env.observe("PRIVATE_SCOPE", index=2).json()["memory_id"]
    foreign = env.observe("PRIVATE_TENANT", index=1).json()["memory_id"]
    for memory_id, index in ((visible, 0), (hidden, 2), (foreign, 1)):
        upload(env, memory_id, index=index)
        upload(env, memory_id, model=TARGET, index=index)
    result = inspect(env, scope_ids=tuple(env.scopes), max_revisions=1)
    assert result.visible_revisions == result.eligible_revisions == 1
    assert result.source_projection_complete and result.target_projection_complete
    assert hidden not in result.model_dump_json() and foreign not in result.model_dump_json()
    with scope_access(env.admin_url, ScopeAccessRequest(
        operation="set", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=env.principals[0], expected_access_epoch=1,
        permissions=("read",), expires_at=datetime.now(UTC) + timedelta(seconds=2),
    )) as changed:
        assert changed.access_epoch == 2
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "SELECT pg_sleep(greatest(0,extract(epoch FROM (%s-clock_timestamp())))+0.01)",
            (changed.expires_at,),
        )
    expired = inspect(env, scope_ids=tuple(env.scopes))
    assert expired.visible_revisions == expired.eligible_revisions == 0
    assert expired.status == "empty"
    assert not expired.target_projection_complete
    assert expired.access_epoch == 2
    assert inspect(env, index=2).target_projection_complete
    assert inspect(env, index=1).target_projection_complete


@pytest.mark.integration
def test_capacity_and_write_permission_block_population_without_changing_retention(env):
    full = env.observe("FULL").json()["memory_id"]
    empty = env.observe("EMPTY").json()["memory_id"]
    upload(env, full)
    for number in range(7):
        upload(env, full, model={**MODEL, "revision": f"retained-{number}"})
    with scope_access(env.admin_url, ScopeAccessRequest(
        operation="set", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=env.principals[0], expected_access_epoch=1,
        permissions=("read",), no_expiry=True,
    )):
        pass
    before = canonical_state(env)
    result = inspect(env)
    assert result.target_capacity_blocked == 1 and result.target_write_denied == 2
    assert result.status == "blocked"
    assert result.blockers == ("target_capacity_blocked", "target_write_denied")
    assert result.target_coverage.eligible_missing == 2
    assert any(
        str(issue.memory_id) == full and issue.code == "target_capacity_blocked"
        for issue in result.issues
    )
    assert any(
        str(issue.memory_id) == empty and issue.code == "target_write_denied"
        for issue in result.issues
    )
    assert canonical_state(env) == before


@pytest.mark.integration
def test_existing_target_at_eight_model_cap_is_not_a_population_blocker(env):
    source = env.observe().json()["memory_id"]
    upload(env, source)
    upload(env, source, model=TARGET)
    for number in range(6):
        upload(env, source, model={**MODEL, "revision": f"retained-{number}"})
    result = inspect(env)
    assert result.source_projection_complete and result.target_projection_complete
    assert result.target_capacity_blocked == 0


@pytest.mark.integration
def test_unsupported_input_profile_is_reported_not_inferred_or_approved(env):
    source = env.observe().json()["memory_id"]
    upload(env, source)
    upload(env, source, model=TARGET)
    changed = inspect(env, target=migration.EmbeddingSpace(**TARGET, input_format="prefix-v2"))
    assert changed.differences == ("revision", "input_format")
    assert changed.status == "blocked"
    assert changed.blockers == ("unsupported_target_input_format",)
    assert not changed.input_formats_supported and not changed.target_projection_complete
    assert changed.source_projection_complete and changed.target_coverage.eligible_ready == 1
    assert not changed.input_profile_verified and not changed.query_space_verified
    old = inspect(env, source=migration.EmbeddingSpace(**MODEL, input_format="legacy-v0"))
    assert not old.source_projection_complete and old.target_projection_complete
    assert old.status == "blocked" and old.blockers == ("unsupported_source_input_format",)


@pytest.mark.integration
def test_purge_removes_all_spaces_and_old_coverage_cannot_authorize_resurrection(env):
    episode = env.observe().json()["memory_id"]
    assertion = env.remember(episode).json()["memory_id"]
    for memory_id in (episode, assertion):
        upload(env, memory_id)
        upload(env, memory_id, model=TARGET)
    complete = inspect(env)
    assert complete.source_projection_complete and complete.target_projection_complete
    retained_request = upload_body(env, assertion, model=TARGET)
    forget(env, episode)
    purged = inspect(env)
    assert purged.status == "empty"
    assert purged.deletion_epoch == complete.deletion_epoch + 1
    assert purged.visible_revisions == purged.eligible_revisions == 0
    assert not purged.target_projection_complete and not purged.source_projection_complete
    assert not purged.rollback_authorized and not purged.cutover_authorized
    response = env.client.post("/v1/embeddings", json=retained_request, headers=env.headers())
    assert response.status_code == 404
    with pytest.raises(AdminError, match="^epoch_conflict$"):
        inspect(
            env, expected_access_epoch=complete.access_epoch,
            expected_deletion_epoch=complete.deletion_epoch,
        )


@pytest.mark.integration
def test_tombstoned_retained_rows_never_count_toward_coverage(env):
    hidden = env.observe("SUPPRESSED_PRIVATE_CONTENT").json()["memory_id"]
    positive = env.observe().json()["memory_id"]
    for memory_id in (hidden, positive):
        upload(env, memory_id)
        upload(env, memory_id, model=TARGET)
    with psycopg.connect(env.admin_url) as conn:
        deletion = receipt(conn, env, mode="suppress")
        target(conn, env, deletion, hidden)
        tombstone(conn, env, hidden)
        conn.execute("UPDATE memory.tenant SET deletion_epoch=2 WHERE id=%s", (env.tenants[0],))
    result = inspect(env, max_revisions=1)
    assert result.visible_revisions == result.eligible_revisions == 1
    assert result.source_projection_complete and result.target_projection_complete
    assert result.deletion_epoch == 2 and hidden not in result.model_dump_json()
    assert "SUPPRESSED_PRIVATE_CONTENT" not in result.model_dump_json()


@pytest.mark.integration
def test_revision_limit_fails_closed_and_issue_samples_are_explicitly_bounded(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    assertion = env.remember(source).json()["memory_id"]
    revise(env, assertion, source)
    with pytest.raises(AdminError, match="^embedding_migration_limit_exceeded$"):
        inspect(env, max_revisions=2)
    complete_inventory = inspect(env, max_revisions=3, max_samples=1)
    assert complete_inventory.visible_revisions == 3
    assert complete_inventory.eligible_revisions == 2
    assert complete_inventory.issues_total == 4
    assert len(complete_inventory.issues) == 1 and complete_inventory.issues_truncated
    no_samples = inspect(env, max_revisions=3, max_samples=0)
    assert no_samples.issues == () and no_samples.issues_total == 4 and no_samples.issues_truncated
    assert not no_samples.target_projection_complete


@pytest.mark.integration
def test_temporal_boundaries_match_recall_without_treating_future_vectors_as_ready(env):
    episode = env.observe("Gold", occurred_at="2100-01-01T00:00:00Z").json()["memory_id"]
    assertion = env.remember(
        episode, valid_from="2100-01-01T00:00:00Z", valid_to="2101-01-01T00:00:00Z",
    ).json()["memory_id"]
    for memory_id in (episode, assertion):
        upload(env, memory_id)
        upload(env, memory_id, model=TARGET)
    current = inspect(env)
    assert current.visible_revisions == 2 and current.eligible_revisions == 0
    assert current.status == "empty"
    assert current.target_coverage.ineligible_present == 2
    assert not current.target_projection_complete
    at_start = datetime(2100, 1, 1, tzinfo=UTC)
    start = inspect(env, as_of=at_start)
    assert start.eligible_revisions == 2 and start.target_projection_complete
    assert start.status == "ready"
    assert set(ids(recall(env, model=TARGET, as_of=at_start.isoformat()))) == {episode, assertion}
    at_end = datetime(2101, 1, 1, tzinfo=UTC)
    end = inspect(env, as_of=at_end)
    assert end.eligible_revisions == 1 and end.target_projection_complete
    assert end.target_coverage.ineligible_present == 1
    assert ids(recall(env, model=TARGET, as_of=at_end.isoformat())) == [episode]
    unknown = inspect(env, as_of=at_start, known_at=datetime(1900, 1, 1, tzinfo=UTC))
    assert unknown.eligible_revisions == 0 and not unknown.target_projection_complete


@pytest.mark.integration
def test_primary_epochs_match_and_admin_and_principal_identity_are_required(env):
    result = inspect(env, expected_access_epoch=1, expected_deletion_epoch=1)
    assert result.access_epoch == result.deletion_epoch == 1 and not result.in_recovery
    for changes in ({"principal_id": env.principals[1]}, {"tenant_id": uuid4()}):
        with pytest.raises(AdminError, match="^not_found$"):
            inspect(env, **changes)
    with pytest.raises(AdminError, match="^epoch_conflict$"):
        inspect(env, expected_access_epoch=2, expected_deletion_epoch=1)
    with pytest.raises(AdminError, match="^admin_role_required$"):
        migration.embedding_migration(env.settings.database_url, request(env))


@pytest.mark.integration
def test_single_readonly_snapshot_runtime_rls_and_unchanged_timeouts(env, monkeypatch):
    original = migration.read_admin_snapshot
    observed = []

    @contextmanager
    def snapshot(url):
        with original(url) as conn:
            yield conn
            settings = conn.execute(
                """SELECT current_user AS role,
                          current_setting('transaction_read_only') AS read_only,
                          current_setting('transaction_isolation') AS isolation,
                          current_setting('statement_timeout') AS statement_timeout,
                          current_setting('lock_timeout') AS lock_timeout,
                          row_security_active('memory.object') AS rls"""
            ).fetchone()
            observed.append(settings)
        assert conn.closed

    monkeypatch.setattr(migration, "read_admin_snapshot", snapshot)
    inspect(env)
    assert observed == [{
        "role": "pgag_runtime", "read_only": "on", "isolation": "repeatable read",
        "statement_timeout": "5s", "lock_timeout": "5s", "rls": True,
    }]


@pytest.mark.integration
def test_readonly_checker_does_not_take_admission_barrier_or_mutate(env, monkeypatch):
    original = psycopg.Connection.execute
    with psycopg.connect(env.admin_url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        with holder.transaction():
            holder.execute("SELECT id FROM memory.tenant WHERE id=%s FOR UPDATE", (env.tenants[0],))

            def execute(conn, query, *args, **kwargs):
                if isinstance(query, str):
                    assert not re.search(
                        r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP)\b", query, re.I,
                    )
                    assert "pg_advisory" not in query
                return original(conn, query, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(psycopg.Connection, "execute", execute)
                assert inspect(env).visible_revisions == 0


@pytest.mark.integration
def test_concurrent_purge_cannot_mix_new_epochs_with_old_projections(env, monkeypatch):
    source = env.observe().json()["memory_id"]
    upload(env, source)
    upload(env, source, model=TARGET)
    original = psycopg.Connection.execute
    removed = []

    def execute(conn, query, *args, **kwargs):
        cursor = original(conn, query, *args, **kwargs)
        if (
            isinstance(query, str) and "pg_is_in_recovery()" in query
            and "p.external_subject" in query and not removed
        ):
            removed.append(True)
            forget(env, source)
        return cursor

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", execute)
        snapshot = inspect(env)
    assert snapshot.deletion_epoch == 1 and snapshot.target_projection_complete
    current = inspect(env)
    assert current.deletion_epoch == 2 and current.visible_revisions == 0
    assert not snapshot.cutover_authorized


@pytest.mark.integration
def test_statement_timeout_is_failure_not_empty_success(env, monkeypatch):
    original = psycopg.Connection.execute

    def execute(conn, query, *args, **kwargs):
        if query == migration.INVENTORY_QUERY:
            original(conn, "SET LOCAL statement_timeout='5ms'")
            return original(conn, "SELECT pg_sleep(0.2)")
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    with pytest.raises(AdminError, match="^admin_database_unavailable$") as raised:
        inspect(env)
    assert raised.value.outcome_unknown is False


@pytest.mark.integration
def test_disabled_rls_fails_closed_even_for_admin_connection(env, monkeypatch):
    original = psycopg.Connection.execute

    def execute(conn, query, *args, **kwargs):
        if isinstance(query, str) and "bool_and(row_security_active" in query:
            return original(conn, "SELECT false AS active")
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    with pytest.raises(AdminError, match="^runtime_role_invalid$"):
        inspect(env)


@pytest.mark.integration
def test_exact_schema_ledger_is_required_before_inventory(env, monkeypatch):
    original = psycopg.Connection.execute

    def execute(conn, query, *args, **kwargs):
        assert query != migration.INVENTORY_QUERY
        if query == "SELECT version FROM public.pgag_schema_migration ORDER BY version":
            return original(
                conn, "SELECT version FROM public.pgag_schema_migration "
                "WHERE version<22 ORDER BY version",
            )
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    with pytest.raises(AdminError, match="^schema_version_mismatch$"):
        inspect(env)


@pytest.mark.integration
def test_standby_projection_observation_is_not_cutover_authority(env, monkeypatch):
    source = env.observe().json()["memory_id"]
    upload(env, source)
    upload(env, source, model=TARGET)
    original = psycopg.Connection.execute

    def execute(conn, query, *args, **kwargs):
        if isinstance(query, str):
            query = query.replace("pg_is_in_recovery()", "true")
        return original(conn, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    result = inspect(env)
    assert result.in_recovery and result.target_projection_complete
    assert result.status == "blocked" and result.blockers == ("standby_snapshot",)
    assert not result.cutover_authorized and not result.rollback_authorized


@pytest.mark.parametrize("changes", [
    ["--max-revisions", "10001"], ["--max-samples", "-1"],
    ["--as-of", "PRIVATE_ARGUMENT"], ["--expected-access-epoch", "1"],
    ["--source-name", " PRIVATE_ARGUMENT"], ["--unknown", "PRIVATE_ARGUMENT"],
])
def test_cli_invalid_arguments_are_sanitized_before_connection(monkeypatch, capsys, changes):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_DSN")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid CLI arguments reached the database")

    monkeypatch.setattr(migration, "embedding_migration", forbidden)
    with pytest.raises(SystemExit) as raised:
        migration.main(cli_args() + changes)
    output = capsys.readouterr()
    assert raised.value.code == 2 and not output.out
    assert "invalid_embedding_migration_arguments" in output.err
    assert "PRIVATE_ARGUMENT" not in output.err and "PRIVATE_DSN" not in output.err


def test_cli_never_falls_back_to_runtime_url(monkeypatch, capsys):
    monkeypatch.delenv("PGAG_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("PGAG_DATABASE_URL", "PRIVATE_DSN")
    with pytest.raises(SystemExit) as raised:
        migration.main(cli_args())
    output = capsys.readouterr()
    assert raised.value.code == 2 and not output.out
    assert "PGAG_ADMIN_DATABASE_URL is required" in output.err
    assert "PRIVATE_DSN" not in output.err


def test_cli_read_failure_has_sanitized_known_outcome(monkeypatch, capsys):
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_DSN")

    @contextmanager
    def unavailable(url):
        raise psycopg.OperationalError("PRIVATE_DSN PRIVATE_DIAGNOSTIC")
        yield

    monkeypatch.setattr(migration, "read_admin_snapshot", unavailable)
    with pytest.raises(SystemExit) as raised:
        migration.main(cli_args())
    output = capsys.readouterr()
    assert raised.value.code == 1 and output.err == ""
    assert json.loads(output.out) == {
        "error": {"code": "admin_database_unavailable", "outcome_unknown": False},
    }


@pytest.mark.integration
def test_module_cli_real_postgresql_contract(env):
    source = env.observe().json()["memory_id"]
    upload(env, source)
    completed = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.embedding_migration", *cli_args(env)],
        env={"PATH": os.environ["PATH"], "PGAG_ADMIN_DATABASE_URL": env.admin_url},
        capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 0 and not completed.stderr
    result = migration.EmbeddingMigrationReport.model_validate_json(completed.stdout)
    assert result.source_projection_complete and not result.target_projection_complete
    assert result.status == "incomplete"
    assert result.target_coverage.eligible_missing == 1
    assert not result.cutover_authorized
