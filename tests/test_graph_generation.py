"""Admin metadata lifecycle contracts; no graph backend serving or activation."""

import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from threading import Barrier, Event
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError
from test_graph_conformance import GraphFixture

from pg_agmemory import graph_generation as generation
from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access

INPUT_TABLES = (
    "memory.object",
    "memory.entity",
    "memory.entity_evidence",
    "memory.assertion",
    "memory.assertion_revision",
    "memory.relation",
    "memory.relation_revision",
)
METADATA_TABLES = ("memory_ops.graph_generation", "memory_ops.graph_generation_state")
PROFILE = "a" * 64
ARTIFACT = "b" * 64


def request(env, operation="get", **values):
    return generation.GraphGenerationRequest(
        operation=operation, tenant_id=env.tenants[0], **values
    )


def execute(env, operation="get", **values):
    with generation.graph_generation(env.admin_url, request(env, operation, **values)) as result:
        assert result.artifact_verified is False
        assert result.serving_enabled is False
        return result


def begin(env, *, snapshot=None, **changes):
    current = snapshot or execute(env)
    return execute(
        env,
        "begin",
        **{
            "expected_revision": current.revision,
            "generation_id": uuid4(),
            "expected_input_digest": current.current_input_digest,
            "profile_digest": PROFILE,
            **changes,
        },
    )


def record(env, reserved, **changes):
    return execute(
        env,
        "record",
        **{
            "expected_revision": reserved.revision,
            "generation_id": reserved.building.id,
            "expected_input_digest": reserved.building.input_digest,
            "artifact_digest": ARTIFACT,
            **changes,
        },
    )


def abandon(env, reserved, **changes):
    return execute(
        env,
        "abandon",
        **{
            "expected_revision": reserved.revision,
            "generation_id": reserved.building.id,
            "reason": "Discard incomplete fixture generation",
            **changes,
        },
    )


def graph_fixture(env):
    graph = GraphFixture(env)
    for name in ("source", "target", "alternate"):
        graph.node(name, label=f"PRIVATE_GRAPH_LABEL_{name}")
    graph.edge("relation", "source", "target")
    return graph


def metadata_rows(env):
    with psycopg.connect(env.admin_url) as conn:
        return {
            table: conn.execute(
                sql.SQL(
                    "SELECT to_jsonb(t) FROM {} t WHERE tenant_id=%s "
                    'ORDER BY to_jsonb(t)::text COLLATE "C"'
                ).format(sql.Identifier(*table.split("."))),
                (env.tenants[0],),
            ).fetchall()
            for table in METADATA_TABLES
        }


def raw_request(operation="get", **values):
    return {"operation": operation, "tenant_id": str(uuid4()), **values}


@pytest.mark.integration
@pytest.mark.parametrize("state", ["recorded", "abandoned"])
def test_terminal_generation_cannot_be_inserted_without_building(env, state):
    current = execute(env)
    before = metadata_rows(env)
    with psycopg.connect(env.admin_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="must begin in building state"):
            conn.execute(
                """INSERT INTO memory_ops.graph_generation
                   (tenant_id,id,state,profile_digest,input_digest,input_snapshot,
                    artifact_digest,abandon_reason,finished_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,clock_timestamp())""",
                (
                    env.tenants[0], uuid4(), state, PROFILE, current.current_input_digest,
                    current.current_input.model_dump_json(),
                    ARTIFACT if state == "recorded" else None,
                    "Direct terminal fixture" if state == "abandoned" else None,
                ),
            )
    assert metadata_rows(env) == before


@pytest.mark.integration
def test_generation_creation_records_server_role_and_time(env):
    current = execute(env)
    identifier = uuid4()
    with psycopg.connect(env.admin_url) as conn:
        actor, before = conn.execute("SELECT current_user,clock_timestamp()").fetchone()
        conn.execute(
            """INSERT INTO memory_ops.graph_generation
               (tenant_id,id,state,profile_digest,input_digest,input_snapshot,database_role,created_at)
               VALUES (%s,%s,'building',%s,%s,%s::jsonb,'not-the-actor','2000-01-01Z')""",
            (
                env.tenants[0], identifier, PROFILE, current.current_input_digest,
                current.current_input.model_dump_json(),
            ),
        )
        conn.execute(
            """INSERT INTO memory_ops.graph_generation_state(tenant_id,revision,building_id)
               VALUES (%s,1,%s)""", (env.tenants[0], identifier),
        )
    result = execute(env)
    assert result.building.id == identifier
    assert result.building.database_role == actor
    assert result.building.created_at >= before


@pytest.mark.parametrize(
    "body",
    [
        raw_request(expected_revision=0),
        raw_request(
            "begin",
            expected_revision=True,
            generation_id=str(uuid4()),
            expected_input_digest="c" * 64,
            profile_digest=PROFILE,
        ),
        raw_request(
            "begin",
            expected_revision=-1,
            generation_id=str(uuid4()),
            expected_input_digest="c" * 64,
            profile_digest=PROFILE,
        ),
        raw_request(
            "begin",
            expected_revision=MAX_EPOCH + 1,
            generation_id=str(uuid4()),
            expected_input_digest="c" * 64,
            profile_digest=PROFILE,
        ),
        raw_request(
            "begin",
            expected_revision=0,
            generation_id=str(uuid4()),
            expected_input_digest="not-a-digest",
            profile_digest=PROFILE,
        ),
        raw_request(
            "begin", expected_revision=0, generation_id=str(uuid4()), expected_input_digest="c" * 64
        ),
        raw_request(
            "record",
            expected_revision=1,
            generation_id=str(uuid4()),
            expected_input_digest="c" * 64,
            artifact_digest=ARTIFACT,
            profile_digest=PROFILE,
        ),
        raw_request("abandon", expected_revision=1, generation_id=str(uuid4()), reason=""),
        raw_request(
            "abandon",
            expected_revision=1,
            generation_id=str(uuid4()),
            reason="cancel",
            expected_input_digest="c" * 64,
        ),
        raw_request(unexpected="PRIVATE_EXTRA_FIELD"),
    ],
)
def test_invalid_generation_request_rejected_before_connection(body, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid request reached the database")

    monkeypatch.setattr(generation, "admin_connection", forbidden)
    with pytest.raises(ValidationError):
        generation.GraphGenerationRequest.model_validate_json(json.dumps(body))


def test_request_is_frozen_and_exact_revision_bounds_are_accepted():
    for revision in (0, MAX_EPOCH):
        parsed = generation.GraphGenerationRequest.model_validate_json(
            json.dumps(
                raw_request(
                    "begin",
                    expected_revision=revision,
                    generation_id=str(uuid4()),
                    expected_input_digest="c" * 64,
                    profile_digest=PROFILE,
                )
            )
        )
        assert parsed.expected_revision == revision
        with pytest.raises(ValidationError, match="frozen"):
            parsed.expected_revision = 2


@pytest.mark.integration
@pytest.mark.parametrize("schema", [18, 19])
def test_previous_schema_receipt_requires_matching_generation_version(env, schema):
    current = record(env, begin(env))
    with psycopg.connect(env.admin_url) as conn:
        # Model an older backup receipt without relabeling it as the current schema.
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation DISABLE TRIGGER guard_graph_generation"
        )
        conn.execute(
            """UPDATE memory_ops.graph_generation SET input_snapshot=jsonb_set(
               input_snapshot,'{schema_version}',to_jsonb(%s::integer))
               WHERE tenant_id=%s AND id=%s""",
            (schema, env.tenants[0], current.head.id),
        )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation ENABLE TRIGGER guard_graph_generation"
        )
    before = metadata_rows(env)
    with pytest.raises(AdminError, match="^graph_generation_invalid$"):
        execute(env)
    assert metadata_rows(env) == before


@pytest.mark.integration
def test_empty_get_is_stable_private_and_does_not_create_ledger(env):
    empty = execute(env)
    assert empty.operation == "get" and empty.tenant_id == env.tenants[0]
    assert empty.revision == 0 and empty.head is None and empty.building is None
    assert not empty.changed
    assert empty.current_input.format == "pgag-graph-input-v1"
    assert empty.current_input.schema_version == 20
    assert empty.current_input.tenant_id == env.tenants[0]
    assert empty.current_input.access_epoch == empty.current_input.deletion_epoch == 1
    assert tuple(row.table for row in empty.current_input.tables) == INPUT_TABLES
    assert all(row.rows == 0 and len(row.digest) == 64 for row in empty.current_input.tables)
    assert execute(env) == empty
    assert metadata_rows(env) == dict.fromkeys(METADATA_TABLES, [])
    graph = graph_fixture(env)
    populated = execute(env)
    assert [row.rows for row in populated.current_input.tables] == [4, 3, 3, 1, 1, 1, 1]
    serialized = populated.model_dump_json()
    assert "PRIVATE_GRAPH_LABEL" not in serialized and env.subjects[0] not in serialized
    assert all(node["memory_id"] not in serialized for node in graph.nodes.values())
    with psycopg.connect(env.admin_url) as conn:
        secrets = conn.execute(
            """SELECT encode(t.dedup_secret,'hex'),encode(k.secret,'hex')
               FROM memory.tenant t JOIN memory_ops.recovery_key k ON k.tenant_id=t.id
               WHERE t.id=%s""",
            (env.tenants[0],),
        ).fetchone()
    assert secrets and all(secret not in serialized for secret in secrets)
    assert execute(env) == populated


@pytest.mark.integration
def test_recorded_parent_history_and_abandon_are_not_serving_activation(env):
    graph_fixture(env)
    first = begin(env)
    assert first.changed and first.revision == 1 and first.head is None
    assert first.building.state == "building" and first.building.parent_id is None
    assert first.building.profile_digest == PROFILE
    assert first.building.input_digest == first.current_input_digest
    assert first.building.input_snapshot == first.current_input
    assert first.building.source_matches is True
    assert first.building.artifact_digest is None and first.building.finished_at is None
    recorded = record(env, first)
    assert recorded.changed and recorded.revision == 2 and recorded.building is None
    assert recorded.head.id == first.building.id and recorded.head.state == "recorded"
    assert recorded.head.artifact_digest == ARTIFACT and recorded.head.abandon_reason is None
    assert recorded.head.created_at <= recorded.head.finished_at
    with psycopg.connect(env.admin_url) as conn:
        assert recorded.head.database_role == conn.execute("SELECT current_user").fetchone()[0]
    history = metadata_rows(env)[METADATA_TABLES[0]]
    second = begin(env)
    assert second.revision == 3 and second.head == recorded.head
    assert second.building.parent_id == recorded.head.id
    cancelled = abandon(env, second)
    assert cancelled.revision == 4 and cancelled.building is None
    assert cancelled.head.id == recorded.head.id and cancelled.head.source_matches is None
    assert cancelled.current_input is None and cancelled.current_input_digest is None
    rows = metadata_rows(env)[METADATA_TABLES[0]]
    assert history[0] in rows
    assert {row[0]["state"] for row in rows} == {"recorded", "abandoned"}
    third = begin(env)
    promoted = record(env, third, artifact_digest="d" * 64)
    assert promoted.revision == 6 and promoted.head.parent_id == recorded.head.id
    assert history[0] in metadata_rows(env)[METADATA_TABLES[0]]
    assert execute(env).head == promoted.head


@pytest.mark.integration
def test_cas_identity_input_and_single_builder_fences_are_explicit(env):
    initial = execute(env)
    with pytest.raises(AdminError, match="^graph_input_changed$"):
        begin(env, snapshot=initial, expected_input_digest="0" * 64)
    assert execute(env) == initial
    reserved = begin(env, snapshot=initial)
    baseline = metadata_rows(env)
    for operation, values, code in [
        ("begin", {"generation_id": uuid4(), "profile_digest": PROFILE}, "graph_build_in_progress"),
        (
            "record",
            {"generation_id": uuid4(), "artifact_digest": ARTIFACT},
            "graph_generation_not_building",
        ),
        (
            "record",
            {
                "generation_id": reserved.building.id,
                "artifact_digest": ARTIFACT,
                "expected_input_digest": "0" * 64,
            },
            "graph_input_changed",
        ),
    ]:
        with pytest.raises(AdminError, match=f"^{code}$"):
            execute(
                env,
                operation,
                **{
                    "expected_revision": 1,
                    "expected_input_digest": reserved.current_input_digest,
                    **values,
                },
            )
        assert metadata_rows(env) == baseline
    with pytest.raises(AdminError, match="^graph_revision_conflict$"):
        begin(env, snapshot=initial)
    cancelled = abandon(env, reserved)
    with pytest.raises(AdminError, match="^graph_generation_exists$"):
        begin(env, snapshot=execute(env), generation_id=reserved.building.id)
    with pytest.raises(AdminError, match="^graph_generation_not_building$"):
        record(env, reserved, expected_revision=cancelled.revision)
    assert execute(env).revision == cancelled.revision


@pytest.mark.integration
@pytest.mark.parametrize("race", ["begin", "record_abandon"])
def test_concurrent_mutations_have_one_cas_winner(env, race):
    starting = execute(env) if race == "begin" else begin(env)
    barrier = Barrier(2)

    def compete(index):
        barrier.wait(timeout=5)
        try:
            if race == "begin":
                return begin(env, snapshot=starting)
            return record(env, starting) if index == 0 else abandon(env, starting)
        except AdminError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, (0, 1)))
    assert results.count("graph_revision_conflict") == 1
    winner = next(item for item in results if not isinstance(item, str))
    current = execute(env)
    assert current.revision == starting.revision + 1 == winner.revision
    assert len(metadata_rows(env)[METADATA_TABLES[0]]) == 1
    if race == "begin":
        assert current.building.id == winner.building.id
    else:
        assert current.building is None
        assert (current.head.id if current.head else None) == (
            winner.head.id if winner.head else None
        )


@pytest.mark.integration
@pytest.mark.parametrize("change", ["entity", "relation", "target_revision", "valid_time_revision"])
def test_relevant_canonical_changes_invalidate_reserved_input(env, change):
    graph = graph_fixture(env)
    reserved = begin(env)
    if change == "entity":
        graph.node("extra")
    elif change == "relation":
        graph.edge("parallel", "source", "target")
    elif change == "target_revision":
        graph.revise("relation", "alternate")
    else:
        graph.revise("relation", "target", valid_from="2026-09-01T00:00:00Z")
    current = execute(env)
    assert current.current_input_digest != reserved.current_input_digest
    assert current.building.source_matches is False and current.revision == reserved.revision
    with pytest.raises(AdminError, match="^graph_input_changed$"):
        record(env, reserved)
    assert execute(env) == current
    cancelled = abandon(env, reserved)
    assert cancelled.changed and cancelled.current_input is None and cancelled.building is None


@pytest.mark.integration
def test_unrelated_observations_assertions_and_other_tenants_do_not_stale_input(env):
    graph_fixture(env)
    reserved = begin(env)
    observed = env.observe("Ordinary Gold assertion evidence")
    assert observed.status_code == 201
    remembered = env.remember(observed.json()["memory_id"])
    assert remembered.status_code == 201
    revised = env.client.post(
        f"/v1/assertions/{remembered.json()['memory_id']}/revisions",
        headers=env.headers(),
        json={
            "expected_revision": 1,
            "value": "Gold corrected",
            "explicit_intent": True,
            "reason": "Not a typed relation",
            "evidence": [{"memory_id": observed.json()["memory_id"], "quote": "Gold"}],
        },
    )
    assert revised.status_code == 201, revised.text
    foreign = GraphFixture(env)
    foreign.node("foreign", index=1)
    foreign.node("other", index=1)
    foreign.edge("foreign-edge", "foreign", "other", index=1)
    current = execute(env)
    assert current.current_input == reserved.current_input
    assert current.current_input_digest == reserved.current_input_digest
    assert current.building.source_matches is True
    assert record(env, reserved).head.source_matches is True


@pytest.mark.integration
@pytest.mark.parametrize("change", ["revoke", "purge"])
def test_current_access_and_deletion_epochs_stale_receipts_without_resurrection(env, change):
    graph = graph_fixture(env)
    reserved = begin(env)
    recorded = record(env, reserved)
    pending = begin(env)
    historical = graph.request(["source"])
    if change == "revoke":
        with scope_access(
            env.admin_url,
            ScopeAccessRequest(
                operation="revoke",
                tenant_id=env.tenants[0],
                scope_id=env.scopes[0],
                principal_id=env.principals[0],
                expected_access_epoch=1,
            ),
        ):
            pass
    else:
        response = env.client.post(
            "/v1/forget",
            headers=env.headers(),
            json={"memory_ids": [graph.nodes["source"]["memory_id"]], "reason": "test purge"},
        )
        assert response.status_code == 202, response.text
    current = execute(env)
    assert current.current_input_digest != pending.current_input_digest
    assert current.head.id == recorded.head.id and current.head.source_matches is False
    assert current.building.source_matches is False
    assert graph.expand(historical)["paths"] == []
    with pytest.raises(AdminError, match="^graph_input_changed$"):
        record(env, pending)
    result = abandon(env, pending)
    assert result.head.id == recorded.head.id and result.head.source_matches is None
    assert result.current_input is None and result.current_input_digest is None


@pytest.mark.integration
def test_abandon_does_not_fingerprint_when_capture_limits_fail(env, monkeypatch):
    graph_fixture(env)
    recorded = record(env, begin(env))
    reserved = begin(env)
    monkeypatch.setattr("pg_agmemory.processing_recovery.MAX_ROWS", 0)
    with pytest.raises(AdminError, match="^graph_input_limit$"):
        execute(env)
    cancelled = abandon(env, reserved)
    assert cancelled.head.id == recorded.head.id and cancelled.head.source_matches is None
    assert cancelled.current_input is None and cancelled.current_input_digest is None
    assert cancelled.building is None


@pytest.mark.integration
def test_runtime_is_denied_and_metadata_tables_force_rls_without_grants(env):
    initial = execute(env)
    for operation, values in [
        ("get", {}),
        (
            "begin",
            {
                "expected_revision": 0,
                "generation_id": uuid4(),
                "expected_input_digest": initial.current_input_digest,
                "profile_digest": PROFILE,
            },
        ),
    ]:
        with pytest.raises(AdminError, match="^admin_role_required$"):
            with generation.graph_generation(
                env.settings.database_url, request(env, operation, **values)
            ):
                pytest.fail("Runtime credentials were accepted")
    with psycopg.connect(env.admin_url) as conn:
        for table in METADATA_TABLES:
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (table,),
            ).fetchone() == (True, True)
            assert conn.execute(
                "SELECT has_table_privilege("
                "'pgag_runtime',%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
                (table,),
            ).fetchone() == (False,)
    for table in METADATA_TABLES:
        with psycopg.connect(env.settings.database_url) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(*table.split("."))))
    assert execute(env) == initial


@pytest.mark.integration
@pytest.mark.parametrize("phase", ["before_commit", "lost_commit_response"])
def test_commit_failure_or_lost_response_requires_get_not_automatic_replay(env, monkeypatch, phase):
    current = execute(env)
    generation_id = uuid4()
    original = psycopg.Connection.transaction

    @contextmanager
    def interrupted(conn, *args, **kwargs):
        with original(conn, *args, **kwargs) as transaction:
            yield transaction
            if phase == "before_commit":
                raise psycopg.OperationalError("PRIVATE_COMMIT_FAILURE")
        if phase == "lost_commit_response":
            raise psycopg.OperationalError("PRIVATE_COMMIT_RESPONSE")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "transaction", interrupted)
        with pytest.raises(AdminError, match="^admin_database_unavailable$") as failed:
            begin(env, snapshot=current, generation_id=generation_id)
        assert failed.value.outcome_unknown
    inspected = execute(env)
    if phase == "before_commit":
        assert inspected == current and metadata_rows(env) == dict.fromkeys(METADATA_TABLES, [])
        assert begin(env, snapshot=current, generation_id=generation_id).revision == 1
    else:
        assert inspected.revision == 1 and inspected.building.id == generation_id
        with pytest.raises(AdminError, match="^graph_revision_conflict$"):
            begin(env, snapshot=current, generation_id=generation_id)


@pytest.mark.integration
def test_admin_barrier_is_held_until_result_delivery_finishes(env):
    initial = execute(env)
    started = Event()

    def inspect():
        started.set()
        return execute(env)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with generation.graph_generation(
            env.admin_url,
            request(
                env,
                "begin",
                expected_revision=0,
                generation_id=uuid4(),
                expected_input_digest=initial.current_input_digest,
                profile_digest=PROFILE,
            ),
        ) as reserved:
            waiting = pool.submit(inspect)
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                waiting.result(timeout=0.2)
        current = waiting.result(timeout=5)
    assert current.revision == reserved.revision and current.building.id == reserved.building.id


@pytest.mark.integration
def test_generation_count_bound_is_explicit_and_cancelled_ids_still_count(env, monkeypatch):
    assert generation.MAX_GENERATIONS == 10000
    monkeypatch.setattr(generation, "MAX_GENERATIONS", 2)
    for _ in range(2):
        abandon(env, begin(env))
    current = execute(env)
    with pytest.raises(AdminError, match="^graph_generation_limit$"):
        begin(env, snapshot=current)
    assert execute(env) == current and current.revision == 4


@pytest.mark.integration
def test_terminal_history_and_deferred_ledger_pointers_cannot_be_rewritten(env):
    first = record(env, begin(env))
    begin(env)
    before = metadata_rows(env)
    statements = [
        (
            "UPDATE memory_ops.graph_generation SET artifact_digest=%s "
            "WHERE tenant_id=%s AND id=%s",
            ("c" * 64, env.tenants[0], first.head.id),
        ),
        (
            "DELETE FROM memory_ops.graph_generation WHERE tenant_id=%s AND id=%s",
            (env.tenants[0], first.head.id),
        ),
        (
            "UPDATE memory_ops.graph_generation SET state='building',"
            "artifact_digest=NULL,finished_at=NULL WHERE tenant_id=%s AND id=%s",
            (env.tenants[0], first.head.id),
        ),
        (
            "UPDATE memory_ops.graph_generation_state "
            "SET head_id=building_id,revision=revision+1 WHERE tenant_id=%s",
            (env.tenants[0],),
        ),
        (
            "UPDATE memory_ops.graph_generation_state "
            "SET building_id=NULL,revision=revision+1 WHERE tenant_id=%s",
            (env.tenants[0],),
        ),
        (
            "DELETE FROM memory_ops.graph_generation_state WHERE tenant_id=%s",
            (env.tenants[0],),
        ),
    ]
    for statement, params in statements:
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(env.admin_url) as conn:
                conn.execute("SET LOCAL pgag.recovery_apply='on'")
                conn.execute(statement, params)
        assert metadata_rows(env) == before


@pytest.mark.integration
def test_exact_revision_exhaustion_preserves_reserved_generation(env):
    reserved = begin(env)
    # Simulate an exhausted restored ledger without MAX_EPOCH individual transitions.
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation_state "
            "DISABLE TRIGGER guard_graph_generation_state"
        )
        conn.execute(
            "UPDATE memory_ops.graph_generation_state SET revision=%s WHERE tenant_id=%s",
            (MAX_EPOCH, env.tenants[0]),
        )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        conn.execute(
            "ALTER TABLE memory_ops.graph_generation_state "
            "ENABLE TRIGGER guard_graph_generation_state"
        )
    current = execute(env)
    assert current.revision == MAX_EPOCH
    before = metadata_rows(env)
    for operation in (record, abandon):
        with pytest.raises(AdminError, match="^graph_revision_exhausted$"):
            operation(env, reserved, expected_revision=MAX_EPOCH)
        assert metadata_rows(env) == before


def cli(env, body, *arguments, url=None):
    return subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "graph-generation", *arguments],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": os.environ["PATH"], "PGAG_ADMIN_DATABASE_URL": url or env.admin_url},
    )


@pytest.mark.parametrize("invalid", ["arguments", "json", "oversize", "extra"])
def test_cli_invalid_input_is_private_and_never_connects(monkeypatch, capsys, invalid):
    payload = (
        b"PRIVATE_INVALID_JSON"
        if invalid == "json"
        else b"x" * 32769
        if invalid == "oversize"
        else json.dumps(raw_request(unexpected="PRIVATE_EXTRA")).encode()
        if invalid == "extra"
        else json.dumps(raw_request()).encode()
    )
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", "PRIVATE_DSN")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid CLI input reached the database")

    monkeypatch.setattr(generation, "graph_generation", forbidden)
    with pytest.raises(SystemExit) as rejected:
        generation.main(["PRIVATE_ARGUMENT"] if invalid == "arguments" else [])
    assert rejected.value.code != 0
    output = capsys.readouterr()
    assert "PRIVATE_" not in output.out + output.err
    assert "Traceback" not in output.out + output.err
    assert output.out or output.err


@pytest.mark.integration
def test_cli_json_roundtrip_and_runtime_error_never_claim_activation(env):
    initial = cli(env, {"operation": "get", "tenant_id": str(env.tenants[0])})
    assert initial.returncode == 0 and initial.stderr == ""
    state = json.loads(initial.stdout)
    assert state["revision"] == 0 and state["serving_enabled"] is False
    body = {
        "operation": "begin",
        "tenant_id": str(env.tenants[0]),
        "expected_revision": 0,
        "generation_id": str(uuid4()),
        "expected_input_digest": state["current_input_digest"],
        "profile_digest": PROFILE,
    }
    begun = cli(env, body)
    assert begun.returncode == 0 and json.loads(begun.stdout)["revision"] == 1
    conflict = cli(env, body)
    assert conflict.returncode == 1 and conflict.stderr == ""
    assert json.loads(conflict.stdout)["error"] == {
        "code": "graph_revision_conflict",
        "outcome_unknown": False,
    }
    denied = cli(
        env, {"operation": "get", "tenant_id": str(env.tenants[0])}, url=env.settings.database_url
    )
    assert denied.returncode == 1 and denied.stderr == ""
    assert json.loads(denied.stdout)["error"]["code"] == "admin_role_required"
    unavailable = cli(
        env,
        {"operation": "get", "tenant_id": str(env.tenants[0])},
        url="postgresql://PRIVATE_USER:PRIVATE_PASSWORD@127.0.0.1:1/PRIVATE_DATABASE",
    )
    assert unavailable.returncode == 1 and unavailable.stderr == ""
    assert json.loads(unavailable.stdout)["error"]["code"] == "admin_database_unavailable"
    assert "PRIVATE_" not in unavailable.stdout and "Traceback" not in unavailable.stdout
