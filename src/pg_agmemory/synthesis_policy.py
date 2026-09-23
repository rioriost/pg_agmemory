import argparse
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, Never
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import Field, ValidationError, model_validator

from pg_agmemory.admin import MAX_EPOCH, AdminError, Epoch, admin_connection, admin_failure
from pg_agmemory.models import Contract, Digest, Predicate, ShortText
from pg_agmemory.transactions import CommitOutcomeUnknown, transaction

RECIPES = {
    "extract": "source-extraction-v1",
    "embed": "canonical-embedding-v1",
    "compact": "working-compaction-v1",
}


class SynthesisPolicy(Contract):
    enabled: Annotated[bool, Field(strict=True)] = False
    profile_digest: Digest | None = None
    consent_references: Annotated[list[ShortText], Field(max_length=64)] = Field(
        default_factory=list
    )
    publish_predicates: Annotated[list[Predicate], Field(max_length=32)] = Field(
        default_factory=list
    )
    kinds: Annotated[list[Literal["extract", "embed", "compact"]], Field(max_length=3)] = Field(
        default_factory=list
    )
    max_pending_jobs: Annotated[int, Field(strict=True, ge=1, le=100)] = 20
    max_input_bytes: Annotated[int, Field(strict=True, ge=1, le=262144)] = 16384
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=4096)] = 1024
    max_calls: Annotated[int, Field(strict=True, ge=1, le=10000)] = 100

    @model_validator(mode="after")
    def canonical(self) -> "SynthesisPolicy":
        for field in ("consent_references", "publish_predicates", "kinds"):
            values = getattr(self, field)
            if len(set(values)) != len(values):
                raise ValueError("Policy lists must be distinct")
            for value in values:
                value.encode("utf-8")
                if any(ord(character) < 32 for character in value):
                    raise ValueError("Policy labels must not contain control characters")
            setattr(self, field, sorted(values))
        if self.enabled and (not self.profile_digest or not self.consent_references):
            raise ValueError("Enabled synthesis requires a pinned profile and explicit consent")
        return self


class SynthesisPolicyRequest(Contract):
    operation: Literal["get", "set"]
    tenant_id: UUID
    scope_id: UUID
    expected_access_epoch: Epoch | None = None
    policy: SynthesisPolicy | None = None

    @model_validator(mode="after")
    def arguments(self) -> "SynthesisPolicyRequest":
        if self.operation == "get":
            if self.policy is not None or self.expected_access_epoch is not None:
                raise ValueError("get does not accept mutation arguments")
        elif self.policy is None or self.expected_access_epoch is None:
            raise ValueError("set requires policy and expected epoch")
        return self


class SynthesisPolicyResult(Contract):
    operation: Literal["get", "set"]
    tenant_id: UUID
    scope_id: UUID
    access_epoch: int
    policy_access_epoch: int | None
    configured: bool
    changed: bool
    policy: SynthesisPolicy


def profile_digest(
    settings: dict[str, Any], *, prompt_digests: dict[str, str] | None = None
) -> str:
    # Bind recipes as well as all provider configuration; credentials are environment references.
    if prompt_digests is None:
        from pg_agmemory.providers import EXTRACTION_SYSTEM_PROMPT, SUMMARY_SYSTEM_PROMPT

        prompt_digests = {
            "extract": hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest(),
            "compact": hashlib.sha256(SUMMARY_SYSTEM_PROMPT.encode()).hexdigest(),
        }
    value = {
        "provider": settings, "recipes": RECIPES, "profile": "local-worker-v1",
        "prompt_digests": prompt_digests,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@contextmanager
def synthesis_policy(
    url: str, request: SynthesisPolicyRequest
) -> Iterator[SynthesisPolicyResult]:
    commit_attempted = False
    try:
        with admin_connection(url, request.tenant_id) as conn:
            with transaction(conn):
                row = conn.execute(
                    """SELECT t.access_epoch FROM memory.tenant t JOIN memory.scope s
                       ON s.tenant_id=t.id AND s.id=%s WHERE t.id=%s"""
                    + (" FOR UPDATE OF t" if request.operation == "set" else ""),
                    (request.scope_id, request.tenant_id),
                ).fetchone()
                if row is None:
                    raise AdminError("not_found")
                epoch = row["access_epoch"]
                if request.operation == "set" and epoch != request.expected_access_epoch:
                    raise AdminError("access_epoch_conflict")
                identity = (request.tenant_id, request.scope_id)
                stored = conn.execute(
                    """SELECT policy,access_epoch FROM memory.scope_synthesis_policy
                       WHERE tenant_id=%s AND scope_id=%s""", identity,
                ).fetchone()
                before = SynthesisPolicy.model_validate(stored["policy"] if stored else {})
                after = request.policy if request.policy is not None else before
                changed = before != after
                policy_epoch = stored["access_epoch"] if stored else None
                if changed:
                    if epoch == MAX_EPOCH:
                        raise AdminError("access_epoch_exhausted")
                    epoch += 1
                    conn.execute(
                        """INSERT INTO memory.scope_synthesis_policy
                           (tenant_id,scope_id,access_epoch,policy) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (tenant_id,scope_id) DO UPDATE SET
                           access_epoch=excluded.access_epoch,policy=excluded.policy""",
                        (*identity, epoch, Jsonb(after.model_dump())),
                    )
                    conn.execute(
                        "UPDATE memory.tenant SET access_epoch=%s WHERE id=%s",
                        (epoch, request.tenant_id),
                    )
                    conn.execute(
                        """INSERT INTO memory_ops.synthesis_policy_event
                           (tenant_id,scope_id,access_epoch,previous_policy,policy)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (*identity, epoch, Jsonb(before.model_dump()), Jsonb(after.model_dump())),
                    )
                    policy_epoch = epoch
                result = SynthesisPolicyResult(
                    operation=request.operation, tenant_id=request.tenant_id,
                    scope_id=request.scope_id, access_epoch=epoch,
                    policy_access_epoch=policy_epoch, configured=stored is not None or changed,
                    changed=changed, policy=after,
                )
                commit_attempted = changed
            yield result
    except ValidationError:
        raise AdminError("synthesis_policy_invalid") from None
    except (psycopg.Error, CommitOutcomeUnknown) as exc:
        raise admin_failure(exc, commit_attempted) from None


class SynthesisParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_synthesis_policy_arguments")


def main(argv: list[str]) -> None:
    parser = SynthesisParser(prog="pg-agmemory scope-synthesis")
    parser.add_argument("operation", choices=("get", "set"))
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--scope-id", required=True, type=UUID)
    parser.add_argument("--expected-access-epoch", type=int)
    parser.add_argument("--policy-file", type=Path)
    args = vars(parser.parse_args(argv))
    path = args.pop("policy_file")
    try:
        if path is not None:
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            args["policy"] = json.loads(raw)
        request = SynthesisPolicyRequest.model_validate(args)
    except (OSError, ValueError, UnicodeError, RecursionError):
        parser.error("invalid_synthesis_policy_arguments")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with synthesis_policy(url, request) as result:
            print(result.model_dump_json(), flush=True)
    except AdminError as exc:
        print(json.dumps({"error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown}}))
        raise SystemExit(1) from None
