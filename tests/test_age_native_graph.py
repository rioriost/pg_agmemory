"""Pinned native VLE contracts; live checks require a dedicated patched AGE database."""

import asyncio
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import psycopg
import pytest
import test_graph_conformance as oracle
from psycopg import sql

from pg_agmemory import age_graph
from pg_agmemory.database import RuntimeValidationError
from pg_agmemory.graph_generation import GraphGenerationRequest, graph_generation
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import ExpandGraph, GraphResult
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

GRAPH = "pgag_age_" + "a" * 32
LIVE = pytest.mark.skipif(
    os.environ.get("PGAG_TEST_AGE_NATIVE") != "1",
    reason="PGAG_TEST_AGE_NATIVE=1 requires a dedicated patched, preloaded AGE database",
)


def test_native_templates_and_budget_are_not_host_bfs():
    for direction in ("outgoing", "incoming", "both"):
        query = age_graph.path_query(GRAPH, direction, 2).as_string()
        assert query.count("ag_catalog.cypher(") == 2
        assert "LINK*1..1" in query and "LINK*2..2" in query
        assert "WHERE s.id IN $seeds" in query
        assert "JOIN canonical_edges e0" in query and "JOIN canonical_edges e1" in query
        assert "p.seed<>p.middle AND p.middle<>p.target" in query
        assert "ORDER BY hops,seed,edge0,revision0,COALESCE(middle,target)" in query
        assert query.rindex("LIMIT %(path_limit)s") > query.index("SELECT * FROM authorized")
        assert "c.revision0::bigint" in query and "c.revision1::bigint" in query
        assert "revision0::jsonb" not in query
        assert "%(age_parameters)s::ag_catalog.agtype" in query


@pytest.mark.parametrize("name", [
    "", None, GRAPH + "\n", GRAPH + "'; SELECT 1", "pgag_age_" + "A" * 32,
    "pgag_m3_" + "a" * 32, "pgag_age_" + "a" * 31,
])
def test_owned_graph_names_only(name):
    with pytest.raises(MemoryError, match="graph_projection_invalid"):
        age_graph.validate_graph_name(name)


@pytest.mark.parametrize("label", age_graph.LABELS)
def test_label_authority_precedes_vle(label):
    policy = age_graph.label_policy(GRAPH, label).as_string()
    for term in (
        "memory.current_tenant()", "memory.current_principal()", "pgag.m3_graph",
        "pgag.m3_as_of", "pgag.m3_known_at", "pgag.m3_scope_ids",
        "memory.entity_evidence", "memory.episode", "memory.object",
    ):
        assert term in policy
    if label in ("LINK", "_ag_label_edge"):
        assert "ar.valid_time @>" in policy and "ar.system_time @>" in policy
        assert "pgag.m3_predicates" in policy
        assert "r.source_id=(properties::jsonb->>'source_id')::uuid" in policy
        assert "rr.target_id=(properties::jsonb->>'target_id')::uuid" in policy


def test_completeness_checks_canonical_inputs_properties_and_physical_endpoints():
    query = age_graph.completeness_query(GRAPH).as_string()
    assert "canonical_nodes" in query and "canonical_edges" in query
    assert "1<>(\n" in query
    assert "s.id=p.start_id" in query and "t.id=p.end_id" in query
    assert "p.p=jsonb_build_object(" in query
    assert "LIMIT %(node_limit)s" in query and "LIMIT %(edge_limit)s" in query
    assert "graph_generation" not in query and "dedup_secret" not in query


def runtime_connection(*, runtime=None, build=None, role=None, preloaded=True):
    conn = Mock()
    cursor = Mock()
    cursor.fetchone = AsyncMock(side_effect=[
        role or {"rolsuper": False, "rolbypassrls": False, "owns_tables": False},
        runtime or {
            "pg": 180006, "preload_diagnostic": True, "extversion": "1.8.0",
            "nspname": "ag_catalog", "stamped": True,
        },
        {
            "build": build if build is not None else {"commit": age_graph.AGE_COMMIT},
            "preloaded": preloaded,
        },
    ])
    conn.execute = AsyncMock(return_value=cursor)
    return conn


@pytest.mark.parametrize("build", [
    {}, {"commit": "e43dc1a12b78fba4acef9835b2b10379b8d243b4"},
    {"commit": age_graph.AGE_COMMIT + "\n"}, [], "1.8.0",
])
def test_catalog_version_is_not_a_patched_build_stamp(build):
    with pytest.raises(RuntimeValidationError) as error:
        asyncio.run(age_graph.validate_age_connection(runtime_connection(build=build)))
    assert error.value.code == "extension_version_mismatch"


def test_valid_patched_runtime_stamp():
    asyncio.run(age_graph.validate_age_connection(runtime_connection()))


@pytest.mark.parametrize("captured_schema_version", [20, None])
def test_historical_schema_projection_is_stale_before_labels_or_native_reads(
    monkeypatch, captured_schema_version,
):
    conn = Mock()
    cursor = Mock()
    cursor.fetchone = AsyncMock(side_effect=[
        {"access_epoch": 1, "deletion_epoch": 1},
        {
            "generation_id": uuid4(), "graph_name": GRAPH, "age_commit": age_graph.AGE_COMMIT,
            "captured_access_epoch": 1, "captured_deletion_epoch": 1,
            "node_count": 0, "edge_revision_count": 0,
            "captured_schema_version": captured_schema_version,
        },
    ])
    conn.execute = AsyncMock(return_value=cursor)
    monkeypatch.setattr(age_graph, "validate_age_connection", AsyncMock())
    labels = AsyncMock()
    monkeypatch.setattr(age_graph, "_validate_labels", labels)
    body = ExpandGraph(
        scope_ids=[uuid4()], seeds=[uuid4()], relation_types=["depends_on"],
        purpose="historical schema refusal",
    )
    with pytest.raises(MemoryError, match="^graph_projection_stale$"):
        asyncio.run(age_graph.AgeGraph(Mock(conn=conn, tenant=uuid4())).expand(body))
    labels.assert_not_awaited()
    assert conn.execute.await_count == 4
    assert "captured_schema_version" in conn.execute.call_args.args[0]


def test_profile_owns_narrow_preload_diagnostic():
    conn = runtime_connection()
    asyncio.run(age_graph.validate_age_connection(conn))
    catalog_query = conn.execute.call_args_list[1].args[0]
    assert "p.proname='pgag_age_preloaded'" in catalog_query
    assert "p.pronargs=0 AND p.prorettype='boolean'::regtype" in catalog_query
    assert "p.provolatile='s' AND p.prosecdef" in catalog_query
    assert "p.proconfig=ARRAY['search_path=pg_catalog']" in catalog_query
    assert "p.proowner=e.extowner" in catalog_query
    assert "NOT pg_has_role(current_user,p.proowner,'MEMBER')" in catalog_query
    assert "has_function_privilege(current_user,p.oid,'EXECUTE')" in catalog_query
    assert "ag_catalog.pgag_age_preloaded()" in conn.execute.call_args_list[2].args[0]
    assert all("memory_ops.age_preloaded" not in call.args[0]
               for call in conn.execute.call_args_list)


@pytest.mark.parametrize("preloaded", [False, None, "true"])
def test_profile_must_report_preloaded_true(preloaded):
    with pytest.raises(RuntimeValidationError) as error:
        asyncio.run(age_graph.validate_age_connection(runtime_connection(preloaded=preloaded)))
    assert error.value.code == "extension_version_mismatch"


def test_inaccessible_or_missing_stamp_is_safe_failure():
    conn = runtime_connection()
    conn.execute.side_effect = [
        conn.execute.return_value,
        psycopg.errors.UndefinedFunction("private server detail"),
    ]
    with pytest.raises(RuntimeValidationError) as error:
        asyncio.run(age_graph.validate_age_connection(conn))
    assert error.value.code == "extension_version_mismatch"
    assert "private server detail" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("pg", 180005), ("extversion", "1.8.1"), ("nspname", "public"),
    ("preload_diagnostic", False), ("stamped", False),
])
def test_runtime_profile_mismatch_fails_closed(field, value):
    runtime = {
        "pg": 180006, "preload_diagnostic": True, "extversion": "1.8.0",
        "nspname": "ag_catalog", "stamped": True, field: value,
    }
    conn = runtime_connection(runtime=runtime)
    with pytest.raises(RuntimeValidationError) as error:
        asyncio.run(age_graph.validate_age_connection(conn))
    assert error.value.code == "extension_version_mismatch"
    assert conn.execute.await_count == 2


def test_missing_extension_rejects_before_calling_profile_functions():
    conn = runtime_connection()
    conn.execute.return_value.fetchone.side_effect = [
        {"rolsuper": False, "rolbypassrls": False, "owns_tables": False}, None,
    ]
    with pytest.raises(RuntimeValidationError) as error:
        asyncio.run(age_graph.validate_age_connection(conn))
    assert error.value.code == "extension_version_mismatch"
    assert conn.execute.await_count == 2


@pytest.mark.parametrize("field", ["rolsuper", "rolbypassrls", "owns_tables"])
def test_privileged_runtime_is_rejected(field):
    role = {"rolsuper": False, "rolbypassrls": False, "owns_tables": False, field: True}
    conn = runtime_connection(role=role)
    with pytest.raises(RuntimeValidationError) as error:
        asyncio.run(age_graph.validate_age_connection(conn))
    assert error.value.code == "runtime_role_invalid"
    assert conn.execute.await_count == 1


def test_context_is_bound_transaction_locally():
    conn = Mock()
    conn.execute = AsyncMock()
    data = ExpandGraph(
        scope_ids=[uuid4()], seeds=[uuid4()], relation_types=["depends_on"], purpose="test"
    )
    now = datetime.now(UTC)
    asyncio.run(age_graph.bind_projection_context(conn, GRAPH, data, now, now))
    query, params = conn.execute.call_args.args
    assert query.count(",%s,true)") == 5
    assert params == (
        GRAPH, now.isoformat(), now.isoformat(), "{" + str(data.scope_ids[0]) + "}",
        "{depends_on}",
    )


@pytest.fixture(scope="session")
def native_profile(database):
    admin_url, runtime_url, _ = database
    with psycopg.connect(admin_url) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS age")
        admin.execute("GRANT USAGE ON SCHEMA ag_catalog TO pgag_runtime")
        admin.execute("GRANT SELECT ON ag_catalog.ag_graph,ag_catalog.ag_label TO pgag_runtime")
    asyncio.run(age_graph.validate_age_runtime(runtime_url))


@pytest.fixture(autouse=True)
def require_native_profile(request):
    if request.node.get_closest_marker("integration") is not None:
        request.getfixturevalue("native_profile")


@contextmanager
def projection(env):
    """Test-only physical fixture: registry publication is covered by admin tests."""
    generation = uuid4()
    name = "pgag_age_" + generation.hex
    with graph_generation(env.admin_url, GraphGenerationRequest(
        operation="get", tenant_id=env.tenants[0],
    )) as current:
        pass
    with graph_generation(env.admin_url, GraphGenerationRequest(
        operation="begin", tenant_id=env.tenants[0], expected_revision=current.revision,
        generation_id=generation, expected_input_digest=current.current_input_digest,
        profile_digest="b" * 64,
    )) as reserved:
        pass
    with graph_generation(env.admin_url, GraphGenerationRequest(
        operation="record", tenant_id=env.tenants[0], expected_revision=reserved.revision,
        generation_id=generation, expected_input_digest=current.current_input_digest,
        artifact_digest="a" * 64,
    )):
        pass
    node, edge = sql.Identifier(name, "Node"), sql.Identifier(name, "LINK")
    with psycopg.connect(env.admin_url) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS age")
        admin.execute("SET LOCAL search_path = ag_catalog, pg_catalog")
        admin.execute("SELECT ag_catalog.create_graph(%s)", (name,))
        admin.execute("SELECT ag_catalog.create_vlabel(%s,'Node')", (name,))
        admin.execute("SELECT ag_catalog.create_elabel(%s,'LINK')", (name,))
        admin.execute(sql.SQL(
            """INSERT INTO {}(properties)
               SELECT jsonb_build_object('id',id::text,'tenant_id',tenant_id::text,
                                         'scope_id',scope_id::text)::ag_catalog.agtype
               FROM memory.entity WHERE tenant_id=%s ORDER BY id"""
        ).format(node), (env.tenants[0],))
        admin.execute(sql.SQL(
            """INSERT INTO {edge}(start_id,end_id,properties)
               SELECT s.id,t.id,jsonb_build_object(
                   'id',r.id::text,'revision',v.revision,'tenant_id',r.tenant_id::text,
                   'scope_id',r.scope_id::text,'source_id',r.source_id::text,
                   'target_id',v.target_id::text)::ag_catalog.agtype
               FROM memory.relation r JOIN memory.relation_revision v
                 ON v.tenant_id=r.tenant_id AND v.assertion_id=r.id
               JOIN {node} s ON (s.properties::jsonb->>'id')::uuid=r.source_id
               JOIN {node} t ON (t.properties::jsonb->>'id')::uuid=v.target_id
               WHERE r.tenant_id=%s ORDER BY r.id,v.revision"""
        ).format(edge=edge, node=node), (env.tenants[0],))
        counts = admin.execute(sql.SQL(
            "SELECT (SELECT count(*) FROM {}),(SELECT count(*) FROM {})"
        ).format(node, edge)).fetchone()
        admin.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(name)))
        admin.execute(sql.SQL(
            "GRANT USAGE ON SCHEMA {},ag_catalog TO pgag_runtime"
        ).format(sql.Identifier(name)))
        admin.execute("GRANT SELECT ON ag_catalog.ag_graph,ag_catalog.ag_label TO pgag_runtime")
        for label in age_graph.LABELS:
            table = sql.Identifier(name, label)
            admin.execute(sql.SQL("REVOKE ALL ON {} FROM PUBLIC,pgag_runtime").format(table))
            admin.execute(sql.SQL("GRANT SELECT ON {} TO pgag_runtime").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(table))
            admin.execute(sql.SQL(
                "CREATE POLICY canonical_read ON {} FOR SELECT TO pgag_runtime USING ({})"
            ).format(table, age_graph.label_policy(name, label)))
        admin.execute(
            """INSERT INTO memory_ops.age_projection(
                   tenant_id,generation_id,graph_name,artifact_digest,profile_digest,input_digest,
                   captured_access_epoch,captured_deletion_epoch,age_commit,node_count,
                   edge_revision_count,enabled)
               SELECT id,%s,%s,%s,%s,%s,access_epoch,deletion_epoch,%s,%s,%s,true
               FROM memory.tenant WHERE id=%s
               ON CONFLICT (tenant_id) DO UPDATE SET generation_id=EXCLUDED.generation_id,
                   graph_name=EXCLUDED.graph_name,
                   input_digest=EXCLUDED.input_digest,
                   captured_access_epoch=EXCLUDED.captured_access_epoch,
                   captured_deletion_epoch=EXCLUDED.captured_deletion_epoch,
                   node_count=EXCLUDED.node_count,edge_revision_count=EXCLUDED.edge_revision_count,
                   enabled=true,revision=memory_ops.age_projection.revision+1""",
            (generation, name, "a" * 64, "b" * 64, current.current_input_digest,
             age_graph.AGE_COMMIT, *counts, env.tenants[0]),
        )
    try:
        yield name, generation
    finally:
        with psycopg.connect(env.admin_url) as admin:
            admin.execute(
                """UPDATE memory_ops.age_projection SET enabled=false,revision=revision+1
                   WHERE tenant_id=%s""", (env.tenants[0],)
            )
            admin.execute("SET LOCAL search_path = ag_catalog, pg_catalog")
            admin.execute("SELECT ag_catalog.drop_graph(%s,true)", (name,))


async def read_graph(env, request, backend=age_graph.AgeGraph):
    async with principal_connection(env.settings.database_url, env.subjects[0]) as (conn, identity):
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await bind_identity(conn, env.subjects[0], identity)
            result = await backend(MemoryService(conn, identity)).expand(
                ExpandGraph.model_validate(request)
            )
    return GraphResult.model_validate(result).model_dump(mode="json")


class NativeGraph(oracle.GraphFixture):
    def expand(self, request):
        with projection(self.env) as (_, generation):
            result = asyncio.run(read_graph(self.env, request))
        assert result["backend"] == "age"
        assert result["projection_watermark"] == str(generation)
        normalized = {**result, "backend": "sql", "projection_watermark": None}
        frozen = {
            **request, "as_of": result["as_of"], "known_at": result["known_at"],
        }
        assert normalized == asyncio.run(read_graph(self.env, frozen, SqlGraph))
        return normalized


@pytest.fixture
def canonical_graph(env):
    return NativeGraph(env)


def native_case(case):
    @wraps(case)
    def run(*args, **kwargs):
        return case(*args, **kwargs)

    return LIVE(pytest.mark.integration(run))


test_multiseed_parallel_cycles_and_global_prefix_budgets = native_case(
    oracle.test_multiseed_parallel_cycles_and_global_prefix_budgets
)
test_second_hop_target_revision_and_half_open_times = native_case(
    oracle.test_second_hop_target_revision_and_half_open_times
)
test_scope_seed_filters_and_historical_current_acl = native_case(
    oracle.test_scope_seed_filters_and_historical_current_acl
)
test_retained_payload_cannot_bridge_or_leak_at_historical_time = native_case(
    oracle.test_retained_payload_cannot_bridge_or_leak_at_historical_time
)
test_default_time_capture_and_isolated_loop_only_seeds = native_case(
    oracle.test_default_time_capture_and_isolated_loop_only_seeds
)


def small_graph(env):
    graph = oracle.GraphFixture(env)
    graph.node("root")
    graph.node("leaf")
    graph.edge("link", "root", "leaf")
    return graph


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("mutation", [
    "node", "edge", "revision", "physical_endpoint", "missing_node", "duplicate_node",
    "access_epoch", "deletion_epoch", "disabled", "writable_label", "rls_disabled",
])
def test_published_projection_fails_closed_after_changes(env, mutation, monkeypatch):
    graph = small_graph(env)
    with projection(env) as (name, _):
        assert asyncio.run(read_graph(env, graph.request(["root"])))["paths"]
        expected = "graph_projection_stale"
        if mutation == "node":
            graph.node("new")
        elif mutation == "edge":
            graph.edge("new", "leaf", "root")
        elif mutation == "revision":
            graph.revise("link", "root")
        else:
            with psycopg.connect(env.admin_url) as admin:
                if mutation == "physical_endpoint":
                    admin.execute(sql.SQL("UPDATE {} SET end_id=start_id").format(
                        sql.Identifier(name, "LINK")
                    ))
                elif mutation == "missing_node":
                    admin.execute(sql.SQL(
                        "DELETE FROM {} WHERE properties::jsonb->>'id'=%s"
                    ).format(sql.Identifier(name, "Node")), (graph.nodes["leaf"]["memory_id"],))
                elif mutation == "duplicate_node":
                    admin.execute(sql.SQL(
                        "INSERT INTO {}(properties) SELECT properties FROM {}"
                    ).format(sql.Identifier(name, "Node"), sql.Identifier(name, "Node")))
                elif mutation in ("access_epoch", "deletion_epoch"):
                    admin.execute(sql.SQL("UPDATE memory.tenant SET {}={}+1 WHERE id=%s").format(
                        sql.Identifier(mutation), sql.Identifier(mutation)
                    ), (env.tenants[0],))
                elif mutation == "disabled":
                    admin.execute(
                        "UPDATE memory_ops.age_projection SET enabled=false,revision=revision+1"
                    )
                    expected = "graph_projection_unavailable"
                elif mutation == "writable_label":
                    admin.execute(sql.SQL("GRANT UPDATE ON {} TO pgag_runtime").format(
                        sql.Identifier(name, "LINK")
                    ))
                    expected = "graph_projection_invalid"
                elif mutation == "rls_disabled":
                    admin.execute(sql.SQL("ALTER TABLE {} DISABLE ROW LEVEL SECURITY").format(
                        sql.Identifier(name, "Node")
                    ))
                    expected = "graph_projection_invalid"
        monkeypatch.setattr(
            SqlGraph, "expand", AsyncMock(side_effect=AssertionError("no fallback"))
        )
        with pytest.raises(MemoryError, match=expected):
            asyncio.run(read_graph(env, graph.request(["root"])))


@LIVE
@pytest.mark.integration
def test_missing_projection_does_not_fall_back_to_sql(env, monkeypatch):
    graph = small_graph(env)
    monkeypatch.setattr(SqlGraph, "expand", AsyncMock(side_effect=AssertionError("no fallback")))
    with pytest.raises(MemoryError, match="graph_projection_unavailable"):
        asyncio.run(read_graph(env, graph.request(["root"])))


@LIVE
@pytest.mark.integration
def test_retained_projection_respects_predicate_narrowing_and_historical_additions(env):
    graph = small_graph(env)
    with projection(env) as (_, generation):
        historical = graph.request(["root"])
        graph.node("later")
        graph.edge("later", "root", "later", predicate="affects")
        result = asyncio.run(read_graph(env, historical))
        oracle.assert_graph_contract(result, graph, historical, backend="age",
                                     watermark=str(generation))
        request = graph.request(["root"], relation_types=["works_for"])
        # A new eligible isolated node is itself a new graph input, even with no eligible edge.
        with pytest.raises(MemoryError, match="graph_projection_stale"):
            asyncio.run(read_graph(env, request))
        assert "later" not in json.dumps(result)


@LIVE
@pytest.mark.integration
def test_acl_expiry_filters_retained_projection_without_an_epoch_change(env):
    graph = small_graph(env)
    request = graph.request(["root"])
    with projection(env) as (_, generation):
        assert asyncio.run(read_graph(env, request))["paths"]
        with psycopg.connect(env.admin_url) as admin:
            admin.execute(
                """UPDATE memory.scope_member SET expires_at=clock_timestamp()-interval '1 second'
                   WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                (env.tenants[0], env.scopes[0], env.principals[0]),
            )
        result = asyncio.run(read_graph(env, request))
        oracle.assert_graph_contract(
            result, graph, request, backend="age", watermark=str(generation), readable_scopes=set(),
        )
        assert result["paths"] == result["nodes"] == result["edges"] == []


@LIVE
@pytest.mark.integration
def test_native_query_errors_never_invoke_sql_traversal(env, monkeypatch):
    graph = small_graph(env)
    with projection(env):
        monkeypatch.setattr(
            age_graph, "path_query", lambda *args: sql.SQL("SELECT 1/0 AS native_error")
        )
        monkeypatch.setattr(
            SqlGraph, "expand", AsyncMock(side_effect=AssertionError("no SQL fallback"))
        )
        with pytest.raises(psycopg.errors.DivisionByZero):
            asyncio.run(read_graph(env, graph.request(["root"])))


@LIVE
@pytest.mark.integration
def test_extra_eligible_edge_cannot_duplicate_a_path_within_published_bounds(env):
    graph = small_graph(env)
    graph.edge("excluded", "root", "leaf", predicate="affects")
    request = graph.request(["root"], relation_types=["depends_on"])
    with projection(env) as (name, _):
        assert len(asyncio.run(read_graph(env, request))["paths"]) == 1
        with psycopg.connect(env.admin_url) as admin:
            admin.execute("SET LOCAL search_path = ag_catalog, pg_catalog")
            admin.execute(sql.SQL(
                """INSERT INTO {edge}(start_id,end_id,properties)
                   SELECT start_id,end_id,
                       (properties::jsonb || '{{"extra":true}}'::jsonb)::ag_catalog.agtype
                   FROM {edge} WHERE properties::jsonb->>'id'=%s"""
            ).format(edge=sql.Identifier(name, "LINK")), (
                graph.edges["link"]["revisions"][0]["assertion"]["memory_id"],
            ))
        with pytest.raises(MemoryError, match="graph_projection_stale"):
            asyncio.run(read_graph(env, request))
