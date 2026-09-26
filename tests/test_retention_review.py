from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pg_agmemory.models import MemoryItem, MemoryReference, RecallResult
from pg_agmemory.retention_review import (
    RetentionReview,
    exclude_pending_result,
    review_retention,
)
from pg_agmemory.service import build_context


def test_every_forget_proposal_stays_pending_without_authorizing_a_purge():
    first, second, third = [MemoryReference(memory_id=uuid4()) for _ in range(3)]
    result = review_retention([first, second, third], [third.memory_id, first.memory_id])
    assert result.retained_refs == (second,)
    assert result.pending_review_refs == (first, third)
    assert result.excluded_memory_ids == (first.memory_id, third.memory_id)
    assert result.purge_authorized is False
    assert result.model_dump(mode="json")["purge_authorized"] is False


@pytest.mark.parametrize("forget_all", [False, True])
def test_keep_all_and_propose_all_are_non_destructive(forget_all):
    refs = [MemoryReference(memory_id=uuid4()) for _ in range(100)]
    result = review_retention(refs, [ref.memory_id for ref in refs] if forget_all else [])
    assert len(result.retained_refs) == (0 if forget_all else 100)
    assert len(result.pending_review_refs) == (100 if forget_all else 0)
    assert result.purge_authorized is False


def test_model_shaped_approval_cannot_enable_deletion():
    with pytest.raises(ValidationError):
        RetentionReview(
            retained_refs=(), pending_review_refs=(), purge_authorized=True,
        )
    with pytest.raises(ValidationError):
        RetentionReview.model_validate_json(
            '{"retained_refs":[],"pending_review_refs":[],"approved":true}'
        )
    result = review_retention([MemoryReference(memory_id=uuid4())], [])
    with pytest.raises(ValidationError):
        result.purge_authorized = True


def test_input_mutation_cannot_change_the_review_snapshot():
    ref = MemoryReference(memory_id=uuid4())
    original = ref.memory_id
    refs = [ref]
    proposed = [original]
    result = review_retention(refs, proposed)
    ref.memory_id = uuid4()
    refs.clear()
    proposed.clear()
    assert result.excluded_memory_ids == (original,)
    assert result.pending_review_refs[0].revision == 1


@pytest.mark.parametrize("count", [0, 101])
def test_review_requires_bounded_observed_references(count):
    with pytest.raises(ValueError):
        review_retention([MemoryReference(memory_id=uuid4()) for _ in range(count)], [])


def test_proposals_cannot_name_unknown_duplicate_or_untyped_objects():
    ref = MemoryReference(memory_id=uuid4())
    for proposed in ([uuid4()], [ref.memory_id, ref.memory_id], [str(ref.memory_id)]):
        with pytest.raises(ValueError):
            review_retention([ref], proposed)
    with pytest.raises(ValueError):
        review_retention([{"memory_id": str(ref.memory_id)}], [])


def test_object_forgetting_does_not_pretend_to_delete_only_one_revision():
    identity = uuid4()
    with pytest.raises(ValueError):
        review_retention([
            MemoryReference(memory_id=identity, revision=1),
            MemoryReference(memory_id=identity, revision=2),
        ], [identity])


def test_constructed_invalid_references_are_revalidated():
    invalid = MemoryReference.model_construct(memory_id=uuid4(), revision=0)
    with pytest.raises(ValidationError):
        review_retention([invalid], [invalid.memory_id])


def native_result():
    items = [
        MemoryItem(
            memory_id=uuid4(), type="episode", content=text,
            recorded_at=datetime.now(UTC), occurred_at=datetime.now(UTC),
        )
        for text in ("Kept source", "Pending source")
    ]
    pack, _, _ = build_context(items, 2000)
    return RecallResult.model_validate({
        "items": items, "context_pack": pack,
        "coverage": {
            "retrieval_complete": True, "synthesis_pending": False,
            "graph_used": False, "truncated": False,
        },
        "consistency": {"access_epoch": 4, "deletion_epoch": 7},
        "search_profile": "simple-v1", "empty_reason": None,
    })


def test_filtered_result_excludes_pending_text_without_mutating_native_evidence():
    original = native_result()
    pending = original.items[1].memory_id
    projected = exclude_pending_result(original, [pending])
    assert len(projected.items) == 1
    assert "Pending source" not in projected.context_pack.text
    assert "Pending source" in original.context_pack.text
    assert projected.consistency == original.consistency
    assert projected.coverage.retrieval_complete is False
    assert projected.coverage.truncated is True
    assert original.coverage.retrieval_complete is True
    assert projected.context_pack.byte_count <= original.context_pack.byte_count


def test_excluding_all_items_is_empty_local_context_not_proof_of_server_purge():
    original = native_result()
    projected = exclude_pending_result(original, [item.memory_id for item in original.items])
    assert projected.items == []
    assert projected.context_pack.text == ""
    assert projected.empty_reason == "not_found"
    assert projected.coverage.retrieval_complete is False
    assert len(original.items) == 2
    assert projected.consistency.deletion_epoch == original.consistency.deletion_epoch


def test_pending_filter_rejects_duplicate_and_untyped_ids():
    original = native_result()
    identity = original.items[0].memory_id
    with pytest.raises(ValueError):
        exclude_pending_result(original, [identity, identity])
    with pytest.raises(ValueError):
        exclude_pending_result(original, [str(identity)])
    with pytest.raises(ValueError):
        exclude_pending_result(original, [{}])
