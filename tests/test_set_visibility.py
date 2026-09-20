import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from test_vectors import request, upload


def bind(conn, env, index):
    conn.execute(
        """SELECT set_config('pgag.tenant_id',%s,false),
                  set_config('pgag.principal_id',%s,false),
                  set_config('pgag.subject',%s,false)""",
        (str(env.tenants[1 if index == 1 else 0]), str(env.principals[index]), env.subjects[index]),
    )


@pytest.mark.parametrize(
    "permissions", [[], ["read"], ["write"], ["delete"], ["admin"], ["read", "write"]]
)
@pytest.mark.parametrize("expiry", [None, -3600, 3600])
@pytest.mark.parametrize("tombstoned", [False, True])
def test_prepared_set_policies_match_original_scalar_oracle(env, permissions, expiry, tombstoned):
    identifiers = []
    for index in range(3):
        episode = env.observe(index=index).json()["memory_id"]
        assertion = env.remember(episode, index=index).json()["memory_id"]
        identifiers.extend((UUID(episode), UUID(assertion)))
        upload(env, episode, index=index)
        upload(env, assertion, index=index)
    with psycopg.connect(env.admin_url, row_factory=dict_row) as conn:
        if tombstoned:
            deletion = uuid4()
            conn.execute(
                """INSERT INTO memory_ops.deletion_request
                   (tenant_id,id,principal_id,mode,state,object_count,deletion_epoch)
                   VALUES (%s,%s,%s,'suppress','blocked_for_reads',2,2)""",
                (env.tenants[0], deletion, env.principals[0]),
            )
            for ordinal, target in enumerate(identifiers[:2], 1):
                conn.execute(
                    "INSERT INTO memory_ops.object_tombstone "
                    "(tenant_id,object_id,scope_id) VALUES (%s,%s,%s)",
                    (env.tenants[0], target, env.scopes[0]),
                )
                conn.execute(
                    "INSERT INTO memory_ops.deletion_target "
                    "(tenant_id,deletion_id,object_id,scope_id,ordinal) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (env.tenants[0], deletion, target, env.scopes[0], ordinal),
                )
            conn.execute("UPDATE memory.tenant SET deletion_epoch=2 WHERE id=%s", (env.tenants[0],))
        conn.execute(
            """UPDATE memory.scope_member SET permissions=%s,expires_at=%s
               WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
            (
                permissions,
                None if expiry is None else datetime.now(UTC) + timedelta(seconds=expiry),
                env.tenants[0],
                env.scopes[0],
                env.principals[0],
            ),
        )
        catalog = conn.execute(
            """SELECT o.tenant_id,o.id,o.scope_id,o.kind,
               EXISTS(SELECT 1 FROM memory_ops.object_tombstone t
                      WHERE t.tenant_id=o.tenant_id AND t.object_id=o.id) AS tombstoned
               FROM memory.object o WHERE o.id=ANY(%s)""",
            (identifiers,),
        ).fetchall()
    rows = json.loads(json.dumps(catalog, default=str))
    relations = {
        "memory.object": ("id", None),
        "memory.episode": ("id", "episode"),
        "memory.assertion": ("id", "assertion"),
        "memory.assertion_revision": ("assertion_id", "assertion"),
        "memory.episode_embedding": ("episode_id", "episode"),
        "memory.assertion_embedding": ("assertion_id", "assertion"),
        "memory.episode_lexical": ("episode_id", "episode"),
        "memory.assertion_lexical": ("assertion_id", "assertion"),
    }
    with psycopg.connect(env.settings.database_url, autocommit=True, row_factory=dict_row) as conn:
        for index in (0, 1, 2, 0):
            bind(conn, env, index)
            for table, (column, kind) in relations.items():
                expected = conn.execute(
                    """SELECT id FROM jsonb_to_recordset(%s) AS x
                       (tenant_id uuid,id uuid,scope_id uuid,kind text,tombstoned boolean)
                       WHERE tenant_id=memory.current_tenant()
                         AND memory.permitted(scope_id,'read') AND NOT tombstoned
                         AND (%s::text IS NULL OR kind=%s) ORDER BY id""",
                    (Jsonb(rows), kind, kind),
                    prepare=True,
                ).fetchall()
                actual = conn.execute(
                    sql.SQL("SELECT {} AS id FROM {} ORDER BY {}").format(
                        sql.Identifier(column),
                        sql.Identifier(*table.split(".")),
                        sql.Identifier(column),
                    ),
                    prepare=True,
                ).fetchall()
                assert actual == expected, (index, table)


def test_prepared_membership_set_refreshes_after_revoke_and_regrant(env):
    source = UUID(env.observe().json()["memory_id"])
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        bind(conn, env, 0)
        query = "SELECT id FROM memory.episode"
        assert conn.execute(query, prepare=True).fetchall() == [(source,)]
        with psycopg.connect(env.admin_url) as admin:
            admin.execute(
                "UPDATE memory.scope_member SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE tenant_id=%s AND principal_id=%s",
                (env.tenants[0], env.principals[0]),
            )
        assert conn.execute(query, prepare=True).fetchall() == []
        with psycopg.connect(env.admin_url) as admin:
            admin.execute(
                "UPDATE memory.scope_member SET expires_at=NULL "
                "WHERE tenant_id=%s AND principal_id=%s",
                (env.tenants[0], env.principals[0]),
            )
        with psycopg.connect(env.admin_url) as admin:
            admin.execute(
                "UPDATE memory.scope_member SET permissions=ARRAY[]::text[] "
                "WHERE tenant_id=%s AND principal_id=%s",
                (env.tenants[0], env.principals[0]),
            )
        assert conn.execute(query, prepare=True).fetchall() == []
        with psycopg.connect(env.admin_url) as admin:
            admin.execute(
                "UPDATE memory.scope_member SET permissions=ARRAY['read'] "
                "WHERE tenant_id=%s AND principal_id=%s",
                (env.tenants[0], env.principals[0]),
            )
        assert conn.execute(query, prepare=True).fetchall() == [(source,)]


def test_10000_native_denials_with_positive_controls(env):
    targets = []
    for number in range(100):
        owner = number % 2
        marker = f"protected_case_{number}"
        response = env.observe("Gold " + marker, index=owner)
        memory_id = response.json()["memory_id"]
        upload(env, memory_id, index=owner)
        targets.append((memory_id, str(env.scopes[owner]), marker))
    actors = []
    with psycopg.connect(env.admin_url) as conn:
        for number in range(20):
            tenant = env.tenants[number % 2]
            principal, scope = uuid4(), uuid4()
            subject = "set-rls-" + str(uuid4())
            conn.execute(
                "INSERT INTO memory.principal VALUES (%s,%s,%s)", (tenant, principal, subject)
            )
            conn.execute("INSERT INTO memory.scope VALUES (%s,%s)", (tenant, scope))
            conn.execute(
                "INSERT INTO memory.scope_member "
                "(tenant_id,scope_id,principal_id,permissions) "
                "VALUES (%s,%s,%s,ARRAY['read','write','delete'])",
                (tenant, scope, principal),
            )
            actors.append(
                (
                    scope,
                    {
                        "Authorization": "Bearer "
                        + env.token(
                            sub=subject,
                            exp=int(time.time()) + 7200,
                        )
                    },
                )
            )
    checked = 0
    for scope, headers in actors:
        positive = env.client.post(
            "/v1/observe",
            headers=headers | {"Idempotency-Key": str(uuid4())},
            json={
                "scope_id": str(scope),
                "source_namespace": "positive",
                "source_event_id": "one",
                "occurred_at": "2026-09-01T00:00:00Z",
                "content": "Gold",
                "consent_reference": "synthetic",
            },
        )
        assert positive.status_code == 201
        own = positive.json()["memory_id"]
        for memory_id, target_scope, marker in targets:
            for mode in ("lexical", "hybrid"):
                result = env.client.post(
                    "/v1/recall",
                    headers=headers,
                    json={
                        "scope_ids": [target_scope],
                        "purpose": "synthetic-denial",
                        "query": marker,
                        "retrieval_mode": mode,
                        **({"vector_query": request()} if mode == "hybrid" else {}),
                    },
                )
                assert result.status_code == 200 and result.json()["items"] == []
                assert result.json()["coverage"]["retrieval_complete"]
                checked += 1
            for path in ("/v1/explain", "/v1/embedding-inputs"):
                result = env.client.post(path, headers=headers, json={"memory_id": memory_id})
                assert result.status_code == 404 and result.json()["code"] == "not_found"
                checked += 1
            result = env.client.post(
                "/v1/episodes/query", headers=headers, json={"scope_ids": [target_scope]}
            )
            assert result.status_code == 200 and result.json()["episodes"] == []
            checked += 1
        assert (
            env.client.post("/v1/explain", headers=headers, json={"memory_id": own}).status_code
            == 200
        )
    assert checked == 10000
