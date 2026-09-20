import asyncio
import logging
import time
from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from pg_agmemory import api, database
from pg_agmemory.api import create_app
from pg_agmemory.database import RuntimeValidationError, Settings

PRIVATE = "DO_NOT_ECHO"


@pytest.fixture
def probe_app():
    return create_app(Settings(PRIVATE, "unused", "unused", "unused"))


def assert_probe(response, ready):
    assert response.status_code == (200 if ready else 503)
    assert response.json() == {"status": "ready" if ready else "not_ready"}
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4
    assert "www-authenticate" not in response.headers
    assert PRIVATE not in response.text + str(response.headers)


def test_probe_is_public_uncached_and_ignores_client_identity(probe_app, monkeypatch):
    calls = []

    async def validate(url):
        calls.append(url)

    monkeypatch.setattr(api, "validate_runtime", validate)
    client = TestClient(probe_app)
    try:
        first = client.get(
            "/readyz",
            headers={
                "Authorization": "Bearer " + PRIVATE,
                "X-Request-ID": PRIVATE,
            },
        )
        second = client.get("/readyz")
        assert_probe(first, True)
        assert_probe(second, True)
        assert first.headers["x-request-id"] != second.headers["x-request-id"]
        assert calls == [PRIVATE, PRIVATE]
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/v1/capabilities").status_code == 401
        assert calls == [PRIVATE, PRIVATE]
    finally:
        client.close()


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeValidationError("runtime_role_invalid", PRIVATE),
        RuntimeValidationError("schema_unavailable", PRIVATE),
        RuntimeValidationError("schema_version_mismatch", PRIVATE),
        RuntimeValidationError("extension_version_mismatch", PRIVATE),
        psycopg.OperationalError(PRIVATE),
        psycopg.errors.QueryCanceled(PRIVATE),
        psycopg.errors.InsufficientPrivilege(PRIVATE),
        psycopg.ProgrammingError(PRIVATE),
        TimeoutError(PRIVATE),
    ],
)
def test_expected_failures_are_sanitized_and_recover(probe_app, monkeypatch, caplog, failure):
    calls = 0

    async def validate(url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure

    monkeypatch.setattr(api, "validate_runtime", validate)
    with caplog.at_level(logging.WARNING, logger="pg_agmemory"):
        client = TestClient(probe_app)
        try:
            unavailable = client.get("/readyz")
            assert_probe(unavailable, False)
            assert_probe(client.get("/readyz"), True)
        finally:
            client.close()
    assert calls == 2
    assert PRIVATE not in caplog.text
    assert "readiness_unavailable" in caplog.text
    assert unavailable.headers["x-request-id"] in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_timeout_covers_entire_check_and_cleans_up(probe_app, monkeypatch):
    monkeypatch.setattr(api, "READINESS_TIMEOUT_SECONDS", 0.03)

    async def scenario():
        calls = 0
        closed = asyncio.Event()

        async def validate(url):
            nonlocal calls
            calls += 1
            if calls == 1:
                try:
                    for _ in range(20):
                        await asyncio.sleep(0.01)
                finally:
                    closed.set()

        monkeypatch.setattr(api, "validate_runtime", validate)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=probe_app), base_url="http://test"
        ) as client:
            assert_probe(await client.get("/readyz"), False)
            assert closed.is_set()
            assert_probe(await client.get("/readyz"), True)
        assert calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_busy_probe_never_queues_and_gate_releases(probe_app, monkeypatch, cancel):
    async def scenario():
        entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        async def validate(url):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                try:
                    await release.wait()
                finally:
                    closed.set()

        monkeypatch.setattr(api, "validate_runtime", validate)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=probe_app), base_url="http://test"
        ) as client:
            first = asyncio.create_task(client.get("/readyz"))
            await asyncio.wait_for(entered.wait(), 1)
            try:
                second = await asyncio.wait_for(client.get("/readyz"), 1)
                assert_probe(second, False)
                assert calls == 1 and not first.done()
                health = await asyncio.wait_for(client.get("/healthz"), 1)
                assert health.status_code == 200 and health.json() == {"status": "ok"}
                if cancel:
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                else:
                    release.set()
                    assert_probe(await first, True)
                assert closed.is_set()
                assert_probe(await client.get("/readyz"), True)
                assert calls == 2
            finally:
                release.set()
                await asyncio.gather(first, return_exceptions=True)

    asyncio.run(scenario())


def test_probe_limits_are_per_app_not_shared_globally(probe_app, monkeypatch):
    async def scenario():
        count = 0
        both, release = asyncio.Event(), asyncio.Event()

        async def validate(url):
            nonlocal count
            count += 1
            if count == 2:
                both.set()
            await release.wait()

        monkeypatch.setattr(api, "validate_runtime", validate)
        other = create_app(Settings(PRIVATE, "unused", "unused", "unused"))
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=probe_app), base_url="http://test"
            ) as first,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=other), base_url="http://test"
            ) as second,
        ):
            tasks = [asyncio.create_task(client.get("/readyz")) for client in (first, second)]
            try:
                await asyncio.wait_for(both.wait(), 1)
                assert count == 2
            finally:
                release.set()
                results = await asyncio.gather(*tasks)
            for result in results:
                assert_probe(result, True)

    asyncio.run(scenario())


def test_unexpected_programming_error_is_not_misreported_as_dependency_failure(
    probe_app, monkeypatch
):
    calls = 0

    async def validate(url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected programming failure")

    monkeypatch.setattr(api, "validate_runtime", validate)
    client = TestClient(probe_app)
    try:
        with pytest.raises(RuntimeError, match="unexpected programming failure"):
            client.get("/readyz")
        assert_probe(client.get("/readyz"), True)
    finally:
        client.close()


def test_readiness_openapi_and_method_contract(probe_app, monkeypatch):
    async def validate(url):
        pytest.fail("Method rejection must not reach the database")

    monkeypatch.setattr(api, "validate_runtime", validate)
    spec = probe_app.openapi()
    endpoint = spec["paths"]["/readyz"]
    assert set(endpoint) == {"get"}
    for status in ("200", "503"):
        assert endpoint["get"]["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ReadinessStatus",
        }
    assert spec["components"]["schemas"]["ReadinessStatus"]["additionalProperties"] is False
    client = TestClient(probe_app)
    try:
        assert client.post("/readyz").status_code == 405
    finally:
        client.close()


@pytest.mark.integration
def test_live_probe_uses_read_only_runtime_catalogs_without_identity_or_mutations(env, monkeypatch):
    queries = []
    original = psycopg.AsyncConnection.execute

    async def execute(conn, query, *args, **kwargs):
        queries.append(query)
        result = await original(conn, query, *args, **kwargs)
        if query == "SET default_transaction_read_only = on":
            cursor = await original(conn, "SHOW transaction_read_only")
            assert (await cursor.fetchone())["transaction_read_only"] == "on"
            cursor = await original(conn, "SELECT current_user AS role")
            assert (await cursor.fetchone())["role"] == conninfo_to_dict(env.settings.database_url)[
                "user"
            ]
        return result

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.AsyncConnection, "execute", execute)
        assert_probe(env.client.get("/readyz"), True)
    assert len(queries) == 4
    assert all(
        query.startswith(("SET default_transaction_read_only", "SELECT")) for query in queries
    )
    assert all("pg_advisory" not in query for query in queries)
    assert all("FROM memory." not in query and "FROM memory_ops." not in query for query in queries)
    assert not env.recall().json()["items"]
    with psycopg.connect(env.admin_url) as conn:
        assert (
            conn.execute(
                "SELECT access_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_ops.scope_access_event WHERE tenant_id=%s",
                (env.tenants[0],),
            ).fetchone()[0]
            == 0
        )
    caps = env.client.get("/v1/capabilities", headers=env.headers()).json()
    assert caps["stage"] == "m2-background-processing" and caps["schema_version"] == 15
    assert caps["health_probes"] == {
        "liveness": "/healthz",
        "readiness": "/readyz",
        "readiness_timeout_seconds": 5.0,
        "readiness_max_in_flight_per_process": 1,
    }
    assert env.observe().status_code == 201


@pytest.mark.integration
@pytest.mark.parametrize("attribute", ["BYPASSRLS", "SUPERUSER"])
def test_post_startup_runtime_role_drift_and_recovery(env, attribute):
    role = conninfo_to_dict(env.settings.database_url)["user"]
    with psycopg.connect(env.admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("ALTER ROLE {} {}").format(sql.Identifier(role), sql.SQL(attribute)))
        try:
            assert_probe(env.client.get("/readyz"), False)
            assert env.client.get("/healthz").json() == {"status": "ok"}
        finally:
            admin.execute(
                sql.SQL("ALTER ROLE {} {}").format(
                    sql.Identifier(role),
                    sql.SQL("NO" + attribute),
                )
            )
    assert_probe(env.client.get("/readyz"), True)


@pytest.mark.integration
@pytest.mark.parametrize("inherited", [False, True])
def test_runtime_ownership_drift_and_recovery(env, inherited):
    role = conninfo_to_dict(env.settings.database_url)["user"]
    owner = "probe_owner_" + uuid4().hex if inherited else role
    with psycopg.connect(env.admin_url, autocommit=True) as admin:
        if inherited:
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
            admin.execute(
                sql.SQL("GRANT {} TO {} WITH INHERIT FALSE").format(
                    sql.Identifier(owner),
                    sql.Identifier(role),
                )
            )
        admin.execute("CREATE TABLE memory.probe_owner_fixture(id integer)")
        try:
            admin.execute(
                sql.SQL("ALTER TABLE memory.probe_owner_fixture OWNER TO {}").format(
                    sql.Identifier(owner),
                )
            )
            assert_probe(env.client.get("/readyz"), False)
        finally:
            admin.execute("DROP TABLE memory.probe_owner_fixture")
            if inherited:
                admin.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(owner),
                        sql.Identifier(role),
                    )
                )
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(owner)))
    assert_probe(env.client.get("/readyz"), True)


@pytest.mark.integration
@pytest.mark.parametrize("drift", ["missing", "gap", "ahead", "privilege"])
def test_schema_drift_and_recovery_without_restarting(env, drift):
    with psycopg.connect(env.admin_url, autocommit=True) as admin:
        if drift == "missing":
            admin.execute("ALTER TABLE public.pgag_schema_migration RENAME TO probe_schema_fixture")
        elif drift == "gap":
            admin.execute("DELETE FROM public.pgag_schema_migration WHERE version=5")
        elif drift == "ahead":
            admin.execute(
                "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)",
                (database.SCHEMA_VERSION + 1,),
            )
        else:
            admin.execute("REVOKE SELECT ON public.pgag_schema_migration FROM pgag_runtime")
        try:
            assert_probe(env.client.get("/readyz"), False)
            assert env.client.get("/healthz").json() == {"status": "ok"}
        finally:
            if drift == "missing":
                admin.execute(
                    "ALTER TABLE public.probe_schema_fixture RENAME TO pgag_schema_migration"
                )
            elif drift == "gap":
                admin.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (5)")
            elif drift == "ahead":
                admin.execute(
                    "DELETE FROM public.pgag_schema_migration WHERE version=%s",
                    (database.SCHEMA_VERSION + 1,),
                )
            else:
                admin.execute("GRANT SELECT ON public.pgag_schema_migration TO pgag_runtime")
    assert_probe(env.client.get("/readyz"), True)


@pytest.mark.integration
@pytest.mark.parametrize("drift", ["version", "schema"])
def test_extension_drift_and_recovery_without_restarting(env, drift):
    with psycopg.connect(env.admin_url, autocommit=True) as admin:
        if drift == "version":
            admin.execute("UPDATE pg_extension SET extversion='0.0.0' WHERE extname='vector'")
        else:
            admin.execute("CREATE SCHEMA probe_vector_fixture")
            admin.execute("ALTER EXTENSION vector SET SCHEMA probe_vector_fixture")
        try:
            assert_probe(env.client.get("/readyz"), False)
        finally:
            if drift == "version":
                admin.execute("UPDATE pg_extension SET extversion='0.8.6' WHERE extname='vector'")
            else:
                admin.execute("ALTER EXTENSION vector SET SCHEMA public")
                admin.execute("DROP SCHEMA probe_vector_fixture")
    assert_probe(env.client.get("/readyz"), True)


@pytest.mark.integration
def test_real_connection_failure_and_recovery_keep_liveness(env, monkeypatch, caplog):
    original = database.connect
    broken = make_conninfo(
        **(
            conninfo_to_dict(env.settings.database_url)
            | {
                "host": "127.0.0.1",
                "port": "1",
                "password": PRIVATE,
            }
        )
    )

    async def connect(url):
        return await original(broken)

    with monkeypatch.context() as patch, caplog.at_level(logging.WARNING, logger="pg_agmemory"):
        patch.setattr(database, "connect", connect)
        assert_probe(env.client.get("/readyz"), False)
        assert env.client.get("/healthz").json() == {"status": "ok"}
    assert PRIVATE not in caplog.text and broken not in caplog.text
    assert_probe(env.client.get("/readyz"), True)


@pytest.mark.integration
def test_real_probe_timeout_releases_connection_and_recovers(env):
    with psycopg.connect(env.admin_url) as blocker:
        blocker.execute("LOCK TABLE public.pgag_schema_migration IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        assert_probe(env.client.get("/readyz"), False)
        assert 4.5 <= time.monotonic() - started < 10
        assert env.client.get("/healthz").json() == {"status": "ok"}
    role = conninfo_to_dict(env.settings.database_url)["user"]
    with psycopg.connect(env.admin_url, autocommit=True) as admin:
        for _ in range(50):
            if (
                admin.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE usename=%s",
                    (role,),
                ).fetchone()[0]
                == 0
            ):
                break
            time.sleep(0.02)
        else:
            pytest.fail("Readiness left a runtime connection behind")
    assert_probe(env.client.get("/readyz"), True)


@pytest.mark.integration
def test_startup_still_rejects_invalid_runtime_configuration(env):
    with pytest.raises(RuntimeValidationError, match="must not own tables"):
        with TestClient(create_app(replace(env.settings, database_url=env.admin_url))):
            pytest.fail("Unsafe runtime role passed startup validation")


@pytest.mark.integration
def test_probe_does_not_wait_for_tenant_drain_lock(env):
    with psycopg.connect(env.admin_url, autocommit=True) as blocker:
        blocker.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),))
        assert_probe(env.client.get("/readyz"), True)
