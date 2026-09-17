import json
import math
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=256)]
Content = Annotated[str, Field(min_length=1, max_length=65536)]
Revision = Annotated[int, Field(ge=1, le=1000, strict=True)]
EntityType = Literal[
    "person", "organization", "project", "component", "incident", "task", "decision", "other"
]
RelationType = Literal["depends_on", "part_of", "affects", "works_for", "decides"]
SearchProfile = Literal["simple-v1", "ja-janome-0.5.0-v1"]
RetrievalMode = Literal["lexical", "vector", "hybrid"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReadinessStatus(Contract):
    status: Literal["ready", "not_ready"]


class EmbeddingModel(Contract):
    name: ShortText
    revision: ShortText
    dimensions: Literal[768] = 768
    distance_metric: Literal["cosine"] = "cosine"
    normalization: Literal["l2-f32-v1"] = "l2-f32-v1"


class VectorQuery(Contract):
    model: EmbeddingModel
    values: Annotated[
        list[Annotated[float, Field(strict=True, allow_inf_nan=False)]],
        Field(min_length=768, max_length=768),
    ]

    @model_validator(mode="after")
    def nonzero_vector(self) -> "VectorQuery":
        norm = math.hypot(*self.values)
        if norm == 0 or not math.isfinite(norm):
            raise ValueError("vector must have a finite nonzero norm")
        return self

    def vector_literal(self) -> str:
        norm = math.hypot(*self.values)
        return json.dumps([value / norm for value in self.values], separators=(",", ":"))


class PutEmbedding(VectorQuery):
    memory_id: UUID
    revision: Revision = 1
    input_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Observe(Contract):
    scope_id: UUID
    source_namespace: ShortText
    source_event_id: ShortText
    occurred_at: AwareDatetime
    content: Content
    consent_reference: ShortText


class Evidence(Contract):
    memory_id: UUID
    quote: Annotated[str, Field(min_length=1, max_length=4096)]


def validate_assertion_content(
    valid_from: datetime | None, valid_to: datetime | None, evidence: list[Evidence]
) -> None:
    if valid_from and valid_to and valid_from >= valid_to:
        raise ValueError("valid_from must precede valid_to")
    if len({item.memory_id for item in evidence}) != len(evidence):
        raise ValueError("evidence IDs must be unique")


class Remember(Contract):
    scope_id: UUID
    subject: ShortText
    predicate: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    value: Content
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=32)]
    explicit_intent: Literal[True]
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> "Remember":
        validate_assertion_content(self.valid_from, self.valid_to, self.evidence)
        return self


class CapturedMemory(Contract):
    subject: ShortText
    predicate: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    value: Content
    evidence_quote: Annotated[str, Field(min_length=1, max_length=4096)]
    explicit_intent: Literal[True]
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> "CapturedMemory":
        validate_assertion_content(self.valid_from, self.valid_to, [])
        return self

    def remember(self, scope_id: UUID, episode_id: UUID) -> Remember:
        return Remember(
            scope_id=scope_id,
            subject=self.subject,
            predicate=self.predicate,
            value=self.value,
            evidence=[Evidence(memory_id=episode_id, quote=self.evidence_quote)],
            explicit_intent=self.explicit_intent,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
        )


class Capture(Contract):
    episode: Observe
    memory: CapturedMemory


class ReviseAssertion(Contract):
    expected_revision: Revision
    value: Content
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=32)]
    explicit_intent: Literal[True]
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    reason: ShortText

    @model_validator(mode="after")
    def valid_interval(self) -> "ReviseAssertion":
        validate_assertion_content(self.valid_from, self.valid_to, self.evidence)
        return self


class Recall(Contract):
    query: Annotated[str, Field(max_length=4096)] = ""
    scope_ids: Annotated[list[UUID], Field(min_length=1, max_length=32)]
    purpose: ShortText
    as_of: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    mode: Literal["implicit", "explicit"] = "explicit"
    token_budget: Annotated[int, Field(ge=64, le=8000)] = 2000
    max_items: Annotated[int, Field(ge=1, le=100)] = 20
    tokenizer_id: Literal["utf8-bytes-v1"] = "utf8-bytes-v1"
    search_profile: SearchProfile = "simple-v1"
    retrieval_mode: RetrievalMode = "lexical"
    vector_query: VectorQuery | None = None

    @model_validator(mode="after")
    def implicit_budget(self) -> "Recall":
        if self.mode == "implicit" and self.token_budget > 2000:
            raise ValueError("implicit recall is limited to 2000 budget units")
        if (self.retrieval_mode == "lexical") != (self.vector_query is None):
            raise ValueError("vector_query is required only for vector or hybrid recall")
        if self.retrieval_mode == "vector" and self.query:
            raise ValueError("vector recall requires an empty text query")
        if self.retrieval_mode == "hybrid" and not self.query:
            raise ValueError("hybrid recall requires a text query")
        return self


class CreateEntity(Contract):
    scope_id: UUID
    entity_type: EntityType
    canonical_label: ShortText
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=32)]
    explicit_intent: Literal[True]

    @model_validator(mode="after")
    def unique_evidence(self) -> "CreateEntity":
        validate_assertion_content(None, None, self.evidence)
        return self


class CreateRelation(Contract):
    scope_id: UUID
    source_entity: UUID
    target_entity: UUID
    predicate: RelationType
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=32)]
    explicit_intent: Literal[True]
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> "CreateRelation":
        validate_assertion_content(self.valid_from, self.valid_to, self.evidence)
        return self


class ReviseRelation(Contract):
    expected_revision: Revision
    target_entity: UUID
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=32)]
    explicit_intent: Literal[True]
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    reason: ShortText

    @model_validator(mode="after")
    def valid_interval(self) -> "ReviseRelation":
        validate_assertion_content(self.valid_from, self.valid_to, self.evidence)
        return self


class ExpandGraph(Contract):
    scope_ids: Annotated[list[UUID], Field(min_length=1, max_length=32)]
    seeds: Annotated[list[UUID], Field(min_length=1, max_length=16)]
    relation_types: Annotated[list[RelationType], Field(min_length=1, max_length=5)]
    purpose: ShortText
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    max_hops: Annotated[int, Field(ge=1, le=2, strict=True)] = 2
    max_paths: Annotated[int, Field(ge=1, le=100, strict=True)] = 100
    as_of: AwareDatetime | None = None
    known_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def unique_filters(self) -> "ExpandGraph":
        for values in (self.scope_ids, self.seeds, self.relation_types):
            if len(set(values)) != len(values):
                raise ValueError("graph filters must be unique")
        return self


class Explain(Contract):
    memory_id: UUID
    revision: Revision = 1


class Forget(Contract):
    memory_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    mode: Literal["preview", "purge"] = "purge"
    reason: ShortText

    @model_validator(mode="after")
    def unique_ids(self) -> "Forget":
        if len(set(self.memory_ids)) != len(self.memory_ids):
            raise ValueError("memory IDs must be unique")
        return self


class Identity(BaseModel):
    tenant_id: UUID
    principal_id: UUID


class RelationEndpoints(BaseModel):
    source_entity: UUID
    target_entity: UUID


class RetrievalEvidence(BaseModel):
    method: Literal["exact_cosine", "rrf-60"]
    lexical_rank: int | None = None
    vector_rank: int | None = None
    vector_distance: float | None = None
    fusion_score: float | None = None


class MemoryItem(BaseModel):
    memory_id: UUID
    revision: int = 1
    type: Literal["episode", "assertion"]
    content: str
    recorded_at: datetime
    occurred_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    epistemic_status: Literal["reported"] = "reported"
    confidence: dict[str, str | None] = Field(
        default_factory=lambda: {"score": None, "method": "uncalibrated"}
    )
    source: list[UUID] = Field(default_factory=list)
    requires_refresh: bool = True
    relation: RelationEndpoints | None = None
    retrieval: RetrievalEvidence | None = None


class EmbeddingInput(BaseModel):
    memory_id: UUID
    revision: Revision
    type: Literal["episode", "assertion"]
    text: str
    input_digest: str
    input_format: Literal["memory-content-v1"] = "memory-content-v1"


class EmbeddingReceipt(BaseModel):
    memory_id: UUID
    revision: Revision
    model: EmbeddingModel
    input_digest: str


class ErrorBody(BaseModel):
    code: str
    request_id: str
    retryable: bool
    details: dict[str, str] = Field(default_factory=dict)


class ObserveResult(BaseModel):
    memory_id: UUID
    revision: Literal[1]
    synthesis_job_id: None = None


class CaptureResult(BaseModel):
    memory_id: UUID
    revision: Literal[1]
    synthesis_job_id: UUID


class RememberResult(BaseModel):
    memory_id: UUID
    revision: Literal[1]
    epistemic_status: Literal["reported"]


class RevisionResult(BaseModel):
    memory_id: UUID
    revision: Revision
    epistemic_status: Literal["reported"]


class ContextPack(BaseModel):
    format: Literal["memory-context-v1"]
    text: str
    tokenizer_id: Literal["utf8-bytes-v1"]
    token_count: None = None
    budget_unit: Literal["utf8_bytes"]
    byte_count: int
    exact_token_count: Literal[False]


class Coverage(BaseModel):
    retrieval_complete: bool
    synthesis_pending: Literal[False]
    jobs_pending: bool = False
    lexical_incomplete: bool = False
    vector_incomplete: bool = False
    graph_used: Literal[False]
    truncated: bool


class Consistency(BaseModel):
    access_epoch: int
    deletion_epoch: int


class RecallResult(BaseModel):
    items: list[MemoryItem]
    context_pack: ContextPack
    coverage: Coverage
    consistency: Consistency
    search_profile: SearchProfile
    retrieval_mode: RetrievalMode = "lexical"
    embedding_model: EmbeddingModel | None = None
    empty_reason: Literal["budget_exhausted", "not_found", "index_incomplete"] | None


class ExplainedSource(BaseModel):
    content: str
    occurred_at: datetime
    consent_reference: str


class EpisodeExplanation(BaseModel):
    memory_id: UUID
    revision: Literal[1]
    type: Literal["episode"]
    source: ExplainedSource


class ExplainedAssertion(BaseModel):
    subject: str
    predicate: str
    value: str
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    known_until: datetime | None = None
    correction_reason: str | None = None


class ExplainedEvidence(BaseModel):
    memory_id: UUID
    quote: str
    occurred_at: datetime


class AssertionExplanation(BaseModel):
    memory_id: UUID
    revision: Revision
    type: Literal["assertion"]
    assertion: ExplainedAssertion
    evidence: list[ExplainedEvidence]
    epistemic_status: Literal["reported"]
    confidence: dict[str, str | None]
    relation: RelationEndpoints | None = None


class DeletionResult(BaseModel):
    deletion_id: UUID
    state: Literal["active_store_purged"]
    object_count: int
    deletion_epoch: int
    scope_ids: list[UUID]
    backup_status: Literal["operator_managed"]
    backup_retention_deadline: None = None


class DeletionProgress(BaseModel):
    deletion_id: UUID
    mode: Literal["purge"]
    state: Literal["active_store_purged"]
    object_count: int
    deletion_epoch: int
    created_at: datetime
    backup_status: Literal["operator_managed"]
    backup_retention_deadline: None = None


class DeletionPreview(BaseModel):
    mode: Literal["preview"]
    object_count: int
    changed: Literal[False]


class MemoryReference(Contract):
    memory_id: UUID
    revision: Revision = 1


JobError = Literal["dependency_unavailable", "stale_context", "invalid_input", "attempt_limit"]


class EnqueueJob(Contract):
    kind: Literal["structured_remember"]
    memory: Remember


class JobReceipt(BaseModel):
    job_id: UUID
    kind: Literal["structured_remember"] = "structured_remember"
    recipe_version: Literal["structured-remember-v1"] = "structured-remember-v1"


class JobDetail(JobReceipt):
    retry_of: UUID | None
    state: Literal["pending", "running", "succeeded", "failed"]
    attempt: int
    max_attempts: Literal[5] = 5
    available_at: datetime
    lease_until: datetime | None
    created_at: datetime
    updated_at: datetime
    error_code: JobError | None
    input_refs: list[MemoryReference]
    result: MemoryReference | None


class EntityReceipt(BaseModel):
    memory_id: UUID
    revision: Literal[1] = 1


class EntitySummary(EntityReceipt):
    scope_id: UUID
    entity_type: EntityType
    canonical_label: str
    recorded_at: datetime


class EntityDetail(EntitySummary):
    evidence: list[ExplainedEvidence]


class GraphEdge(RelationEndpoints):
    assertion: MemoryReference
    predicate: RelationType
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    epistemic_status: Literal["reported"] = "reported"


class GraphPath(BaseModel):
    nodes: list[UUID]
    assertions: list[MemoryReference]


class GraphCoverage(BaseModel):
    complete_within_bounds: bool
    truncated: bool
    max_hops: int


class GraphResult(BaseModel):
    backend: Literal["sql"] = "sql"
    projection_watermark: None = None
    as_of: datetime
    known_at: datetime
    nodes: list[EntitySummary]
    edges: list[GraphEdge]
    paths: list[GraphPath]
    coverage: GraphCoverage
    consistency: Consistency
    empty_reason: Literal["not_found"] | None


class PendingEffect(Contract):
    operation_id: UUID
    description: ShortText
    status: Literal["planned", "dispatched", "unknown"]


StateText = Annotated[str, Field(min_length=1, max_length=4096)]


class CheckpointState(Contract):
    goal: StateText
    constraints: Annotated[list[StateText], Field(max_length=64)] = Field(default_factory=list)
    completed_actions: Annotated[list[StateText], Field(max_length=100)] = Field(
        default_factory=list
    )
    decisions: Annotated[list[StateText], Field(max_length=64)] = Field(default_factory=list)
    unresolved_questions: Annotated[list[StateText], Field(max_length=64)] = Field(
        default_factory=list
    )
    next_actions: Annotated[list[StateText], Field(max_length=64)] = Field(default_factory=list)
    pending_effects: Annotated[list[PendingEffect], Field(max_length=100)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_effects(self) -> "CheckpointState":
        if len({effect.operation_id for effect in self.pending_effects}) != len(
            self.pending_effects
        ):
            raise ValueError("operation IDs must be unique")
        return self


class CreateCheckpoint(Contract):
    scope_id: UUID
    run_id: UUID
    branch_id: UUID
    expected_head: UUID | None
    harness_id: ShortText
    harness_version: ShortText
    state_schema_version: Literal[1] = 1
    event_watermark: Annotated[int, Field(ge=0, le=9223372036854775807, strict=True)]
    state: CheckpointState
    memory_refs: Annotated[list[MemoryReference], Field(max_length=100)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_references(self) -> "CreateCheckpoint":
        refs = {(ref.memory_id, ref.revision) for ref in self.memory_refs}
        if len(refs) != len(self.memory_refs):
            raise ValueError("memory references must be unique")
        return self


class RestoreCheckpoint(Contract):
    checkpoint_id: UUID
    target_branch_id: UUID
    harness_id: ShortText
    harness_version: ShortText
    state_schema_version: Literal[1] = 1


class CheckpointReceipt(BaseModel):
    checkpoint_id: UUID
    run_id: UUID
    branch_id: UUID
    sequence: int
    parent_checkpoint: UUID | None
    checksum: str
    checksum_algorithm: Literal["hmac-sha256-v1"] = "hmac-sha256-v1"


EffectStatus = Literal["planned", "dispatched", "unknown", "confirmed", "failed"]
EffectRevision = Annotated[int, Field(ge=1, le=4, strict=True)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PlanToolEffect(Contract):
    scope_id: UUID
    run_id: UUID
    operation_id: UUID
    tool_name: ShortText
    action_hash: Digest
    memory_refs: Annotated[list[MemoryReference], Field(max_length=100)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_references(self) -> "PlanToolEffect":
        if len({(ref.memory_id, ref.revision) for ref in self.memory_refs}) != len(
            self.memory_refs
        ):
            raise ValueError("memory references must be unique")
        return self


class TransitionToolEffect(Contract):
    expected_revision: EffectRevision
    status: Literal["dispatched", "unknown", "confirmed", "failed"]
    reason: ShortText
    receipt_reference: ShortText | None = None
    receipt_source: Literal["provider_receipt", "operator_review"] | None = None

    @model_validator(mode="after")
    def terminal_receipt(self) -> "TransitionToolEffect":
        if self.status in ("confirmed", "failed"):
            if self.receipt_reference is None or self.receipt_source is None:
                raise ValueError("terminal outcomes require a receipt reference and source")
        elif self.receipt_reference is not None or self.receipt_source is not None:
            raise ValueError("receipts belong to terminal outcomes")
        return self


class ToolEffectReceipt(BaseModel):
    memory_id: UUID
    revision: EffectRevision
    status: EffectStatus


class ToolEffectSummary(ToolEffectReceipt):
    operation_id: UUID


class ToolEffectEvent(BaseModel):
    revision: EffectRevision
    status: EffectStatus
    recorded_at: datetime
    reason: str
    receipt_reference: str | None
    receipt_source: Literal["provider_receipt", "operator_review"] | None
    origin: Literal["api", "checkpoint_restore"]


class ToolEffectDetail(ToolEffectSummary):
    scope_id: UUID
    run_id: UUID
    tool_name: str
    action_fingerprint: Digest
    external_idempotency_key: Digest
    run_invalidated: bool
    memory_refs: list[MemoryReference]
    history: list[ToolEffectEvent]


class CheckpointEnvelope(CheckpointReceipt):
    scope_id: UUID
    harness_id: str
    harness_version: str
    state_schema_version: Literal[1]
    event_watermark: int
    state: CheckpointState
    memory_refs: list[MemoryReference]
    saved_access_epoch: int
    saved_deletion_epoch: int
    current_access_epoch: int
    current_deletion_epoch: int
    requires_reconciliation: list[UUID]
    untracked_effects: list[UUID] = Field(default_factory=list)
    tool_effects: list[ToolEffectSummary] = Field(default_factory=list)
    resume_allowed: bool
    automatic_reexecution: Literal[False] = False
