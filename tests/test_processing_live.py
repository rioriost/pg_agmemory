"""Opt-in local-model lifecycle smoke, never synthetic provider substitution.

Requires PGAG_M2_LIVE_PROCESSING=1 and PGAG_LIVE_PROVIDER_CONFIG. The operator must
already have the pinned local models installed and supply a disposable test DB.
One successful case makes exactly three model calls: extract, embed, summarize.
No download, automatic retry, semantic-quality claim, or human assessment occurs.
"""

import asyncio
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from test_processing import configure, enqueue, forget, get, run, working

from pg_agmemory.models import WorkingSnapshot
from pg_agmemory.native_client import AdapterFailure
from pg_agmemory.providers import HTTPProvider, ProviderFailure
from pg_agmemory.recall_hook import HookInput, HookSettings, run_context
from pg_agmemory.worker_profile import WorkerProfile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("PGAG_M2_LIVE_PROCESSING") != "1",
        reason="Set PGAG_M2_LIVE_PROCESSING=1 only for authorized local-model processing",
    ),
]

TEXT_MODEL = {
    "name": "qwen2.5:7b",
    "revision": "ollama-sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
}
EMBEDDING_MODEL = {
    "name": "qwen3-embedding:0.6b",
    "revision": "ollama-sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1",
}
CALL_LIMIT = 3
LITERAL_SOURCE = "Alice / preferred_editor: Vim"


@pytest.fixture
def m2_local_profile():
    path = os.environ.get("PGAG_LIVE_PROVIDER_CONFIG")
    if not path:
        pytest.fail("PGAG_LIVE_PROVIDER_CONFIG is required for the explicit M2 live opt-in")
    try:
        with Path(path).open("rb") as stream:
            profile = WorkerProfile.parse(stream.read(32769))
    except OSError:
        pytest.fail("The explicit live provider configuration could not be read", pytrace=False)
    except ProviderFailure as exc:
        pytest.fail(f"Live worker profile rejected: {exc.error.code}", pytrace=False)
    settings = profile.settings
    assert settings.backend == "local_http", "This smoke must never use remote/paid adapters"
    assert type(profile.provider) is HTTPProvider
    assert settings.text_model is not None and settings.text_model.model_dump() == TEXT_MODEL
    assert settings.embedding_model is not None
    assert settings.embedding_model.model_dump() == EMBEDDING_MODEL
    assert settings.embedding_target in (None, EMBEDDING_MODEL["name"])
    assert settings.max_output_tokens is not None and 1 <= settings.max_output_tokens <= 4096
    assert os.environ.get("PGAG_TEST_DATABASE_URL"), "A disposable test database is required"
    return profile


def checked_run(env, profile, job_id, kind, record_property):
    outcome = run(env, profile)
    response = get(env, job_id)
    assert response.status_code == 200, response.text
    detail = response.json()
    diagnosis = {
        "kind": kind,
        "outcome": outcome["outcome"],
        "state": detail["state"],
        "attempt": detail["attempt"],
        "error_code": detail["error_code"],
        "call": detail.get("call"),
    }
    record_property(f"live_{kind}_outcome", json.dumps(diagnosis, sort_keys=True))
    assert outcome["outcome"] == "succeeded", json.dumps(diagnosis, sort_keys=True)
    assert outcome["job_id"] == job_id
    assert detail["kind"] == kind and detail["state"] == "succeeded"
    assert detail["attempt"] == 1 and detail["retry_of"] is None
    assert detail["lease_until"] is None and detail["error_code"] is None
    assert detail["call"]["outcome"] == "succeeded"
    assert detail["call"]["billing_unknown"] is False
    assert detail["processing_result"] == outcome["result"]
    return outcome["result"]


def test_live_local_durable_processing_compaction_and_explicit_snapshot_restore(
    m2_local_profile, request, monkeypatch, record_property
):
    profile = m2_local_profile
    # Resolve DB/process fixtures only after both opt-ins and the exact local profile pass.
    env = request.getfixturevalue("env")
    api_process = request.getfixturevalue("api_process")
    configured = configure(
        env,
        profile,
        max_calls=CALL_LIMIT,
        max_pending_jobs=3,
        max_output_tokens=profile.settings.max_output_tokens,
    )
    record_property("live_profile_digest", profile.digest)
    record_property("live_text_model", json.dumps(TEXT_MODEL, sort_keys=True))
    record_property("live_embedding_model", json.dumps(EMBEDDING_MODEL, sort_keys=True))
    record_property("live_model_identity", "operator revision pins; no weight attestation")
    record_property("live_quality_claim", "none; lifecycle and contract observations only")
    calls = []
    original_call = profile.call

    async def observe_real_call(kind, text):
        assert len(calls) < CALL_LIMIT, "The live smoke must not make additional model calls"
        assert kind not in [call["kind"] for call in calls], "No retry is permitted"
        with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
            reservations = conn.execute(
                """SELECT j.id,j.state,j.attempt,j.lease_token,j.lease_until>clock_timestamp()
                          AS lease_live,c.lease_token AS reserved_token,c.outcome,
                          c.billing_unknown,c.profile_digest,c.input_bytes,c.max_output_tokens,
                          c.policy_epoch
                   FROM memory_ops.job j JOIN memory_ops.model_call c
                     ON c.tenant_id=j.tenant_id AND c.job_id=j.id
                   WHERE j.tenant_id=%s AND j.kind=%s AND j.state='running'""",
                (env.tenants[0], kind),
            ).fetchall()
        assert len(reservations) == 1
        reservation = reservations[0]
        assert reservation["attempt"] == 1 and reservation["lease_live"]
        assert reservation["lease_token"] == reservation["reserved_token"]
        assert reservation["outcome"] == "unknown" and reservation["billing_unknown"]
        assert reservation["profile_digest"] == profile.digest
        assert reservation["policy_epoch"] == configured.policy_access_epoch
        assert reservation["input_bytes"] == len(text.encode("utf-8"))
        assert reservation["max_output_tokens"] == (
            None if kind == "embed" else profile.settings.max_output_tokens
        )
        calls.append(
            {
                "kind": kind,
                "job_id": str(reservation["id"]),
                "text": text,
                "input_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
        try:
            return await original_call(kind, text)
        except ProviderFailure as exc:
            record_property(
                f"live_{kind}_provider_error", json.dumps(exc.error.model_dump(), sort_keys=True)
            )
            raise

    monkeypatch.setattr(profile, "call", observe_real_call)
    try:
        observed = env.observe(LITERAL_SOURCE, auto_extract=True)
        assert observed.status_code == 201, observed.text
        source = observed.json()["memory_id"]
        extract_job = observed.json()["synthesis_job_id"]
        assert extract_job
        assert run(env) == {"outcome": "idle"}
        extracted = checked_run(env, profile, extract_job, "extract", record_property)
        assert calls[0]["text"] == LITERAL_SOURCE
        assert calls[0]["job_id"] == extract_job
        counts = extracted["counts"]
        record_property("live_extraction_counts", json.dumps(counts, sort_keys=True))
        assert set(counts) == {"published", "duplicate", "quarantined"}
        assert all(type(value) is int and value >= 0 for value in counts.values())
        assert counts["duplicate"] == 0
        assert 1 <= sum(counts.values()) <= 16, (
            "The live model abstained; no accepted/quarantined extraction was exercised. "
            "Do not substitute synthetic candidates or retry."
        )
        assert extracted["status"] == "untrusted"
        derivation = extracted["derivation"]
        assert derivation["input_digest"] == calls[0]["input_digest"]
        assert derivation["model"] == TEXT_MODEL
        assert derivation["source"] == {"memory_id": source, "revision": 1}
        assert derivation["job_id"] == extract_job
        assert derivation["profile_digest"] == profile.digest
        assert derivation["recipe_version"] == "source-extraction-v1"
        assert derivation["status"] == "untrusted"
        reviewed = env.client.get(
            f"/v1/jobs/{extract_job}/candidates", headers=env.headers()
        )
        assert reviewed.status_code == 200, reviewed.text
        proposals = reviewed.json()["candidates"]
        assert Counter(item["disposition"] for item in proposals) == {
            key: value for key, value in counts.items() if value
        }
        assert len(extracted["assertions"]) == counts["published"]
        for item in proposals:
            candidate = item["candidate"]
            assert LITERAL_SOURCE[candidate["start"] : candidate["end"]] == (
                candidate["evidence_quote"]
            )
            assert candidate["subject"] in candidate["evidence_quote"]
            assert candidate["value"] in candidate["evidence_quote"]
            if item["disposition"] == "published":
                assert item["reason"] == "allowed_literal_preference"
                assert candidate["predicate"] == "preferred_editor"
                assert (
                    candidate["subject"] + " / preferred_editor: " + candidate["value"]
                    == candidate["evidence_quote"] == LITERAL_SOURCE
                )
            else:
                assert item["disposition"] == "quarantined"
                assert item["assertion_id"] is None
        for assertion in extracted["assertions"]:
            response = env.client.post("/v1/explain", json=assertion, headers=env.headers())
            assert response.status_code == 200, response.text
            explanation = response.json()
            assert explanation["epistemic_status"] == "inferred"
            assert explanation["derivation"]["input_digest"] == calls[0]["input_digest"]
            assert explanation["derivation"]["model"] == TEXT_MODEL
            assert explanation["derivation"]["status"] == "untrusted"
            assert explanation["evidence"][0]["quote"] == LITERAL_SOURCE

        canonical_response = env.client.post(
            "/v1/embedding-inputs",
            json={"memory_id": source, "revision": 1},
            headers=env.headers(),
        )
        assert canonical_response.status_code == 200, canonical_response.text
        canonical = canonical_response.json()
        assert canonical["text"] == LITERAL_SOURCE
        assert canonical["input_digest"] == hashlib.sha256(LITERAL_SOURCE.encode()).hexdigest()
        embedding_request = enqueue(env, source, "embed")
        assert embedding_request.status_code == 202, embedding_request.text
        embed_job = embedding_request.json()["job_id"]
        embedded = checked_run(env, profile, embed_job, "embed", record_property)
        assert calls[1]["text"] == canonical["text"]
        assert calls[1]["input_digest"] == canonical["input_digest"]
        assert embedded["model"] == EMBEDDING_MODEL
        assert embedded["input_digest"] == canonical["input_digest"]
        assert embedded["memory_id"] == source and embedded["revision"] == 1
        with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
            rows = conn.execute(
                """SELECT input_digest,model_name,model_revision,vector_dims(embedding)
                          AS dimensions,vector_norm(embedding) AS norm
                   FROM memory.episode_embedding WHERE tenant_id=%s AND episode_id=%s""",
                (env.tenants[0], source),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["input_digest"] == canonical["input_digest"]
        assert rows[0]["model_name"] == EMBEDDING_MODEL["name"]
        assert rows[0]["model_revision"] == EMBEDDING_MODEL["revision"]
        assert rows[0]["dimensions"] == 768 and rows[0]["norm"] == pytest.approx(1)

        branch, checkpoint_request, head, work_source = working(env)
        original_response = env.client.get(f"/v1/checkpoints/{head}", headers=env.headers())
        assert original_response.status_code == 200, original_response.text
        original = original_response.json()
        compact_body = {**branch, "expected_head": head, "through_sequence": 1}
        compact_headers = env.headers()
        queued = env.client.post(
            "/v1/working/compact", json=compact_body, headers=compact_headers
        )
        assert queued.status_code == 202, queued.text
        compact_job = queued.json()["job_id"]
        tail_response = env.observe("Synthetic tail: reviewer approval is still pending.")
        assert tail_response.status_code == 201, tail_response.text
        tail_source = tail_response.json()["memory_id"]
        tail = env.client.post(
            "/v1/working/events",
            json={**branch, "source": {"memory_id": tail_source}},
            headers=env.headers(),
        )
        assert tail.status_code == 201 and tail.json()["sequence"] == 2, tail.text
        compacted = checked_run(env, profile, compact_job, "compact", record_property)
        assert calls[2]["job_id"] == compact_job
        assert json.loads(calls[2]["text"]) == [
            {
                "sequence": 1,
                "source_id": work_source,
                "revision": 1,
                "content": "Synthetic event mentions file src/main.py at version 1.2",
            }
        ]
        snapshot_id = compacted["checkpoint_id"]
        response = env.client.get(
            f"/v1/working/snapshots/{snapshot_id}", headers=env.headers()
        )
        assert response.status_code == 200, response.text
        snapshot = WorkingSnapshot.model_validate(response.json())
        assert snapshot.checkpoint.state.model_dump(mode="json") == original["state"]
        assert snapshot.checkpoint.event_watermark == checkpoint_request["event_watermark"]
        assert snapshot.status == compacted["status"] == "untrusted"
        assert snapshot.summary.strip()
        assert snapshot.model == TEXT_MODEL
        assert snapshot.input_digest == calls[2]["input_digest"]
        assert snapshot.recipe_version == "working-compaction-v1"
        assert snapshot.coverage_start == snapshot.coverage_end == 1
        assert snapshot.input_refs[0].memory_id == UUID(work_source)
        assert len(snapshot.input_refs) == 1
        assert [event.source.memory_id for event in snapshot.tail.events] == [UUID(tail_source)]
        assert not snapshot.checkpoint.automatic_reexecution
        assert not snapshot.checkpoint.resume_allowed

        assert enqueue(env, source).json()["job_id"] == extract_job
        assert enqueue(env, source, "embed").json()["job_id"] == embed_job
        replay = env.client.post(
            "/v1/working/compact", json=compact_body, headers=compact_headers
        )
        assert replay.status_code == 202 and replay.json() == queued.json()
        assert run(env, profile) == {"outcome": "idle"}
        assert [call["kind"] for call in calls] == ["extract", "embed", "compact"]

        with api_process("m2-live-processing-api.log") as (http, _):
            restore = http.post(
                "/v1/checkpoints/restore",
                json={
                    "checkpoint_id": snapshot_id,
                    "target_branch_id": str(uuid4()),
                    "harness_id": checkpoint_request["harness_id"],
                    "harness_version": checkpoint_request["harness_version"],
                },
                headers=env.headers(),
            )
            assert restore.status_code == 201, restore.text
            restored = restore.json()
            assert restored["parent_checkpoint"] == snapshot_id
            assert restored["state"] == original["state"]
            assert not restored["automatic_reexecution"] and not restored["resume_allowed"]
            size = len(snapshot.model_dump_json().encode("utf-8"))
            assert 1 < size <= 65536
            settings = HookSettings(
                api_url=str(http.base_url).rstrip("/"),
                api_token=env.token(),
                scope_ids=[env.scopes[0]],
                token_budget=2000,
                max_items=1,
                timeout_seconds=20,
                working_snapshot_budget_bytes=size,
            )
            data = HookInput(
                event="after_compaction",
                query="",
                working_snapshot_id=UUID(snapshot_id),
            )
            context = asyncio.run(run_context(settings, data))
            assert context.status == "ok" and context.error is None
            assert context.working_snapshot.checkpoint.checkpoint_id == UUID(snapshot_id)
            assert context.working_snapshot.checkpoint.state.model_dump(mode="json") == (
                original["state"]
            )
            assert context.working_snapshot.summary == snapshot.summary
            assert context.working_snapshot.status == "untrusted"
            assert not context.working_snapshot.checkpoint.automatic_reexecution
            assert not context.working_snapshot.checkpoint.resume_allowed
            assert context.result.items[0].memory_id == UUID(tail_source)
            assert context.result.context_pack.byte_count <= settings.token_budget
            with pytest.raises(AdapterFailure, match="budget_exhausted"):
                asyncio.run(
                    run_context(
                        settings.model_copy(update={"working_snapshot_budget_bytes": size - 1}),
                        data,
                    )
                )
            record_property("live_snapshot_budget_bytes", size)
            record_property("live_hook_tail_items", len(context.result.items))

        with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
            ledger = conn.execute(
                """SELECT j.kind,j.attempt,c.outcome,c.billing_unknown,c.profile_digest
                   FROM memory_ops.model_call c JOIN memory_ops.job j
                     ON j.tenant_id=c.tenant_id AND j.id=c.job_id
                   WHERE c.tenant_id=%s""",
                (env.tenants[0],),
            ).fetchall()
        assert len(ledger) == CALL_LIMIT
        assert {row["kind"] for row in ledger} == {"extract", "embed", "compact"}
        assert all(
            row["attempt"] == 1
            and row["outcome"] == "succeeded"
            and not row["billing_unknown"]
            and row["profile_digest"] == profile.digest
            for row in ledger
        )
        assert forget(env, source).status_code == 202
        assert forget(env, work_source).status_code == 202
        assert forget(env, tail_source).status_code == 202
        assert env.client.get(
            f"/v1/working/snapshots/{snapshot_id}", headers=env.headers()
        ).status_code == 404
        assert env.client.get(
            f"/v1/jobs/{extract_job}/candidates", headers=env.headers()
        ).status_code == 404
        with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
            retained = conn.execute(
                """SELECT outcome,billing_unknown,profile_digest FROM memory_ops.model_call
                   WHERE tenant_id=%s""",
                (env.tenants[0],),
            ).fetchall()
            remaining = conn.execute(
                "SELECT count(*) AS total FROM memory.episode_embedding WHERE tenant_id=%s",
                (env.tenants[0],),
            ).fetchone()
        assert remaining["total"] == 0
        assert len(retained) == CALL_LIMIT
        assert all(
            row["outcome"] == "succeeded"
            and not row["billing_unknown"]
            and row["profile_digest"] == profile.digest
            for row in retained
        )
    finally:
        record_property("live_actual_model_calls", len(calls))
        record_property(
            "live_actual_model_call_kinds", json.dumps([call["kind"] for call in calls])
        )
