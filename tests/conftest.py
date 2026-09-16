import os
import secrets
import time
from dataclasses import dataclass
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

from pg_agmemory.api import create_app
from pg_agmemory.database import Settings, migrate


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
    migrate(url)
    migrate(url)
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
    yield url, make_conninfo(**params)
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.fixture
def env(database):
    admin_url, runtime_url = database
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
