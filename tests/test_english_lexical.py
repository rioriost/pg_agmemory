import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest
from test_lexical import ids, purge, recorded_at, revise
from test_source_access import allow_notice, apply_notice, bind, deny_notice

from pg_agmemory import lexical
from pg_agmemory.database import SCHEMA_VERSION, reindex_lexical
from pg_agmemory.lexical import ENGLISH_PROFILE, JAPANESE_PROFILE, rebuild, text_search_config
from pg_agmemory.scope_access import ScopeAccessRequest, scope_access

FUTURE = "2100-01-01T00:00:00Z"
MODEL = {"name": "english-test-basis", "revision": "v1"}
VECTOR = [1.0] + [0.0] * 767


def recall(env, query="", **changes):
    response = env.recall(query=query, search_profile=ENGLISH_PROFILE, **changes)
    assert response.status_code == 200, response.text
    return response.json()


def projections(env, profile):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute(
            """SELECT 'episode',tenant_id,episode_id,1::bigint,profile,search_text::text
               FROM memory.episode_lexical WHERE profile=%s
               UNION ALL
               SELECT 'assertion',tenant_id,assertion_id,revision,profile,search_text::text
               FROM memory.assertion_lexical WHERE profile=%s
               ORDER BY 1,2,3,4""",
            (profile, profile),
        ).fetchall()


def embed(env, memory):
    source = env.client.post(
        "/v1/embedding-inputs", json={"memory_id": memory, "revision": 1}, headers=env.headers(),
    )
    assert source.status_code == 200, source.text
    uploaded = env.client.post(
        "/v1/embeddings",
        json={
            "memory_id": memory, "revision": 1, "input_digest": source.json()["input_digest"],
            "model": MODEL, "values": VECTOR,
        },
        headers=env.headers(),
    )
    assert uploaded.status_code == 201, uploaded.text


@pytest.mark.parametrize("profile,configuration", [
    ("simple-v1", "pg_catalog.simple"),
    (JAPANESE_PROFILE, "pg_catalog.simple"),
    (ENGLISH_PROFILE, "pg_catalog.english"),
])
def test_configuration_is_explicit_whitelisted_catalog_name(profile, configuration):
    assert text_search_config(profile) == configuration


@pytest.mark.parametrize("profile", ["english", "pg_catalog.english", "unknown", None, [], True])
def test_invalid_profile_never_reaches_sql_or_connects(profile):
    with pytest.raises(ValueError, match="Unsupported lexical profile"):
        text_search_config(profile)
    connection = Mock()
    with pytest.raises(ValueError, match="Unsupported projected lexical profile"):
        rebuild(connection, profile=profile)
    connection.execute.assert_not_called()
    with pytest.raises(ValueError, match="Unsupported projected lexical profile"):
        reindex_lexical("must-not-connect", profile=profile)


def test_simple_projection_is_not_a_reindex_target():
    with pytest.raises(ValueError, match="Unsupported projected lexical profile"):
        rebuild(Mock(), profile="simple-v1")


@pytest.mark.integration
def test_english_stemming_is_opt_in_all_lexemes_and_preserves_raw_evidence(env):
    text = 'Cedar approval requirements govern approved releases. "Keep raw wording."'
    identity = env.observe(text).json()["memory_id"]
    exact = env.recall(query="approval").json()
    queries = ("approve", "approves", "approved", "approving", "approvals", "require", "release")
    for query in queries:
        result = recall(env, query)
        assert ids(result) == {identity}
        assert result["items"] == exact["items"]
        assert result["context_pack"] == exact["context_pack"]
        assert result["items"][0]["content"] == text
        assert result["search_profile"] == ENGLISH_PROFILE
        assert result["coverage"]["retrieval_complete"]
        assert not result["coverage"]["lexical_incomplete"]
    assert ids(env.recall(query="approve").json()) == set()
    assert ids(env.recall(query="approve", search_profile=JAPANESE_PROFILE).json()) == set()
    assert ids(recall(env, "Cedar approve")) == {identity}
    assert ids(recall(env, "Cedar approve absentterm")) == set()
    assert env.recall().json()["search_profile"] == "simple-v1"
    explained = env.client.post(
        "/v1/explain", json={"memory_id": identity}, headers=env.headers(),
    )
    assert explained.json()["source"]["content"] == text
    with psycopg.connect(env.admin_url) as conn:
        rows = conn.execute(
            """SELECT profile,search_text=to_tsvector(
                      CASE WHEN profile=%s THEN 'pg_catalog.english'::regconfig
                           ELSE 'pg_catalog.simple'::regconfig END,%s)
               FROM memory.episode_lexical WHERE episode_id=%s ORDER BY profile""",
            (ENGLISH_PROFILE, text, identity),
        ).fetchall()
        assert rows == [(ENGLISH_PROFILE, True), (JAPANESE_PROFILE, True)]


@pytest.mark.integration
@pytest.mark.parametrize("query", ["the and a", "THE", "or not", "!!!"])
def test_nonempty_stopword_or_zero_lexeme_query_is_not_authorized_browse(env, query):
    identity = env.observe("Cedar approval policy").json()["memory_id"]
    result = recall(env, query)
    assert result["items"] == [] and result["empty_reason"] == "not_found"
    assert result["context_pack"]["text"] == ""
    assert result["coverage"]["retrieval_complete"]
    assert ids(recall(env, "")) == {identity}
    required = recall(
        env, query, required_memory_refs=[{"memory_id": identity, "revision": 1}], max_items=1,
    )
    assert ids(required) == {identity}


@pytest.mark.integration
def test_revision_specific_english_index_and_required_refs_preserve_temporal_evidence(env):
    source = env.observe("approval approved cancellation cancelled").json()["memory_id"]
    identity = env.remember(
        source, subject="Cedar", predicate="status", value="approval",
        valid_from="2026-09-01T00:00:00Z",
        evidence=[{"memory_id": source, "quote": "approval"}],
    ).json()["memory_id"]
    before = recorded_at(env, identity)
    revise(env, identity, source, "cancellation")
    current = recall(env, "cancel", filters={"kind": "assertion"})
    assert ids(current) == {identity} and current["items"][0]["revision"] == 2
    assert recall(env, "approve", filters={"kind": "assertion"})["items"] == []
    historical = recall(
        env, "approve", known_at=before, as_of=FUTURE, filters={"kind": "assertion"},
    )
    assert ids(historical) == {identity} and historical["items"][0]["revision"] == 1
    assert historical["items"][0]["source"] == [source]
    required = recall(
        env, "the", known_at=before, as_of=FUTURE,
        filters={"kind": "assertion", "subject": "Cedar"},
        required_memory_refs=[{"memory_id": identity, "revision": 1}], max_items=1,
    )
    assert required["items"] == historical["items"]
    assert required["context_pack"] == historical["context_pack"]
    for changes in (
        {"known_at": FUTURE},
        {"known_at": before, "filters": {"subject": "Wrong"}},
        {"known_at": before, "as_of": "2026-08-01T00:00:00Z"},
    ):
        unavailable = env.recall(
            search_profile=ENGLISH_PROFILE, required_memory_refs=[{"memory_id": identity}],
            **changes,
        )
        assert unavailable.status_code == 404
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            """SELECT profile,revision FROM memory.assertion_lexical
               WHERE assertion_id=%s ORDER BY profile,revision""",
            (identity,),
        ).fetchall() == [(ENGLISH_PROFILE, 1), (ENGLISH_PROFILE, 2),
                        (JAPANESE_PROFILE, 1), (JAPANESE_PROFILE, 2)]
    purge(env, source)
    assert recall(env, "approve", known_at=before)["items"] == []
    assert recall(env, "cancel")["items"] == []
    for profile in (JAPANESE_PROFILE, ENGLISH_PROFILE):
        reindex_lexical(env.admin_url, profile=profile)
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM memory.assertion_lexical WHERE assertion_id=%s", (identity,),
        ).fetchone()[0] == 0


@pytest.mark.integration
def test_missing_english_projection_has_scoped_coverage_and_required_refs_never_fallback(env):
    visible = env.observe("Cedar approval").json()["memory_id"]
    missing = env.observe("Cedar approved").json()["memory_id"]
    private = env.observe("Private approval", index=2).json()["memory_id"]
    foreign = env.observe("Foreign approval", index=1).json()["memory_id"]
    future = env.observe(
        "Future approval", occurred_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    ).json()["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "DELETE FROM memory.episode_lexical WHERE profile=%s AND episode_id=ANY(%s)",
            (ENGLISH_PROFILE, [private, foreign, future]),
        )
    all_scopes = [str(scope) for scope in env.scopes]
    for inaccessible in (private, foreign):
        assert env.recall(
            search_profile=ENGLISH_PROFILE, required_memory_refs=[{"memory_id": inaccessible}],
            scope_ids=all_scopes,
        ).status_code == 404
    assert not recall(env, "approve", scope_ids=all_scopes)["coverage"]["lexical_incomplete"]
    assert recall(env, "approve", as_of=FUTURE)["coverage"]["lexical_incomplete"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "DELETE FROM memory.episode_lexical WHERE profile=%s AND episode_id=%s",
            (ENGLISH_PROFILE, missing),
        )
    partial = recall(env, "approve")
    assert ids(partial) == {visible} and partial["coverage"]["lexical_incomplete"]
    assert not partial["coverage"]["retrieval_complete"]
    assert not env.recall(search_profile=JAPANESE_PROFILE).json()["coverage"]["lexical_incomplete"]
    assert not env.recall().json()["coverage"]["lexical_incomplete"]
    assert recall(env, known_at="2000-01-01T00:00:00Z")["coverage"]["retrieval_complete"]
    unmatched = recall(env, "absentterm")
    assert unmatched["items"] == [] and unmatched["empty_reason"] == "index_incomplete"
    required = recall(
        env, "the", required_memory_refs=[{"memory_id": missing}], max_items=1,
    )
    assert ids(required) == {missing} and required["coverage"]["lexical_incomplete"]
    assert {visible, missing} <= ids(recall(env))
    japanese_before = projections(env, JAPANESE_PROFILE)
    report = reindex_lexical(env.admin_url, profile=ENGLISH_PROFILE)
    assert report["profile"] == ENGLISH_PROFILE and report["episodes"] >= 5
    assert projections(env, JAPANESE_PROFILE) == japanese_before
    assert recall(env, "approve")["coverage"]["retrieval_complete"]


@pytest.mark.integration
def test_english_missing_historical_projection_does_not_poison_current_coverage(env):
    source = env.observe("approval cancellation").json()["memory_id"]
    identity = env.remember(
        source, value="approval", evidence=[{"memory_id": source, "quote": "approval"}],
    ).json()["memory_id"]
    before = recorded_at(env, identity)
    revise(env, identity, source, "cancellation")
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """DELETE FROM memory.assertion_lexical
               WHERE profile=%s AND assertion_id=%s AND revision=1""",
            (ENGLISH_PROFILE, identity),
        )
    assert recall(env, "cancel", filters={"kind": "assertion"})["coverage"]["retrieval_complete"]
    historical = recall(env, "approve", known_at=before, filters={"kind": "assertion"})
    assert historical["items"] == [] and historical["coverage"]["lexical_incomplete"]
    reindex_lexical(env.admin_url, profile=ENGLISH_PROFILE)
    assert ids(recall(env, "approve", known_at=before, filters={"kind": "assertion"})) == {identity}


@pytest.mark.integration
def test_english_acl_and_source_authority_remain_current_under_frozen_time(env):
    identity = env.observe("Cedar approval").json()["memory_id"]
    arguments = {"scope_ids": [str(env.scopes[0])], "as_of": FUTURE, "known_at": FUTURE}
    assert recall(env, "approve", index=2, **arguments)["items"] == []
    bind(env)
    apply_notice(env, allow_notice(env))
    assert ids(recall(env, "approve", index=2, **arguments)) == {identity}
    apply_notice(env, deny_notice())
    assert recall(env, "approve", index=2, **arguments)["items"] == []
    assert env.recall(
        index=2, search_profile=ENGLISH_PROFILE, required_memory_refs=[{"memory_id": identity}],
        **arguments,
    ).status_code == 404
    with scope_access(env.admin_url, ScopeAccessRequest(
        operation="revoke", tenant_id=env.tenants[0], scope_id=env.scopes[0],
        principal_id=env.principals[0],
        expected_access_epoch=recall(env)["consistency"]["access_epoch"],
    )):
        pass
    assert recall(env, "approve", **arguments)["items"] == []


@pytest.mark.integration
def test_english_lexical_branch_in_hybrid_and_vector_coverage_remain_independent(env):
    identity = env.observe("Cedar approval").json()["memory_id"]
    embed(env, identity)
    vector_query = {"model": MODEL, "values": VECTOR}
    hybrid = recall(env, "approve", retrieval_mode="hybrid", vector_query=vector_query)
    assert ids(hybrid) == {identity}
    assert hybrid["items"][0]["retrieval"]["lexical_rank"] == 1
    assert hybrid["items"][0]["retrieval"]["vector_rank"] == 1
    stopwords = recall(env, "the and a", retrieval_mode="hybrid", vector_query=vector_query)
    assert ids(stopwords) == {identity}
    assert stopwords["items"][0]["retrieval"]["lexical_rank"] is None
    assert stopwords["items"][0]["retrieval"]["vector_rank"] == 1
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "DELETE FROM memory.episode_lexical WHERE episode_id=%s AND profile=%s",
            (identity, ENGLISH_PROFILE),
        )
    partial = recall(env, "approve", retrieval_mode="hybrid", vector_query=vector_query)
    assert ids(partial) == {identity} and partial["coverage"]["lexical_incomplete"]
    assert partial["items"][0]["retrieval"]["lexical_rank"] is None
    vector = recall(env, retrieval_mode="vector", vector_query=vector_query)
    assert ids(vector) == {identity}
    assert vector["coverage"]["retrieval_complete"] and not vector["coverage"]["lexical_incomplete"]
    assert not vector["coverage"]["vector_incomplete"]


@pytest.mark.integration
def test_english_and_default_japanese_rebuild_only_replace_selected_profile(env, monkeypatch):
    source = env.observe("Cedar approval 東京都の契約").json()["memory_id"]
    memory = env.remember(
        source, subject="Cedar", value="approval",
        evidence=[{"memory_id": source, "quote": "approval"}],
    ).json()["memory_id"]
    japanese = projections(env, JAPANESE_PROFILE)

    def unavailable(text):
        raise AssertionError("English rebuild must not invoke Japanese tokenization")

    with monkeypatch.context() as patch:
        patch.setattr(lexical, "segment", unavailable)
        report = reindex_lexical(env.admin_url, profile=ENGLISH_PROFILE)
    assert report["episodes"] >= 1 and report["assertion_revisions"] >= 1
    assert projections(env, JAPANESE_PROFILE) == japanese
    english = projections(env, ENGLISH_PROFILE)
    assert reindex_lexical(env.admin_url)["profile"] == JAPANESE_PROFILE
    assert projections(env, ENGLISH_PROFILE) == english
    assert memory in ids(recall(env, "approve"))


@pytest.mark.integration
def test_english_rebuild_failure_rolls_back_deleted_and_partly_rebuilt_rows(env, monkeypatch):
    env.observe("Cedar approval")
    env.observe("Cedar approved")
    before = {profile: projections(env, profile) for profile in (JAPANESE_PROFILE, ENGLISH_PROFILE)}
    original = psycopg.Connection.execute
    inserted = 0

    def fail(self, query, params=None, **kwargs):
        nonlocal inserted
        if (
            isinstance(query, str)
            and query.lstrip().startswith("INSERT INTO memory.episode_lexical")
        ):
            if params is not None and ENGLISH_PROFILE in params:
                inserted += 1
                if inserted == 2:
                    raise RuntimeError("simulated English rebuild failure")
        return original(self, query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", fail)
        with pytest.raises(RuntimeError, match="simulated English rebuild failure"):
            reindex_lexical(env.admin_url, profile=ENGLISH_PROFILE)
    assert inserted == 2
    for profile in before:
        assert projections(env, profile) == before[profile]


@pytest.mark.integration
def test_english_projection_failure_rolls_back_observe_and_revision(env, monkeypatch):
    source = env.observe("approval cancellation").json()["memory_id"]
    memory = env.remember(
        source, value="approval", evidence=[{"memory_id": source, "quote": "approval"}],
    ).json()["memory_id"]
    original = psycopg.AsyncConnection.execute

    async def fail(self, query, params=None, **kwargs):
        if (
            isinstance(query, str) and "INSERT INTO memory." in query
            and "_lexical" in query and params is not None and ENGLISH_PROFILE in params
        ):
            raise psycopg.errors.QueryCanceled("simulated projection failure")
        return await original(self, query, params, **kwargs)

    event = str(uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.AsyncConnection, "execute", fail)
        observed = env.observe("Cedar approval", source_event_id=event)
        assert observed.status_code == 503
        revised = env.client.post(
            f"/v1/assertions/{memory}/revisions", headers=env.headers(),
            json={
                "expected_revision": 1, "value": "cancellation", "explicit_intent": True,
                "evidence": [{"memory_id": source, "quote": "cancellation"}],
                "reason": "correction",
            },
        )
        assert revised.status_code == 503
    assert env.observe("Cedar approval", source_event_id=event).status_code == 201
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT current_revision FROM memory.assertion WHERE id=%s", (memory,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM memory.assertion_lexical WHERE assertion_id=%s", (memory,),
        ).fetchone()[0] == 2
    assert revise(env, memory, source, "cancellation")["revision"] == 2


@pytest.mark.integration
def test_english_rebuild_skips_stale_tombstoned_payload_without_erasing_japanese(env):
    identity = env.observe("Cedar approval").json()["memory_id"]
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            "ALTER TABLE memory_ops.object_tombstone DISABLE TRIGGER tombstone_manifest_complete",
        )
        conn.execute(
            """INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id)
               VALUES (%s,%s,%s)""",
            (env.tenants[0], identity, env.scopes[0]),
        )
        conn.execute(
            "ALTER TABLE memory_ops.object_tombstone ENABLE TRIGGER tombstone_manifest_complete",
        )
    japanese = projections(env, JAPANESE_PROFILE)
    reindex_lexical(env.admin_url, profile=ENGLISH_PROFILE)
    assert recall(env, "approve")["items"] == []
    assert projections(env, JAPANESE_PROFILE) == japanese
    with psycopg.connect(env.admin_url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM memory.episode_lexical WHERE episode_id=%s AND profile=%s",
            (identity, ENGLISH_PROFILE),
        ).fetchone()[0] == 0


@pytest.mark.integration
def test_reindex_cli_explicit_english_profile_and_runtime_role_remain_bounded(env):
    env.observe("Cedar approval")
    japanese = projections(env, JAPANESE_PROFILE)
    completed = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "reindex-lexical", "--profile", ENGLISH_PROFILE],
        env={**os.environ, "PGAG_ADMIN_DATABASE_URL": env.admin_url},
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["profile"] == ENGLISH_PROFILE
    assert projections(env, JAPANESE_PROFILE) == japanese
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reindex_lexical(env.settings.database_url, profile=ENGLISH_PROFILE)
    for arguments in (["migrate", "--profile", ENGLISH_PROFILE],
                      ["reindex-lexical", "--profile", "simple-v1"]):
        invalid = subprocess.run(
            [sys.executable, "-m", "pg_agmemory.cli", *arguments],
            env={**os.environ, "PGAG_ADMIN_DATABASE_URL": env.admin_url},
            capture_output=True, text=True, timeout=30,
        )
        assert invalid.returncode == 2 and invalid.stdout == ""


@pytest.mark.integration
def test_schema_23_upgrade_backfills_english_for_existing_historical_revisions(database):
    admin_url, _, records = database
    assert SCHEMA_VERSION == 23
    with psycopg.connect(admin_url) as conn:
        for record in records:
            expected = conn.execute(
                """SELECT revision FROM memory.assertion_revision
                   WHERE tenant_id=%s AND assertion_id=%s ORDER BY revision""",
                (record["tenant"], record["assertion"]),
            ).fetchall()
            assert conn.execute(
                """SELECT revision FROM memory.assertion_lexical
                   WHERE tenant_id=%s AND assertion_id=%s AND profile=%s ORDER BY revision""",
                (record["tenant"], record["assertion"], ENGLISH_PROFILE),
            ).fetchall() == expected
        assert conn.execute(
            """SELECT revision FROM memory.assertion_revision
               WHERE tenant_id=%s AND assertion_id=%s ORDER BY revision""",
            (records[0]["tenant"], records[0]["assertion"]),
        ).fetchall() == [(1,), (2,)]
        assert conn.execute(
            """SELECT bool_and(lex.search_text=to_tsvector('pg_catalog.english',e.content))
               FROM memory.episode_lexical lex
               JOIN memory.episode e ON e.tenant_id=lex.tenant_id AND e.id=lex.episode_id
               WHERE lex.profile=%s""",
            (ENGLISH_PROFILE,),
        ).fetchone()[0]
