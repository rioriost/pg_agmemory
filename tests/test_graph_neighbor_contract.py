import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from psycopg import sql

from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import ExpandGraph, GraphPath
from pg_agmemory.service import MemoryError


def context():
    memory = Mock(tenant=uuid4(), conn=AsyncMock())
    root, scope = uuid4(), uuid4()
    request = ExpandGraph(
        scope_ids=[scope], seeds=[root], relation_types=["depends_on"], purpose="contract"
    )
    walk = GraphPath(nodes=[root], assertions=[])
    time = datetime(2026, 9, 21, tzinfo=UTC)
    return memory, request, walk, time


@pytest.mark.parametrize("parameter", [
    "tenant", "node", "direction", "scopes", "predicates",
    "known", "as_of", "visited", "limit",
])
def test_adjacency_backend_cannot_override_canonical_authority_or_budget(parameter):
    memory, request, walk, time = context()
    with pytest.raises(MemoryError, match="graph_invalidated") as caught:
        asyncio.run(SqlGraph(memory)._canonical_neighbors(
            walk, request, time, time, 2, sql.SQL("SELECT 1"), {parameter: "override"}
        ))
    assert caught.value.status == 409
    memory.conn.execute.assert_not_awaited()


def test_adjacency_values_remain_bound_and_share_canonical_filters():
    memory, request, walk, time = context()
    memory.conn.execute.return_value.fetchall.return_value = []
    extra = {"candidate_value": "not SQL: '); SELECT secret; --"}
    adjacent = sql.SQL(
        "SELECT id,revision,source_id,target_id,next_id "
        "FROM candidate_rows WHERE key=%(candidate_value)s"
    )
    assert asyncio.run(SqlGraph(memory)._canonical_neighbors(
        walk, request, time, time, 2, adjacent, extra
    )) == []
    query, parameters = memory.conn.execute.await_args.args
    rendered = query.as_string()
    assert adjacent.as_string() in rendered
    assert extra["candidate_value"] not in rendered
    assert "valid_time @> %(as_of)s" in rendered
    assert "system_time @> %(known)s" in rendered
    assert "scope_id=ANY(%(scopes)s)" in rendered
    assert "WHERE NOT x.next_id = ANY(%(visited)s)" in rendered
    assert "ORDER BY x.id,x.revision,x.next_id LIMIT %(limit)s" in rendered
    assert parameters == {
        "tenant": memory.tenant,
        "node": walk.nodes[-1],
        "direction": request.direction,
        "scopes": request.scope_ids,
        "predicates": request.relation_types,
        "known": time,
        "as_of": time,
        "visited": walk.nodes,
        "limit": 2,
        **extra,
    }
    assert extra == {"candidate_value": "not SQL: '); SELECT secret; --"}
