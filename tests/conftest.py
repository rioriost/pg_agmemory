import asyncio
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pg_agmemory import database as database_module
from pg_agmemory import lexical
from pg_agmemory.api import create_app
from pg_agmemory.database import Settings, migrate, validate_runtime


@pytest.fixture
def live_provider():
    from pg_agmemory.providers import make_provider, parse_settings

    path = os.environ.get("PGAG_LIVE_PROVIDER_CONFIG")
    if not path:
        pytest.skip("Set PGAG_LIVE_PROVIDER_CONFIG only for authorized real inference")
    with open(path, "rb") as stream:
        return make_provider(parse_settings(stream.read(32769)))


@pytest.fixture
def lose_first_response_transport():
    class LoseFirstResponse(httpx.AsyncHTTPTransport):
        calls = 0
        committed_response = None

        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            self.calls += 1
            if self.calls == 1:
                await response.aread()
                assert response.status_code == 201
                self.committed_response = response.json()
                await response.aclose()
                raise httpx.ReadError("simulated post-commit disconnect")
            return response

    return LoseFirstResponse()


@dataclass
class Environment:
    client: TestClient
    settings: Settings
    admin_url: str
    private_key: bytes
    tenants: list[UUID]
    principals: list[UUID]
    scopes: list[UUID]
    subjects: list[str]

    def token(self, index=0, **overrides):
        now = int(time.time())
        return jwt.encode(
            {
                "sub": self.subjects[index],
                "iss": "https://issuer.test",
                "aud": "pgag-test",
                "iat": now,
                "exp": now + 300,
                **overrides,
            },
            self.private_key,
            algorithm="RS256",
        )

    def headers(self, index=0, key=None):
        return {
            "Authorization": f"Bearer {self.token(index)}",
            "Idempotency-Key": key or str(uuid4()),
        }

    def observe(self, content="ACME contract is Gold", index=0, **overrides):
        body = {
            "scope_id": str(self.scopes[index]),
            "source_namespace": "test",
            "source_event_id": str(uuid4()),
            "occurred_at": "2026-09-01T00:00:00Z",
            "content": content,
            "consent_reference": "test-consent",
            **overrides,
        }
        return self.client.post("/v1/observe", json=body, headers=self.headers(index))

    def remember(self, source, index=0, **overrides):
        return self.client.post(
            "/v1/remember",
            json={
                "scope_id": str(self.scopes[index]),
                "subject": "ACME",
                "predicate": "contract_tier",
                "value": "Gold",
                "explicit_intent": True,
                "evidence": [{"memory_id": source, "quote": "Gold"}],
                **overrides,
            },
            headers=self.headers(index),
        )

    def recall(self, index=0, **overrides):
        return self.client.post(
            "/v1/recall",
            json={
                "scope_ids": [str(self.scopes[index])],
                "purpose": "test",
                "query": "",
                "token_budget": 8000,
                **overrides,
            },
            headers=self.headers(index),
        )


@pytest.fixture(scope="session")
def database():
    url = os.environ.get("PGAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PGAG_TEST_DATABASE_URL must identify a disposable PostgreSQL database")
    legacy = seed_legacy_database(url)
    role = "pgag_test_" + uuid4().hex
    password = secrets.token_urlsafe(32)
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE pgag_runtime").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    params = conninfo_to_dict(url)
    params.update(user=role, password=password)
    runtime_url = make_conninfo(**params)
    with pytest.raises(RuntimeError, match="run pg-agmemory migrate"):
        asyncio.run(validate_runtime(runtime_url))
    with psycopg.connect(url) as admin:
        admin.execute(
            files("pg_agmemory").joinpath("storage/002_assertion_revisions.sql").read_text()
        )
        admin.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (2)")
        record = legacy[0]
        admin.execute(
            """INSERT INTO memory.assertion_revision
               (tenant_id,assertion_id,scope_id,revision,value,valid_time,
                explicit_intent,correction_reason)
               VALUES (%s,%s,%s,2,'Gold corrected','[2026-09-05,)',true,'v2 upgrade fixture')""",
            (record["tenant"], record["assertion"], record["scope"]),
        )
        admin.execute(
            """INSERT INTO memory.provenance_edge
               (tenant_id,child_id,child_revision,parent_id,scope_id,quote)
               VALUES (%s,%s,2,%s,%s,'Gold')""",
            (record["tenant"], record["assertion"], record["source"], record["scope"]),
        )
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    seed_v3_checkpoint(url, legacy[0])
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    seed_v4_effect(url, legacy[0])
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    seed_v5_graph(url, legacy[0])
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    seed_v6_job(url, legacy[0])
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    with pytest.MonkeyPatch.context() as patch:

        def fail_after_backfill(conn):
            lexical.rebuild(conn)
            raise RuntimeError("simulated backfill failure")

        patch.setattr("pg_agmemory.database.rebuild", fail_after_backfill)
        with pytest.raises(RuntimeError, match="simulated backfill failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert admin.execute("SELECT to_regclass('memory.episode_lexical')").fetchone()[0] is None
        assert (
            admin.execute("SELECT max(version) FROM public.pgag_schema_migration").fetchone()[0]
            == 6
        )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:7])
        migrate(url)
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    execute = psycopg.Connection.execute
    with pytest.MonkeyPatch.context() as patch:

        def fail_vector_ledger(self, query, params=None, **kwargs):
            if (
                query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)"
                and params == (8,)
            ):
                raise RuntimeError("simulated schema 8 ledger failure")
            return execute(self, query, params, **kwargs)

        patch.setattr(psycopg.Connection, "execute", fail_vector_ledger)
        with pytest.raises(RuntimeError, match="schema 8 ledger failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert admin.execute("SELECT to_regclass('memory.episode_embedding')").fetchone()[0] is None
        assert (
            admin.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
            is None
        )
        assert (
            admin.execute("SELECT max(version) FROM public.pgag_schema_migration").fetchone()[0]
            == 7
        )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:8])
        migrate(url)
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    with pytest.MonkeyPatch.context() as patch:

        def fail_access_ledger(self, query, params=None, **kwargs):
            if (
                query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)"
                and params == (9,)
            ):
                raise RuntimeError("simulated schema 9 ledger failure")
            return execute(self, query, params, **kwargs)

        patch.setattr(psycopg.Connection, "execute", fail_access_ledger)
        with pytest.raises(RuntimeError, match="schema 9 ledger failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert (
            admin.execute("SELECT to_regclass('memory_ops.scope_access_event')").fetchone()[0]
            is None
        )
        assert (
            admin.execute("SELECT max(version) FROM public.pgag_schema_migration").fetchone()[0]
            == 8
        )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:9])
        migrate(url)
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    with pytest.MonkeyPatch.context() as patch:

        def fail_cancel_ledger(self, query, params=None, **kwargs):
            if (
                query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)"
                and params == (10,)
            ):
                raise RuntimeError("simulated schema 10 ledger failure")
            return execute(self, query, params, **kwargs)

        patch.setattr(psycopg.Connection, "execute", fail_cancel_ledger)
        with pytest.raises(RuntimeError, match="schema 10 ledger failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert (
            admin.execute("SELECT max(version) FROM public.pgag_schema_migration").fetchone()[0]
            == 9
        )
        assert (
            "cancelled"
            not in admin.execute(
                "SELECT pg_get_functiondef('memory.guard_job()'::regprocedure)"
            ).fetchone()[0]
        )
        assert (
            admin.execute(
                "SELECT count(*) FROM pg_constraint WHERE conrelid='memory_ops.job'::regclass "
                "AND conname='job_payload_state_check'"
            ).fetchone()[0]
            == 0
        )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:10])
        migrate(url)
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        asyncio.run(validate_runtime(runtime_url))
    with pytest.MonkeyPatch.context() as patch:

        def fail_capture_policy_ledger(self, query, params=None, **kwargs):
            if (
                query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)"
                and params == (11,)
            ):
                raise RuntimeError("simulated schema 11 ledger failure")
            return execute(self, query, params, **kwargs)

        patch.setattr(psycopg.Connection, "execute", fail_capture_policy_ledger)
        with pytest.raises(RuntimeError, match="schema 11 ledger failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert admin.execute(
            "SELECT to_regclass('memory.scope_capture_policy'),"
            "to_regclass('memory_ops.capture_policy_event')"
        ).fetchone() == (None, None)
        assert (
            admin.execute("SELECT max(version) FROM public.pgag_schema_migration").fetchone()[0]
            == 10
        )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:11])
        migrate(url)
    for version, table in (
        (12, "memory.scope_synthesis_policy"),
        (13, "memory.working_event"),
        (14, "memory_ops.deletion_target"),
    ):
        if version == 14:
            with psycopg.connect(url) as admin:
                for record in legacy:
                    record["legacy_deletion_id"] = uuid4()
                    admin.execute(
                        """INSERT INTO memory_ops.deletion_request
                           (tenant_id,id,principal_id,mode,state,object_count,deletion_epoch)
                           VALUES (%s,%s,%s,'suppress','blocked_for_reads',1,2)""",
                        (record["tenant"], record["legacy_deletion_id"], record["principal"]),
                    )
                    admin.execute(
                        "UPDATE memory.tenant SET deletion_epoch=2 WHERE id=%s",
                        (record["tenant"],),
                    )
        with pytest.MonkeyPatch.context() as patch:
            def fail_processing_ledger(self, query, params=None, _version=version, **kwargs):
                if (
                    query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)"
                    and params == (_version,)
                ):
                    raise RuntimeError("simulated processing migration failure")
                return execute(self, query, params, **kwargs)

            patch.setattr(psycopg.Connection, "execute", fail_processing_ledger)
            with pytest.raises(RuntimeError, match="simulated processing migration failure"):
                migrate(url)
        with psycopg.connect(url) as admin:
            assert admin.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None
            if version == 14:
                assert admin.execute(
                    """SELECT count(*) FROM pg_attribute
                       WHERE attrelid='memory_ops.deletion_request'::regclass
                       AND attname='target_manifest_version' AND NOT attisdropped"""
                ).fetchone()[0] == 0
            if version == 13:
                assert admin.execute(
                    """SELECT count(*) FROM pg_attribute
                       WHERE attrelid='memory_ops.extraction_candidate'::regclass
                       AND attname='adopted_assertion_id' AND NOT attisdropped"""
                ).fetchone()[0] == 0
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:version])
            migrate(url)
    with pytest.MonkeyPatch.context() as patch:
        def fail_restore_ledger(self, query, params=None, **kwargs):
            if query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)" \
                    and params == (15,):
                raise RuntimeError("simulated restore migration failure")
            return execute(self, query, params, **kwargs)
        patch.setattr(psycopg.Connection, "execute", fail_restore_ledger)
        with pytest.raises(RuntimeError, match="simulated restore migration failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert admin.execute(
            "SELECT to_regprocedure('memory.recovery_apply_authorized()')"
        ).fetchone()[0] is None
        assert admin.execute("SELECT to_regclass('memory_ops.recovery_key')").fetchone()[0] is None
        assert admin.execute(
            "SELECT max(version) FROM public.pgag_schema_migration"
        ).fetchone()[0] == 14
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:15])
        migrate(url)
    with pytest.MonkeyPatch.context() as patch:
        def fail_visibility_ledger(self, query, params=None, **kwargs):
            if query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)" \
                    and params == (16,):
                raise RuntimeError("simulated visibility migration failure")
            return execute(self, query, params, **kwargs)
        patch.setattr(psycopg.Connection, "execute", fail_visibility_ledger)
        with pytest.raises(RuntimeError, match="simulated visibility migration failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert admin.execute(
            "SELECT max(version) FROM public.pgag_schema_migration"
        ).fetchone()[0] == 15
        expression = admin.execute(
            """SELECT pg_get_expr(polqual,polrelid) FROM pg_policy
               WHERE polrelid='memory.object'::regclass AND polname='object_read'"""
        ).fetchone()[0]
        assert "memory.permitted" in expression
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:16])
        migrate(url)
    with psycopg.connect(url) as admin:
        expression = admin.execute(
            """SELECT pg_get_expr(polqual,polrelid) FROM pg_policy
               WHERE polrelid='memory.object'::regclass AND polname='object_read'"""
        ).fetchone()[0]
    with pytest.MonkeyPatch.context() as patch:
        def fail_tombstone_set_ledger(self, query, params=None, **kwargs):
            if query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)" \
                    and params == (17,):
                raise RuntimeError("simulated tombstone set migration failure")
            return execute(self, query, params, **kwargs)
        patch.setattr(psycopg.Connection, "execute", fail_tombstone_set_ledger)
        with pytest.raises(RuntimeError, match="simulated tombstone set migration failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert admin.execute(
            "SELECT max(version) FROM public.pgag_schema_migration"
        ).fetchone()[0] == 16
        assert admin.execute(
            """SELECT pg_get_expr(polqual,polrelid) FROM pg_policy
               WHERE polrelid='memory.object'::regclass AND polname='object_read'"""
        ).fetchone()[0] == expression
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", database_module.MIGRATIONS[:17])
        migrate(url)
    with pytest.MonkeyPatch.context() as patch:
        def fail_tombstone_read_ledger(self, query, params=None, **kwargs):
            if query == "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)" \
                    and params == (18,):
                raise RuntimeError("simulated tombstone read migration failure")
            return execute(self, query, params, **kwargs)
        patch.setattr(psycopg.Connection, "execute", fail_tombstone_read_ledger)
        with pytest.raises(RuntimeError, match="simulated tombstone read migration failure"):
            migrate(url)
    with psycopg.connect(url) as admin:
        assert admin.execute(
            "SELECT max(version) FROM public.pgag_schema_migration"
        ).fetchone()[0] == 17
        assert "memory.permitted" in admin.execute(
            """SELECT pg_get_expr(polqual,polrelid) FROM pg_policy
               WHERE polrelid='memory_ops.object_tombstone'::regclass
                 AND polname='tombstone_read'"""
        ).fetchone()[0]
    migrate(url)
    asyncio.run(validate_runtime(runtime_url))
    with psycopg.connect(url) as admin:
        assert admin.execute(
            "SELECT state,attempt,payload FROM memory_ops.job WHERE id=%s",
            (legacy[0]["job"]["result"]["job_id"],),
        ).fetchone() == ("pending", 0, legacy[0]["job"]["body"]["memory"])
    yield url, runtime_url, legacy
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.fixture
def env(database):
    admin_url, runtime_url, _ = database
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    tenants = [uuid4(), uuid4()]
    principals = [uuid4(), uuid4(), uuid4()]
    scopes = [uuid4(), uuid4(), uuid4()]
    subjects = [str(uuid4()) for _ in principals]
    with psycopg.connect(admin_url, row_factory=dict_row) as admin:
        for tenant in tenants:
            admin.execute(
                "INSERT INTO memory.tenant(id, dedup_secret) VALUES (%s, %s)",
                (tenant, secrets.token_bytes(32)),
            )
        for i, principal in enumerate(principals):
            tenant = tenants[i] if i < 2 else tenants[0]
            admin.execute(
                "INSERT INTO memory.principal(tenant_id, id, external_subject) VALUES (%s, %s, %s)",
                (tenant, principal, subjects[i]),
            )
        for i, scope in enumerate(scopes):
            tenant = tenants[i] if i < 2 else tenants[0]
            admin.execute("INSERT INTO memory.scope VALUES (%s, %s)", (tenant, scope))
            owner = principals[i] if i < 2 else principals[2]
            admin.execute(
                """INSERT INTO memory.scope_member(tenant_id, scope_id, principal_id, permissions)
                   VALUES (%s, %s, %s, %s)""",
                (tenant, scope, owner, ["read", "write", "delete"]),
            )
    settings = Settings(runtime_url, public, "https://issuer.test", "pgag-test")
    with TestClient(create_app(settings)) as client:
        yield Environment(
            client, settings, admin_url, private, tenants, principals, scopes, subjects
        )


def seed_legacy_database(url):
    records = []
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT to_regnamespace('memory')").fetchone()[0] is not None:
            pytest.fail("Integration tests require a fresh disposable database")
        conn.execute(files("pg_agmemory").joinpath("storage/001_initial.sql").read_text())
        conn.execute(
            """CREATE TABLE public.pgag_schema_migration
               (version integer PRIMARY KEY, applied_at timestamptz DEFAULT clock_timestamp())"""
        )
        conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (1)")
        for _ in range(2):
            tenant, principal, scope, source, assertion, deleted = [uuid4() for _ in range(6)]
            secret = secrets.token_bytes(32)
            subject = str(uuid4())
            conn.execute(
                "INSERT INTO memory.tenant(id, dedup_secret) VALUES (%s,%s)", (tenant, secret)
            )
            conn.execute(
                "INSERT INTO memory.principal VALUES (%s,%s,%s)", (tenant, principal, subject)
            )
            conn.execute("INSERT INTO memory.scope VALUES (%s,%s)", (tenant, scope))
            conn.execute(
                """INSERT INTO memory.scope_member
                   (tenant_id,scope_id,principal_id,permissions)
                   VALUES (%s,%s,%s,ARRAY['read','write','delete'])""",
                (tenant, scope, principal),
            )
            for object_id, kind in [
                (source, "episode"),
                (assertion, "assertion"),
                (deleted, "episode"),
            ]:
                conn.execute(
                    """INSERT INTO memory.object(tenant_id,id,scope_id,kind,created_at)
                       VALUES (%s,%s,%s,%s,'2026-09-10T00:00:00Z')""",
                    (tenant, object_id, scope, kind),
                )
            conn.execute(
                """INSERT INTO memory.episode
                   (tenant_id,id,scope_id,occurred_at,content,consent_reference)
                   VALUES (%s,%s,%s,'2026-09-01T00:00:00Z','ACME Gold','legacy-consent')""",
                (tenant, source, scope),
            )
            conn.execute(
                """INSERT INTO memory.assertion
                   (tenant_id,id,scope_id,subject,predicate,value,valid_time,system_time,
                    explicit_intent)
                   VALUES (%s,%s,%s,'ACME','contract_tier','Gold',
                           '[2026-09-01,2026-10-01)','[2026-09-10,)',true)""",
                (tenant, assertion, scope),
            )
            conn.execute(
                """INSERT INTO memory.provenance_edge
                   (tenant_id,child_id,parent_id,scope_id,quote) VALUES (%s,%s,%s,%s,'Gold')""",
                (tenant, assertion, source, scope),
            )
            conn.execute(
                """INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id)
                   VALUES (%s,%s,%s)""",
                (tenant, deleted, scope),
            )
            # Preserve the v0.0.1 normalized field order for idempotency compatibility.
            payload = {
                "scope_id": str(scope),
                "subject": "ACME",
                "predicate": "contract_tier",
                "value": "Gold",
                "evidence": [{"memory_id": str(source), "quote": "Gold"}],
                "explicit_intent": True,
                "valid_from": "2026-09-01T00:00:00Z",
                "valid_to": "2026-10-01T00:00:00Z",
            }
            key = "legacy-remember"
            digest = hmac.new(
                secret, json.dumps(payload, separators=(",", ":")).encode(), hashlib.sha256
            ).hexdigest()
            result = {"memory_id": str(assertion), "revision": 1, "epistemic_status": "reported"}
            conn.execute(
                """INSERT INTO memory_ops.idempotency
                   (tenant_id,principal_id,operation,key_digest,request_digest,result)
                   VALUES (%s,%s,'remember',%s,%s,%s)""",
                (
                    tenant,
                    principal,
                    hmac.new(secret, key.encode(), hashlib.sha256).hexdigest(),
                    digest,
                    Jsonb(result),
                ),
            )
            records.append(
                {
                    "tenant": tenant,
                    "principal": principal,
                    "scope": scope,
                    "source": source,
                    "assertion": assertion,
                    "deleted": deleted,
                    "subject": subject,
                    "payload": payload,
                    "result": result,
                    "key": key,
                }
            )
    return records


def seed_v3_checkpoint(url, record):
    checkpoint, run, branch, operation = [uuid4() for _ in range(4)]
    state = {
        "goal": "Legacy v3 checkpoint",
        "constraints": [],
        "completed_actions": [],
        "decisions": [],
        "unresolved_questions": [],
        "next_actions": [],
        "pending_effects": [
            {
                "operation_id": str(operation),
                "description": "Legacy planned hint",
                "status": "planned",
            }
        ],
    }
    payload = {
        "checkpoint_id": str(checkpoint),
        "scope_id": str(record["scope"]),
        "run_id": str(run),
        "branch_id": str(branch),
        "sequence": 1,
        "parent_checkpoint": None,
        "harness_id": "legacy-harness",
        "harness_version": "1.0",
        "state_schema_version": 1,
        "event_watermark": 10,
        "state": state,
        "memory_refs": [{"memory_id": str(record["assertion"]), "revision": 1}],
        "saved_access_epoch": 1,
        "saved_deletion_epoch": 1,
    }
    with psycopg.connect(url) as conn:
        conn.execute(files("pg_agmemory").joinpath("storage/003_checkpoints.sql").read_text())
        conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (3)")
        secret = conn.execute(
            "SELECT dedup_secret FROM memory.tenant WHERE id = %s", (record["tenant"],)
        ).fetchone()[0]
        checksum = hmac.new(
            secret,
            (
                "checkpoint-envelope-v1:"
                + json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        conn.execute(
            """INSERT INTO memory.checkpoint_run
               (tenant_id,scope_id,run_id,harness_id,harness_version,state_schema_version)
               VALUES (%s,%s,%s,'legacy-harness','1.0',1)""",
            (record["tenant"], record["scope"], run),
        )
        conn.execute(
            """INSERT INTO memory.checkpoint_branch(tenant_id,scope_id,run_id,branch_id)
               VALUES (%s,%s,%s,%s)""",
            (record["tenant"], record["scope"], run, branch),
        )
        conn.execute(
            """INSERT INTO memory.object(tenant_id,id,scope_id,kind)
               VALUES (%s,%s,%s,'checkpoint')""",
            (record["tenant"], checkpoint, record["scope"]),
        )
        conn.execute(
            """INSERT INTO memory.checkpoint
               (tenant_id,id,scope_id,run_id,branch_id,sequence,state,event_watermark,
                reference_count,access_epoch,deletion_epoch,checksum)
               VALUES (%s,%s,%s,%s,%s,1,%s,10,1,1,1,%s)""",
            (record["tenant"], checkpoint, record["scope"], run, branch, Jsonb(state), checksum),
        )
        conn.execute(
            """INSERT INTO memory.checkpoint_reference
               (tenant_id,checkpoint_id,scope_id,source_id,source_revision,source_kind)
               VALUES (%s,%s,%s,%s,1,'assertion')""",
            (record["tenant"], checkpoint, record["scope"], record["assertion"]),
        )
    record["checkpoint"] = payload
    record["checksum"] = checksum


def seed_v4_effect(url, record):
    effect, operation = uuid4(), uuid4()
    payload = {
        "scope_id": str(record["scope"]),
        "run_id": record["checkpoint"]["run_id"],
        "operation_id": str(operation),
        "tool_name": "legacy.send",
        "action_hash": "b" * 64,
        "memory_refs": [{"memory_id": str(record["assertion"]), "revision": 1}],
    }
    result = {"memory_id": str(effect), "revision": 1, "status": "planned"}
    key = "legacy-plan-effect"
    with psycopg.connect(url) as conn:
        conn.execute(files("pg_agmemory").joinpath("storage/004_tool_effects.sql").read_text())
        conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (4)")
        conn.execute(
            """SELECT set_config('pgag.tenant_id',%s,true),
                      set_config('pgag.principal_id',%s,true)""",
            (str(record["tenant"]), str(record["principal"])),
        )
        secret = conn.execute(
            "SELECT dedup_secret FROM memory.tenant WHERE id = %s", (record["tenant"],)
        ).fetchone()[0]

        def digest(value):
            return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()

        request_digest = digest(json.dumps(payload, separators=(",", ":")))
        fingerprint = digest("effect-action-v1:" + payload["action_hash"])
        external_key = digest(
            "effect-dispatch-v1:"
            + json.dumps([payload["scope_id"], payload["run_id"], str(operation), fingerprint])
        )
        conn.execute(
            "INSERT INTO memory.object(tenant_id,id,scope_id,kind) VALUES (%s,%s,%s,'tool_effect')",
            (record["tenant"], effect, record["scope"]),
        )
        conn.execute(
            """INSERT INTO memory.tool_effect
               (tenant_id,id,scope_id,run_id,operation_id,tool_name,
                action_fingerprint,external_idempotency_key,reference_count)
               VALUES (%s,%s,%s,%s,%s,'legacy.send',%s,%s,1)""",
            (
                record["tenant"],
                effect,
                record["scope"],
                payload["run_id"],
                operation,
                fingerprint,
                external_key,
            ),
        )
        conn.execute(
            """INSERT INTO memory.tool_effect_reference
               (tenant_id,effect_id,scope_id,source_id,source_revision,source_kind)
               VALUES (%s,%s,%s,%s,1,'assertion')""",
            (record["tenant"], effect, record["scope"], record["assertion"]),
        )
        conn.execute(
            """INSERT INTO memory_ops.tool_effect_identity
               (tenant_id,scope_id,run_id,operation_id,effect_id,request_digest)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                record["tenant"],
                record["scope"],
                payload["run_id"],
                operation,
                effect,
                request_digest,
            ),
        )
        for revision, status in [(1, "planned"), (2, "dispatched")]:
            conn.execute(
                """INSERT INTO memory.tool_effect_revision
                   (tenant_id,effect_id,revision,status,reason,origin)
                   VALUES (%s,%s,%s,%s,'Legacy v4 event','api')""",
                (record["tenant"], effect, revision, status),
            )
        conn.execute(
            """INSERT INTO memory_ops.idempotency
               (tenant_id,principal_id,operation,key_digest,request_digest,result)
               VALUES (%s,%s,'plan_tool_effect',%s,%s,%s)""",
            (record["tenant"], record["principal"], digest(key), request_digest, Jsonb(result)),
        )
    record["effect"] = {
        "payload": payload,
        "result": result,
        "key": key,
        "external_key": external_key,
        "fingerprint": fingerprint,
    }


def seed_v5_graph(url, record):
    source, target, relation = [uuid4() for _ in range(3)]
    with psycopg.connect(url) as conn:
        conn.execute(files("pg_agmemory").joinpath("storage/005_relational_graph.sql").read_text())
        conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (5)")
        for entity_id, label in [(source, "ACME"), (target, "Gold")]:
            conn.execute(
                "INSERT INTO memory.object(tenant_id,id,scope_id,kind) VALUES (%s,%s,%s,'entity')",
                (record["tenant"], entity_id, record["scope"]),
            )
            conn.execute(
                """INSERT INTO memory.entity
                   (tenant_id,id,scope_id,entity_type,canonical_label,reference_count,explicit_intent)
                   VALUES (%s,%s,%s,'organization',%s,1,true)""",
                (record["tenant"], entity_id, record["scope"], label),
            )
            conn.execute(
                """INSERT INTO memory.entity_evidence(tenant_id,entity_id,scope_id,source_id,quote)
                   VALUES (%s,%s,%s,%s,%s)""",
                (record["tenant"], entity_id, record["scope"], record["source"], label),
            )
        conn.execute(
            "INSERT INTO memory.object(tenant_id,id,scope_id,kind) VALUES (%s,%s,%s,'assertion')",
            (record["tenant"], relation, record["scope"]),
        )
        conn.execute(
            """INSERT INTO memory.assertion(tenant_id,id,scope_id,subject,predicate,is_relation)
               VALUES (%s,%s,%s,'ACME','depends_on',true)""",
            (record["tenant"], relation, record["scope"]),
        )
        conn.execute(
            "INSERT INTO memory.relation(tenant_id,id,scope_id,source_id) VALUES (%s,%s,%s,%s)",
            (record["tenant"], relation, record["scope"], source),
        )
        conn.execute(
            """INSERT INTO memory.assertion_revision
               (tenant_id,assertion_id,scope_id,revision,value,valid_time,explicit_intent)
               VALUES (%s,%s,%s,1,'Gold','(,)',true)""",
            (record["tenant"], relation, record["scope"]),
        )
        conn.execute(
            """INSERT INTO memory.provenance_edge
               (tenant_id,child_id,child_revision,parent_id,scope_id,quote)
               VALUES (%s,%s,1,%s,%s,'Gold')""",
            (record["tenant"], relation, record["source"], record["scope"]),
        )
        conn.execute(
            """INSERT INTO memory.relation_revision
               (tenant_id,assertion_id,scope_id,revision,target_id) VALUES (%s,%s,%s,1,%s)""",
            (record["tenant"], relation, record["scope"], target),
        )
    record["graph"] = {"source": source, "target": target, "relation": relation}


def seed_v6_job(url, record):
    source, job = uuid4(), uuid4()
    key = str(uuid4())
    memory = {
        "scope_id": str(record["scope"]),
        "subject": "東京都",
        "predicate": "contract_tier",
        "value": "Gold",
        "evidence": [{"memory_id": str(source), "quote": "Gold"}],
        "explicit_intent": True,
        "valid_from": None,
        "valid_to": None,
    }
    body = {"kind": "structured_remember", "memory": memory}
    result = {
        "job_id": str(job),
        "kind": "structured_remember",
        "recipe_version": "structured-remember-v1",
    }
    with psycopg.connect(url) as conn:
        conn.execute(files("pg_agmemory").joinpath("storage/006_durable_jobs.sql").read_text())
        conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (6)")
        secret = conn.execute(
            "SELECT dedup_secret FROM memory.tenant WHERE id = %s", (record["tenant"],)
        ).fetchone()[0]

        def digest(value):
            return hmac.new(bytes(secret), value.encode(), hashlib.sha256).hexdigest()

        intent = digest("structured-remember-v1:" + json.dumps(memory, sort_keys=True))
        identity = digest("job-identity-v1:" + intent + ":None")
        request = digest(json.dumps({"retry_of": None, "request": body}, sort_keys=True))
        for object_id, kind in ((source, "episode"), (job, "job")):
            conn.execute(
                """INSERT INTO memory.object(tenant_id,id,scope_id,kind)
                   VALUES (%s,%s,%s,%s)""",
                (record["tenant"], object_id, record["scope"], kind),
            )
        conn.execute(
            """INSERT INTO memory.episode
               (tenant_id,id,scope_id,occurred_at,content,consent_reference)
               VALUES (%s,%s,%s,'2026-09-01','東京都の契約はGoldです。','v6-consent')""",
            (record["tenant"], source, record["scope"]),
        )
        conn.execute(
            """INSERT INTO memory_ops.job
               (tenant_id,id,scope_id,principal_id,kind,recipe_version,intent_digest,payload,
                reference_count,captured_access_epoch,captured_deletion_epoch)
               VALUES (%s,%s,%s,%s,'structured_remember','structured-remember-v1',%s,%s,1,1,1)""",
            (
                record["tenant"],
                job,
                record["scope"],
                record["principal"],
                intent,
                Jsonb(memory),
            ),
        )
        conn.execute(
            """INSERT INTO memory_ops.job_input(tenant_id,job_id,scope_id,source_id)
               VALUES (%s,%s,%s,%s)""",
            (record["tenant"], job, record["scope"], source),
        )
        conn.execute(
            """INSERT INTO memory_ops.job_identity
               (tenant_id,scope_id,principal_id,input_digest,job_id) VALUES (%s,%s,%s,%s,%s)""",
            (record["tenant"], record["scope"], record["principal"], identity, job),
        )
        conn.execute(
            """INSERT INTO memory_ops.idempotency
               (tenant_id,principal_id,operation,key_digest,request_digest,result)
               VALUES (%s,%s,'enqueue_job',%s,%s,%s)""",
            (record["tenant"], record["principal"], digest(key), request, Jsonb(result)),
        )
    record["job"] = {"body": body, "key": key, "result": result, "source": source}


@pytest.fixture
def api_process(env, tmp_path):
    @contextmanager
    def start(name):
        log_path = tmp_path / name
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        settings = {
            key: value
            for key, value in os.environ.items()
            if key not in ("PGAG_TEST_DATABASE_URL", "PGAG_ADMIN_DATABASE_URL")
        } | {
            "PGAG_DATABASE_URL": env.settings.database_url,
            "PGAG_JWT_PUBLIC_KEY": env.settings.jwt_public_key,
            "PGAG_JWT_ISSUER": env.settings.jwt_issuer,
            "PGAG_JWT_AUDIENCE": env.settings.jwt_audience,
        }
        with log_path.open("w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "pg_agmemory.api:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                env=settings,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
                    for _ in range(100):
                        if process.poll() is not None:
                            pytest.fail("API startup failed: " + log_path.read_text())
                        try:
                            if client.get("/healthz").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    else:
                        pytest.fail("API did not become ready: " + log_path.read_text())
                    yield client, process
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)

    return start
