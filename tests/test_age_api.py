"""Optional AGE API configuration, lifecycle gates, and real publication reads."""

import asyncio
import logging
import os
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from test_age_projection import operate, prepared, publish
from test_graph_artifact import artifact_dir as artifact_dir
from test_graph_generation import graph_fixture

from pg_agmemory import api
from pg_agmemory.database import RuntimeValidationError, Settings
from pg_agmemory.models import ExpandGraph

PRIVATE = "AGE_API_PRIVATE_UNQUALIFIED_STAMP"
LIVE = pytest.mark.skipif(
    os.environ.get("PGAG_TEST_AGE_NATIVE") != "1",
    reason="PGAG_TEST_AGE_NATIVE=1 requires the dedicated patched AGE image",
)


@pytest.fixture
def offline_settings():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return Settings(PRIVATE, public, "https://issuer.test", "pgag-test")


def test_graph_backend_is_sql_by_default_and_environment_selection_is_strict(
    offline_settings, monkeypatch,
):
    assert offline_settings.graph_backend == "sql"
    for name, value in {
        "PGAG_DATABASE_URL": offline_settings.database_url,
        "PGAG_JWT_PUBLIC_KEY": offline_settings.jwt_public_key,
        "PGAG_JWT_ISSUER": offline_settings.jwt_issuer,
        "PGAG_JWT_AUDIENCE": offline_settings.jwt_audience,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PGAG_GRAPH_BACKEND", raising=False)
    assert Settings.from_env().graph_backend == "sql"
    for value in ("sql", "age"):
        monkeypatch.setenv("PGAG_GRAPH_BACKEND", value)
        assert Settings.from_env().graph_backend == value
    for value in ("", "AGE", " age", "age ", "native_vle", "fallback"):
        monkeypatch.setenv("PGAG_GRAPH_BACKEND", value)
        with pytest.raises(ValueError, match="PGAG_GRAPH_BACKEND must be sql or age"):
            Settings.from_env()
        with pytest.raises(ValueError, match="PGAG_GRAPH_BACKEND must be sql or age"):
            replace(offline_settings, graph_backend=value)


def test_sql_startup_and_readiness_never_require_age(offline_settings, monkeypatch):
    core = AsyncMock()
    age = AsyncMock(side_effect=AssertionError("SQL must not validate AGE"))
    monkeypatch.setattr(api, "validate_runtime", core)
    monkeypatch.setattr(api, "validate_age_runtime", age)
    with TestClient(api.create_app(offline_settings)) as client:
        core.assert_awaited_once_with(offline_settings.database_url)
        assert client.get("/readyz").json() == {"status": "ready"}
        assert client.get("/healthz").json() == {"status": "ok"}
    assert core.await_count == 2
    age.assert_not_called()


def test_age_startup_and_each_readiness_request_validate_the_profile(
    offline_settings, monkeypatch,
):
    core, age = AsyncMock(), AsyncMock()
    monkeypatch.setattr(api, "validate_runtime", core)
    monkeypatch.setattr(api, "validate_age_runtime", age)
    with TestClient(api.create_app(replace(offline_settings, graph_backend="age"))) as client:
        age.assert_awaited_once_with(offline_settings.database_url)
        for _ in range(2):
            response = client.get("/readyz")
            assert response.status_code == 200 and response.json() == {"status": "ready"}
            assert response.headers["cache-control"] == "no-store"
    assert age.await_count == core.await_count == 3


def test_age_readiness_failure_is_sanitized_and_can_recover(
    offline_settings, monkeypatch, caplog,
):
    monkeypatch.setattr(api, "validate_runtime", AsyncMock())
    age = AsyncMock(side_effect=[
        RuntimeValidationError("extension_version_mismatch", PRIVATE), None,
    ])
    monkeypatch.setattr(api, "validate_age_runtime", age)
    client = TestClient(api.create_app(replace(offline_settings, graph_backend="age")))
    try:
        with caplog.at_level(logging.WARNING, logger="pg_agmemory"):
            unavailable = client.get("/readyz")
            recovered = client.get("/readyz")
        assert unavailable.status_code == 503
        assert unavailable.json() == {"status": "not_ready"}
        assert unavailable.headers["cache-control"] == "no-store"
        assert UUID(unavailable.headers["x-request-id"]).version == 4
        assert recovered.status_code == 200 and recovered.json() == {"status": "ready"}
        assert PRIVATE not in unavailable.text + str(unavailable.headers) + caplog.text
        assert all(record.exc_info is None for record in caplog.records)
        assert age.await_count == 2
    finally:
        client.close()


def test_graph_route_selects_only_the_explicitly_configured_adapter(
    offline_settings, monkeypatch,
):
    memory = Mock()
    monkeypatch.setattr(api, "service", Mock(return_value=memory))
    data = ExpandGraph(
        scope_ids=[uuid4()], seeds=[uuid4()], relation_types=["depends_on"],
        purpose="Offline router contract",
    )
    for backend in ("sql", "age"):
        sql_expand = AsyncMock(return_value={"selected": "sql"})
        age_expand = AsyncMock(return_value={"selected": "age"})
        sql_graph = Mock(return_value=Mock(expand=sql_expand))
        age_graph = Mock(return_value=Mock(expand=age_expand))
        monkeypatch.setattr(api, "SqlGraph", sql_graph)
        monkeypatch.setattr(api, "AgeGraph", age_graph)
        app = api.create_app(replace(offline_settings, graph_backend=backend))
        endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/graph/expand")
        assert asyncio.run(endpoint(data, Mock())) == {"selected": backend}
        selected, rejected = (
            (age_expand, sql_expand) if backend == "age" else (sql_expand, age_expand)
        )
        selected.assert_awaited_once_with(data)
        rejected.assert_not_called()
        (age_graph if backend == "age" else sql_graph).assert_called_once_with(memory)
        (sql_graph if backend == "age" else age_graph).assert_not_called()


@pytest.fixture(scope="session")
def age_api_profile(database):
    with psycopg.connect(database[0]) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS age")
        admin.execute("GRANT USAGE ON SCHEMA ag_catalog TO pgag_runtime")
        admin.execute("GRANT SELECT ON ag_catalog.ag_graph,ag_catalog.ag_label TO pgag_runtime")
        assert admin.execute("SELECT ag_catalog.pgag_age_preloaded()").fetchone() == (True,)


@pytest.fixture
def published_graph(env, artifact_dir, age_api_profile):
    graph = graph_fixture(env)
    graph.edge("second-hop", "target", "alternate")
    current, path = prepared(env, artifact_dir)
    result = publish(env, current, path)
    assert result.artifact_verified and result.serving_enabled
    return graph, result


def age_app(env):
    return api.create_app(replace(env.settings, graph_backend="age"))


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    assert response.json() == {
        "code": code, "request_id": request_id, "retryable": status == 503, "details": {},
    }
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE not in response.text + str(response.headers)


def forbid_sql_fallback(monkeypatch):
    expand = AsyncMock(side_effect=AssertionError("AGE must not fall back to SQL traversal"))
    monkeypatch.setattr(api.SqlGraph, "expand", expand)
    return expand


@LIVE
@pytest.mark.integration
def test_published_graph_http_result_matches_sql_and_reports_configured_age(
    env, published_graph,
):
    graph, published = published_graph
    request = graph.request(["source"], direction="both", max_paths=2)
    expected = graph.expand(request)
    with TestClient(age_app(env)) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
        capabilities = client.get("/v1/capabilities", headers=env.headers())
        assert capabilities.status_code == 200
        assert capabilities.json()["graph_backend"] == "age"
        response = client.post("/v1/graph/expand", headers=env.headers(), json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["backend"] == "age"
    assert result["projection_watermark"] == str(published.projection.generation_id)
    assert {**result, "backend": "sql", "projection_watermark": None} == expected
    assert response.headers["cache-control"] == "no-store"
    capabilities = env.client.get("/v1/capabilities", headers=env.headers())
    assert capabilities.json()["graph_backend"] == "sql"


@LIVE
@pytest.mark.integration
def test_disabled_projection_returns_http_409_without_sql_fallback(
    env, published_graph, monkeypatch,
):
    graph, published = published_graph
    request = graph.request(["source"])
    assert graph.expand(request)["paths"]
    operate(env, "disable", expected_revision=published.revision)
    fallback = forbid_sql_fallback(monkeypatch)
    with TestClient(age_app(env)) as client:
        response = client.post("/v1/graph/expand", headers=env.headers(), json=request)
        capabilities = client.get("/v1/capabilities", headers=env.headers())
        assert capabilities.json()["graph_backend"] == "age"
    assert_error(response, 409, "graph_projection_unavailable")
    fallback.assert_not_called()


@LIVE
@pytest.mark.integration
def test_new_canonical_input_returns_http_409_without_sql_fallback(
    env, published_graph, monkeypatch,
):
    graph, _ = published_graph
    graph.node("new-after-publication")
    request = graph.request(["source"])
    assert graph.expand(request)["paths"]
    fallback = forbid_sql_fallback(monkeypatch)
    with TestClient(age_app(env)) as client:
        response = client.post("/v1/graph/expand", headers=env.headers(), json=request)
    assert_error(response, 409, "graph_projection_stale")
    fallback.assert_not_called()


@LIVE
@pytest.mark.integration
def test_missing_projection_returns_http_409_even_when_sql_has_paths(
    env, age_api_profile, monkeypatch,
):
    graph = graph_fixture(env)
    request = graph.request(["source"])
    assert graph.expand(request)["paths"]
    fallback = forbid_sql_fallback(monkeypatch)
    with TestClient(age_app(env)) as client:
        response = client.post("/v1/graph/expand", headers=env.headers(), json=request)
    assert_error(response, 409, "graph_projection_unavailable")
    fallback.assert_not_called()


@contextmanager
def incorrect_build_stamp(admin_url):
    with psycopg.connect(admin_url) as admin:
        definition = admin.execute(
            "SELECT pg_get_functiondef('ag_catalog.pgag_age_build()'::regprocedure)"
        ).fetchone()[0]
        admin.execute(
            """CREATE OR REPLACE FUNCTION ag_catalog.pgag_age_build() RETURNS pg_catalog.jsonb
               LANGUAGE sql IMMUTABLE SECURITY INVOKER
               RETURN '{"commit":"AGE_API_PRIVATE_UNQUALIFIED_STAMP"}'::pg_catalog.jsonb"""
        )
    try:
        yield
    finally:
        with psycopg.connect(admin_url) as admin:
            admin.execute(definition)


@LIVE
@pytest.mark.integration
def test_wrong_stamp_rejects_startup_readiness_and_requests_without_leaking_details(
    env, published_graph, monkeypatch, caplog,
):
    graph, _ = published_graph
    request = graph.request(["source"])
    with TestClient(age_app(env)) as client:
        healthy = client.post("/v1/graph/expand", headers=env.headers(), json=request)
        assert healthy.status_code == 200, healthy.text
        fallback = forbid_sql_fallback(monkeypatch)
        with incorrect_build_stamp(env.admin_url):
            with pytest.raises(RuntimeValidationError) as startup:
                with TestClient(age_app(env)):
                    pytest.fail("Startup accepted an unqualified build stamp")
            assert startup.value.code == "extension_version_mismatch"
            assert PRIVATE not in str(startup.value)
            with caplog.at_level(logging.WARNING, logger="pg_agmemory"):
                readiness = client.get("/readyz")
                response = client.post("/v1/graph/expand", headers=env.headers(), json=request)
            assert readiness.status_code == 503 and readiness.json() == {"status": "not_ready"}
            assert_error(response, 503, "graph_backend_unqualified")
            assert PRIVATE not in readiness.text + str(readiness.headers) + caplog.text
            assert env.settings.database_url not in response.text + caplog.text
            with TestClient(api.create_app(env.settings)) as sql_client:
                assert sql_client.get("/readyz").json() == {"status": "ready"}
        assert client.get("/readyz").json() == {"status": "ready"}
        recovered = client.post("/v1/graph/expand", headers=env.headers(), json=request)
        assert recovered.status_code == 200 and recovered.json() == healthy.json()
        fallback.assert_not_called()
