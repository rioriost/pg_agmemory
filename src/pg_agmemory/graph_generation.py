"""Admin-only graph input/generation receipts; never artifact verification or activation."""

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal, Never, Self
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import Field, ValidationError, model_validator

from pg_agmemory.admin import MAX_EPOCH, AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.deletion_history import HistoryContract
from pg_agmemory.models import Digest, ShortText
from pg_agmemory.processing_recovery import StateFingerprint, fingerprint_tables
from pg_agmemory.recovery_apply import secret_for
from pg_agmemory.transactions import CommitOutcomeUnknown, transaction

Revision = Annotated[int, Field(ge=0, le=MAX_EPOCH, strict=True)]
MAX_GENERATIONS = 10000
MAX_REQUEST_BYTES = 32768
INPUT_TABLES = {
    "memory.object": "id",
    "memory.entity": "id",
    "memory.entity_evidence": "entity_id,source_id",
    "memory.assertion": "id",
    "memory.assertion_revision": "assertion_id,revision",
    "memory.relation": "id",
    "memory.relation_revision": "assertion_id,revision",
}
INPUT_FILTERS: dict[str, sql.Composable] = {
    "memory.object": sql.SQL(
        "t.kind='entity' OR EXISTS (SELECT 1 FROM memory.relation r "
        "WHERE r.tenant_id=t.tenant_id AND r.id=t.id)"
    ),
    "memory.assertion": sql.SQL("t.is_relation"),
    "memory.assertion_revision": sql.SQL(
        "EXISTS (SELECT 1 FROM memory.relation r "
        "WHERE r.tenant_id=t.tenant_id AND r.id=t.assertion_id)"
    ),
}
AdminConnection = psycopg.Connection[dict[str, Any]]


class GraphInput(HistoryContract):
    format: Literal["pgag-graph-input-v1"] = "pgag-graph-input-v1"
    schema_version: Literal[19, 20, 21, 22] = 22
    tenant_id: UUID
    access_epoch: Epoch
    deletion_epoch: Epoch
    tables: tuple[StateFingerprint, ...]

    @model_validator(mode="after")
    def complete(self) -> Self:
        if tuple(row.table for row in self.tables) != tuple(INPUT_TABLES):
            raise ValueError("Complete ordered graph inputs are required")
        return self


class GraphGenerationRequest(HistoryContract):
    operation: Literal["get", "begin", "record", "abandon"]
    tenant_id: UUID
    expected_revision: Revision | None = None
    generation_id: UUID | None = None
    expected_input_digest: Digest | None = None
    profile_digest: Digest | None = None
    artifact_digest: Digest | None = None
    reason: ShortText | None = None

    @model_validator(mode="after")
    def arguments(self) -> Self:
        required = {
            "get": set(),
            "begin": {
                "expected_revision", "generation_id", "expected_input_digest", "profile_digest",
            },
            "record": {
                "expected_revision", "generation_id", "expected_input_digest", "artifact_digest",
            },
            "abandon": {"expected_revision", "generation_id", "reason"},
        }[self.operation]
        supplied = {
            key for key in (
                "expected_revision", "generation_id", "expected_input_digest",
                "profile_digest", "artifact_digest", "reason",
            ) if getattr(self, key) is not None
        }
        if supplied != required:
            raise ValueError("Operation requires its exact argument set")
        return self


class GenerationRecord(HistoryContract):
    id: UUID
    parent_id: UUID | None
    state: Literal["building", "recorded", "abandoned"]
    profile_digest: Digest
    input_digest: Digest
    input_snapshot: GraphInput
    artifact_digest: Digest | None
    abandon_reason: ShortText | None
    database_role: str
    created_at: datetime
    finished_at: datetime | None
    source_matches: bool | None


class GraphGenerationResult(HistoryContract):
    operation: Literal["get", "begin", "record", "abandon"]
    tenant_id: UUID
    revision: Revision
    head: GenerationRecord | None
    building: GenerationRecord | None
    current_input: GraphInput | None
    current_input_digest: Digest | None
    changed: bool
    artifact_verified: Literal[False] = False
    serving_enabled: Literal[False] = False


def input_digest(snapshot: GraphInput, secret: bytes) -> str:
    payload = json.dumps(
        snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()
    return hmac.new(secret, b"pgag-graph-input-v1:" + payload, hashlib.sha256).hexdigest()


def capture_input(conn: AdminConnection, tenant_id: UUID, secret: bytes) -> GraphInput:
    if SCHEMA_VERSION != 22:
        raise AdminError("schema_version_mismatch")
    tenant = conn.execute(
        "SELECT access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s", (tenant_id,),
    ).fetchone()
    if tenant is None:
        raise AdminError("not_found")
    try:
        tables = fingerprint_tables(
            conn, tenant_id, secret, INPUT_TABLES, filters=INPUT_FILTERS,
        )
    except AdminError as exc:
        translated = {
            "processing_recovery_limit": "graph_input_limit",
            "processing_recovery_timeout": "graph_input_timeout",
        }
        if exc.code in translated:
            raise AdminError(translated[exc.code]) from None
        raise
    return GraphInput(tenant_id=tenant_id, tables=tables, **tenant)


def _state(conn: AdminConnection, tenant_id: UUID) -> dict[str, Any]:
    row = conn.execute(
        """SELECT revision,head_id,building_id FROM memory_ops.graph_generation_state
           WHERE tenant_id=%s""", (tenant_id,),
    ).fetchone()
    if row is not None:
        return row
    if conn.execute(
        "SELECT 1 FROM memory_ops.graph_generation WHERE tenant_id=%s LIMIT 1", (tenant_id,),
    ).fetchone():
        raise AdminError("graph_generation_invalid")
    return {"revision": 0, "head_id": None, "building_id": None}


def _record(
    conn: AdminConnection, tenant_id: UUID, generation_id: UUID | None,
    secret: bytes, current_digest: str | None,
) -> GenerationRecord | None:
    if generation_id is None:
        return None
    row = conn.execute(
        "SELECT * FROM memory_ops.graph_generation WHERE tenant_id=%s AND id=%s",
        (tenant_id, generation_id),
    ).fetchone()
    if row is None:
        raise AdminError("graph_generation_invalid")
    snapshot = GraphInput.model_validate_json(json.dumps(row["input_snapshot"], allow_nan=False))
    if snapshot.tenant_id != tenant_id or not hmac.compare_digest(
        input_digest(snapshot, secret), row["input_digest"],
    ):
        raise AdminError("graph_generation_invalid")
    return GenerationRecord(
        **{key: value for key, value in row.items() if key not in ("tenant_id", "input_snapshot")},
        input_snapshot=snapshot,
        source_matches=(
            hmac.compare_digest(row["input_digest"], current_digest)
            if current_digest is not None else None
        ),
    )


def _mutate(
    conn: AdminConnection, request: GraphGenerationRequest, state: dict[str, Any],
    current: GraphInput | None, digest: str | None, building: GenerationRecord | None,
) -> tuple[UUID | None, UUID | None]:
    tenant, generation = request.tenant_id, request.generation_id
    if request.operation == "begin":
        if state["building_id"] is not None:
            raise AdminError("graph_build_in_progress")
        if conn.execute(
            "SELECT 1 FROM memory_ops.graph_generation WHERE tenant_id=%s AND id=%s",
            (tenant, generation),
        ).fetchone():
            raise AdminError("graph_generation_exists")
        count = conn.execute(
            """SELECT count(*) AS total FROM
               (SELECT 1 FROM memory_ops.graph_generation WHERE tenant_id=%s LIMIT %s) g""",
            (tenant, MAX_GENERATIONS),
        ).fetchone()
        if count is None or count["total"] >= MAX_GENERATIONS:
            raise AdminError("graph_generation_limit")
        assert current is not None and digest is not None
        if digest != request.expected_input_digest:
            raise AdminError("graph_input_changed")
        conn.execute(
            """INSERT INTO memory_ops.graph_generation
               (tenant_id,id,parent_id,state,profile_digest,input_digest,input_snapshot)
               VALUES (%s,%s,%s,'building',%s,%s,%s)""",
            (tenant, generation, state["head_id"], request.profile_digest, digest,
             Jsonb(current.model_dump(mode="json"))),
        )
        return state["head_id"], generation
    if building is None or generation != state["building_id"] or building.state != "building":
        raise AdminError("graph_generation_not_building")
    if request.operation == "record":
        if (
            digest != building.input_digest
            or request.expected_input_digest != building.input_digest
        ):
            raise AdminError("graph_input_changed")
        conn.execute(
            """UPDATE memory_ops.graph_generation SET state='recorded',artifact_digest=%s
               WHERE tenant_id=%s AND id=%s""",
            (request.artifact_digest, tenant, generation),
        )
        return generation, None
    conn.execute(
        """UPDATE memory_ops.graph_generation SET state='abandoned',abandon_reason=%s
           WHERE tenant_id=%s AND id=%s""", (request.reason, tenant, generation),
    )
    return state["head_id"], None


@contextmanager
def graph_generation(
    url: str, request: GraphGenerationRequest,
) -> Iterator[GraphGenerationResult]:
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
                secret = secret_for(conn, request.tenant_id)
                state = _state(conn, request.tenant_id)
                changed = request.operation != "get"
                if changed:
                    if request.expected_revision != state["revision"]:
                        raise AdminError("graph_revision_conflict")
                    if state["revision"] == MAX_EPOCH:
                        raise AdminError("graph_revision_exhausted")
                current = (
                    capture_input(conn, request.tenant_id, secret)
                    if request.operation != "abandon" else None
                )
                digest = input_digest(current, secret) if current is not None else None
                building = _record(
                    conn, request.tenant_id, state["building_id"], secret, digest,
                )
                if changed:
                    previous_revision = state["revision"]
                    head_id, building_id = _mutate(
                        conn, request, state, current, digest, building,
                    )
                    state = {
                        "revision": state["revision"] + 1,
                        "head_id": head_id,
                        "building_id": building_id,
                    }
                    if previous_revision == 0:
                        conn.execute(
                            """INSERT INTO memory_ops.graph_generation_state
                               (tenant_id,revision,head_id,building_id) VALUES (%s,1,%s,%s)""",
                            (request.tenant_id, head_id, building_id),
                        )
                    else:
                        changed_state = conn.execute(
                            """UPDATE memory_ops.graph_generation_state
                               SET revision=%s,head_id=%s,building_id=%s
                               WHERE tenant_id=%s AND revision=%s""",
                            (state["revision"], head_id, building_id,
                             request.tenant_id, previous_revision),
                        )
                        if changed_state.rowcount != 1:
                            raise AdminError("graph_revision_conflict")
                    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                result = GraphGenerationResult(
                    operation=request.operation, tenant_id=request.tenant_id,
                    revision=state["revision"], current_input=current,
                    current_input_digest=digest, changed=changed,
                    head=_record(conn, request.tenant_id, state["head_id"], secret, digest),
                    building=_record(conn, request.tenant_id, state["building_id"], secret, digest),
                )
                commit_attempted = changed
            yield result
    except ValidationError:
        raise AdminError("graph_generation_invalid") from None
    except (psycopg.Error, CommitOutcomeUnknown) as exc:
        raise admin_failure(exc, commit_attempted) from None


class GraphGenerationParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_graph_generation_arguments")


def main(argv: list[str]) -> None:
    parser = GraphGenerationParser(
        prog="pg-agmemory graph-generation", description="Read a closed JSON request from stdin.",
    )
    parser.parse_args(argv)
    try:
        raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            parser.error("oversized request")
        request = GraphGenerationRequest.model_validate_json(raw)
    except (OSError, ValueError, UnicodeError, RecursionError):
        parser.error("invalid request")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with graph_generation(url, request) as result:
            print(result.model_dump_json(), flush=True)
    except AdminError as exc:
        print(json.dumps({
            "error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown},
        }), flush=True)
        raise SystemExit(1) from None
