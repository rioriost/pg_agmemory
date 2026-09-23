import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, Never
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pg_agmemory.admin import MAX_EPOCH, AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.models import Contract, Observe, ShortText
from pg_agmemory.transactions import CommitOutcomeUnknown, transaction

PolicyList = Annotated[list[ShortText], Field(max_length=64)]
POLICY_COLUMNS = "enabled,source_namespaces,consent_references,max_content_bytes"


class CapturePolicy(Contract):
    enabled: Annotated[bool, Field(strict=True)]
    source_namespaces: PolicyList | None
    consent_references: PolicyList | None
    max_content_bytes: Annotated[int, Field(ge=1, le=262144, strict=True)]

    @model_validator(mode="after")
    def canonical_lists(self) -> "CapturePolicy":
        for name in ("source_namespaces", "consent_references"):
            values = getattr(self, name)
            if values is not None:
                if len(values) != len(set(values)):
                    raise ValueError("Policy lists must contain distinct values")
                for value in values:
                    value.encode("utf-8")
                    if any(ord(character) < 32 for character in value):
                        raise ValueError("Policy labels must not contain control characters")
                setattr(self, name, sorted(values))
        return self

    @classmethod
    def legacy(cls) -> "CapturePolicy":
        return cls(
            enabled=True, source_namespaces=None, consent_references=None, max_content_bytes=262144
        )

    def permits(self, data: Observe) -> bool:
        return (
            self.enabled
            and (self.source_namespaces is None or data.source_namespace in self.source_namespaces)
            and (
                self.consent_references is None or data.consent_reference in self.consent_references
            )
            and len(data.content.encode("utf-8")) <= self.max_content_bytes
        )


class CapturePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: Literal["get", "set"]
    tenant_id: UUID
    scope_id: UUID
    expected_access_epoch: Epoch | None = None
    policy: CapturePolicy | None = None

    @model_validator(mode="after")
    def command_arguments(self) -> "CapturePolicyRequest":
        if self.operation == "get":
            if self.expected_access_epoch is not None or self.policy is not None:
                raise ValueError("get does not accept mutation arguments")
        elif self.expected_access_epoch is None or self.policy is None:
            raise ValueError("set requires an expected epoch and a complete policy")
        return self


class CapturePolicyResult(BaseModel):
    operation: Literal["get", "set"]
    tenant_id: UUID
    scope_id: UUID
    access_epoch: int
    policy_access_epoch: int | None
    configured: bool
    changed: bool
    policy: CapturePolicy


def stored_policy(row: dict[str, Any] | None) -> CapturePolicy:
    if row is None:
        return CapturePolicy.legacy()
    return CapturePolicy.model_validate({key: row[key] for key in POLICY_COLUMNS.split(",")})


@contextmanager
def capture_policy(url: str, request: CapturePolicyRequest) -> Iterator[CapturePolicyResult]:
    commit_attempted = False
    try:
        with admin_connection(url, request.tenant_id) as conn:
            with transaction(conn):
                target = conn.execute(
                    """SELECT t.access_epoch FROM memory.tenant t
                       JOIN memory.scope s ON s.tenant_id=t.id AND s.id=%s WHERE t.id=%s"""
                    + (" FOR UPDATE OF t" if request.operation == "set" else ""),
                    (request.scope_id, request.tenant_id),
                ).fetchone()
                if target is None:
                    raise AdminError("not_found")
                epoch = target["access_epoch"]
                if request.operation == "set" and epoch != request.expected_access_epoch:
                    raise AdminError("access_epoch_conflict")
                identity = (request.tenant_id, request.scope_id)
                row = conn.execute(
                    f"SELECT access_epoch,{POLICY_COLUMNS} FROM memory.scope_capture_policy "
                    "WHERE tenant_id=%s AND scope_id=%s",
                    identity,
                ).fetchone()
                before = stored_policy(row)
                after = request.policy if request.policy is not None else before
                changed = before != after
                policy_epoch = row["access_epoch"] if row else None
                if changed:
                    if epoch == MAX_EPOCH:
                        raise AdminError("access_epoch_exhausted")
                    epoch += 1
                    conn.execute(
                        """INSERT INTO memory.scope_capture_policy
                           (tenant_id,scope_id,access_epoch,enabled,source_namespaces,
                            consent_references,max_content_bytes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (tenant_id,scope_id) DO UPDATE SET
                             access_epoch=excluded.access_epoch,enabled=excluded.enabled,
                             source_namespaces=excluded.source_namespaces,
                             consent_references=excluded.consent_references,
                             max_content_bytes=excluded.max_content_bytes""",
                        (
                            *identity,
                            epoch,
                            after.enabled,
                            after.source_namespaces,
                            after.consent_references,
                            after.max_content_bytes,
                        ),
                    )
                    conn.execute(
                        "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s",
                        (epoch, request.tenant_id),
                    )
                    conn.execute(
                        """INSERT INTO memory_ops.capture_policy_event
                           (tenant_id,scope_id,access_epoch,previous_policy,policy)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (*identity, epoch, Jsonb(before.model_dump()), Jsonb(after.model_dump())),
                    )
                    policy_epoch = epoch
                result = CapturePolicyResult(
                    operation=request.operation,
                    tenant_id=request.tenant_id,
                    scope_id=request.scope_id,
                    access_epoch=epoch,
                    policy_access_epoch=policy_epoch,
                    configured=row is not None or changed,
                    changed=changed,
                    policy=after,
                )
                commit_attempted = changed
            yield result
    except ValidationError:
        raise AdminError("capture_policy_invalid") from None
    except (psycopg.Error, CommitOutcomeUnknown) as exc:
        raise admin_failure(exc, commit_attempted) from None


class PolicyParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_capture_policy_arguments")


def main(argv: list[str]) -> None:
    parser = PolicyParser(prog="pg-agmemory scope-capture")
    parser.add_argument("operation", choices=("get", "set"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--scope-id", required=True, type=UUID)
    parser.add_argument("--expected-access-epoch", type=int)
    parser.add_argument("--policy-file", type=Path)
    args = vars(parser.parse_args(argv))
    path = args.pop("policy_file")
    try:
        if path is not None:
            if args["operation"] != "set":
                parser.error("invalid_capture_policy_arguments")
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                parser.error("invalid_capture_policy_arguments")
            args["policy"] = json.loads(raw)
        request = CapturePolicyRequest.model_validate(args)
    except (OSError, ValueError, UnicodeError, RecursionError):
        parser.error("invalid_capture_policy_arguments")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory scope-capture: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with capture_policy(url, request) as result:
            print(result.model_dump_json(), flush=True)
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown}}),
            flush=True,
        )
        raise SystemExit(1) from None
