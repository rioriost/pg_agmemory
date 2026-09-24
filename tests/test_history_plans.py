"""Bound actual runtime history plans, including the pagination sentinel."""

import asyncio
import json
import re
from collections.abc import Mapping
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from test_graphs import create_entity, create_relation, revise_body
from test_revisions import history, seed_revision_limit_prefix

from pg_agmemory.database import connect
from pg_agmemory.models import AssertionHistory, AssertionHistoryPage, Identity
from pg_agmemory.service import MemoryService
from pg_agmemory.transactions import async_transaction

pytestmark = pytest.mark.integration


def history_statement(statement):
    return (
        isinstance(statement, str)
        and "evidence_refs" in statement
        and "memory.assertion_revision" in statement
    )


def parameter_values(statement, parameters):
    if isinstance(parameters, Mapping):
        names = dict.fromkeys(re.findall(r"%\((\w+)\)s", statement))
        assert names
        return [parameters[name] for name in names]
    return parameters


@pytest.mark.parametrize("plan_mode", [
    "auto", "force_custom_plan", "force_generic_plan", "nested_loop",
])
def test_exact_limit_typed_history_has_bounded_runtime_plans(
    env, monkeypatch, record_property, plan_mode,
):
    memory, source, entities = seed_revision_limit_prefix(env, typed=True)
    appended = env.client.post(
        f"/v1/relations/{memory}/revisions",
        headers=env.headers(),
        json={
            "expected_revision": 999,
            "target_entity": entities[1],
            "evidence": [{"memory_id": source, "quote": "Silver"}],
            "explicit_intent": True,
            "reason": "History plan fixture",
        },
    )
    assert appended.status_code == 201, appended.text
    assert appended.json()["revision"] == 1000

    async def inspect():
        async with await connect(env.settings.database_url) as conn:
            if plan_mode != "auto":
                conn.prepare_threshold = 0
            async with async_transaction(conn):
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute(
                    """SELECT set_config('pgag.tenant_id',%s,true),
                              set_config('pgag.principal_id',%s,true)""",
                    (str(env.tenants[0]), str(env.principals[0])),
                )
                assert (await (await conn.execute("SHOW statement_timeout")).fetchone()) == {
                    "statement_timeout": "5s",
                }
                assert (await (await conn.execute("SHOW row_security")).fetchone()) == {
                    "row_security": "on",
                }
                role = await (await conn.execute(
                    "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user",
                )).fetchone()
                assert role == {"rolsuper": False, "rolbypassrls": False}
                if plan_mode != "auto":
                    mode = "force_generic_plan" if plan_mode == "nested_loop" else plan_mode
                    await conn.execute(
                        sql.SQL("SET LOCAL plan_cache_mode={}").format(sql.Literal(mode)),
                    )
                if plan_mode == "nested_loop":
                    await conn.execute("SET LOCAL enable_hashjoin=off")
                    await conn.execute("SET LOCAL enable_mergejoin=off")
                    await conn.execute("SET LOCAL enable_material=off")
                service = MemoryService(
                    conn, Identity(tenant_id=env.tenants[0], principal_id=env.principals[0]),
                )
                execute = conn.execute
                captured = []

                async def record_execute(query, params=None, **kwargs):
                    rendered = query.as_string() if isinstance(query, sql.Composable) else query
                    if history_statement(rendered):
                        captured.append((rendered, params))
                    return await execute(query, params, **kwargs)

                for before in (1001, 501, 101):
                    captured.clear()
                    with monkeypatch.context() as patch:
                        patch.setattr(conn, "execute", record_execute)
                        result = await service.assertion_history(
                            AssertionHistory(
                                memory_id=memory, max_items=100, before_revision=before,
                            ),
                        )
                    assert len(captured) == 1
                    statement, parameters = captured[0]
                    page = AssertionHistoryPage.model_validate(result).model_dump(mode="json")
                    numbers = list(range(before - 1, before - 101, -1))
                    assert page["current_revision"] == 1000
                    assert [row["revision"] for row in page["revisions"]] == numbers
                    assert page["next_before_revision"] == (numbers[-1] if before > 101 else None)
                    for row in page["revisions"]:
                        assert row["evidence_refs"] == [{"memory_id": source, "revision": 1}]
                        assert row["relation"] == {
                            "source_entity": entities[0], "target_entity": entities[1],
                        }
                    if plan_mode == "auto":
                        explained = await execute(
                            "EXPLAIN (ANALYZE, FORMAT JSON, TIMING OFF) " + statement,
                            parameters, prepare=False,
                        )
                    else:
                        prepared = [
                            row for row in await (await execute(
                                """SELECT name,statement,generic_plans,custom_plans
                                   FROM pg_prepared_statements""",
                            )).fetchall() if history_statement(row["statement"])
                        ]
                        assert len(prepared) == 1
                        kind = (
                            "custom_plans" if plan_mode == "force_custom_plan" else "generic_plans"
                        )
                        assert prepared[0][kind] > 0
                        explained = await execute(
                            sql.SQL("EXPLAIN (ANALYZE, FORMAT JSON, TIMING OFF) EXECUTE {} ({})")
                            .format(
                                sql.Identifier(prepared[0]["name"]),
                                sql.SQL(",").join(
                                    sql.Literal(value)
                                    for value in parameter_values(statement, parameters)
                                ),
                            ),
                            prepare=False,
                        )
                    plan = (await explained.fetchone())["QUERY PLAN"]
                    expected_rows = min(before - 1, 101)
                    assert plan[0]["Plan"]["Actual Rows"] == expected_rows, json.dumps(plan)
                    nodes = [plan[0]["Plan"]]
                    scans = {}
                    indexed_page_revisions = False
                    while nodes:
                        node = nodes.pop()
                        nodes.extend(node.get("Plans", []))
                        if re.search(r"\brevision\s*=\s*ANY\s*\(", node.get("Index Cond", "")):
                            indexed_page_revisions = True
                        relation = node.get("Relation Name")
                        if relation in {
                            "assertion_revision", "relation_revision",
                            "relation", "provenance_edge",
                        }:
                            scans.setdefault(relation, []).append({
                                "rows": node["Actual Rows"], "loops": node["Actual Loops"],
                                "filtered": node.get("Rows Removed by Filter", 0)
                                + node.get("Rows Removed by Index Recheck", 0),
                            })
                    assert set(scans) == {
                        "assertion_revision", "relation_revision", "relation", "provenance_edge",
                    }, json.dumps(plan)
                    assert indexed_page_revisions, json.dumps(plan)
                    record_property(f"history_before_{before}_scans", json.dumps(scans))
                    for relation, counts in scans.items():
                        if relation == "relation":
                            assert all(count["loops"] <= 1 for count in counts), json.dumps(plan)
                            assert any(count["rows"] == 1 for count in counts), json.dumps(plan)
                            continue
                        assert len(counts) == 1, json.dumps(plan)
                        count = counts[0]
                        if relation == "provenance_edge":
                            assert 1 <= count["loops"] <= expected_rows, json.dumps(plan)
                            assert count["rows"] * count["loops"] == expected_rows, json.dumps(plan)
                            work = (count["rows"] + count["filtered"]) * count["loops"]
                            # A single history scan is linear; per-row probes must be exact.
                            assert work <= (1000 if count["loops"] == 1 else expected_rows), (
                                json.dumps(plan)
                            )
                            continue
                        assert count["loops"] == 1, json.dumps(plan)
                        if relation == "assertion_revision":
                            # Sorting before LIMIT may read the bounded 1000-revision history.
                            assert expected_rows <= count["rows"] <= 1000, json.dumps(plan)
                        else:
                            assert count["rows"] == expected_rows, json.dumps(plan)
                            work = (count["rows"] + count["filtered"]) * count["loops"]
                            assert work <= expected_rows, json.dumps(plan)

    asyncio.run(inspect())


def test_hidden_historical_relation_revision_invalidates_selected_page_not_sentinel(env):
    source, old_target, current_target = [
        create_entity(env, name) for name in ("Source", "Private historical", "Current")
    ]
    relation = create_relation(env, source, old_target)
    appended = env.client.post(
        f"/v1/relations/{relation['memory_id']}/revisions",
        headers=env.headers(), json=revise_body(relation, current_target),
    )
    assert appended.status_code == 201, appended.text
    memory = relation["memory_id"]
    before = history(env, memory)
    assert before.status_code == 200 and len(before.json()["revisions"]) == 2
    policy = sql.Identifier("history_hidden_revision_" + uuid4().hex)
    with psycopg.connect(env.admin_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                """CREATE POLICY {} ON memory.relation_revision AS RESTRICTIVE
                   FOR SELECT TO pgag_runtime USING (
                       tenant_id <> {} OR assertion_id <> {} OR revision <> 1
                   )""",
            ).format(policy, sql.Literal(env.tenants[0]), sql.Literal(memory)),
        )
        try:
            latest = history(env, memory, max_items=1)
            assert latest.status_code == 200, latest.text
            assert [row["revision"] for row in latest.json()["revisions"]] == [2]
            assert latest.json()["next_before_revision"] == 2
            assert latest.json()["revisions"][0]["relation"]["target_entity"] == (
                current_target["memory_id"]
            )
            for options in ({}, {"max_items": 1, "before_revision": 2}):
                hidden = history(env, memory, **options)
                assert hidden.status_code == 409
                assert hidden.json()["code"] == "relation_invalidated"
                assert "revisions" not in hidden.json()
                for identifier in (memory, old_target["memory_id"], current_target["memory_id"]):
                    assert identifier not in hidden.text
        finally:
            admin.execute(
                sql.SQL("DROP POLICY {} ON memory.relation_revision").format(policy),
            )
    assert history(env, memory).json() == before.json()
