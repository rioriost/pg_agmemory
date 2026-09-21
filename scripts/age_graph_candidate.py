"""Disposable AGE fixed-hop candidate, not an activated application backend.

Build only on a quiesced fixture using Dockerfile.age. Rebuild before each request;
there is no generation, freshness, concurrent rebuild, or production qualification.
Native VLE remains an inert strategy until a separately pinned artifact qualifies.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pg_agmemory.database import Connection
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import ExpandGraph, GraphPath, GraphResult
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

AGE_COMMIT = "e43dc1a12b78fba4acef9835b2b10379b8d243b4"
AGE_VERSION = "1.8.0"
FORMAT = "pgag-age-fixed-candidate-v1"
MAX_NODES = 1024
MAX_EDGE_REVISIONS = 4096
LABELS = ("_ag_label_vertex", "_ag_label_edge", "Node", "LINK")
GRAPH_PATTERN = re.compile(r"pgag_m3_[0-9a-f]{32}", re.ASCII)
METADATA_TABLE = "_pgag_candidate_metadata"
DIRECTIONS = ("outgoing", "incoming", "both")


def validate_graph_name(graph_name: str) -> str:
    if not isinstance(graph_name, str) or GRAPH_PATTERN.fullmatch(graph_name) is None:
        raise MemoryError("graph_projection_invalid", 409)
    return graph_name


def validate_strategy(strategy: str) -> None:
    if strategy != "fixed":
        raise MemoryError("graph_backend_unqualified", 409)


def validate_metadata(value: Any, graph_name: str) -> dict[str, Any]:
    validate_graph_name(graph_name)
    expected = {
        "format": FORMAT, "graph_name": graph_name, "age_commit": AGE_COMMIT,
        "age_version": AGE_VERSION, "candidate_only": True, "activation_permitted": False,
        "projection_watermark": None,
    }
    if not isinstance(value, dict) or set(value) != set(expected) | {
        "node_count", "edge_revision_count",
    }:
        raise MemoryError("graph_projection_invalid", 409)
    if any(type(value[key]) is not type(item) or value[key] != item
           for key, item in expected.items()):
        raise MemoryError("graph_projection_invalid", 409)
    for key, maximum in (("node_count", MAX_NODES), ("edge_revision_count", MAX_EDGE_REVISIONS)):
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise MemoryError("graph_projection_invalid", 409)
    return value


def cypher_template(direction: str, *, strategy: str = "fixed") -> str:
    if direction not in ("outgoing", "incoming"):
        raise ValueError("a template has exactly one directed orientation")
    if strategy not in ("fixed", "native_vle"):
        raise MemoryError("graph_backend_unqualified", 409)
    if strategy == "fixed":
        edge = "-[e:LINK]->" if direction == "outgoing" else "<-[e:LINK]-"
        return (
            f"MATCH (s:Node {{id: $node}}){edge}(n:Node) "
            "RETURN e.id, e.revision, s.id, n.id"
        )
    edge = "-[:LINK*1..1]->" if direction == "outgoing" else "<-[:LINK*1..1]-"
    return (
        f"MATCH p=(s:Node {{id: $node}}){edge}(n:Node) "
        "WITH s, n, relationships(p)[0] AS e RETURN e.id, e.revision, s.id, n.id"
    )


def adjacent_query(graph_name: str, direction: str, *, strategy: str = "fixed") -> sql.Composed:
    validate_graph_name(graph_name)
    validate_strategy(strategy)
    if direction not in DIRECTIONS:
        raise ValueError("invalid graph direction")
    branches = []
    for orientation in (("outgoing", "incoming") if direction == "both" else (direction,)):
        template = cypher_template(orientation, strategy=strategy)
        origin = sql.SQL("r.source_id") if orientation == "outgoing" else sql.SQL("v.target_id")
        target = sql.SQL("v.target_id") if orientation == "outgoing" else sql.SQL("r.source_id")
        # AGE requires constant graph/Cypher arguments. The graph is a validated
        # owned identifier; all request values are in the bound agtype map.
        cypher = sql.SQL("$pgag_candidate$" + template + "$pgag_candidate$")
        branches.append(sql.SQL(
            """SELECT r.id,v.revision,r.source_id,v.target_id,{target} AS next_id
               FROM ag_catalog.cypher({graph},{cypher},%(age_parameters)s::ag_catalog.agtype)
                 AS c(edge_id ag_catalog.agtype,revision ag_catalog.agtype,
                      origin_id ag_catalog.agtype,next_id ag_catalog.agtype)
               JOIN memory.relation r
                 ON r.tenant_id=%(tenant)s AND r.id=(c.edge_id::jsonb #>> '{{}}')::uuid
               JOIN memory.relation_revision v
                 ON v.tenant_id=r.tenant_id AND v.assertion_id=r.id
                AND v.revision=c.revision::bigint
               WHERE {origin}=%(node)s
                 AND {origin}=(c.origin_id::jsonb #>> '{{}}')::uuid
                 AND {target}=(c.next_id::jsonb #>> '{{}}')::uuid"""
        ).format(
            target=target, origin=origin, graph=sql.Literal(graph_name), cypher=cypher,
        ))
    return sql.SQL(" UNION ALL ").join(branches)


def _context_policy(graph_name: str) -> sql.Composed:
    return sql.SQL(
        """current_setting('pgag.m3_graph',true)={}
           AND memory.current_tenant() IS NOT NULL AND memory.current_principal() IS NOT NULL
           AND NULLIF(current_setting('pgag.m3_as_of',true),'')::timestamptz IS NOT NULL
           AND NULLIF(current_setting('pgag.m3_known_at',true),'')::timestamptz IS NOT NULL"""
    ).format(sql.Literal(graph_name))


def _entity_predicate(alias: str, object_alias: str) -> sql.Composed:
    # These aliases are internal constants, never caller-controlled identifiers.
    return sql.SQL(
        """{e}.scope_id=ANY(NULLIF(current_setting('pgag.m3_scope_ids',true),'')::uuid[])
           AND {o}.created_at<=NULLIF(current_setting('pgag.m3_known_at',true),'')::timestamptz
           AND (SELECT count(*) FROM memory.entity_evidence ee
                JOIN memory.episode ep ON ep.tenant_id=ee.tenant_id AND ep.id=ee.source_id
                WHERE ee.tenant_id={e}.tenant_id AND ee.entity_id={e}.id)={e}.reference_count"""
    ).format(e=sql.Identifier(alias), o=sql.Identifier(object_alias))


def label_policy(graph_name: str, label: str) -> sql.Composed:
    validate_graph_name(graph_name)
    if label not in LABELS:
        raise ValueError("only owned AGE labels are allowed")
    common = _context_policy(graph_name)
    if label in ("Node", "_ag_label_vertex"):
        return sql.SQL(
            """{common} AND EXISTS (
                SELECT 1 FROM memory.entity e JOIN memory.object o USING (tenant_id,id)
                WHERE e.tenant_id=memory.current_tenant()
                  AND e.tenant_id=(properties::jsonb->>'tenant_id')::uuid
                  AND e.id=(properties::jsonb->>'id')::uuid
                  AND e.scope_id=(properties::jsonb->>'scope_id')::uuid
                  AND {entity})"""
        ).format(common=common, entity=_entity_predicate("e", "o"))
    return sql.SQL(
        """{common} AND EXISTS (
            SELECT 1 FROM memory.relation r
            JOIN memory.relation_revision rr
              ON rr.tenant_id=r.tenant_id AND rr.assertion_id=r.id
            JOIN memory.assertion a ON a.tenant_id=r.tenant_id AND a.id=r.id
            JOIN memory.assertion_revision ar ON ar.tenant_id=rr.tenant_id
              AND ar.assertion_id=rr.assertion_id AND ar.revision=rr.revision
            JOIN memory.entity se ON se.tenant_id=r.tenant_id AND se.id=r.source_id
            JOIN memory.object so ON so.tenant_id=se.tenant_id AND so.id=se.id
            JOIN memory.entity te ON te.tenant_id=r.tenant_id AND te.id=rr.target_id
            JOIN memory.object t_o ON t_o.tenant_id=te.tenant_id AND t_o.id=te.id
            WHERE r.tenant_id=memory.current_tenant()
              AND r.tenant_id=(properties::jsonb->>'tenant_id')::uuid
              AND r.id=(properties::jsonb->>'id')::uuid
              AND r.scope_id=(properties::jsonb->>'scope_id')::uuid
              AND rr.revision=(properties::jsonb->>'revision')::bigint
              AND r.source_id=(properties::jsonb->>'source_id')::uuid
              AND rr.target_id=(properties::jsonb->>'target_id')::uuid
              AND r.scope_id=ANY(
                  NULLIF(current_setting('pgag.m3_scope_ids',true),'')::uuid[])
              AND a.predicate=ANY(
                  NULLIF(current_setting('pgag.m3_predicates',true),'')::text[])
              AND ar.valid_time @> NULLIF(
                  current_setting('pgag.m3_as_of',true),'')::timestamptz
              AND ar.system_time @> NULLIF(
                  current_setting('pgag.m3_known_at',true),'')::timestamptz
              AND {source} AND {target})"""
    ).format(
        common=common, source=_entity_predicate("se", "so"),
        target=_entity_predicate("te", "t_o"),
    )


def _admin_connection(admin_url: str) -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(
        admin_url, row_factory=dict_row, connect_timeout=5,
        options="-c statement_timeout=5000 -c lock_timeout=5000",
    )


def build_projection(admin_url: str, graph_name: str) -> dict[str, Any]:
    validate_graph_name(graph_name)
    with _admin_connection(admin_url) as admin:
        admin.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # This rejects an RLS-filtered builder instead of projecting an empty
        # subset; it does not grant a role permission to bypass RLS.
        admin.execute("SET LOCAL row_security = off")
        admin.execute("CREATE EXTENSION IF NOT EXISTS age VERSION '1.8.0'")
        identity = admin.execute(
            """SELECT current_setting('server_version_num')::int AS pg,
                      current_setting('shared_preload_libraries') AS preload,
                      extversion FROM pg_extension WHERE extname='age'"""
        ).fetchone()
        if (not identity or identity["pg"] != 180006 or identity["extversion"] != AGE_VERSION
                or "age" not in identity["preload"].split(",")):
            raise MemoryError("graph_backend_unqualified", 409)
        if admin.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s", (graph_name,),
        ).fetchone():
            raise MemoryError("graph_projection_exists", 409)
        counts = admin.execute(
            """SELECT
                (SELECT count(*) FROM (SELECT 1 FROM memory.entity LIMIT %s) n) AS nodes,
                (SELECT count(*) FROM
                    (SELECT 1 FROM memory.relation_revision LIMIT %s) e) AS edges""",
            (MAX_NODES + 1, MAX_EDGE_REVISIONS + 1),
        ).fetchone()
        assert counts is not None
        metadata = validate_metadata({
            "format": FORMAT, "graph_name": graph_name, "age_commit": AGE_COMMIT,
            "age_version": AGE_VERSION, "candidate_only": True, "activation_permitted": False,
            "projection_watermark": None, "node_count": counts["nodes"],
            "edge_revision_count": counts["edges"],
        }, graph_name)
        admin.execute("SET LOCAL search_path = ag_catalog, pg_catalog")
        admin.execute("SELECT ag_catalog.create_graph(%s)", (graph_name,))
        admin.execute("SELECT ag_catalog.create_vlabel(%s,%s)", (graph_name, "Node"))
        admin.execute("SELECT ag_catalog.create_elabel(%s,%s)", (graph_name, "LINK"))
        node = sql.Identifier(graph_name, "Node")
        edge = sql.Identifier(graph_name, "LINK")
        admin.execute(sql.SQL(
            """INSERT INTO {}(properties)
               SELECT jsonb_build_object('id',id::text,'tenant_id',tenant_id::text,
                                         'scope_id',scope_id::text)::ag_catalog.agtype
               FROM memory.entity ORDER BY tenant_id,id"""
        ).format(node))
        admin.execute(sql.SQL(
            """INSERT INTO {edge}(start_id,end_id,properties)
               SELECT s.id,t.id,jsonb_build_object(
                   'id',r.id::text,'revision',v.revision,'tenant_id',r.tenant_id::text,
                   'scope_id',r.scope_id::text,'source_id',r.source_id::text,
                   'target_id',v.target_id::text)::ag_catalog.agtype
               FROM memory.relation r JOIN memory.relation_revision v
                 ON v.tenant_id=r.tenant_id AND v.assertion_id=r.id
               JOIN {node} s ON (s.properties::jsonb->>'id')::uuid=r.source_id
                 AND (s.properties::jsonb->>'tenant_id')::uuid=r.tenant_id
               JOIN {node} t ON (t.properties::jsonb->>'id')::uuid=v.target_id
                 AND (t.properties::jsonb->>'tenant_id')::uuid=r.tenant_id
               ORDER BY r.tenant_id,r.id,v.revision"""
        ).format(edge=edge, node=node))
        actual = admin.execute(sql.SQL(
            "SELECT (SELECT count(*) FROM {}) AS nodes,(SELECT count(*) FROM {}) AS edges"
        ).format(node, edge)).fetchone()
        if actual != counts:
            raise MemoryError("graph_projection_invalid", 409)
        admin.execute(sql.SQL(
            "REVOKE ALL ON SCHEMA {} FROM PUBLIC"
        ).format(sql.Identifier(graph_name)))
        admin.execute(sql.SQL(
            "GRANT USAGE ON SCHEMA {},ag_catalog TO pgag_runtime"
        ).format(sql.Identifier(graph_name)))
        admin.execute(
            "GRANT SELECT ON ag_catalog.ag_graph,ag_catalog.ag_label TO pgag_runtime"
        )
        for label in LABELS:
            table = sql.Identifier(graph_name, label)
            admin.execute(sql.SQL("REVOKE ALL ON {} FROM PUBLIC,pgag_runtime").format(table))
            admin.execute(sql.SQL("GRANT SELECT ON {} TO pgag_runtime").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(table))
            admin.execute(sql.SQL(
                "CREATE POLICY canonical_read ON {} FOR SELECT TO pgag_runtime USING ({})"
            ).format(table, label_policy(graph_name, label)))
        table = sql.Identifier(graph_name, METADATA_TABLE)
        admin.execute(sql.SQL(
            "CREATE TABLE {}(singleton boolean PRIMARY KEY CHECK(singleton),payload jsonb NOT NULL)"
        ).format(table))
        admin.execute(sql.SQL("INSERT INTO {} VALUES(true,%s)").format(table), (Jsonb(metadata),))
        admin.execute(sql.SQL("REVOKE ALL ON {} FROM PUBLIC,pgag_runtime").format(table))
        admin.execute(sql.SQL("GRANT SELECT ON {} TO pgag_runtime").format(table))
        return metadata


def drop_projection(admin_url: str, graph_name: str) -> None:
    validate_graph_name(graph_name)
    with _admin_connection(admin_url) as admin:
        row = admin.execute(sql.SQL(
            "SELECT payload FROM {} WHERE singleton"
        ).format(sql.Identifier(graph_name, METADATA_TABLE))).fetchone()
        if row is None:
            raise MemoryError("graph_projection_invalid", 409)
        validate_metadata(row["payload"], graph_name)
        admin.execute("SET LOCAL search_path = ag_catalog, pg_catalog")
        admin.execute("SELECT ag_catalog.drop_graph(%s,true)", (graph_name,))


async def bind_projection_context(
    conn: Connection, graph_name: str, data: ExpandGraph,
    as_of: datetime, known_at: datetime,
) -> None:
    validate_graph_name(graph_name)
    await conn.execute(
        """SELECT set_config('pgag.m3_graph',%s,true),
                  set_config('pgag.m3_as_of',%s,true),
                  set_config('pgag.m3_known_at',%s,true),
                  set_config('pgag.m3_scope_ids',%s,true),
                  set_config('pgag.m3_predicates',%s,true)""",
        (
            graph_name, as_of.isoformat(), known_at.isoformat(),
            "{" + ",".join(str(scope) for scope in data.scope_ids) + "}",
            "{" + ",".join(data.relation_types) + "}",
        ),
    )


async def _validate_runtime_projection(conn: Connection, graph_name: str) -> None:
    role = await (await conn.execute(
        "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
    )).fetchone()
    if role is None or role["rolsuper"] or role["rolbypassrls"]:
        raise MemoryError("graph_runtime_invalid", 409)
    rows = await (await conn.execute(
        """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                  pg_has_role(current_user,c.relowner,'MEMBER') AS owns,
                  has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE') AS writes
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname=%s AND c.relname=ANY(%s) AND c.relkind='r'""",
        (graph_name, list(LABELS)),
    )).fetchall()
    if len(rows) != len(LABELS) or any(
        not row["relrowsecurity"] or not row["relforcerowsecurity"] or row["owns"] or row["writes"]
        for row in rows
    ):
        raise MemoryError("graph_projection_invalid", 409)
    row = await (await conn.execute(sql.SQL(
        "SELECT payload FROM {} WHERE singleton"
    ).format(sql.Identifier(graph_name, METADATA_TABLE)))).fetchone()
    if row is None:
        raise MemoryError("graph_projection_invalid", 409)
    validate_metadata(row["payload"], graph_name)


class AgeGraphCandidate(SqlGraph):
    def __init__(self, memory: MemoryService, graph_name: str, *, strategy: str = "fixed") -> None:
        super().__init__(memory)
        self.graph_name = validate_graph_name(graph_name)
        validate_strategy(strategy)
        self.strategy = strategy

    async def neighbors(
        self, walk: GraphPath, data: ExpandGraph, as_of: datetime, known_at: datetime, limit: int,
    ) -> list[dict[str, Any]]:
        await bind_projection_context(self.conn, self.graph_name, data, as_of, known_at)
        return await self._canonical_neighbors(
            walk, data, as_of, known_at, limit,
            adjacent_query(self.graph_name, data.direction, strategy=self.strategy),
            {"age_parameters": json.dumps({"node": str(walk.nodes[-1])})},
        )


async def expand_candidate(
    runtime_url: str, subject: str, data: ExpandGraph, graph_name: str, *, strategy: str = "fixed",
) -> dict[str, Any]:
    validate_graph_name(graph_name)
    validate_strategy(strategy)
    async with principal_connection(runtime_url, subject) as (conn, identity):
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await bind_identity(conn, subject, identity)
            await conn.execute("SET LOCAL search_path = ag_catalog, pg_catalog")
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            await _validate_runtime_projection(conn, graph_name)
            times = await (await conn.execute(
                """SELECT COALESCE(%s::timestamptz,statement_timestamp()) AS as_of,
                          COALESCE(%s::timestamptz,statement_timestamp()) AS known_at""",
                (data.as_of, data.known_at),
            )).fetchone()
            assert times is not None
            request = data.model_copy(update=times)
            await bind_projection_context(
                conn, graph_name, request, times["as_of"], times["known_at"],
            )
            result = await AgeGraphCandidate(
                MemoryService(conn, identity), graph_name, strategy=strategy,
            ).expand(request)
            output = GraphResult.model_validate(result).model_dump(mode="json")
            output["backend"] = "age-fixed-hop-candidate"
            output["projection_watermark"] = None
            return output
