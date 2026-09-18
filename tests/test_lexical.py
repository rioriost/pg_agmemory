import asyncio
import inspect
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from pg_agmemory import lexical
from pg_agmemory.database import migrate, reindex_lexical
from pg_agmemory.lexical import JAPANESE_PROFILE, TokenizerUnavailable, segment
from pg_agmemory.worker import run_once

pytestmark = pytest.mark.integration


def recall(env, query="", **kwargs):
    response = env.recall(query=query, search_profile=JAPANESE_PROFILE, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def ids(result):
    return {item["memory_id"] for item in result["items"]}


def recorded_at(env, memory):
    response = env.client.post(
        "/v1/explain",
        headers=env.headers(),
        json={"memory_id": memory, "revision": 1},
    )
    assert response.status_code == 200, response.text
    return response.json()["assertion"]["recorded_at"]


def revise(env, memory, source, value, revision=1):
    response = env.client.post(
        f"/v1/assertions/{memory}/revisions",
        headers=env.headers(),
        json={
            "expected_revision": revision,
            "value": value,
            "explicit_intent": True,
            "evidence": [{"memory_id": source, "quote": value}],
            "reason": "Correction",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def purge(env, memory):
    response = env.client.post(
        "/v1/forget",
        headers=env.headers(),
        json={"memory_ids": [memory], "reason": "test", "mode": "purge"},
    )
    assert response.status_code == 202, response.text


def _process_peak_rss_kib():
    """Keep this probe self-contained for execution in a cold interpreter."""
    import resource
    import sys
    from pathlib import Path

    if sys.platform.startswith("linux"):
        # Unlike ru_maxrss, VmHWM excludes the previous process image after exec.
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                _, value, unit = line.split()
                assert unit == "kB", line
                return int(value)
        raise RuntimeError("Missing VmHWM in /proc/self/status")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // 1024 if sys.platform == "darwin" else peak


@pytest.mark.parametrize(
    "text,expected",
    [
        ("東京都で契約を更新した。", " 東京 都 で 契約 を 更新 し た 。"),
        ("東京", " 東京 "),
        ("Gold INC-1842 pg_agmemory café", "Gold INC-1842 pg_agmemory café"),
        ("ｺﾞｰﾙﾄﾞ Ｇｏｌｄ", "ｺﾞｰﾙﾄﾞ Ｇｏｌｄ"),
        ("", ""),
        ("!!! ' & | <->", "!!! ' & | <->"),
    ],
)
def test_pinned_segmentation_surface_and_unmodified_non_japanese(text, expected):
    assert segment(text) == expected


def test_segmentation_keeps_no_input_prefix_cache_and_is_concurrent():
    samples = ["東京都の契約を更新した。", "株式会社青空の決定", "INC-1842"] * 8
    expected = [segment(sample) for sample in samples]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(segment, samples)) == expected
    tokenizer = lexical._tokenizer()
    assert tokenizer.matcher.max_cached_word_len == 0
    assert all(not cache for cache in tokenizer.matcher.cache)


def test_lazy_dictionary_loading_and_precompiled_container_memory():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            inspect.getsource(_process_peak_rss_kib)
            + """
import sys
import pg_agmemory.api
from pg_agmemory.lexical import segment
assert "janome.tokenizer" not in sys.modules
assert segment("Gold INC-1842") == "Gold INC-1842"
assert "janome.tokenizer" not in sys.modules
assert segment("\\u6771\\u4eac\\u90fd").split() == ["\\u6771\\u4eac", "\\u90fd"]
peak = _process_peak_rss_kib()
assert peak <= 256 * 1024, f"Cold tokenizer peak {peak} KiB; precompile the packaged dictionary"
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux fork/exec RSS accounting")
@pytest.mark.parametrize("allocation_mib", [0, 272])
def test_cold_process_peak_rss_excludes_pre_exec_memory(allocation_mib):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
import sys
large_parent = bytearray(320 * 1024 * 1024)
# Force fork/exec rather than depending on subprocess's platform-specific spawn path.
pid = os.fork()
if pid == 0:
    os.execv(sys.executable, [sys.executable, "-c", sys.argv[1]])
_, status = os.waitpid(pid, 0)
sys.exit(os.waitstatus_to_exitcode(status))
""",
            inspect.getsource(_process_peak_rss_kib)
            + f"""
import json
import resource
allocation = bytearray({allocation_mib} * 1024 * 1024)
del allocation
print(json.dumps({{
    "peak_kib": _process_peak_rss_kib(),
    "ru_maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
}}))
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert measured["ru_maxrss_kib"] >= 320 * 1024, measured
    assert measured["peak_kib"] >= allocation_mib * 1024, measured
    # A real over-budget peak must still fail the gate even after its memory is freed.
    assert (measured["peak_kib"] > 256 * 1024) == bool(allocation_mib), measured


def test_japanese_retrieval_is_opt_in_and_preserves_evidence(env):
    text = "東京都で契約を更新した。管理番号INC-1842。"
    source = env.observe(text).json()["memory_id"]
    assert ids(env.recall(query="契約").json()) == set()
    result = recall(env, "契約")
    assert ids(result) == {source}
    assert result["items"][0]["content"] == text
    assert result["search_profile"] == JAPANESE_PROFILE
    assert result["coverage"]["retrieval_complete"] is True
    assert result["coverage"]["lexical_incomplete"] is False
    assert ids(recall(env, "東京")) == {source}
    assert ids(recall(env, "INC-1842")) == {source}
    assert ids(recall(env, "INC-1843")) == set()
    assert ids(recall(env, "!!!")) == set()
    assert ids(recall(env)) == {source}
    assert env.recall().json()["search_profile"] == "simple-v1"
    explanation = env.client.post(
        "/v1/explain",
        headers=env.headers(),
        json={"memory_id": source},
    )
    assert explanation.json()["source"]["content"] == text


def test_ascii_english_identifiers_and_ranking_match_legacy(env):
    for text in ["Gold INC-1842 pg_agmemory café", "Gold INC-1843", "Gold"]:
        env.observe(text)
    for query in ("Gold", "INC-1842", "pg_agmemory", "café", "", "' OR 1=1"):
        legacy = env.recall(query=query).json()
        japanese = recall(env, query)
        assert japanese["items"] == legacy["items"]
        assert japanese["context_pack"] == legacy["context_pack"]


def test_revision_specific_search_time_and_purge(env):
    old_source = env.observe("東京都の契約を更新した。").json()["memory_id"]
    new_source = env.observe("東京都の契約を終了した。").json()["memory_id"]
    memory = env.remember(
        old_source,
        subject="東京都",
        value="契約を更新した",
        evidence=[{"memory_id": old_source, "quote": "契約を更新した"}],
    ).json()["memory_id"]
    before = recorded_at(env, memory)
    revise(env, memory, new_source, "契約を終了した")
    current = next(item for item in recall(env, "終了")["items"] if item["memory_id"] == memory)
    assert current["revision"] == 2 and current["source"] == [new_source]
    assert memory not in ids(recall(env, "更新"))
    historic = next(
        item
        for item in recall(env, "更新", known_at=before)["items"]
        if item["memory_id"] == memory
    )
    assert historic["revision"] == 1 and historic["source"] == [old_source]
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            """SELECT revision FROM memory.assertion_lexical
               WHERE assertion_id = %s ORDER BY revision""",
            (memory,),
        ).fetchall() == [(1,), (2,)]
    purge(env, old_source)
    assert memory not in ids(recall(env, "終了"))
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.assertion_lexical WHERE assertion_id = %s",
                (memory,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.episode_lexical WHERE episode_id = %s",
                (old_source,),
            ).fetchone()[0]
            == 0
        )
    reindex_lexical(env.admin_url)
    assert ids(recall(env, "終了")) == {new_source}


def test_missing_projection_is_visible_without_fallback_and_browse_still_works(env):
    first = env.observe("Gold").json()["memory_id"]
    source = env.observe("東京都の契約は Gold です。").json()["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("DELETE FROM memory.episode_lexical WHERE episode_id = %s", (source,))
    result = recall(env, "Gold")
    assert ids(result) == {first}
    assert result["coverage"]["lexical_incomplete"] is True
    assert result["coverage"]["retrieval_complete"] is False
    missing = recall(env, "契約")
    assert missing["items"] == [] and missing["empty_reason"] == "index_incomplete"
    browsed = recall(env, max_items=1)
    assert ids(browsed) == {source} and browsed["coverage"]["lexical_incomplete"]
    assert ids(env.recall(query="Gold").json()) == {first, source}
    assert env.recall(query="Gold").json()["coverage"]["lexical_incomplete"] is False
    report = reindex_lexical(env.admin_url)
    assert report["profile"] == JAPANESE_PROFILE and report["episodes"] >= 2
    assert ids(recall(env, "契約")) == {source}
    assert recall(env)["coverage"]["retrieval_complete"] is True


def test_projection_coverage_respects_current_acl_and_time_filters(env):
    visible = env.observe("東京都の契約").json()["memory_id"]
    secret = env.observe("秘密の契約", index=2).json()["memory_id"]
    foreign = env.observe("他社の契約", index=1).json()["memory_id"]
    future = env.observe(
        "将来の契約",
        occurred_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    ).json()["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "DELETE FROM memory.episode_lexical WHERE episode_id = ANY(%s)",
            ([secret, foreign, future],),
        )
    narrowed = recall(env, "契約", scope_ids=[str(scope) for scope in env.scopes])
    assert ids(narrowed) == {visible}
    assert narrowed["coverage"]["lexical_incomplete"] is False
    future_time = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert recall(env, as_of=future_time)["coverage"]["lexical_incomplete"] is True
    assert recall(env, known_at="2000-01-01T00:00:00Z")["coverage"]["lexical_incomplete"] is False


def test_current_acl_revocation_hides_existing_lexical_rows(env):
    source = env.observe("東京都の契約").json()["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read'])""",
            (env.tenants[0], env.scopes[0], env.principals[2]),
        )
    assert ids(recall(env, "契約", index=2, scope_ids=[str(env.scopes[0])])) == {source}
    with psycopg.connect(env.admin_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        conn.execute(
            """UPDATE memory.scope_member SET permissions = ARRAY[]::text[]
               WHERE scope_id = %s AND principal_id = %s""",
            (env.scopes[0], env.principals[2]),
        )
        conn.execute(
            "UPDATE memory.tenant SET access_epoch = access_epoch + 1 WHERE id = %s",
            (env.tenants[0],),
        )
    denied = recall(env, "契約", index=2, scope_ids=[str(env.scopes[0])])
    assert denied["items"] == [] and denied["coverage"]["lexical_incomplete"] is False


def test_historical_missing_index_does_not_claim_current_gap(env):
    source = env.observe("契約を更新した。契約を終了した。").json()["memory_id"]
    memory = env.remember(
        source,
        value="契約を更新した",
        evidence=[{"memory_id": source, "quote": "契約を更新した"}],
    ).json()["memory_id"]
    before = recorded_at(env, memory)
    revise(env, memory, source, "契約を終了した")
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "DELETE FROM memory.assertion_lexical WHERE assertion_id = %s AND revision = 1",
            (memory,),
        )
    assert recall(env, "契約")["coverage"]["lexical_incomplete"] is False
    assert recall(env, "契約", known_at=before)["coverage"]["lexical_incomplete"] is True


def test_budget_remains_exact_utf8_context_limit(env):
    env.observe("東京都で契約を更新した。")
    full = recall(env, "契約")
    pack = full["context_pack"]
    size = pack["byte_count"]
    assert pack["tokenizer_id"] == "utf8-bytes-v1" and pack["token_count"] is None
    exact = recall(env, "契約", token_budget=size)
    assert exact["context_pack"] == pack and exact["items"] == full["items"]
    smaller = recall(env, "契約", token_budget=size - 1)
    assert smaller["items"] == [] and smaller["empty_reason"] == "budget_exhausted"
    assert smaller["context_pack"]["byte_count"] <= size - 1


def test_exact_maximum_japanese_content_is_fully_indexed(env):
    text = "東京" * 32764 + "契約を終了した。"
    assert len(text) == 65536
    observed = env.observe(text)
    assert observed.status_code == 201, observed.text
    result = recall(env, "終了")
    assert result["empty_reason"] == "budget_exhausted"
    assert result["coverage"]["truncated"] and not result["coverage"]["lexical_incomplete"]
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                """SELECT search_text @@ plainto_tsquery('simple','終了')
               FROM memory.episode_lexical WHERE episode_id = %s""",
                (observed.json()["memory_id"],),
            ).fetchone()[0]
            is True
        )
    assert env.observe(text + "東").status_code == 422


def test_failed_indexing_rolls_back_observe_and_revision(env, monkeypatch):
    source = env.observe("Gold Silver").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    event = str(uuid4())

    def unavailable(text):
        raise TokenizerUnavailable("Japanese tokenizer unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("pg_agmemory.service.segment", unavailable)
        response = env.observe("東京都の契約", source_event_id=event)
        assert response.status_code == 503 and response.json()["retryable"] is True
        failed = env.client.post(
            f"/v1/assertions/{memory}/revisions",
            headers=env.headers(),
            json={
                "expected_revision": 1,
                "value": "Silver",
                "explicit_intent": True,
                "evidence": [{"memory_id": source, "quote": "Silver"}],
                "reason": "test",
            },
        )
        assert failed.status_code == 503
    assert env.observe("東京都の契約", source_event_id=event).status_code == 201
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT current_revision FROM memory.assertion WHERE id = %s",
                (memory,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.assertion_revision WHERE assertion_id = %s",
                (memory,),
            ).fetchone()[0]
            == 1
        )
    assert revise(env, memory, source, "Silver")["revision"] == 2


def test_dictionary_failure_never_logs_input_or_exits_service(env, monkeypatch, caplog):
    tokenizer = lexical._tokenizer()
    sensitive = "秘密の契約"

    def corrupt(index):
        raise ValueError(sensitive)

    monkeypatch.setattr(tokenizer.sys_dic, "_find_entry", corrupt)
    with caplog.at_level(logging.ERROR):
        response = env.observe(sensitive)
    assert response.status_code == 503
    assert "japanese_dictionary_error" in caplog.text
    assert sensitive not in caplog.text and "input=" not in caplog.text
    assert sensitive not in response.text


def test_worker_retries_failed_projection_and_publishes_only_once(env, monkeypatch):
    source = env.observe("東京都の契約はGoldです。").json()["memory_id"]
    body = {
        "kind": "structured_remember",
        "memory": {
            "scope_id": str(env.scopes[0]),
            "subject": "東京都",
            "predicate": "contract_tier",
            "value": "Gold",
            "explicit_intent": True,
            "evidence": [{"memory_id": source, "quote": "Gold"}],
        },
    }
    job = env.client.post("/v1/jobs", headers=env.headers(), json=body).json()["job_id"]

    def unavailable(text):
        raise TokenizerUnavailable("Japanese tokenizer unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("pg_agmemory.service.segment", unavailable)
        assert (
            asyncio.run(run_once(env.settings.database_url, env.subjects[0]))["outcome"]
            == "pending"
        )
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.assertion WHERE tenant_id = %s",
                (env.tenants[0],),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT error_code FROM memory_ops.job WHERE id = %s",
                (job,),
            ).fetchone()[0]
            == "dependency_unavailable"
        )
    # Respect the persisted retry schedule rather than mutating the guarded state.
    time.sleep(3.1)
    result = asyncio.run(run_once(env.settings.database_url, env.subjects[0]))
    assert result["outcome"] == "succeeded"
    assert result["result"]["memory_id"] in ids(recall(env, "東京"))
    assert asyncio.run(run_once(env.settings.database_url, env.subjects[0]))["outcome"] == "idle"


def test_relation_revisions_index_canonical_entity_labels(env):
    source = env.observe("東京都 大阪府 京都府").json()["memory_id"]
    entities = []
    for label in ("東京都", "大阪府", "京都府"):
        created = env.client.post(
            "/v1/entities",
            headers=env.headers(),
            json={
                "scope_id": str(env.scopes[0]),
                "entity_type": "organization",
                "canonical_label": label,
                "explicit_intent": True,
                "evidence": [{"memory_id": source, "quote": label}],
            },
        )
        assert created.status_code == 201
        entities.append(created.json()["memory_id"])
    relation = env.client.post(
        "/v1/relations",
        headers=env.headers(),
        json={
            "scope_id": str(env.scopes[0]),
            "source_entity": entities[0],
            "target_entity": entities[1],
            "predicate": "depends_on",
            "explicit_intent": True,
            "evidence": [{"memory_id": source, "quote": "東京都"}],
        },
    ).json()["memory_id"]
    before = recorded_at(env, relation)
    changed = env.client.post(
        f"/v1/relations/{relation}/revisions",
        headers=env.headers(),
        json={
            "expected_revision": 1,
            "target_entity": entities[2],
            "explicit_intent": True,
            "evidence": [{"memory_id": source, "quote": "京都府"}],
            "reason": "test",
        },
    )
    assert changed.status_code == 201
    assert relation in ids(recall(env, "京都"))
    assert relation not in ids(recall(env, "大阪"))
    assert relation in ids(recall(env, "大阪", known_at=before))
    assert not set(entities) & ids(recall(env))
    purge(env, entities[1])
    assert relation not in ids(recall(env, "京都"))


@pytest.mark.parametrize("kind", ["episode", "assertion"])
def test_projection_rls_and_runtime_immutability(env, kind):
    source = env.observe("Gold").json()["memory_id"]
    memory = source if kind == "episode" else env.remember(source).json()["memory_id"]
    table, column = f"{kind}_lexical", f"{kind}_id"
    for index in (1, 2):
        with psycopg.connect(env.settings.database_url) as conn:
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (
                    str(env.tenants[index] if index < 2 else env.tenants[0]),
                    str(env.principals[index]),
                ),
            )
            assert (
                conn.execute(
                    sql.SQL("SELECT search_text FROM memory.{} WHERE {} = %s").format(
                        sql.Identifier(table),
                        sql.Identifier(column),
                    ),
                    (memory,),
                ).fetchall()
                == []
            )
    with psycopg.connect(env.settings.database_url) as conn:
        conn.execute(
            """SELECT set_config('pgag.tenant_id',%s,true),
                      set_config('pgag.principal_id',%s,true)""",
            (str(env.tenants[0]), str(env.principals[0])),
        )
        assert conn.execute(
            sql.SQL("SELECT search_text FROM memory.{} WHERE {} = %s").format(
                sql.Identifier(table),
                sql.Identifier(column),
            ),
            (memory,),
        ).fetchone()
        for query in (
            "UPDATE memory.{} SET search_text = ''::tsvector",
            "DELETE FROM memory.{}",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(sql.SQL(query).format(sql.Identifier(table)))


def test_projection_foreign_keys_and_profile_constraints(env):
    source = env.observe("Gold").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    attempts = [
        (
            "INSERT INTO memory.episode_lexical VALUES (%s,%s,%s,%s,''::tsvector)",
            (env.tenants[0], source, env.scopes[0], "unversioned"),
            psycopg.errors.CheckViolation,
        ),
        (
            "INSERT INTO memory.assertion_lexical VALUES (%s,%s,2,%s,%s,''::tsvector)",
            (env.tenants[0], memory, env.scopes[0], JAPANESE_PROFILE),
            psycopg.errors.ForeignKeyViolation,
        ),
        (
            "INSERT INTO memory.episode_lexical VALUES (%s,%s,%s,%s,''::tsvector)",
            (env.tenants[1], source, env.scopes[1], JAPANESE_PROFILE),
            psycopg.errors.ForeignKeyViolation,
        ),
    ]
    for query, parameters, error in attempts:
        with psycopg.connect(env.admin_url) as conn, pytest.raises(error):
            conn.execute(query, parameters)


def test_rebuild_is_atomic_and_excludes_tombstones(env, monkeypatch):
    source = env.observe("東京都の契約").json()["memory_id"]
    secret = env.observe("秘密の契約").json()["memory_id"]
    original = recall(env)
    calls = 0
    real_segment = lexical.segment

    def broken(text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TokenizerUnavailable("Japanese tokenizer unavailable")
        return real_segment(text)

    with monkeypatch.context() as patch:
        patch.setattr(lexical, "segment", broken)
        with pytest.raises(TokenizerUnavailable):
            reindex_lexical(env.admin_url)
    assert calls == 2 and recall(env) == original
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id)
               VALUES (%s,%s,%s)""",
            (env.tenants[0], secret, env.scopes[0]),
        )
    # Simulate a stale payload with an already applied tombstone during maintenance.
    reindex_lexical(env.admin_url)
    assert ids(recall(env)) == {source}
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.episode_lexical WHERE episode_id = %s",
                (secret,),
            ).fetchone()[0]
            == 0
        )


def test_reindex_cli_and_wrong_schema_or_runtime_credentials_fail_closed(env):
    source = env.observe("東京都の契約").json()["memory_id"]
    process = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "reindex-lexical"],
        env={**os.environ, "PGAG_ADMIN_DATABASE_URL": env.admin_url},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert set(json.loads(process.stdout)) == {"profile", "episodes", "assertion_revisions"}
    assert source in ids(recall(env, "契約"))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reindex_lexical(env.settings.database_url)
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (999)")
        try:
            with pytest.raises(RuntimeError, match="schema version mismatch"):
                reindex_lexical(env.admin_url)
        finally:
            conn.execute("DELETE FROM public.pgag_schema_migration WHERE version = 999")


@pytest.mark.parametrize("arguments", [["--subject", "narrow-scope"], ["--once"]])
def test_reindex_rejects_misleading_scope_or_worker_flags(env, arguments):
    result = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "reindex-lexical", *arguments],
        env={**os.environ, "PGAG_ADMIN_DATABASE_URL": env.admin_url},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2 and result.stdout == ""


def test_v6_job_replay_and_historical_backfill_survive_migration(env, database):
    _, _, records = database
    record = records[0]
    job = record["job"]
    headers = {
        "Authorization": f"Bearer {env.token(sub=record['subject'])}",
        "Idempotency-Key": job["key"],
    }
    response = env.client.post("/v1/jobs", headers=headers, json=job["body"])
    assert response.status_code == 202 and response.json() == job["result"]
    queued = env.client.get(f"/v1/jobs/{job['result']['job_id']}", headers=headers).json()
    assert queued["state"] == "pending" and queued["attempt"] == 0
    indexed = env.client.post(
        "/v1/recall",
        headers=headers,
        json={
            "scope_ids": [str(record["scope"])],
            "purpose": "test",
            "query": "契約",
            "search_profile": JAPANESE_PROFILE,
            "token_budget": 8000,
        },
    ).json()
    assert str(job["source"]) in ids(indexed)
    assert indexed["coverage"]["lexical_incomplete"] is False
    completed = asyncio.run(run_once(env.settings.database_url, record["subject"]))
    assert completed["outcome"] == "succeeded"
    assert env.client.post("/v1/jobs", headers=headers, json=job["body"]).json() == job["result"]
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            """SELECT revision FROM memory.assertion_lexical
               WHERE assertion_id = %s ORDER BY revision""",
            (record["assertion"],),
        ).fetchall() == [(1,), (2,)]
    migrate(env.admin_url)


def test_japanese_profile_contract_and_limits(env):
    capabilities = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert capabilities["schema_version"] == 11
    assert capabilities["search_profiles"] == ["simple-v1", JAPANESE_PROFILE]
    assert capabilities["default_search_profile"] == "simple-v1"
    assert capabilities["japanese_fts"]["normalization"] == "none"
    assert capabilities["vector_search"] is True
    assert env.recall(search_profile="unknown").status_code == 422
    assert env.recall(search_profile=JAPANESE_PROFILE, query="東" * 4097).status_code == 422
    assert (
        env.client.post("/v1/recall", json={"search_profile": JAPANESE_PROFILE}).status_code == 401
    )
    schema = env.client.get("/openapi.json").json()
    profile = schema["components"]["schemas"]["Recall"]["properties"]["search_profile"]
    assert profile["enum"] == ["simple-v1", JAPANESE_PROFILE] and profile["default"] == "simple-v1"
