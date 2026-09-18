import asyncio
import json
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from pg_agmemory.evaluation import (
    BASELINES,
    EvaluationDataset,
    EvaluationQuestion,
    EvaluationSource,
    retrieval_report,
)
from pg_agmemory.evaluation_runner import (
    EvaluationAnswer,
    LocalEvaluation,
    read_bounded,
    rendered_sources,
    select_context,
)
from pg_agmemory.models import Recall
from pg_agmemory.providers import (
    GeneratedEmbedding,
    HTTPProvider,
    ProviderFailure,
    ProviderSettings,
)
from pg_agmemory.sdk import AsyncMemoryClient


def configuration():
    return ProviderSettings(
        backend="local_http",
        endpoint="http://127.0.0.1:11434/v1",
        embedding_model={"name": "synthetic-embedding", "revision": "test-v1"},
        text_model={"name": "synthetic-text", "revision": "test-v1"},
        max_output_tokens=128,
    )


def corpus():
    return EvaluationDataset(
        dataset_id="runner-contract-only",
        origin="synthetic",
        license="MIT",
        sources=[
            EvaluationSource(
                source_id="later",
                group_id="one",
                occurred_at="2026-09-02T00:00:00Z",
                text="Synthetic later evidence",
            ),
            EvaluationSource(
                source_id="earlier",
                group_id="one",
                occurred_at="2026-09-01T00:00:00Z",
                text="Synthetic earlier evidence",
            ),
        ],
        questions=[
            EvaluationQuestion(
                question_id="question-one",
                group_id="one",
                category="contract",
                language="en",
                split="test",
                query="Synthetic evidence",
                relevant={"later": 3},
                answer="SCORER-ONLY GOLD MUST NEVER ENTER A MODEL REQUEST",
            )
        ],
    )


def journal(evaluator):
    return [json.loads(line) for line in evaluator.journal.read_text().splitlines()]


@pytest.mark.parametrize(
    "changes",
    [
        {"max_calls": 0},
        {"max_calls": 10001},
        {"max_calls": True},
        {"budget_bytes": 255},
        {"budget_bytes": 8001},
        {"budget_bytes": 512.5},
    ],
)
def test_runner_rejects_invalid_operator_budgets(tmp_path, changes):
    with pytest.raises(ValueError):
        LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl", **changes)
    assert not (tmp_path / "calls.jsonl").exists()


def test_runner_rejects_external_profiles_and_existing_journals(tmp_path):
    settings = configuration().model_copy(
        update={
            "backend": "openai_compatible",
            "endpoint": "https://provider.invalid/v1",
        }
    )
    with pytest.raises(ValueError):
        LocalEvaluation(settings, journal=tmp_path / "calls.jsonl")
    path = tmp_path / "calls.jsonl"
    path.write_text("previous execution\n")
    with pytest.raises(FileExistsError):
        LocalEvaluation(configuration(), journal=path)
    assert path.read_text() == "previous execution\n"


def test_context_selection_counts_complete_utf8_source_envelopes():
    sources = corpus().sources
    sources[0] = sources[0].model_copy(update={"text": "\u65e5\u672c\u8a9e"})
    size = len(rendered_sources(sources).encode("utf-8"))
    assert select_context(sources, budget=size, full=True) == sources
    assert select_context(sources, budget=size - 1, full=True) is None
    newest_size = len(rendered_sources([sources[-1]]).encode("utf-8"))
    assert select_context(sources, budget=newest_size, full=False) == [sources[-1]]
    assert select_context(sources, budget=newest_size - 1, full=False) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"answer": "invented", "abstained": True, "citations": []},
        {"answer": "", "abstained": True, "citations": ["source"]},
        {"answer": "value", "abstained": False, "citations": []},
        {"answer": "", "abstained": False, "citations": ["source"]},
        {"answer": "value", "abstained": False, "citations": ["source", "source"]},
        {"answer": "", "abstained": "true", "citations": []},
    ],
)
def test_answer_contract_cannot_disguise_claims_as_abstention(payload):
    with pytest.raises(ValidationError):
        EvaluationAnswer.model_validate(payload)


def test_answer_prompt_excludes_gold_and_binds_exact_citations(tmp_path, monkeypatch):
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl")
    data = corpus()

    async def exchange(self, path, payload):
        assert path == "chat/completions"
        user = json.loads(payload["messages"][1]["content"])
        assert user["question"] == data.questions[0].query
        assert data.questions[0].answer not in json.dumps(payload)
        assert set(user) == {"question", "evidence"}
        assert set(user["evidence"][0]) == {"source_id", "text", "source_occurred_at"}
        assert payload["seed"] == 17 and payload["temperature"] == 0
        assert journal(evaluator)[-1]["event"] == "call_reserved"
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "answer": "Synthetic",
                                "abstained": False,
                                "citations": ["later"],
                            }
                        ),
                    },
                }
            ]
        }

    monkeypatch.setattr(HTTPProvider, "exchange", exchange)
    answer = asyncio.run(evaluator.answer(data.questions[0], data.sources, seed=17))
    assert answer.citations == ["later"]
    assert [row["event"] for row in journal(evaluator)] == [
        "call_reserved",
        "answer_response",
        "call_completed",
    ]


@pytest.mark.parametrize(
    "wire",
    [
        {"choices": [None]},
        {"choices": [{"finish_reason": "stop", "message": None}]},
        {"choices": [{"finish_reason": "length", "message": {"role": "assistant"}}]},
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "function_call": {"name": "execute"},
                        "content": '{"answer":"","abstained":true,"citations":[]}',
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"answer":"value","abstained":false,"citations":["unauthorized"]}'
                        ),
                    },
                }
            ]
        },
    ],
)
def test_invalid_answer_never_completes_reserved_call(tmp_path, monkeypatch, wire):
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl")

    async def exchange(self, path, payload):
        return wire

    monkeypatch.setattr(HTTPProvider, "exchange", exchange)
    data = corpus()
    with pytest.raises(ValueError):
        asyncio.run(evaluator.answer(data.questions[0], data.sources, seed=17))
    assert evaluator.calls == 1
    assert journal(evaluator)[-1]["billing_unknown"] is True


def test_call_budget_is_reserved_before_network_and_never_retried(tmp_path, monkeypatch):
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl", max_calls=1)

    async def embed(self, data):
        assert journal(evaluator)[-1]["billing_unknown"]
        raise ProviderFailure("provider_unavailable", unknown=True)

    monkeypatch.setattr(HTTPProvider, "embed", embed)
    with pytest.raises(ProviderFailure):
        asyncio.run(evaluator.embed("Synthetic source"))
    with pytest.raises(ValueError, match="budget exhausted"):
        asyncio.run(evaluator.embed("Different input cannot reset the call budget"))
    assert evaluator.calls == 1 and len(journal(evaluator)) == 1


def test_profile_digest_pins_prompt_schema_budget_and_model(tmp_path, monkeypatch):
    first = LocalEvaluation(configuration(), journal=tmp_path / "one")
    second = LocalEvaluation(configuration(), journal=tmp_path / "two", budget_bytes=4000)
    assert first.profile_digest() != second.profile_digest()
    original = first.profile_digest()
    monkeypatch.setattr("pg_agmemory.evaluation_runner.ANSWER_SYSTEM_PROMPT", "Changed recipe")
    assert first.profile_digest() != original


def provision_scope(env):
    scope = uuid4()
    with psycopg.connect(env.admin_url) as admin:
        admin.execute("INSERT INTO memory.scope VALUES (%s,%s)", (env.tenants[0], scope))
        admin.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,%s)""",
            (env.tenants[0], scope, env.principals[0], ["read", "write", "delete"]),
        )
    return {"one": scope}


@pytest.mark.integration
def test_native_runner_executes_all_arms_then_purges_owned_sources(
    env, api_process, tmp_path, monkeypatch
):
    scopes = provision_scope(env)
    data = corpus()
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl")

    async def embed(self, source):
        return GeneratedEmbedding(
            model=self.settings.embedding_model,
            values=[1.0] + [0.0] * 767,
            input_digest=source.digest(),
        )

    monkeypatch.setattr(HTTPProvider, "embed", embed)

    async def execute(url):
        async with AsyncMemoryClient(url, env.token()) as client:
            run, answers = await evaluator.run(
                client,
                data,
                scopes,
                implementation_sha="a" * 40,
            )
            assert answers == [] and evaluator.calls == 3
            assert len(run.observations) == len(BASELINES)
            assert run.observations[1].ranked_ids == ["earlier", "later"]
            report = retrieval_report(data, run, bootstrap_samples=100)
            assert report.baselines["vector"].recall_at_20.mean == 1
            assert report.m2_qualified is False
            assert not (
                await client.recall(
                    Recall(
                        scope_ids=list(scopes.values()),
                        purpose="confirm_evaluation_cleanup",
                    )
                )
            ).items
            with pytest.raises(ValueError, match="cannot retry"):
                await evaluator.run(client, data, scopes, implementation_sha="a" * 40)

    with api_process("evaluation-api.log") as (http, _):
        asyncio.run(execute(str(http.base_url)))
    assert sum(row["event"] == "purged" for row in journal(evaluator)) == 1


@pytest.mark.integration
def test_failed_native_measurement_persists_unknown_call_and_purges(
    env, api_process, tmp_path, monkeypatch
):
    scopes = provision_scope(env)
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl")

    async def fail(self, source):
        raise ProviderFailure("provider_unavailable", unknown=True)

    monkeypatch.setattr(HTTPProvider, "embed", fail)

    async def execute(url):
        async with AsyncMemoryClient(url, env.token()) as client:
            with pytest.raises(ProviderFailure):
                await evaluator.run(
                    client,
                    corpus(),
                    scopes,
                    implementation_sha="a" * 40,
                )
            assert not (
                await client.recall(
                    Recall(
                        scope_ids=list(scopes.values()),
                        purpose="confirm_failed_evaluation_cleanup",
                    )
                )
            ).items

    with api_process("evaluation-failure-api.log") as (http, _):
        asyncio.run(execute(str(http.base_url)))
    events = journal(evaluator)
    assert [row["event"] for row in events] == [
        "started",
        "observe_reserved",
        "admitted",
        "call_reserved",
        "failed",
        "purge_reserved",
        "purged",
    ]
    assert events[3]["billing_unknown"] and evaluator.calls == 1


def test_input_limit_rejects_a_valid_prefix_followed_by_unread_data(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b"{}" + b" " * 20)
    with pytest.raises(ValueError, match="exceeds"):
        read_bounded(path, 2)
    assert read_bounded(path, 22) == path.read_bytes()


def test_journal_can_preserve_invalid_untrusted_unicode_for_diagnosis(tmp_path):
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl")
    evaluator.record("invalid_wire", content="\ud800")
    assert journal(evaluator)[0]["content"] == "\ud800"


@pytest.mark.integration
def test_invalid_answers_stay_in_every_baseline_denominator_without_retries(
    env, api_process, tmp_path, monkeypatch
):
    scopes = provision_scope(env)
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl")

    async def embed(self, source):
        return GeneratedEmbedding(
            model=self.settings.embedding_model,
            values=[1.0] + [0.0] * 767,
            input_digest=source.digest(),
        )

    async def invalid_answer(self, path, payload):
        assert path == "chat/completions" and payload["seed"] == 17
        return {"choices": [None]}

    monkeypatch.setattr(HTTPProvider, "embed", embed)
    monkeypatch.setattr(HTTPProvider, "exchange", invalid_answer)

    async def execute(url):
        async with AsyncMemoryClient(url, env.token()) as client:
            run, answers = await evaluator.run(
                client,
                corpus(),
                scopes,
                implementation_sha="a" * 40,
                answer_seeds=(17,),
            )
            assert len(run.observations) == len(answers) == 6
            assert {answer.baseline for answer in answers} == set(BASELINES)
            assert all(
                answer.failure_code == "invalid_answer_response"
                and answer.answer is None
                and answer.exact_match is False
                and answer.skipped_reason is None
                for answer in answers
            )
            assert evaluator.calls == 9
            assert not (
                await client.recall(
                    Recall(
                        scope_ids=list(scopes.values()),
                        purpose="confirm_failed_answer_cleanup",
                    )
                )
            ).items

    with api_process("evaluation-invalid-answers.log") as (http, _):
        asyncio.run(execute(str(http.base_url)))
    assert sum(row["event"] == "answer_failed" for row in journal(evaluator)) == 6


@pytest.mark.integration
def test_nonempty_scope_is_rejected_without_calls_or_deletion(env, api_process, tmp_path):
    saved = env.observe().json()
    evaluator = LocalEvaluation(configuration(), journal=tmp_path / "calls.jsonl")

    async def execute(url):
        async with AsyncMemoryClient(url, env.token()) as client:
            with pytest.raises(ValueError, match="dedicated empty scopes"):
                await evaluator.run(
                    client,
                    corpus(),
                    {"one": env.scopes[0]},
                    implementation_sha="a" * 40,
                )

    with api_process("evaluation-nonempty-api.log") as (http, _):
        asyncio.run(execute(str(http.base_url)))
    assert evaluator.calls == 0
    assert env.recall().json()["items"][0]["memory_id"] == saved["memory_id"]
