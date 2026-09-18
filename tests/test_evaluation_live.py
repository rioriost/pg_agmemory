import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from pg_agmemory.evaluation import retrieval_report
from pg_agmemory.evaluation_fixtures import synthetic_dataset
from pg_agmemory.evaluation_public import load_longmemeval
from pg_agmemory.evaluation_runner import LocalEvaluation
from pg_agmemory.providers import parse_settings
from pg_agmemory.sdk import AsyncMemoryClient


@pytest.fixture
def evaluation_request():
    mode = os.environ.get("PGAG_M2_EVALUATION_MODE")
    if mode is None:
        pytest.skip("Opt in explicitly to bounded local M2 evaluation")
    if mode not in ("dev", "test", "public"):
        pytest.fail("PGAG_M2_EVALUATION_MODE must be dev, test or public")
    required = (
        "PGAG_LIVE_PROVIDER_CONFIG",
        "PGAG_M2_EVALUATION_OUTPUT",
        "PGAG_M2_IMPLEMENTATION_SHA",
    )
    if any(not os.environ.get(name) for name in required):
        pytest.fail(
            "Live evaluation requires a profile, new output directory and implementation SHA"
        )
    with Path(os.environ["PGAG_LIVE_PROVIDER_CONFIG"]).open("rb") as stream:
        settings = parse_settings(stream.read(32769))
    if (
        settings.backend != "local_http"
        or settings.embedding_model is None
        or settings.embedding_model.name != "qwen3-embedding:0.6b"
        or settings.embedding_model.revision
        != "ollama-sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d"
        or settings.text_model is None
        or settings.text_model.name != "qwen2.5:7b"
        or settings.text_model.revision
        != "ollama-sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
        or settings.max_output_tokens != 512
    ):
        pytest.fail("Live evaluation requires the operator-approved pinned local profile")
    output = Path(os.environ["PGAG_M2_EVALUATION_OUTPUT"])
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    return mode, settings, output


@pytest.mark.integration
@pytest.mark.live
def test_live_native_retrieval_baselines(evaluation_request, env, api_process):
    mode, settings, output = evaluation_request
    split = "dev" if mode == "dev" else "test"
    if mode == "public":
        path = os.environ.get("PGAG_M2_LONGMEMEVAL_ORACLE")
        if not path:
            pytest.fail("Public evaluation requires the exact hash-pinned oracle file")
        data = load_longmemeval(Path(path))
    else:
        data = synthetic_dataset()
    scopes = {
        group: uuid4() for group in sorted({q.group_id for q in data.questions if q.split == split})
    }
    with psycopg.connect(env.admin_url) as admin:
        for scope in scopes.values():
            admin.execute("INSERT INTO memory.scope VALUES (%s,%s)", (env.tenants[0], scope))
            admin.execute(
                """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
                   VALUES (%s,%s,%s,%s)""",
                (env.tenants[0], scope, env.principals[0], ["read", "write", "delete"]),
            )
    (output / "dataset.json").write_text(data.model_dump_json(indent=2), encoding="utf-8")
    (output / "scope-map.json").write_text(
        json.dumps({group: str(scope) for group, scope in scopes.items()}, indent=2),
        encoding="utf-8",
    )
    evaluator = LocalEvaluation(
        settings, journal=output / "journal.jsonl", max_calls=3000, budget_bytes=8000
    )

    async def execute(url):
        async with AsyncMemoryClient(url, env.token(exp=int(time.time()) + 7200)) as client:
            return await evaluator.run(
                client,
                data,
                scopes,
                split=split,
                implementation_sha=os.environ["PGAG_M2_IMPLEMENTATION_SHA"],
                answer_seeds=(17, 29) if mode == "public" else (),
            )

    with api_process("live-evaluation-api.log") as (http, _):
        measured, answers = asyncio.run(execute(str(http.base_url)))
    report = retrieval_report(data, measured)
    (output / "retrieval-run.json").write_text(measured.model_dump_json(indent=2), encoding="utf-8")
    (output / "retrieval-report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    (output / "answers.json").write_text(
        json.dumps(
            {
                "answer_model": settings.text_model.model_dump(),
                "profile_digest": evaluator.profile_digest(),
                "answer_prompt_revision": "grounded-qa-v1",
                "measurement": "mechanical_exact_match_not_upstream_or_human_grading",
                "calls": evaluator.calls,
                "m2_qualified": False,
                "records": [answer.model_dump(mode="json") for answer in answers],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    evaluator.record("completed", calls=evaluator.calls, m2_qualified=False)
    assert report.gates["observed_scope_leakage"].status == "passed"
    if mode == "test":
        assert report.held_out_questions == 600 and report.held_out_groups == 50
        assert report.gates["recall_at_20"].status == "passed"
        assert report.gates["ranking_non_regression"].status == "passed"
    assert not report.m2_qualified
