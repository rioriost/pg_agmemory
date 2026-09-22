"""Opt-in, pinned AGE VLE reads with canonical authority and fail-closed freshness."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from pg_agmemory.database import Connection, RuntimeValidationError, connect
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import ExpandGraph, GraphEdge, GraphPath, MemoryReference
from pg_agmemory.service import MemoryError, MemoryService

AGE_COMMIT = "72707aab7ce982bf13cad3d102bd869dab07d64b"
AGE_VERSION = "1.8.0"
GRAPH_PATTERN = re.compile(r"^pgag_age_[0-9a-f]{32}$", re.ASCII)
LABELS = ("_ag_label_vertex", "_ag_label_edge", "Node", "LINK")
MAX_NODES = 10000
MAX_EDGE_REVISIONS = 40000


def validate_graph_name(graph_name: str) -> str:
    if not isinstance(graph_name, str) or GRAPH_PATTERN.fullmatch(graph_name) is None:
        raise MemoryError("graph_projection_invalid", 409)
    return graph_name


async def validate_age_connection(conn: Connection) -> None:
    role = await (await conn.execute(
        """SELECT rolsuper,rolbypassrls,EXISTS (
               SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE (n.nspname IN ('memory','memory_ops','ag_catalog')
                      OR n.nspname ~ '^pgag_age_[0-9a-f]{32}$')
                 AND pg_has_role(current_user,c.relowner,'MEMBER')) AS owns_tables
           FROM pg_roles WHERE rolname=current_user"""
    )).fetchone()
    if not role or role["rolsuper"] or role["rolbypassrls"] or role["owns_tables"]:
        raise RuntimeValidationError(
            "runtime_role_invalid", "AGE runtime must not own tables or bypass RLS"
        )
    message = "Pinned patched AGE build and preloaded PostgreSQL 18.6 are required"
    try:
        runtime = await (await conn.execute(
            """SELECT current_setting('server_version_num')::int AS pg,
                      e.extversion,n.nspname,
                      EXISTS (
                          SELECT FROM pg_proc p JOIN pg_namespace pn ON pn.oid=p.pronamespace
                          WHERE pn.nspname='ag_catalog' AND p.proname='pgag_age_build'
                            AND p.pronargs=0 AND p.prorettype='jsonb'::regtype
                            AND p.provolatile='i' AND NOT p.prosecdef
                            AND p.proowner=e.extowner
                      ) AS stamped,
                      EXISTS (
                          SELECT FROM pg_proc p JOIN pg_namespace pn ON pn.oid=p.pronamespace
                          WHERE pn.nspname='ag_catalog' AND p.proname='pgag_age_preloaded'
                            AND p.pronargs=0 AND p.prorettype='boolean'::regtype
                            AND p.provolatile='s' AND p.prosecdef
                            AND p.proconfig=ARRAY['search_path=pg_catalog']
                            AND p.proowner=e.extowner
                            AND NOT pg_has_role(current_user,p.proowner,'MEMBER')
                            AND has_function_privilege(current_user,p.oid,'EXECUTE')
                      ) AS preload_diagnostic
               FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
               WHERE e.extname='age'"""
        )).fetchone()
        if (not runtime or runtime["pg"] != 180006 or runtime["extversion"] != AGE_VERSION
                or runtime["nspname"] != "ag_catalog" or not runtime["stamped"]
                or runtime["preload_diagnostic"] is not True):
            raise RuntimeValidationError("extension_version_mismatch", message)
        row = await (await conn.execute(
            """SELECT ag_catalog.pgag_age_build() AS build,
                      ag_catalog.pgag_age_preloaded() AS preloaded"""
        )).fetchone()
        if (not row or not isinstance(row["build"], dict)
                or row["build"].get("commit") != AGE_COMMIT or row["preloaded"] is not True):
            raise RuntimeValidationError("extension_version_mismatch", message)
    except psycopg.Error as exc:
        raise RuntimeValidationError("extension_version_mismatch", message) from exc


async def validate_age_runtime(url: str) -> None:
    async with await connect(url) as conn:
        await conn.execute("SET default_transaction_read_only = on")
        await validate_age_connection(conn)


def _context_policy(graph_name: str) -> sql.Composed:
    return sql.SQL(
        """current_setting('pgag.m3_graph',true)={}
           AND memory.current_tenant() IS NOT NULL AND memory.current_principal() IS NOT NULL
           AND NULLIF(current_setting('pgag.m3_as_of',true),'')::timestamptz IS NOT NULL
           AND NULLIF(current_setting('pgag.m3_known_at',true),'')::timestamptz IS NOT NULL"""
    ).format(sql.Literal(graph_name))


def _entity_predicate(alias: str, object_alias: str) -> sql.Composed:
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


async def _validate_labels(conn: Connection, graph_name: str) -> None:
    rows = await (await conn.execute(
        """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                  pg_has_role(current_user,c.relowner,'MEMBER') AS owns,
                  has_table_privilege(current_user,c.oid,'SELECT') AS reads,
                  has_table_privilege(current_user,c.oid,
                                      'INSERT,UPDATE,DELETE,TRUNCATE') AS writes
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname=%s AND c.relname=ANY(%s) AND c.relkind='r'""",
        (graph_name, list(LABELS)),
    )).fetchall()
    if len(rows) != len(LABELS) or any(
        not r["relrowsecurity"] or not r["relforcerowsecurity"] or r["owns"]
        or r["writes"] or not r["reads"] for r in rows
    ):
        raise MemoryError("graph_projection_invalid", 409)


def _canonical_ctes() -> sql.Composed:
    return sql.SQL(
        """canonical_nodes AS MATERIALIZED (
            SELECT e.id,e.tenant_id,e.scope_id FROM memory.entity e
            JOIN memory.object o USING (tenant_id,id)
            WHERE e.tenant_id=memory.current_tenant() AND {entity}
            LIMIT %(node_limit)s
        ), canonical_edges AS MATERIALIZED (
            SELECT r.id,rr.revision,r.tenant_id,r.scope_id,r.source_id,rr.target_id,
                   a.predicate,lower(ar.valid_time) AS valid_from,
                   upper(ar.valid_time) AS valid_to,lower(ar.system_time) AS recorded_at
            FROM memory.relation r JOIN memory.relation_revision rr
              ON rr.tenant_id=r.tenant_id AND rr.assertion_id=r.id
            JOIN memory.assertion a ON a.tenant_id=r.tenant_id AND a.id=r.id
            JOIN memory.assertion_revision ar ON ar.tenant_id=rr.tenant_id
              AND ar.assertion_id=rr.assertion_id AND ar.revision=rr.revision
            JOIN canonical_nodes s ON s.id=r.source_id
            JOIN canonical_nodes t ON t.id=rr.target_id
            WHERE r.tenant_id=memory.current_tenant()
              AND r.scope_id=ANY(%(scopes)s) AND a.predicate=ANY(%(predicates)s)
              AND ar.valid_time @> %(as_of)s AND ar.system_time @> %(known_at)s
            LIMIT %(edge_limit)s
        )"""
    ).format(entity=_entity_predicate("e", "o"))


def completeness_query(graph_name: str) -> sql.Composed:
    validate_graph_name(graph_name)
    # A bounded, request-visible whole-graph proof, not a constant-time watermark.
    return sql.SQL(
        """WITH {canonical},
        projected_nodes AS MATERIALIZED (
            SELECT id,properties::jsonb AS p FROM {nodes} LIMIT %(node_limit)s
        ), projected_edges AS MATERIALIZED (
            SELECT start_id,end_id,properties::jsonb AS p FROM {edges} LIMIT %(edge_limit)s
        )
        SELECT (SELECT count(*) FROM canonical_nodes)<%(node_limit)s
           AND (SELECT count(*) FROM canonical_edges)<%(edge_limit)s
           AND (SELECT count(*) FROM projected_nodes)<%(node_limit)s
           AND (SELECT count(*) FROM projected_edges)<%(edge_limit)s
           AND (SELECT count(*) FROM projected_nodes)=(SELECT count(*) FROM canonical_nodes)
           AND (SELECT count(*) FROM projected_edges)=(SELECT count(*) FROM canonical_edges)
           AND NOT EXISTS (
               SELECT FROM canonical_nodes n WHERE 1<>(
                   SELECT count(*) FROM projected_nodes p WHERE p.p=jsonb_build_object(
                       'id',n.id::text,'tenant_id',n.tenant_id::text,'scope_id',n.scope_id::text)))
           AND NOT EXISTS (
               SELECT FROM canonical_edges e WHERE 1<>(
                   SELECT count(*) FROM projected_edges p
                   JOIN projected_nodes s ON s.id=p.start_id
                   JOIN projected_nodes t ON t.id=p.end_id
                   WHERE p.p=jsonb_build_object(
                       'id',e.id::text,'revision',e.revision,'tenant_id',e.tenant_id::text,
                       'scope_id',e.scope_id::text,'source_id',e.source_id::text,
                       'target_id',e.target_id::text)
                     AND s.p->>'id'=e.source_id::text AND t.p->>'id'=e.target_id::text
                     AND s.p->>'tenant_id'=e.tenant_id::text
                     AND t.p->>'tenant_id'=e.tenant_id::text)) AS complete"""
    ).format(
        canonical=_canonical_ctes(), nodes=sql.Identifier(graph_name, "Node"),
        edges=sql.Identifier(graph_name, "LINK"),
    )


def cypher_template(direction: str, hops: int) -> str:
    if direction not in ("outgoing", "incoming", "both") or hops not in (1, 2):
        raise ValueError("invalid bounded traversal")
    link = f"[:LINK*{hops}..{hops}]"
    link = {"outgoing": f"-{link}->", "incoming": f"<-{link}-", "both": f"-{link}-"}[
        direction
    ]
    middle = ", nodes(p)[1] AS m, relationships(p)[1] AS e1" if hops == 2 else ""
    result = ", m.id, e1.id, e1.revision" if hops == 2 else ""
    return (
        f"MATCH p=(s:Node){link}(t:Node) WHERE s.id IN $seeds "
        f"WITH s,t,relationships(p)[0] AS e0{middle} "
        f"RETURN s.id,t.id,e0.id,e0.revision{result}"
    )


def _native_branch(graph_name: str, direction: str, hops: int) -> sql.Composed:
    cypher = sql.SQL("$pgag_native$" + cypher_template(direction, hops) + "$pgag_native$")
    tail = (
        ",middle ag_catalog.agtype,edge1 ag_catalog.agtype,revision1 ag_catalog.agtype"
        if hops == 2 else ""
    )
    middle = "(c.middle::jsonb #>> '{}')::uuid" if hops == 2 else "NULL::uuid"
    edge1 = "(c.edge1::jsonb #>> '{}')::uuid" if hops == 2 else "NULL::uuid"
    revision1 = "c.revision1::bigint" if hops == 2 else "NULL::bigint"
    return sql.SQL(
        """SELECT {hops} AS hops,(c.seed::jsonb #>> '{{}}')::uuid AS seed,
                  (c.target::jsonb #>> '{{}}')::uuid AS target,
                  (c.edge0::jsonb #>> '{{}}')::uuid AS edge0,c.revision0::bigint AS revision0,
                  {middle} AS middle,{edge1} AS edge1,{revision1} AS revision1
           FROM ag_catalog.cypher({graph},{cypher},%(age_parameters)s::ag_catalog.agtype)
           AS c(seed ag_catalog.agtype,target ag_catalog.agtype,
                edge0 ag_catalog.agtype,revision0 ag_catalog.agtype{tail})"""
    ).format(
        hops=sql.Literal(hops), middle=sql.SQL(middle), edge1=sql.SQL(edge1),
        revision1=sql.SQL(revision1), graph=sql.Literal(graph_name), cypher=cypher,
        tail=sql.SQL(tail),
    )


def path_query(graph_name: str, direction: str, max_hops: int) -> sql.Composed:
    validate_graph_name(graph_name)
    branches = sql.SQL(" UNION ALL ").join(
        _native_branch(graph_name, direction, hops) for hops in range(1, max_hops + 1)
    )
    return sql.SQL(
        """WITH {canonical}, native AS MATERIALIZED ({native}),
        authorized AS (
            SELECT p.*,to_jsonb(e0) AS first_edge,to_jsonb(e1) AS second_edge
            FROM native p JOIN canonical_nodes s ON s.id=p.seed
            JOIN canonical_nodes t ON t.id=p.target
            JOIN canonical_edges e0 ON e0.id=p.edge0 AND e0.revision=p.revision0
            LEFT JOIN canonical_nodes m ON m.id=p.middle
            LEFT JOIN canonical_edges e1 ON e1.id=p.edge1 AND e1.revision=p.revision1
            WHERE p.seed=ANY(%(seeds)s) AND p.seed<>p.target
              AND (p.hops=1 OR (m.id IS NOT NULL AND e1.id IS NOT NULL
                               AND p.seed<>p.middle AND p.middle<>p.target))
              AND (
                  (%(direction)s IN ('outgoing','both') AND e0.source_id=p.seed
                   AND e0.target_id=COALESCE(p.middle,p.target))
                  OR (%(direction)s IN ('incoming','both') AND e0.target_id=p.seed
                      AND e0.source_id=COALESCE(p.middle,p.target)))
              AND (p.hops=1 OR (
                  (%(direction)s IN ('outgoing','both') AND e1.source_id=p.middle
                   AND e1.target_id=p.target)
                  OR (%(direction)s IN ('incoming','both') AND e1.target_id=p.middle
                      AND e1.source_id=p.target)))
        )
        SELECT * FROM authorized
        ORDER BY hops,seed,edge0,revision0,COALESCE(middle,target),edge1,revision1,target
        LIMIT %(path_limit)s"""
    ).format(canonical=_canonical_ctes(), native=branches)


class AgeGraph:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def expand(self, data: ExpandGraph) -> dict[str, Any]:
        """Run inside the caller's identity-bound transaction and tenant barrier."""
        try:
            await validate_age_connection(self.conn)
        except RuntimeValidationError:
            raise MemoryError("graph_backend_unqualified", 503) from None
        await self.conn.execute("SET LOCAL search_path = ag_catalog, pg_catalog")
        await self.conn.execute("SET LOCAL statement_timeout = '5s'")
        timeline = await (await self.conn.execute(
            """SELECT COALESCE(%s::timestamptz,statement_timestamp()) AS as_of,
                      COALESCE(%s::timestamptz,statement_timestamp()) AS known_at,
                      access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s""",
            (data.as_of, data.known_at, self.tenant),
        )).fetchone()
        if timeline is None:
            raise MemoryError("not_found", 404)
        projection = await (await self.conn.execute(
            """SELECT generation_id,graph_name,age_commit,captured_access_epoch,
                      captured_deletion_epoch,node_count,edge_revision_count
               FROM memory_ops.age_projection WHERE tenant_id=%s AND enabled""",
            (self.tenant,),
        )).fetchone()
        if projection is None:
            raise MemoryError("graph_projection_unavailable", 409)
        graph_name = validate_graph_name(projection["graph_name"])
        if (projection["age_commit"] != AGE_COMMIT
                or not isinstance(projection["generation_id"], UUID)
                or not 0 <= projection["node_count"] <= MAX_NODES
                or not 0 <= projection["edge_revision_count"] <= MAX_EDGE_REVISIONS):
            raise MemoryError("graph_projection_invalid", 409)
        if (projection["captured_access_epoch"] != timeline["access_epoch"]
                or projection["captured_deletion_epoch"] != timeline["deletion_epoch"]):
            raise MemoryError("graph_projection_stale", 409)
        await _validate_labels(self.conn, graph_name)
        await bind_projection_context(
            self.conn, graph_name, data, timeline["as_of"], timeline["known_at"]
        )
        parameters = {
            "node_limit": projection["node_count"] + 1,
            "edge_limit": projection["edge_revision_count"] + 1,
            "scopes": data.scope_ids, "predicates": data.relation_types,
            "as_of": timeline["as_of"], "known_at": timeline["known_at"],
            "seeds": data.seeds, "direction": data.direction,
            "age_parameters": json.dumps({"seeds": [str(seed) for seed in data.seeds]}),
            "path_limit": data.max_paths + 1,
        }
        proof = await (await self.conn.execute(
            completeness_query(graph_name), parameters
        )).fetchone()
        if not proof or not proof["complete"]:
            raise MemoryError("graph_projection_stale", 409)
        rows = await (await self.conn.execute(
            path_query(graph_name, data.direction, data.max_hops), parameters
        )).fetchall()
        canonical = SqlGraph(self.memory)
        seeds = await canonical.visible_entities(data.seeds, data.scope_ids, timeline["known_at"])
        node_ids = {node.memory_id for node in seeds}
        edges: dict[tuple[UUID, int], GraphEdge] = {}
        paths = []
        for row in rows[:data.max_paths]:
            nodes = [row["seed"], row["target"]]
            if row["hops"] == 2:
                nodes.insert(1, row["middle"])
            refs = []
            for value in (row["first_edge"], row["second_edge"]):
                if value is None:
                    continue
                ref = MemoryReference(memory_id=value["id"], revision=value["revision"])
                refs.append(ref)
                edges[(ref.memory_id, ref.revision)] = GraphEdge(
                    assertion=ref, predicate=value["predicate"],
                    source_entity=value["source_id"], target_entity=value["target_id"],
                    valid_from=value["valid_from"], valid_to=value["valid_to"],
                    recorded_at=value["recorded_at"],
                )
            paths.append(GraphPath(nodes=nodes, assertions=refs))
            node_ids.update(nodes)
        visible = await canonical.visible_entities(
            list(node_ids), data.scope_ids, timeline["known_at"]
        )
        if len(visible) != len(node_ids):
            raise MemoryError("graph_invalidated", 409)
        truncated = len(rows) > data.max_paths
        return {
            "backend": "age", "projection_watermark": projection["generation_id"],
            "as_of": timeline["as_of"], "known_at": timeline["known_at"],
            "nodes": visible, "edges": [edges[key] for key in sorted(edges)], "paths": paths,
            "coverage": {
                "complete_within_bounds": not truncated, "truncated": truncated,
                "max_hops": data.max_hops,
            },
            "consistency": {
                "access_epoch": timeline["access_epoch"],
                "deletion_epoch": timeline["deletion_epoch"],
            },
            "empty_reason": None if paths else "not_found",
        }
