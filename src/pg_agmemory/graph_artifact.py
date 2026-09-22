"""Deterministic private graph build inputs, never a serving projection or ACL view."""

import argparse
import hashlib
import hmac
import json
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal, Never, Self
from uuid import UUID

import psycopg
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from pg_agmemory.admin import AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.deletion_history import HistoryContract
from pg_agmemory.graph_generation import (
    AdminConnection,
    GenerationRecord,
    GraphInput,
    _record,
    _state,
    capture_input,
    input_digest,
)
from pg_agmemory.models import Digest, RelationType, Revision
from pg_agmemory.recovery_apply import secret_for

MAX_NODES = 10000
MAX_EDGE_REVISIONS = 40000
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_SECONDS = 30


class ArtifactNode(HistoryContract):
    memory_id: UUID
    scope_id: UUID
    recorded_at: AwareDatetime


class ArtifactEdge(HistoryContract):
    memory_id: UUID
    revision: Revision
    scope_id: UUID
    source_entity: UUID
    target_entity: UUID
    predicate: RelationType
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None
    recorded_at: AwareDatetime
    superseded_at: AwareDatetime | None

    @model_validator(mode="after")
    def half_open_times(self) -> Self:
        if (
            self.valid_from is not None and self.valid_to is not None
            and self.valid_from >= self.valid_to
        ) or (self.superseded_at is not None and self.recorded_at >= self.superseded_at):
            raise ValueError("Graph artifact intervals must be nonempty and half open")
        return self


class GraphArtifact(HistoryContract):
    format: Literal["pgag-graph-artifact-v1"] = "pgag-graph-artifact-v1"
    schema_version: Literal[19] = 19
    tenant_id: UUID
    generation_id: UUID
    parent_id: UUID | None
    profile_digest: Digest
    input_digest: Digest
    input_snapshot: GraphInput
    nodes: Annotated[tuple[ArtifactNode, ...], Field(max_length=10000)]
    edge_revisions: Annotated[tuple[ArtifactEdge, ...], Field(max_length=40000)]
    signature: Digest
    serving_enabled: Literal[False] = False
    permission_filter_required: Literal[True] = True

    @model_validator(mode="after")
    def canonical_graph(self) -> Self:
        nodes = {node.memory_id: node for node in self.nodes}
        node_ids = tuple(node.memory_id for node in self.nodes)
        edge_ids = tuple((edge.memory_id, edge.revision) for edge in self.edge_revisions)
        if (
            self.tenant_id != self.input_snapshot.tenant_id
            or self.generation_id == self.parent_id
            or node_ids != tuple(sorted(set(node_ids)))
            or edge_ids != tuple(sorted(set(edge_ids)))
        ):
            raise ValueError("Graph artifact identity and canonical order must match")
        for edge in self.edge_revisions:
            if any(
                endpoint not in nodes or nodes[endpoint].scope_id != edge.scope_id
                for endpoint in (edge.source_entity, edge.target_entity)
            ):
                raise ValueError("Graph artifact endpoints must exist in the edge scope")
        return self


class GraphArtifactRequest(HistoryContract):
    operation: Literal["export", "check"]
    tenant_id: UUID
    generation_id: UUID
    expected_revision: Epoch
    file: Path

    @model_validator(mode="after")
    def file_name(self) -> Self:
        path = str(self.file)
        path.encode("utf-8")
        if "\x00" in path or self.file.name in ("", ".", ".."):
            raise ValueError("An artifact file path is required")
        return self


class GraphArtifactResult(HistoryContract):
    operation: Literal["export", "check"]
    tenant_id: UUID
    generation_id: UUID
    revision: Epoch
    artifact_digest: Digest
    node_count: Annotated[int, Field(ge=0, le=10000)]
    edge_revision_count: Annotated[int, Field(ge=0, le=40000)]
    artifact_verified: Literal[True] = True
    serving_enabled: Literal[False] = False


def canonical_bytes(artifact: GraphArtifact, *, unsigned: bool = False) -> bytes:
    value = artifact.model_dump(mode="json", exclude={"signature"} if unsigned else set())
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise AdminError("graph_artifact_limit")
    return payload


def signature(artifact: GraphArtifact, secret: bytes) -> str:
    return hmac.new(
        secret, b"pgag-graph-artifact-v1:" + canonical_bytes(artifact, unsigned=True),
        hashlib.sha256,
    ).hexdigest()


def _deadline(started: float) -> None:
    if time.monotonic() - started > MAX_SECONDS:
        raise AdminError("graph_artifact_timeout")


def materialize(
    conn: AdminConnection, tenant_id: UUID, generation: GenerationRecord,
    current: GraphInput, digest: str, secret: bytes, started: float,
) -> GraphArtifact:
    nodes = conn.execute(
        """SELECT e.id AS memory_id,e.scope_id,o.created_at AS recorded_at
           FROM memory.entity e JOIN memory.object o USING(tenant_id,id)
           WHERE e.tenant_id=%s ORDER BY e.id LIMIT %s""",
        (tenant_id, MAX_NODES + 1),
    ).fetchall()
    _deadline(started)
    if len(nodes) > MAX_NODES:
        raise AdminError("graph_artifact_limit")
    edges = conn.execute(
        """SELECT r.id AS memory_id,v.revision,r.scope_id,
                  r.source_id AS source_entity,v.target_id AS target_entity,a.predicate,
                  lower(ar.valid_time) AS valid_from,upper(ar.valid_time) AS valid_to,
                  lower(ar.system_time) AS recorded_at,upper(ar.system_time) AS superseded_at
           FROM memory.relation r JOIN memory.relation_revision v
             ON v.tenant_id=r.tenant_id AND v.assertion_id=r.id
           JOIN memory.assertion a ON a.tenant_id=r.tenant_id AND a.id=r.id
           JOIN memory.assertion_revision ar ON ar.tenant_id=v.tenant_id
             AND ar.assertion_id=v.assertion_id AND ar.revision=v.revision
           WHERE r.tenant_id=%s ORDER BY r.id,v.revision LIMIT %s""",
        (tenant_id, MAX_EDGE_REVISIONS + 1),
    ).fetchall()
    _deadline(started)
    if len(edges) > MAX_EDGE_REVISIONS:
        raise AdminError("graph_artifact_limit")
    counts = {table.table: table.rows for table in current.tables}
    if len(nodes) != counts["memory.entity"] or len(edges) != counts["memory.relation_revision"]:
        raise AdminError("graph_artifact_invalid")
    artifact = GraphArtifact(
        tenant_id=tenant_id, generation_id=generation.id, parent_id=generation.parent_id,
        profile_digest=generation.profile_digest, input_digest=digest, input_snapshot=current,
        nodes=tuple(ArtifactNode.model_validate(row) for row in nodes),
        edge_revisions=tuple(ArtifactEdge.model_validate(row) for row in edges),
        signature="0" * 64,
    )
    signed = artifact.model_copy(update={"signature": signature(artifact, secret)})
    _deadline(started)
    return signed


def read_artifact(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise AdminError("graph_artifact_file_invalid")
            if metadata.st_size > MAX_ARTIFACT_BYTES:
                raise AdminError("graph_artifact_limit")
            payload = stream.read(MAX_ARTIFACT_BYTES + 1)
    except OSError:
        raise AdminError("graph_artifact_file_invalid") from None
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise AdminError("graph_artifact_limit")
    return payload


def write_artifact(path: Path, payload: bytes) -> None:
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise AdminError("graph_artifact_limit")
    try:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise AdminError("graph_artifact_output_failed") from None
    owned: os.stat_result | None = None
    try:
        try:
            fd = os.open(
                path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
            with os.fdopen(fd, "wb") as stream:
                owned = os.fstat(stream.fileno())
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        except OSError:
            if owned is not None:
                try:
                    current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                        raise AdminError("graph_artifact_cleanup_failed")
                    os.unlink(path.name, dir_fd=directory)
                    os.fsync(directory)
                except FileNotFoundError:
                    pass
                except OSError:
                    raise AdminError("graph_artifact_cleanup_failed") from None
            raise AdminError("graph_artifact_output_failed") from None
    finally:
        os.close(directory)


@contextmanager
def graph_artifact(
    url: str, request: GraphArtifactRequest,
) -> Iterator[GraphArtifactResult]:
    try:
        with admin_connection(url, request.tenant_id) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.execute("SET LOCAL timezone='UTC'")
                started = time.monotonic()
                secret = secret_for(conn, request.tenant_id)
                state = _state(conn, request.tenant_id)
                if state["revision"] != request.expected_revision:
                    raise AdminError("graph_revision_conflict")
                if request.generation_id not in (state["head_id"], state["building_id"]):
                    raise AdminError("graph_generation_unavailable")
                current = capture_input(conn, request.tenant_id, secret)
                digest = input_digest(current, secret)
                generation = _record(conn, request.tenant_id, request.generation_id, secret, digest)
                if generation is None or generation.state not in ("building", "recorded"):
                    raise AdminError("graph_generation_unavailable")
                if not generation.source_matches:
                    raise AdminError("graph_input_changed")
                expected = materialize(
                    conn, request.tenant_id, generation, current, digest, secret, started,
                )
                payload = canonical_bytes(expected)
                file_digest = hashlib.sha256(payload).hexdigest()
                if generation.state == "recorded" and generation.artifact_digest != file_digest:
                    raise AdminError("graph_artifact_invalid")
                if request.operation == "check":
                    actual = read_artifact(request.file)
                    artifact = GraphArtifact.model_validate_json(actual)
                    if (
                        not hmac.compare_digest(artifact.signature, signature(artifact, secret))
                        or artifact != expected or actual != payload
                    ):
                        raise AdminError("graph_artifact_invalid")
                _deadline(started)
                result = GraphArtifactResult(
                    operation=request.operation, tenant_id=request.tenant_id,
                    generation_id=request.generation_id, revision=state["revision"],
                    artifact_digest=file_digest, node_count=len(expected.nodes),
                    edge_revision_count=len(expected.edge_revisions),
                )
            # The tenant barrier spans file creation and delivery, but no database
            # transaction claims atomicity with the caller-owned filesystem.
            if request.operation == "export":
                write_artifact(request.file, payload)
            yield result
    except ValidationError:
        raise AdminError("graph_artifact_invalid") from None
    except psycopg.Error as exc:
        raise admin_failure(exc, False) from None


class GraphArtifactParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_graph_artifact_arguments")


def main(argv: list[str]) -> None:
    parser = GraphArtifactParser(prog="pg-agmemory graph-artifact")
    parser.add_argument("operation", choices=("export", "check"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--generation-id", required=True, type=UUID)
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--file", required=True, type=Path)
    try:
        request = GraphArtifactRequest.model_validate(vars(parser.parse_args(argv)))
    except ValidationError:
        parser.error("invalid request")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with graph_artifact(url, request) as result:
            print(result.model_dump_json(), flush=True)
    except AdminError as exc:
        print(json.dumps({
            "error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown},
        }), flush=True)
        raise SystemExit(1) from None
