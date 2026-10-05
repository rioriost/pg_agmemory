"""JWT dependency regressions and the Native authentication boundary, without a database."""

import base64
import json
import time
from contextlib import asynccontextmanager

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from pg_agmemory import api
from pg_agmemory.database import Settings
from pg_agmemory.service import MemoryError


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def claims():
    now = int(time.time())
    return {
        "sub": "synthetic-jwt-user", "iss": "https://issuer.test", "aud": "pgag-test",
        "iat": now - 10, "exp": now + 120,
    }


@pytest.fixture
def json_recursion_error(monkeypatch):
    original_loads = json.loads

    def loads(value, *args, **kwargs):
        # Parser depth limits vary by interpreter; exercise its exception explicitly.
        if isinstance(value, bytes) and b'"synthetic-recursion-boundary"' in value:
            raise RecursionError("synthetic JSON nesting limit")
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", loads)


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def signed_raw(signing_key, header, payload):
    message = f"{encoded(header)}.{encoded(payload)}"
    signature = signing_key.sign(message.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{message}.{encoded(signature)}"


@pytest.mark.parametrize("location", ["header", "payload"])
def test_jwt_parser_recursion_is_a_token_error(
    signing_key, location, json_recursion_error,
):
    nested = b'{"nested":"synthetic-recursion-boundary"}'
    header, payload = b'{"alg":"RS256"}', b"{}"
    if location == "header":
        header = nested
    else:
        payload = nested
    token = signed_raw(signing_key, header, payload)
    with pytest.raises(jwt.DecodeError, match=f"Invalid {location} string"):
        jwt.decode(token, signing_key.public_key(), algorithms=["RS256"])
    with pytest.raises(jwt.DecodeError, match=f"Invalid {location} string"):
        jwt.decode(token, options={"verify_signature": False})


def test_unverified_decode_does_not_mutate_reused_options(signing_key, claims):
    token = jwt.encode({**claims, "exp": claims["iat"]}, signing_key, algorithm="RS256")
    options = {"verify_signature": False}
    assert jwt.decode(token, options=options)["sub"] == claims["sub"]
    assert options == {"verify_signature": False}
    options["verify_signature"] = True
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(
            token, signing_key.public_key(), algorithms=["RS256"],
            issuer=claims["iss"], audience=claims["aud"], options=options,
        )
    assert options == {"verify_signature": True}


@pytest.mark.parametrize("mutation", [
    "valid", "expired", "issuer", "audience", "missing-exp", "hs256", "none",
    "parser-header", "parser-payload", "signature-junk",
])
def test_native_jwt_rejection_precedes_database_access(
    signing_key, claims, monkeypatch, mutation, json_recursion_error,
):
    public = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    settings = Settings("unused-database", public, claims["iss"], claims["aud"])
    database_subjects = []

    @asynccontextmanager
    async def unavailable_database(_url, subject, **_kwargs):
        database_subjects.append(subject)
        raise MemoryError("dependency_unavailable", 503)
        yield

    monkeypatch.setattr(api, "principal_connection", unavailable_database)
    payload = claims.copy()
    if mutation == "expired":
        payload["exp"] = payload["iat"]
    elif mutation in ("issuer", "audience"):
        payload["iss" if mutation == "issuer" else "aud"] = "untrusted"
    elif mutation == "missing-exp":
        del payload["exp"]
    token = jwt.encode(payload, signing_key, algorithm="RS256")
    if mutation == "hs256":
        token = jwt.encode(payload, "synthetic-hmac-secret-" * 4, algorithm="HS256")
    elif mutation == "none":
        token = jwt.encode(payload, "", algorithm="none")
    elif mutation.startswith("parser-"):
        nested = b'"synthetic-recursion-boundary"'
        header = b'{"alg":"RS256"}'
        body = json.dumps(payload).encode("ascii")
        if mutation == "parser-header":
            header = header[:-1] + b',"nested":' + nested + b"}"
        else:
            body = body[:-1] + b',"nested":' + nested + b"}"
        token = signed_raw(signing_key, header, body)
    elif mutation == "signature-junk":
        token += "!"
    assert len(token) < 16384
    client = TestClient(api.create_app(settings))
    try:
        response = client.post(
            "/v1/recall", json={}, headers={"Authorization": "Bearer " + token},
        )
    finally:
        client.close()
    if mutation == "valid":
        assert database_subjects == [claims["sub"]]
        assert response.status_code == 503
        assert response.json()["code"] == "dependency_unavailable"
    else:
        assert database_subjects == []
        assert response.status_code == 401
        assert response.json()["code"] == "unauthenticated"
        assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert token not in response.text
