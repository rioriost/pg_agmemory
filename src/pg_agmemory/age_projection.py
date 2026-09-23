"""Administrative publication of verified canonical artifacts to pinned AGE."""

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Never, Self
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import StrictBool, ValidationError, model_validator

from pg_agmemory.admin import MAX_EPOCH, AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.age_graph import AGE_COMMIT, AGE_VERSION, LABELS, label_policy
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.deletion_history import HistoryContract
from pg_agmemory.graph_artifact import GraphArtifact, GraphArtifactRequest, current_artifact
from pg_agmemory.graph_generation import AdminConnection, Revision, _state
from pg_agmemory.models import Digest
from pg_agmemory.transactions import CommitOutcomeUnknown, transaction


class AgeProjectionRequest(HistoryContract):
    operation: Literal["get", "publish", "disable"]
    tenant_id: UUID
    expected_revision: Revision | None = None
    generation_id: UUID | None = None
    expected_generation_revision: Epoch | None = None
    file: Path | None = None
    rebuild_missing: StrictBool = False

    @model_validator(mode="after")
    def arguments(self) -> Self:
        required = {
            "get": set(),
            "publish": {
                "expected_revision", "generation_id", "expected_generation_revision", "file",
            },
            "disable": {"expected_revision"},
        }[self.operation]
        supplied = {
            key for key in (
                "expected_revision", "generation_id", "expected_generation_revision", "file",
            ) if getattr(self, key) is not None
        }
        if supplied != required:
            raise ValueError("Operation requires its exact argument set")
        if self.rebuild_missing and self.operation != "publish":
            raise ValueError("Missing projection rebuild requires explicit publication")
        return self


class AgeProjection(HistoryContract):
    tenant_id: UUID
    generation_id: UUID
    graph_name: str
    artifact_digest: Digest
    profile_digest: Digest
    input_digest: Digest
    captured_access_epoch: Epoch
    captured_deletion_epoch: Epoch
    captured_schema_version: Literal[20, 21]
    age_commit: Literal["72707aab7ce982bf13cad3d102bd869dab07d64b"]
    node_count: int
    edge_revision_count: int
    enabled: bool
    revision: Epoch
    created_at: datetime
    updated_at: datetime
    database_role: str


class AgeProjectionResult(HistoryContract):
    operation: Literal["get", "publish", "disable"]
    tenant_id: UUID
    revision: Revision
    projection: AgeProjection | None
    changed: bool
    artifact_verified: bool
    serving_enabled: bool
    rebuilt_missing_projection: bool = False


def validate_build(conn: AdminConnection) -> None:
    try:
        row = conn.execute(
            """SELECT current_setting('server_version_num')::int AS pg,e.extversion,
                      n.nspname,ag_catalog.pgag_age_build() AS build,
                      ag_catalog.pgag_age_preloaded() AS preloaded
               FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
               WHERE e.extname='age'"""
        ).fetchone()
    except (psycopg.errors.UndefinedFunction, psycopg.errors.InvalidSchemaName):
        raise AdminError("graph_backend_unqualified") from None
    if (
        row is None or row["pg"] != 180006 or row["extversion"] != AGE_VERSION
        or row["nspname"] != "ag_catalog" or row["preloaded"] is not True
        or not isinstance(row["build"], dict) or row["build"].get("commit") != AGE_COMMIT
    ):
        raise AdminError("graph_backend_unqualified")


def _projection(conn: AdminConnection, tenant_id: UUID) -> AgeProjection | None:
    row = conn.execute(
        "SELECT * FROM memory_ops.age_projection WHERE tenant_id=%s", (tenant_id,),
    ).fetchone()
    return AgeProjection.model_validate(row) if row is not None else None


def build_graph(conn: AdminConnection, artifact: GraphArtifact) -> str:
    graph_name = "pgag_age_" + artifact.generation_id.hex
    if conn.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname=%s", (graph_name,),
    ).fetchone():
        raise AdminError("graph_projection_exists")
    conn.execute("SET LOCAL search_path=ag_catalog,pg_catalog")
    conn.execute("SELECT ag_catalog.create_graph(%s)", (graph_name,))
    conn.execute("SELECT ag_catalog.create_vlabel(%s,'Node')", (graph_name,))
    conn.execute("SELECT ag_catalog.create_elabel(%s,'LINK')", (graph_name,))
    nodes, edges = sql.Identifier(graph_name, "Node"), sql.Identifier(graph_name, "LINK")
    conn.execute(sql.SQL(
        """INSERT INTO {}(properties)
           SELECT jsonb_build_object('id',x.memory_id::text,'scope_id',x.scope_id::text,
                                     'tenant_id',%s::text)::ag_catalog.agtype
           FROM jsonb_to_recordset(%s) AS x(memory_id uuid,scope_id uuid)
           ORDER BY x.memory_id"""
    ).format(nodes), (artifact.tenant_id, Jsonb([
        node.model_dump(mode="json") for node in artifact.nodes
    ])))
    conn.execute(sql.SQL(
        """INSERT INTO {edges}(start_id,end_id,properties)
           SELECT s.id,t.id,jsonb_build_object('id',x.memory_id::text,'revision',x.revision,
               'tenant_id',%s::text,'scope_id',x.scope_id::text,
               'source_id',x.source_entity::text,'target_id',x.target_entity::text)
               ::ag_catalog.agtype
           FROM jsonb_to_recordset(%s) AS x(memory_id uuid,revision bigint,scope_id uuid,
                                           source_entity uuid,target_entity uuid)
           JOIN {nodes} s ON (s.properties::jsonb->>'id')::uuid=x.source_entity
           JOIN {nodes} t ON (t.properties::jsonb->>'id')::uuid=x.target_entity
           ORDER BY x.memory_id,x.revision"""
    ).format(edges=edges, nodes=nodes), (artifact.tenant_id, Jsonb([
        edge.model_dump(mode="json") for edge in artifact.edge_revisions
    ])))
    counts = conn.execute(sql.SQL(
        "SELECT (SELECT count(*) FROM {}) AS nodes,(SELECT count(*) FROM {}) AS edges"
    ).format(nodes, edges)).fetchone()
    if counts != {"nodes": len(artifact.nodes), "edges": len(artifact.edge_revisions)}:
        raise AdminError("graph_projection_invalid")
    conn.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(graph_name)))
    conn.execute(sql.SQL(
        "GRANT USAGE ON SCHEMA {},ag_catalog TO pgag_runtime"
    ).format(sql.Identifier(graph_name)))
    conn.execute("GRANT SELECT ON ag_catalog.ag_graph,ag_catalog.ag_label TO pgag_runtime")
    for label in LABELS:
        table = sql.Identifier(graph_name, label)
        conn.execute(sql.SQL("REVOKE ALL ON {} FROM PUBLIC,pgag_runtime").format(table))
        conn.execute(sql.SQL("GRANT SELECT ON {} TO pgag_runtime").format(table))
        conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
        conn.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(table))
        conn.execute(sql.SQL(
            "CREATE POLICY canonical_read ON {} FOR SELECT TO pgag_runtime USING ({})"
        ).format(table, label_policy(graph_name, label)))
        conn.execute(sql.SQL("ANALYZE {}").format(table))
    return graph_name


def _publish(
    conn: AdminConnection, request: AgeProjectionRequest, previous: AgeProjection | None,
) -> None:
    validate_build(conn)
    if (previous is not None and previous.enabled
            and previous.captured_schema_version != SCHEMA_VERSION):
        raise AdminError("graph_projection_stale")
    state = _state(conn, request.tenant_id)
    if state["head_id"] != request.generation_id:
        raise AdminError("graph_generation_unavailable")
    assert request.generation_id is not None
    assert request.expected_generation_revision is not None and request.file is not None
    artifact, verified = current_artifact(conn, GraphArtifactRequest(
        operation="check", tenant_id=request.tenant_id, generation_id=request.generation_id,
        expected_revision=request.expected_generation_revision, file=request.file,
    ))
    if request.rebuild_missing and (previous is None or previous.enabled):
        raise AdminError("graph_projection_unavailable")
    previous_missing = False
    if previous is not None:
        locations = conn.execute(
            """SELECT (SELECT oid FROM pg_namespace WHERE nspname=%s) AS schema_oid,
                      (SELECT namespace::oid FROM ag_catalog.ag_graph
                       WHERE name=%s) AS graph_schema""",
            (previous.graph_name, previous.graph_name),
        ).fetchone()
        assert locations is not None
        previous_missing = locations["schema_oid"] is None and locations["graph_schema"] is None
        if not previous_missing and (
            locations["schema_oid"] is None or locations["graph_schema"] is None
            or locations["schema_oid"] != locations["graph_schema"]
        ):
            raise AdminError("graph_projection_invalid")
        if previous_missing and not request.rebuild_missing:
            raise AdminError("graph_projection_missing")
        if not previous_missing and request.rebuild_missing:
            raise AdminError("graph_projection_not_missing")
    replacing_same = (
        previous is not None and previous.generation_id == artifact.generation_id
    )
    if replacing_same and not previous_missing:
        assert previous is not None
        conn.execute("SET LOCAL search_path=ag_catalog,pg_catalog")
        conn.execute("SELECT ag_catalog.drop_graph(%s,true)", (previous.graph_name,))
    graph_name = build_graph(conn, artifact)
    values = {
        "tenant_id": request.tenant_id, "generation_id": artifact.generation_id,
        "graph_name": graph_name, "artifact_digest": verified.artifact_digest,
        "profile_digest": artifact.profile_digest, "input_digest": artifact.input_digest,
        "captured_access_epoch": artifact.input_snapshot.access_epoch,
        "captured_deletion_epoch": artifact.input_snapshot.deletion_epoch,
        "age_commit": AGE_COMMIT, "node_count": len(artifact.nodes),
        "edge_revision_count": len(artifact.edge_revisions), "enabled": True,
        "revision": (previous.revision if previous else 0) + 1,
    }
    if previous is None:
        conn.execute(sql.SQL("INSERT INTO memory_ops.age_projection ({}) VALUES ({})").format(
            sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values),
        ), tuple(values.values()))
    else:
        assignments = {key: value for key, value in values.items() if key != "tenant_id"}
        cursor = conn.execute(sql.SQL(
            "UPDATE memory_ops.age_projection SET {} WHERE tenant_id=%s AND revision=%s"
        ).format(sql.SQL(",").join(
            sql.SQL("{}=%s").format(sql.Identifier(key)) for key in assignments
        )), (*assignments.values(), request.tenant_id, previous.revision))
        if cursor.rowcount != 1:
            raise AdminError("graph_projection_revision_conflict")
        # Only the schema named by the prior, generation-bound registry is removed.
        if not replacing_same and not previous_missing:
            conn.execute("SELECT ag_catalog.drop_graph(%s,true)", (previous.graph_name,))


@contextmanager
def age_projection(
    url: str, request: AgeProjectionRequest,
) -> Iterator[AgeProjectionResult]:
    commit_attempted = False
    try:
        with admin_connection(url, request.tenant_id) as conn:
            with transaction(conn):
                conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                    + (" READ ONLY" if request.operation == "get" else "")
                )
                conn.execute("SET LOCAL timezone='UTC'")
                if conn.execute(
                    "SELECT 1 FROM memory.tenant WHERE id=%s", (request.tenant_id,),
                ).fetchone() is None:
                    raise AdminError("not_found")
                previous = _projection(conn, request.tenant_id)
                revision = previous.revision if previous else 0
                changed = request.operation != "get"
                if changed and request.expected_revision != revision:
                    raise AdminError("graph_projection_revision_conflict")
                if changed and revision == MAX_EPOCH:
                    raise AdminError("graph_revision_exhausted")
                if request.operation == "publish":
                    _publish(conn, request, previous)
                elif request.operation == "disable":
                    if previous is None:
                        raise AdminError("graph_projection_unavailable")
                    conn.execute(
                        """UPDATE memory_ops.age_projection SET enabled=false,revision=revision+1
                           WHERE tenant_id=%s AND revision=%s""", (request.tenant_id, revision),
                    )
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                final = _projection(conn, request.tenant_id)
                result = AgeProjectionResult(
                    operation=request.operation, tenant_id=request.tenant_id,
                    revision=final.revision if final else 0, projection=final, changed=changed,
                    artifact_verified=request.operation == "publish",
                    serving_enabled=bool(
                        final and final.enabled and final.captured_schema_version == SCHEMA_VERSION
                    ),
                    rebuilt_missing_projection=request.rebuild_missing,
                )
                commit_attempted = changed
            yield result
    except ValidationError:
        raise AdminError("graph_projection_invalid") from None
    except (psycopg.Error, CommitOutcomeUnknown) as exc:
        raise admin_failure(exc, commit_attempted) from None


class AgeProjectionParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_age_projection_arguments")


def main(argv: list[str]) -> None:
    parser = AgeProjectionParser(prog="pg-agmemory age-projection")
    parser.add_argument("operation", choices=("get", "publish", "disable"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--generation-id", type=UUID)
    parser.add_argument("--expected-generation-revision", type=int)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--rebuild-missing", action="store_true")
    try:
        request = AgeProjectionRequest.model_validate(vars(parser.parse_args(argv)))
    except ValidationError:
        parser.error("invalid request")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with age_projection(url, request) as result:
            print(result.model_dump_json(), flush=True)
    except AdminError as exc:
        print(json.dumps({
            "error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown},
        }), flush=True)
        raise SystemExit(1) from None
