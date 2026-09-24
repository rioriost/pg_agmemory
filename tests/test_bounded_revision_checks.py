"""Schema-22 deferred checks retain integrity at the guarded COMMIT boundary."""

import psycopg
import pytest
from test_graphs import create_entity, create_relation, revise_body
from test_revisions import explain, history, revise

from pg_agmemory.transactions import transaction

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("damage", ["gap", "evidence", "head", "current_interval"])
def test_deferred_history_damage_rolls_back_at_runtime_commit(env, damage):
    source = env.observe("Gold Silver").json()["memory_id"]
    memory = env.remember(source).json()["memory_id"]
    for expected in (1, 2):
        response = revise(env, memory, source, expected=expected)
        assert response.status_code == 201, response.text
    before = history(env, memory).json()
    reached_commit = False
    message = "requires evidence" if damage == "evidence" else "must be contiguous"
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match=message), transaction(conn):
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            if damage in ("gap", "evidence"):
                conn.execute(
                    """DELETE FROM memory.provenance_edge
                       WHERE tenant_id=%s AND child_id=%s AND child_revision=2""",
                    (env.tenants[0], memory),
                )
                if damage == "gap":
                    conn.execute(
                        """DELETE FROM memory.assertion_revision
                           WHERE tenant_id=%s AND assertion_id=%s AND revision=2""",
                        (env.tenants[0], memory),
                    )
            elif damage == "head":
                conn.execute(
                    """UPDATE memory.assertion SET current_revision=2
                       WHERE tenant_id=%s AND id=%s""",
                    (env.tenants[0], memory),
                )
            else:
                conn.execute(
                    """UPDATE memory.assertion_revision
                       SET system_time=tstzrange(lower(system_time),clock_timestamp(),'[)')
                       WHERE tenant_id=%s AND assertion_id=%s AND revision=3""",
                    (env.tenants[0], memory),
                )
            reached_commit = True
        assert reached_commit and not conn.closed
    assert history(env, memory).json() == before
    assert explain(env, memory, 3).json()["assertion"]["known_until"] is None
    assert revise(env, memory, source, expected=3).status_code == 201


def test_deleting_historical_relation_target_rolls_back_at_runtime_commit(env):
    source, target = create_entity(env, "A"), create_entity(env, "B")
    relation = create_relation(env, source, target)
    response = env.client.post(
        f"/v1/relations/{relation['memory_id']}/revisions",
        headers=env.headers(), json=revise_body(relation, target),
    )
    assert response.status_code == 201, response.text
    memory = relation["memory_id"]
    before = history(env, memory).json()
    reached_commit = False
    with psycopg.connect(env.settings.database_url, autocommit=True) as conn:
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="exact entity target"),
            transaction(conn),
        ):
            conn.execute(
                """SELECT set_config('pgag.tenant_id',%s,true),
                          set_config('pgag.principal_id',%s,true)""",
                (str(env.tenants[0]), str(env.principals[0])),
            )
            conn.execute(
                """DELETE FROM memory.relation_revision
                   WHERE tenant_id=%s AND assertion_id=%s AND revision=1""",
                (env.tenants[0], memory),
            )
            reached_commit = True
        assert reached_commit and not conn.closed
    assert history(env, memory).json() == before
