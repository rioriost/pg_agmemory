import hashlib
import json
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import ConfigDict, ValidationError

from pg_agmemory.capture_policy import POLICY_COLUMNS, stored_policy
from pg_agmemory.embeddings import Embeddings
from pg_agmemory.jobs import Jobs
from pg_agmemory.models import (
    AdoptCandidate,
    Evidence,
    Explain,
    InferredMemory,
    MemoryReference,
    ProcessMemory,
    Remember,
)
from pg_agmemory.service import MemoryError, MemoryService
from pg_agmemory.synthesis_policy import RECIPES, SynthesisPolicy

if TYPE_CHECKING:
    from pg_agmemory.worker_profile import WorkerProfile


class ProposalEvidence(Evidence):
    model_config = ConfigDict(str_strip_whitespace=False)


class AdoptedMemory(Remember):
    model_config = ConfigDict(str_strip_whitespace=False)


class Processing:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.conn = memory.conn
        self.tenant = memory.tenant

    async def policy(self, scope_id: UUID, kind: str) -> tuple[SynthesisPolicy, int]:
        await self.memory.scope(scope_id, "write")
        row = await (
            await self.conn.execute(
                """SELECT policy,access_epoch FROM memory.scope_synthesis_policy
                   WHERE tenant_id=%s AND scope_id=%s""", (self.tenant, scope_id),
            )
        ).fetchone()
        try:
            policy = SynthesisPolicy.model_validate(row["policy"] if row else {})
        except ValidationError:
            raise MemoryError("synthesis_policy_invalid", 503) from None
        if row is None or not policy.enabled or kind not in policy.kinds:
            raise MemoryError("synthesis_policy_denied", 403)
        return policy, row["access_epoch"]

    async def source(
        self, scope_id: UUID, ref: MemoryReference, policy: SynthesisPolicy
    ) -> dict[str, Any]:
        obj = await self.memory.object(ref.memory_id)
        if obj["scope_id"] != scope_id:
            raise MemoryError("invalid_processing_reference", 422)
        if obj["kind"] == "checkpoint":
            from pg_agmemory.checkpoints import Checkpoints

            saved = await Checkpoints(self.memory).load(ref.memory_id)
            if ref.revision != 1:
                raise MemoryError("invalid_processing_reference", 422)
            for item in saved["memory_refs"]:
                await self.source(scope_id, MemoryReference.model_validate(item), policy)
            return obj
        if obj["kind"] in ("assertion", "entity"):
            await self.memory.validate_refs(scope_id, [ref], "invalid_processing_reference")
            table = (
                """SELECT parent_id AS id FROM memory.provenance_edge
                   WHERE tenant_id=%s AND child_id=%s AND child_revision=%s"""
                if obj["kind"] == "assertion" else
                """SELECT source_id AS id FROM memory.entity_evidence
                   WHERE tenant_id=%s AND entity_id=%s AND %s=1"""
            )
            parents = await (
                await self.conn.execute(table, (self.tenant, ref.memory_id, ref.revision))
            ).fetchall()
            if not parents:
                raise MemoryError("invalid_processing_reference", 422)
            for parent in parents:
                await self.source(scope_id, MemoryReference(memory_id=parent["id"]), policy)
            return obj
        if obj["kind"] != "episode" or ref.revision != 1:
            raise MemoryError("invalid_processing_reference", 422)
        episode = await (
            await self.conn.execute(
                """SELECT content,consent_reference,source_namespace FROM memory.episode
                   WHERE tenant_id=%s AND id=%s""", (self.tenant, ref.memory_id),
            )
        ).fetchone()
        capture_row = await (
            await self.conn.execute(
                f"SELECT {POLICY_COLUMNS} FROM memory.scope_capture_policy "
                "WHERE tenant_id=%s AND scope_id=%s", (self.tenant, scope_id),
            )
        ).fetchone()
        try:
            capture = stored_policy(capture_row)
        except ValidationError:
            raise MemoryError("capture_policy_invalid", 503) from None
        if (
            episode is None or not capture.enabled
            or episode["consent_reference"] not in policy.consent_references
            or (capture.consent_references is not None
                and episode["consent_reference"] not in capture.consent_references)
            or (capture.source_namespaces is not None
                and episode["source_namespace"] not in capture.source_namespaces)
            or len(episode["content"].encode()) > capture.max_content_bytes
        ):
            raise MemoryError("synthesis_policy_denied", 403)
        return obj | episode

    async def enqueue(self, data: ProcessMemory, key: str) -> dict[str, Any]:
        policy, epoch = await self.policy(data.scope_id, data.kind)
        obj = await self.source(data.scope_id, data.source, policy)
        if data.kind == "extract" and obj["kind"] != "episode":
            raise MemoryError("invalid_processing_reference", 422)
        if data.kind == "embed" and obj["kind"] not in ("episode", "assertion"):
            raise MemoryError("invalid_processing_reference", 422)
        text = obj["content"] if data.kind == "extract" else (
            await Embeddings(self.memory).input(Explain(**data.source.model_dump()))
        ).text
        if not 0 < len(text.encode()) <= policy.max_input_bytes or len(text) > 65536:
            raise MemoryError("processing_input_limit", 422)
        return await self.enqueue_intent(
            data.scope_id, data.kind, [data.source], {}, policy, epoch, key, retry_of=data.retry_of
        )

    async def enqueue_intent(
        self, scope_id: UUID, kind: str, refs: list[MemoryReference], extra: dict[str, Any],
        policy: SynthesisPolicy, epoch: int, key: str,
        *, retry_of: UUID | None = None,
    ) -> dict[str, Any]:
        refs = sorted(refs, key=lambda ref: str(ref.memory_id))
        if len(refs) > 101 or len({ref.memory_id for ref in refs}) != len(refs):
            raise MemoryError("invalid_processing_reference", 422)
        payload = {
            "scope_id": str(scope_id), "kind": kind, "recipe_version": RECIPES[kind],
            "profile_digest": policy.profile_digest, "policy_epoch": epoch,
            "input_refs": [ref.model_dump(mode="json") for ref in refs], **extra,
        }
        # Policy changes fence egress, but must not turn an unknown call into new work.
        identity_payload = {
            name: value for name, value in payload.items() if name != "policy_epoch"
        }
        body = json.dumps(identity_payload, sort_keys=True)
        key_hash, request_hash, previous = await self.memory.replay(
            "processing", key, body + ":" + str(retry_of)
        )
        if previous is not None:
            await Jobs(self.memory).load(UUID(previous["job_id"]))
            return previous
        intent = await self.memory.digest("processing-v1:" + body)
        if retry_of is not None:
            parent = await Jobs(self.memory).load(retry_of, "write")
            if parent["principal_id"] != self.memory.principal:
                raise MemoryError("not_found", 404)
            if parent["state"] != "failed":
                raise MemoryError("job_retry_conflict", 409)
            if parent["intent_digest"] != intent:
                raise MemoryError("job_intent_conflict", 409)
            call = await (
                await self.conn.execute(
                    """SELECT outcome,billing_unknown FROM memory_ops.model_call
                       WHERE tenant_id=%s AND job_id=%s""", (self.tenant, retry_of),
                )
            ).fetchone()
            if call and (call["outcome"] == "unknown" or call["billing_unknown"]):
                raise MemoryError("job_retry_unknown", 409)
        identity = await self.memory.digest(
            "processing-identity-v1:" + intent + ":" + str(retry_of)
        )
        duplicate = await (
            await self.conn.execute(
                """SELECT job_id FROM memory_ops.job_identity WHERE tenant_id=%s
                   AND scope_id=%s AND principal_id=%s AND input_digest=%s""",
                (self.tenant, scope_id, self.memory.principal, identity),
            )
        ).fetchone()
        if duplicate:
            await Jobs(self.memory).load(duplicate["job_id"])
            job_id = duplicate["job_id"]
        else:
            count = await (
                await self.conn.execute(
                    """SELECT count(*) AS total FROM memory_ops.job WHERE tenant_id=%s
                       AND scope_id=%s AND state IN ('pending','running')""",
                    (self.tenant, scope_id),
                )
            ).fetchone()
            if count and count["total"] >= policy.max_pending_jobs:
                raise MemoryError("job_limit_exceeded", 422)
            job_id = await self.memory.new_object(scope_id, "job")
            await self.conn.execute(
                """INSERT INTO memory_ops.job
                   (tenant_id,id,scope_id,principal_id,kind,recipe_version,intent_digest,
                    payload,reference_count,retry_of,captured_access_epoch,captured_deletion_epoch)
                   SELECT id,%s,%s,%s,%s,%s,%s,%s,%s,%s,access_epoch,deletion_epoch
                   FROM memory.tenant WHERE id=%s""",
                (job_id, scope_id, self.memory.principal, kind, RECIPES[kind], intent,
                 Jsonb(payload), len(refs), retry_of, self.tenant),
            )
            for ref in refs:
                await self.conn.execute(
                    """INSERT INTO memory_ops.job_input
                       (tenant_id,job_id,scope_id,source_id,source_revision)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (self.tenant, job_id, scope_id, ref.memory_id, ref.revision),
                )
            await self.conn.execute(
                """INSERT INTO memory_ops.job_identity
                   (tenant_id,scope_id,principal_id,input_digest,job_id) VALUES (%s,%s,%s,%s,%s)""",
                (self.tenant, scope_id, self.memory.principal, identity, job_id),
            )
            await self.memory.audit("enqueue_" + kind, job_id)
        result = {"job_id": str(job_id), "kind": kind, "recipe_version": RECIPES[kind]}
        await self.memory.save_result("processing", key_hash, request_hash, result)
        return result

    async def prepare(
        self, job_id: UUID, token: UUID, profile: "WorkerProfile", *, reserve: bool
    ) -> dict[str, Any]:
        row = await Jobs(self.memory).lease(job_id, token)
        policy, epoch = await self.policy(row["scope_id"], row["kind"])
        payload = row["payload"]
        if (
            profile.digest != policy.profile_digest
            or payload["profile_digest"] != profile.digest
            or payload["policy_epoch"] != epoch
        ):
            raise MemoryError("synthesis_policy_denied", 403)
        profile.validate_kind(row["kind"], policy.max_output_tokens)
        refs = [MemoryReference.model_validate(ref) for ref in row["input_refs"]]
        for ref in refs:
            await self.source(row["scope_id"], ref, policy)
        if row["kind"] == "extract":
            source = await self.source(row["scope_id"], refs[0], policy)
            text = source["content"]
        elif row["kind"] == "embed":
            text = (await Embeddings(self.memory).input(Explain(**refs[0].model_dump()))).text
        else:
            from pg_agmemory.compaction import Working

            text = await Working(self.memory).input(payload)
        size = len(text.encode())
        if not 0 < size <= policy.max_input_bytes or len(text) > 65536:
            raise MemoryError("processing_input_limit", 422)
        digest = hashlib.sha256(text.encode()).hexdigest()
        fingerprint = await self.memory.digest("model-call-input-v1:" + digest)
        call = await (
            await self.conn.execute(
                "SELECT * FROM memory_ops.model_call WHERE tenant_id=%s AND job_id=%s",
                (self.tenant, job_id),
            )
        ).fetchone()
        if reserve:
            if call:
                raise MemoryError("billing_unknown", 409)
            budget = await (
                await self.conn.execute(
                    """SELECT count(*) AS total FROM memory_ops.model_call
                       WHERE tenant_id=%s AND scope_id=%s AND policy_epoch=%s""",
                    (self.tenant, row["scope_id"], epoch),
                )
            ).fetchone()
            if budget and budget["total"] >= policy.max_calls:
                raise MemoryError("processing_call_limit", 422)
            await self.conn.execute(
                """INSERT INTO memory_ops.model_call
                   (tenant_id,job_id,scope_id,policy_epoch,lease_token,profile_digest,
                    input_digest,input_bytes,max_output_tokens)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (self.tenant, job_id, row["scope_id"], epoch, token, profile.digest, fingerprint,
                 size, profile.settings.max_output_tokens if row["kind"] != "embed" else None),
            )
        elif not call or call["lease_token"] != token or call["input_digest"] != fingerprint:
            raise MemoryError("job_intent_conflict", 409)
        return {"job": row, "policy": policy, "text": text, "input_digest": digest}

    async def publish(
        self, job_id: UUID, token: UUID, profile: "WorkerProfile", generated: Any
    ) -> dict[str, Any]:
        prepared = await self.prepare(job_id, token, profile, reserve=False)
        row, digest = prepared["job"], prepared["input_digest"]
        if generated.input_digest != digest:
            raise MemoryError("job_intent_conflict", 409)
        expected_model = (
            profile.settings.embedding_model if row["kind"] == "embed"
            else profile.settings.text_model
        )
        if generated.model != expected_model:
            raise MemoryError("job_intent_conflict", 409)
        if row["kind"] == "extract":
            from pg_agmemory.providers import ExtractionResult, InferenceInput, parse_extraction

            generated = ExtractionResult.model_validate(generated)
            parse_extraction(
                {"candidates": [item.model_dump() for item in generated.candidates]},
                InferenceInput(text=prepared["text"]), generated.model,
            )
            result = await self.publish_candidates(
                row, generated, prepared["policy"], prepared["text"],
                profile.prompt_digests["extract"],
            )
        elif row["kind"] == "embed":
            from pg_agmemory.models import PutEmbedding

            result = await Embeddings(self.memory).put(
                PutEmbedding(**row["input_refs"][0], **generated.model_dump()),
                "model-embedding-v1:" + str(job_id),
            )
        else:
            from pg_agmemory.compaction import Working

            result = await Working(self.memory).publish(row, generated)
        result.update({
            "model": generated.model.model_dump(mode="json"),
            "input_digest": generated.input_digest,
            "profile_digest": profile.digest,
            "prompt_digest": profile.prompt_digests.get(row["kind"]),
            "policy_epoch": row["payload"]["policy_epoch"],
            "recipe_version": row["recipe_version"],
        })
        await self.conn.execute(
            """UPDATE memory_ops.model_call SET outcome='succeeded',billing_unknown=false
               WHERE tenant_id=%s AND job_id=%s""", (self.tenant, job_id),
        )
        saved = await (
            await self.conn.execute(
                """UPDATE memory_ops.job SET state='succeeded',processing_result=%s,payload=NULL,
                   lease_token=NULL,lease_until=NULL,error_code=NULL WHERE tenant_id=%s
                   AND id=%s AND lease_token=%s AND lease_until>clock_timestamp() RETURNING id""",
                (Jsonb(result), self.tenant, job_id, token),
            )
        ).fetchone()
        if not saved:
            raise MemoryError("job_lease_conflict", 409)
        await self.memory.audit("job_succeeded", job_id)
        return result

    async def publish_candidates(
        self, row: dict[str, Any], generated: Any, policy: SynthesisPolicy,
        input_text: str, prompt_digest: str,
    ) -> dict[str, Any]:
        counts = {"published": 0, "duplicate": 0, "quarantined": 0}
        refs = []
        source_id = UUID(row["input_refs"][0]["memory_id"]) if isinstance(
            row["input_refs"][0]["memory_id"], str
        ) else row["input_refs"][0]["memory_id"]
        derivation = {
            "job_id": str(row["id"]), "model": generated.model.model_dump(),
            "recipe_version": row["recipe_version"], "prompt_version": "source-extraction-v1",
            "prompt_digest": prompt_digest,
            "profile_digest": row["payload"]["profile_digest"],
            "policy_epoch": row["payload"]["policy_epoch"],
            "input_digest": generated.input_digest,
            "source": {"memory_id": str(source_id), "revision": 1},
            "status": "untrusted",
        }
        proposed: dict[tuple[str, str], set[str]] = {}
        for candidate in generated.candidates:
            proposed.setdefault((candidate.subject, candidate.predicate), set()).add(
                candidate.value
            )
        for ordinal, candidate in enumerate(generated.candidates):
            disposition, reason = "quarantined", "predicate_not_allowed"
            assertion_id = None
            # This profile intentionally accepts only a quoted preference declaration.
            # Lexical grounding alone cannot prove the semantics of arbitrary model claims.
            declaration = (
                candidate.subject + " / " + candidate.predicate + ": " + candidate.value
            )
            low_impact = candidate.predicate in {
                "preferred_language", "preferred_editor", "preferred_theme", "preferred_format",
            }
            unsafe = re.search(
                r"(?i)(approv|permission|password|secret|token|execute|ignore|grant|denied|"
                r"\bnot\b|\bmaybe\b|\bunknown\b|承認|秘密|実行|無視|禁止)",
                candidate.evidence_quote,
            )
            if low_impact and candidate.predicate in policy.publish_predicates:
                reason = "unsupported_or_ambiguous"
                ambiguous = len(proposed[(candidate.subject, candidate.predicate)]) > 1
                if (
                    declaration == candidate.evidence_quote == input_text
                    and not unsafe and not ambiguous
                    and all(value == value.strip() for value in (
                        candidate.subject, candidate.value, candidate.evidence_quote
                    ))
                    and re.fullmatch(r"[\w .+#/-]{1,128}", candidate.value)
                ):
                    existing = await (
                        await self.conn.execute(
                            """SELECT a.id,r.value FROM memory.assertion a
                               JOIN memory.assertion_revision r ON r.tenant_id=a.tenant_id
                               AND r.assertion_id=a.id AND r.revision=a.current_revision
                               WHERE a.tenant_id=%s AND a.scope_id=%s
                               AND a.subject=%s AND a.predicate=%s""",
                            (self.tenant, row["scope_id"], candidate.subject, candidate.predicate),
                        )
                    ).fetchall()
                    if existing:
                        exact = next(
                            (item for item in existing if item["value"] == candidate.value), None
                        )
                        if exact and all(item["value"] == candidate.value for item in existing):
                            disposition, reason = "duplicate", "existing_value"
                            # Do not attach another source's deletion lifetime to an existing fact.
                        else:
                            reason = "conflicting_value"
                    else:
                        saved = await self.memory.publish_assertion(InferredMemory(
                            scope_id=row["scope_id"], subject=candidate.subject,
                            predicate=candidate.predicate, value=candidate.value,
                            evidence=[
                                Evidence(memory_id=source_id, quote=candidate.evidence_quote)
                            ],
                        ))
                        assertion_id = UUID(saved["memory_id"])
                        metadata = derivation | {
                            "start": candidate.start, "end": candidate.end,
                            "source_class": "model_inference", "status": "untrusted",
                        }
                        await self.conn.execute(
                            """INSERT INTO memory.assertion_derivation
                               (tenant_id,assertion_id,revision,scope_id,job_id,metadata)
                               VALUES (%s,%s,1,%s,%s,%s)""",
                            (self.tenant, assertion_id, row["scope_id"], row["id"],
                             Jsonb(metadata)),
                        )
                        disposition, reason = "published", "allowed_literal_preference"
                        refs.append({"memory_id": str(assertion_id), "revision": 1})
            await self.conn.execute(
                """INSERT INTO memory_ops.extraction_candidate
                   (tenant_id,job_id,scope_id,ordinal,candidate,disposition,reason,assertion_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (self.tenant, row["id"], row["scope_id"], ordinal,
                 Jsonb(candidate.model_dump()), disposition, reason, assertion_id),
            )
            counts[disposition] += 1
        return {
            "counts": counts, "assertions": refs, "status": "untrusted", "derivation": derivation,
        }

    async def candidates(self, job_id: UUID) -> dict[str, Any]:
        row = await Jobs(self.memory).load(job_id)
        if row["kind"] != "extract":
            raise MemoryError("not_found", 404)
        for ref in row["input_refs"]:
            await self.memory.object(ref["memory_id"])
        rows = await (
            await self.conn.execute(
                """SELECT ordinal,candidate,disposition,reason,assertion_id,
                          adopted_assertion_id,adopted_by
                   FROM memory_ops.extraction_candidate WHERE tenant_id=%s AND job_id=%s
                   ORDER BY ordinal""", (self.tenant, job_id),
            )
        ).fetchall()
        return {
            "job_id": job_id, "candidates": rows, "status": "untrusted",
            "input_refs": row["input_refs"],
            "derivation": row["processing_result"].get("derivation")
            if row["processing_result"] else None,
        }

    async def adopt(
        self, job_id: UUID, ordinal: int, data: AdoptCandidate, key: str
    ) -> dict[str, Any]:
        row = await Jobs(self.memory).load(job_id, "write")
        if row["kind"] != "extract" or row["state"] != "succeeded":
            raise MemoryError("candidate_adoption_conflict", 409)
        source_ref = row["input_refs"][0]
        source = await Embeddings(self.memory).input(Explain(**source_ref))
        if source.type != "episode" or source.input_digest != data.expected_input_digest:
            raise MemoryError("candidate_input_conflict", 409)
        payload = json.dumps({
            "job_id": str(job_id), "ordinal": ordinal, "request": data.model_dump(mode="json"),
        }, sort_keys=True)
        key_hash, request_hash, previous = await self.memory.replay("adopt_candidate", key, payload)
        if previous is not None:
            return previous
        proposal = await (
            await self.conn.execute(
                """SELECT candidate,disposition,adopted_assertion_id
                   FROM memory_ops.extraction_candidate
                   WHERE tenant_id=%s AND job_id=%s AND ordinal=%s FOR UPDATE""",
                (self.tenant, job_id, ordinal),
            )
        ).fetchone()
        if proposal is None:
            raise MemoryError("not_found", 404)
        if proposal["disposition"] != "quarantined":
            raise MemoryError("candidate_adoption_conflict", 409)
        if proposal["adopted_assertion_id"] is not None:
            await self.memory.object(proposal["adopted_assertion_id"], "write")
            result: dict[str, Any] = {
                "memory_id": str(proposal["adopted_assertion_id"]),
                "revision": 1, "epistemic_status": "reported",
            }
        else:
            candidate = proposal["candidate"]
            derivation = row["processing_result"].get("derivation")
            if not isinstance(derivation, dict):
                raise MemoryError("candidate_adoption_conflict", 409)
            if (
                derivation["input_digest"] != source.input_digest
                or source.text[candidate["start"]:candidate["end"]] != candidate["evidence_quote"]
            ):
                raise MemoryError("candidate_input_conflict", 409)
            result = await self.memory.publish_assertion(AdoptedMemory(
                scope_id=row["scope_id"], subject=candidate["subject"],
                predicate=candidate["predicate"], value=candidate["value"],
                evidence=[ProposalEvidence(
                    memory_id=source_ref["memory_id"], quote=candidate["evidence_quote"],
                )],
                explicit_intent=data.explicit_intent,
            ))
            metadata = derivation | {
                "source_class": "caller_explicit_adoption", "candidate_ordinal": ordinal,
                "start": candidate["start"], "end": candidate["end"],
                "adopted_by": str(self.memory.principal), "adoption_reason": data.reason,
                "declared_explicit_intent": True, "human_review_verified": False,
            }
            assertion_id = UUID(str(result["memory_id"]))
            await self.conn.execute(
                """INSERT INTO memory.assertion_derivation
                   (tenant_id,assertion_id,revision,scope_id,job_id,metadata)
                   VALUES (%s,%s,1,%s,%s,%s)""",
                (self.tenant, assertion_id, row["scope_id"], job_id, Jsonb(metadata)),
            )
            await self.conn.execute(
                """UPDATE memory_ops.extraction_candidate
                   SET adopted_assertion_id=%s,adopted_by=%s
                   WHERE tenant_id=%s AND job_id=%s AND ordinal=%s""",
                (assertion_id, self.memory.principal, self.tenant, job_id, ordinal),
            )
            await self.memory.audit("adopt_candidate", assertion_id)
        await self.memory.save_result("adopt_candidate", key_hash, request_hash, result)
        return result
