import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from importlib.resources import files
from uuid import UUID, uuid4

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

from pg_agmemory.api import create_app
from pg_agmemory.database import Settings, migrate, validate_runtime


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
    migrate(url)
    migrate(url)
    asyncio.run(validate_runtime(runtime_url))
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
