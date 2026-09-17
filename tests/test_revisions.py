from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from pg_agmemory.api import create_app
from pg_agmemory.database import migrate
from pg_agmemory.service import MemoryError, MemoryService

pytestmark = pytest.mark.integration


def revise(env, memory, source, expected=1, headers=None, **overrides):
    return env.client.post(
        f"/v1/assertions/{memory}/revisions",
        headers=headers or env.headers(),
        json={
            "expected_revision": expected,
            "value": "Silver",
            "evidence": [{"memory_id": source, "quote": "Silver"}],
            "explicit_intent": True,
            "reason": "Corrected source",
            **overrides,
        },
    )


def explain(env, memory, revision=1, index=0):
    return env.client.post(
        "/v1/explain",
        json={"memory_id": memory, "revision": revision},
        headers=env.headers(index),
    )


def assertions(env, **kwargs):
    response = env.recall(**kwargs)
    assert response.status_code == 200, response.text
    return [item for item in response.json()["items"] if item["type"] == "assertion"]


def test_bitemporal_correction_keeps_historical_value_evidence_and_boundaries(env):
    first = env.observe().json()["memory_id"]
    memory = env.remember(first, valid_from="2026-09-01T00:00:00Z").json()["memory_id"]
    before = explain(env, memory).json()
    second = env.observe("ACME contract is Silver from September 5").json()["memory_id"]
    response = revise(env, memory, second, valid_from="2026-09-05T00:00:00Z")
    assert response.status_code == 201, response.text
    assert response.json()["revision"] == 2
    old = explain(env, memory, 1).json()
    current = explain(env, memory, 2).json()
    assert old["assertion"]["value"] == "Gold"
    assert old["evidence"][0]["memory_id"] == first
    assert current["assertion"]["value"] == "Silver"
    assert current["evidence"][0]["memory_id"] == second
    assert old["assertion"]["known_until"] == current["assertion"]["recorded_at"]
    assert current["assertion"]["known_until"] is None
    assert current["assertion"]["correction_reason"] == "Corrected source"
    assert before["assertion"]["recorded_at"] == old["assertion"]["recorded_at"]
    boundary = datetime.fromisoformat(current["assertion"]["recorded_at"])
    for known_at, as_of, expected in [
        (before["assertion"]["recorded_at"], "2026-09-03T00:00:00Z", 1),
        ((boundary - timedelta(microseconds=1)).isoformat(), "2026-09-06T00:00:00Z", 1),
        (boundary.isoformat(), "2026-09-03T00:00:00Z", None),
        (boundary.isoformat(), "2026-09-05T00:00:00Z", 2),
    ]:
        items = assertions(env, known_at=known_at, as_of=as_of)
        assert [item["revision"] for item in items] == ([] if expected is None else [expected])
        if items:
            assert items[0]["source"] == ([first] if expected == 1 else [second])
    assert assertions(env, query="Gold") == []
    found = assertions(env, query="ACME Silver")
    assert found[0]["revision"] == 2
    assert found[0]["recorded_at"] == current["assertion"]["recorded_at"]
    assert f"{memory}@2" in env.recall(query="Silver").json()["context_pack"]["text"]
    assert explain(env, memory, 3).status_code == 404
    assert explain(env, second, 2).status_code == 404
    # Omitting revision retains the v0.0.1 default, not a mutable "latest" alias.
    assert (
        env.client.post("/v1/explain", json={"memory_id": memory}, headers=env.headers()).json()[
            "revision"
        ]
        == 1
    )


def test_full_interval_replacement_and_unknown_date_are_not_scheduling(env):
    first = env.observe().json()["memory_id"]
    memory = env.remember(first).json()["memory_id"]
    second = env.observe("Silver").json()["memory_id"]
    updated = revise(
        env, memory, second, valid_from="2030-01-01T00:00:00Z", valid_to="2031-01-01T00:00:00Z"
    )
    assert updated.status_code == 201
    assert assertions(env, as_of="2029-12-31T23:59:59Z") == []
    assert assertions(env, as_of="2030-01-01T00:00:00Z")[0]["revision"] == 2
    assert assertions(env, as_of="2031-01-01T00:00:00Z") == []
    assert revise(env, memory, second, expected=2).status_code == 201
    assert explain(env, memory, 3).json()["assertion"]["valid_from"] is None
    assert assertions(env, as_of="1900-01-01T00:00:00Z")[0]["revision"] == 3


def test_parallel_revision_cas_and_idempotency(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    headers = env.headers()
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: revise(env, memory, source, headers=headers), range(8)))
    assert all(response.status_code == 201 for response in responses)
    assert all(response.json()["revision"] == 2 for response in responses)
    assert revise(env, memory, source).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        competing = list(
            pool.map(
                lambda i: revise(env, memory, source, expected=2, reason=f"correction-{i}"),
                range(2),
            )
        )
    assert sorted(response.status_code for response in competing) == [201, 409]
    assert revise(env, memory, source, headers=headers).json() == responses[0].json()
    assert revise(env, memory, source, headers=headers, reason="different").status_code == 409
    other = env.remember(source).json()["memory_id"]
    assert revise(env, other, source, headers=headers).status_code == 409
    assert assertions(env, query="Silver")[0]["revision"] == 3


@pytest.mark.parametrize("delete_source", [True, False])
def test_purge_removes_all_revision_text_and_replay_after_historical_source_loss(
    env, delete_source
):
    first = env.observe().json()["memory_id"]
    memory = env.remember(first).json()["memory_id"]
    second = env.observe("Silver").json()["memory_id"]
    headers = env.headers()
    assert revise(env, memory, second, headers=headers).status_code == 201
    known_at = explain(env, memory).json()["assertion"]["recorded_at"]
    target = first if delete_source else memory
    deleted = env.client.post(
        "/v1/forget", json={"memory_ids": [target], "reason": "test"}, headers=env.headers()
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["object_count"] == (2 if delete_source else 1)
    for revision in (1, 2):
        assert explain(env, memory, revision).status_code == 404
    assert assertions(env, known_at=known_at) == []
    assert revise(env, memory, second, headers=headers).status_code == 404
    assert revise(env, memory, second, expected=2).status_code == 404
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.assertion_revision WHERE assertion_id = %s", (memory,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory.provenance_edge WHERE child_id = %s", (memory,)
            ).fetchone()[0]
            == 0
        )
    assert explain(env, second).status_code == 200


def test_revision_validation_current_acl_and_read_only_member(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    for index in (1, 2):
        assert revise(env, memory, source, headers=env.headers(index)).status_code == 404
        assert explain(env, memory, 1, index).status_code == 404
    assert revise(env, source, source).status_code == 404
    for invalid in [
        {"expected_revision": True},
        {"expected_revision": 0},
        {"expected_revision": 1001},
        {"reason": ""},
        {"scope_id": str(env.scopes[1])},
        {"subject": "other"},
        {"system_time": "[1900-01-01,)"},
        {"epistemic_status": "verified"},
        {"evidence": []},
        {"evidence": [{"memory_id": source, "quote": "invented"}]},
        {"valid_from": "2026-09-05T00:00:00Z", "valid_to": "2026-09-05T00:00:00Z"},
        {"valid_from": "2026-09-05T00:00:00"},
    ]:
        assert revise(env, memory, source, **invalid).status_code == 422
    key = env.headers()
    assert revise(env, memory, source, headers=key).status_code == 201
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET permissions = ARRAY['read']
               WHERE tenant_id = %s AND principal_id = %s""",
            (env.tenants[0], env.principals[0]),
        )
    assert revise(env, memory, source, expected=2).status_code == 404
    assert revise(env, memory, source, headers=key).status_code == 404
    assert explain(env, memory, 2).status_code == 200
    with psycopg.connect(env.admin_url) as conn:
        conn.execute(
            """UPDATE memory.scope_member SET permissions = '{}'
               WHERE tenant_id = %s AND principal_id = %s""",
            (env.tenants[0], env.principals[0]),
        )
    assert explain(env, memory, 1).status_code == 404
    assert explain(env, memory, 2).status_code == 404


def test_failed_publication_rolls_back_head_intervals_evidence_and_idempotency(env, monkeypatch):
    source = env.observe("Gold Silver").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    original = explain(env, memory).json()
    headers = env.headers()
    audit = MemoryService.audit

    async def fail_audit(self, action, target):
        raise MemoryError("publication_failed", 503)

    monkeypatch.setattr(MemoryService, "audit", fail_audit)
    assert revise(env, memory, source, headers=headers).status_code == 503
    assert explain(env, memory).json() == original
    assert explain(env, memory, 2).status_code == 404
    monkeypatch.setattr(MemoryService, "audit", audit)
    assert revise(env, memory, source, headers=headers).json()["revision"] == 2


def test_database_rejects_history_edits_gaps_and_missing_revision_evidence(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                "UPDATE memory.assertion_revision SET value = 'forged' WHERE assertion_id = %s",
                (memory,),
            )
        for operation, parameters in [
            (
                """INSERT INTO memory.assertion_revision
                (tenant_id,assertion_id,scope_id,revision,value,valid_time,
                 explicit_intent,correction_reason)
                VALUES (%s,%s,%s,2,'Silver','(,)',true,'missing evidence')""",
                (env.tenants[0], memory, env.scopes[0]),
            ),
            (
                """UPDATE memory.assertion_revision
                SET system_time = tstzrange(lower(system_time),clock_timestamp(),'[)')
                WHERE tenant_id = %s AND assertion_id = %s""",
                (env.tenants[0], memory),
            ),
        ]:
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(
                    """SELECT set_config('pgag.tenant_id',%s,true),
                              set_config('pgag.principal_id',%s,true)""",
                    (str(env.tenants[0]), str(env.principals[0])),
                )
                conn.execute(operation, parameters)
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert explain(env, memory).json()["assertion"]["known_until"] is None
    assert revise(env, memory, source).status_code == 201


def test_gist_exclusion_rejects_overlapping_history_independently_of_adoption_trigger(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.ExclusionViolation), conn.transaction():
            conn.execute("ALTER TABLE memory.assertion_revision DISABLE TRIGGER adopt_revision")
            conn.execute(
                """INSERT INTO memory.assertion_revision
                   (tenant_id,assertion_id,scope_id,revision,value,valid_time,system_time,
                    explicit_intent,correction_reason)
                   SELECT tenant_id,assertion_id,scope_id,2,'Silver',valid_time,system_time,
                          true,'overlap' FROM memory.assertion_revision
                   WHERE assertion_id = %s""",
                (memory,),
            )
    assert revise(env, memory, source).status_code == 201


def test_schema_upgrade_preserves_legacy_tenants_times_evidence_replays_and_deletion(env, database):
    admin_url, _, records = database
    with psycopg.connect(admin_url) as conn:
        assert conn.execute(
            "SELECT version FROM public.pgag_schema_migration ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,)]
        for record in records:
            row = conn.execute(
                """SELECT value, lower(valid_time), lower(system_time), revision
                   FROM memory.assertion_revision WHERE assertion_id = %s AND revision = 1""",
                (record["assertion"],),
            ).fetchone()
            assert row[0] == "Gold" and row[3] == 1
            assert row[1].isoformat() == "2026-09-01T00:00:00+00:00"
            assert row[2].isoformat() == "2026-09-10T00:00:00+00:00"
            token = env.token(sub=record["subject"])
            headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": record["key"]}
            replayed = env.client.post("/v1/remember", headers=headers, json=record["payload"])
            assert replayed.status_code == 201, replayed.text
            assert replayed.json() == record["result"]
            hidden = env.client.post(
                "/v1/explain", headers=headers, json={"memory_id": str(record["deleted"])}
            )
            assert hidden.status_code == 404
            response = env.client.post(
                "/v1/explain", headers=headers, json={"memory_id": str(record["assertion"])}
            )
            assert response.json()["evidence"][0]["memory_id"] == str(record["source"])
    capabilities = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert capabilities["schema_version"] == 6 and capabilities["temporal_revisions"] is True
    schema = env.client.get("/openapi.json").json()
    contract = schema["paths"]["/v1/assertions/{memory_id}/revisions"]["post"]
    assert contract["security"] == [{"BearerAuth": []}]
    assert contract["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RevisionResult"
    }


def test_newer_or_noncontiguous_schema_history_fails_closed(env):
    with psycopg.connect(env.admin_url, autocommit=True) as conn:
        conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (999)")
        try:
            with pytest.raises(RuntimeError, match="Unsupported database migration history"):
                migrate(env.admin_url)
            with pytest.raises(RuntimeError, match="schema version mismatch"):
                with TestClient(create_app(env.settings)):
                    pass
        finally:
            conn.execute("DELETE FROM public.pgag_schema_migration WHERE version = 999")


def test_unknown_assertion_matches_inaccessible_response(env):
    source = env.observe("Gold Silver").json()["memory_id"]
    response = revise(env, str(uuid4()), source)
    assert response.status_code == 404 and response.json()["code"] == "not_found"


@pytest.mark.parametrize("typed", [False, True])
def test_exact_revision_limit_preserves_replay_and_history(env, typed):
    source = env.observe("Gold Silver").json()["memory_id"]
    entities = []
    if typed:
        for label in ("Gold", "Silver"):
            response = env.client.post(
                "/v1/entities",
                headers=env.headers(),
                json={
                    "scope_id": str(env.scopes[0]),
                    "entity_type": "component",
                    "canonical_label": label,
                    "explicit_intent": True,
                    "evidence": [{"memory_id": source, "quote": label}],
                },
            )
            assert response.status_code == 201
            entities.append(response.json()["memory_id"])
    memory = str(uuid4())
    with psycopg.connect(env.admin_url) as conn:
        # Bulk-seed a valid prefix with the anchor's deferred history check enabled;
        # the actual 999 -> 1000 adoption below still uses the HTTP/DB runtime path.
        conn.execute("ALTER TABLE memory.assertion_revision DISABLE TRIGGER adopt_revision")
        conn.execute("ALTER TABLE memory.assertion_revision DISABLE TRIGGER revision_history")
        conn.execute("ALTER TABLE memory.assertion_revision DISABLE TRIGGER relation_shape")
        if typed:
            conn.execute("ALTER TABLE memory.relation_revision DISABLE TRIGGER relation_shape")
        conn.execute(
            """INSERT INTO memory.object(tenant_id,id,scope_id,kind)
               VALUES (%s,%s,%s,'assertion')""",
            (env.tenants[0], memory, env.scopes[0]),
        )
        conn.execute(
            """INSERT INTO memory.assertion
               (tenant_id,id,scope_id,subject,predicate,current_revision,is_relation)
               VALUES (%s,%s,%s,%s,%s,999,%s)""",
            (
                env.tenants[0],
                memory,
                env.scopes[0],
                "Gold" if typed else "ACME",
                "depends_on" if typed else "contract_tier",
                typed,
            ),
        )
        if typed:
            conn.execute(
                "INSERT INTO memory.relation(tenant_id,id,scope_id,source_id) VALUES (%s,%s,%s,%s)",
                (env.tenants[0], memory, env.scopes[0], entities[0]),
            )
        conn.execute(
            """INSERT INTO memory.assertion_revision
               (tenant_id,assertion_id,scope_id,revision,value,valid_time,system_time,
                explicit_intent,correction_reason)
               SELECT %s,%s,%s,n,'Silver','(,)',
                      tstzrange('2026-09-01T00:00:00Z'::timestamptz + n * interval '1 second',
                                CASE WHEN n < 999 THEN
                                    '2026-09-01T00:00:00Z'::timestamptz
                                    + (n+1) * interval '1 second'
                                END, '[)'),
                      true,CASE WHEN n > 1 THEN 'limit fixture' END
               FROM generate_series(1,999) n""",
            (env.tenants[0], memory, env.scopes[0]),
        )
        conn.execute(
            """INSERT INTO memory.provenance_edge
               (tenant_id,child_id,child_revision,parent_id,scope_id,quote)
               SELECT %s,%s,n,%s,%s,'Silver' FROM generate_series(1,999) n""",
            (env.tenants[0], memory, source, env.scopes[0]),
        )
        if typed:
            conn.execute(
                """INSERT INTO memory.relation_revision
                   (tenant_id,assertion_id,scope_id,revision,target_id)
                   SELECT %s,%s,%s,n,%s FROM generate_series(1,999) n""",
                (env.tenants[0], memory, env.scopes[0], entities[1]),
            )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        conn.execute("ALTER TABLE memory.assertion_revision ENABLE TRIGGER adopt_revision")
        conn.execute("ALTER TABLE memory.assertion_revision ENABLE TRIGGER revision_history")
        conn.execute("ALTER TABLE memory.assertion_revision ENABLE TRIGGER relation_shape")
        if typed:
            conn.execute("ALTER TABLE memory.relation_revision ENABLE TRIGGER relation_shape")

    def append(expected, headers=None):
        if not typed:
            return revise(env, memory, source, expected=expected, headers=headers)
        return env.client.post(
            f"/v1/relations/{memory}/revisions",
            headers=headers or env.headers(),
            json={
                "expected_revision": expected,
                "target_entity": entities[1],
                "evidence": [{"memory_id": source, "quote": "Silver"}],
                "explicit_intent": True,
                "reason": "Correction",
            },
        )

    key = env.headers()
    final = append(999, headers=key)
    assert final.status_code == 201, final.text
    assert final.json()["revision"] == 1000
    exceeded = append(1000)
    assert exceeded.status_code == 422
    assert exceeded.json()["code"] == "revision_limit_exceeded"
    assert append(999, headers=key).json() == final.json()
    assert explain(env, memory, 1000).status_code == 200
    assert assertions(env)[0]["revision"] == 1000
