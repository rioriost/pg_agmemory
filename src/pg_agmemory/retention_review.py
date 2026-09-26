"""Non-destructive, caller-owned review of model-proposed object forgetting."""

from collections.abc import Sequence
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from pg_agmemory.models import ContextPack, MemoryReference, RecallResult
from pg_agmemory.service import build_context


class RetentionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    retained_refs: tuple[MemoryReference, ...]
    pending_review_refs: tuple[MemoryReference, ...]
    purge_authorized: Literal[False] = False

    @model_validator(mode="after")
    def distinct_objects(self) -> Self:
        refs = self.retained_refs + self.pending_review_refs
        if not 1 <= len(refs) <= 100 or len({ref.memory_id for ref in refs}) != len(refs):
            raise ValueError("Review requires one to 100 distinct observed objects")
        return self

    @property
    def excluded_memory_ids(self) -> tuple[UUID, ...]:
        """Read-side exclusions for this caller workflow, not server-side deletion."""
        return tuple(reference.memory_id for reference in self.pending_review_refs)


def review_retention(
    observed_refs: Sequence[MemoryReference], proposed_forget_ids: Sequence[UUID],
) -> RetentionReview:
    if not 1 <= len(observed_refs) <= 100:
        raise ValueError("Retention review requires one to 100 observed object references")
    if not all(isinstance(reference, MemoryReference) for reference in observed_refs):
        raise ValueError("Retention review requires typed observed references")
    references = tuple(
        MemoryReference.model_validate(reference.model_dump()) for reference in observed_refs
    )
    known = {reference.memory_id for reference in references}
    if len(known) != len(references):
        raise ValueError("Retention review cannot mix duplicate objects or their revisions")
    proposed = tuple(proposed_forget_ids)
    if (
        not all(isinstance(memory_id, UUID) for memory_id in proposed)
        or len(set(proposed)) != len(proposed)
        or not set(proposed) <= known
    ):
        raise ValueError("Forget proposals must be distinct observed object IDs")
    pending = set(proposed)
    return RetentionReview(
        retained_refs=tuple(ref for ref in references if ref.memory_id not in pending),
        pending_review_refs=tuple(ref for ref in references if ref.memory_id in pending),
    )


def exclude_pending_result(
    result: RecallResult, excluded_ids: Sequence[UUID],
) -> RecallResult:
    """Project a Native result for this workflow; do not claim to delete server data."""
    excluded = tuple(excluded_ids)
    if (
        len(excluded) > 100 or not all(isinstance(memory_id, UUID) for memory_id in excluded)
        or len(set(excluded)) != len(excluded)
    ):
        raise ValueError("Pending exclusions require at most 100 distinct object IDs")
    admitted = [
        item.model_copy(deep=True) for item in result.items if item.memory_id not in excluded
    ]
    removed = len(admitted) != len(result.items)
    pack, selected, omitted = build_context(admitted, result.context_pack.byte_count)
    if omitted:
        raise ValueError("Pending exclusion projection cannot silently truncate admitted evidence")
    coverage = result.coverage.model_copy(deep=True)
    if removed:
        coverage.retrieval_complete = False
        coverage.truncated = True
    return result.model_copy(deep=True, update={
        "items": selected,
        "context_pack": ContextPack.model_validate(pack),
        "coverage": coverage,
        "empty_reason": (None if selected else "not_found") if removed else result.empty_reason,
    })
