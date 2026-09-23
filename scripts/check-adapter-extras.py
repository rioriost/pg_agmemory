import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from importlib.resources import files
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pg_agmemory.api import create_app
from pg_agmemory.source_access import MAX_SOURCE_LEASE_SECONDS, SourceIdentity, SourceNotice
from pg_agmemory.source_dataset import MAX_DATASET_TARGETS, SourceDatasetRequest
from pg_agmemory.source_notice import (
    MAX_SIGNED_NOTICE_BYTES,
    SignedSourceNoticeDelivery,
    SourceNoticeProfile,
    verify_source_notice,
)

profile = sys.argv[1]
assert MAX_SOURCE_LEASE_SECONDS == 300
assert SourceIdentity(source_system="synthetic", dataset_id="data", source_subject="reader")
assert MAX_DATASET_TARGETS == 100 and callable(SourceDatasetRequest)
assert MAX_SIGNED_NOTICE_BYTES == 16384 and callable(verify_source_notice)
assert profile in ("core", "hook", "sdk", "providers", "langgraph")
assert callable(create_app)
assert importlib.util.find_spec("mcp") is None
assert (importlib.util.find_spec("httpx") is not None) == (profile != "core")
assert (importlib.util.find_spec("langgraph") is not None) == (profile == "langgraph")
assert files("pg_agmemory").joinpath("py.typed").is_file()
source_help = subprocess.run(
    ["pg-agmemory", "source-access", "--help"],
    capture_output=True, text=True, timeout=15,
)
assert source_help.returncode == 0 and "notice-file" in source_help.stdout
assert "Traceback" not in source_help.stderr
dataset_help = subprocess.run(
    ["pg-agmemory", "source-dataset", "--help"],
    capture_output=True, text=True, timeout=15,
)
assert dataset_help.returncode == 0 and "expected-target-digest" in dataset_help.stdout
assert "Traceback" not in dataset_help.stderr
notice_help = subprocess.run(
    ["pg-agmemory", "source-notice", "--help"],
    capture_output=True, text=True, timeout=15,
)
assert notice_help.returncode == 0 and "delivery-file" in notice_help.stdout
assert "Traceback" not in notice_help.stderr
signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
notice_profile = SourceNoticeProfile(
    issuer="synthetic-source", audience="synthetic-notices", subject="synthetic-coordinator",
    key_id="synthetic-key",
    public_key=signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode(),
    tenant_id=UUID(int=1), scope_id=UUID(int=2), principal_id=UUID(int=3),
    source=SourceIdentity(source_system="synthetic", dataset_id="data", source_subject="reader"),
)
notice = SourceNotice(
    source=notice_profile.source, sequence=1, decision="deny", reason="unavailable",
)
issued_at = int(time.time())
signed_notice = SignedSourceNoticeDelivery(token=jwt.encode(
    {
        "iss": notice_profile.issuer, "aud": notice_profile.audience, "sub": notice_profile.subject,
        "iat": issued_at, "exp": issued_at + 60, "notice": notice.model_dump(mode="json"),
    },
    signing_key, algorithm="RS256",
    headers={"typ": "pgag-source-notice+jwt", "kid": notice_profile.key_id},
))
assert verify_source_notice(notice_profile, signed_notice) == notice
if profile == "langgraph":
    from pg_agmemory.langgraph import LangGraphMemory, build_turn_graph

    assert callable(LangGraphMemory) and callable(build_turn_graph)
else:
    try:
        importlib.import_module("pg_agmemory.langgraph")
    except ImportError as exc:
        assert str(exc) == "LangGraph pilot requires the pg-agmemory[langgraph] extra"
    else:
        raise AssertionError("LangGraph pilot imported without its optional dependency")
if profile == "core":
    try:
        importlib.import_module("pg_agmemory.sdk")
    except ImportError as exc:
        assert str(exc) == "Python SDK requires the pg-agmemory[sdk] extra"
    else:
        raise AssertionError("SDK imported without its HTTP dependency")
    try:
        importlib.import_module("pg_agmemory.external_source")
    except ImportError as exc:
        assert str(exc) == "Python SDK requires the pg-agmemory[sdk] extra"
    else:
        raise AssertionError("External source adapter imported without the SDK dependency")
    try:
        importlib.import_module("pg_agmemory.providers")
    except ImportError as exc:
        assert str(exc) == "Inference providers require the pg-agmemory[providers] extra"
    else:
        raise AssertionError("Provider module imported without its HTTP dependency")
else:
    from pg_agmemory.external_source import ExternalSourceMemory, snapshot_digest
    from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

    assert callable(ExternalSourceMemory)
    assert len(snapshot_digest("synthetic optional-install check")) == 64

    async def probe():
        try:
            async with AsyncMemoryClient("http://127.0.0.1:1", "fixed.identity.signature"):
                raise AssertionError("Unreachable SDK endpoint accepted")
        except MemoryClientError as exc:
            assert exc.error.code == "native_api_unavailable"
            assert not exc.error.outcome_unknown

    asyncio.run(probe())
    from pg_agmemory.providers import ProviderSettings, make_provider

    inspected = asyncio.run(make_provider(ProviderSettings(
        backend="local_http", endpoint="http://127.0.0.1:1/v1",
        text_model={"name": "synthetic", "revision": "1"},
    )).inspect())
    assert inspected["inference_tested"] is False
for command in ("mcp", "recall-hook"):
    process = subprocess.run(
        ["pg-agmemory", command],
        input='{"event":"session_start","query":""}',
        env={
            "PATH": os.environ["PATH"],
            "PGAG_HOOK_API_URL": "http://127.0.0.1:1",
            "PGAG_HOOK_API_TOKEN": "fixed.identity.signature",
            "PGAG_HOOK_SCOPE_IDS": '["00000000-0000-0000-0000-000000000001"]',
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    if profile != "core" and command == "recall-hook":
        assert process.returncode == 1
        result = json.loads(process.stdout)
        assert result["status"] == "error" and result["result"] is None
        assert result["error"]["code"] == "native_api_unavailable"
    else:
        assert process.returncode == 2 and process.stdout == ""
        extra = "mcp" if command == "mcp" else "hook"
        assert f"requires the pg-agmemory[{extra}] extra" in process.stderr
    assert "Traceback" not in process.stderr
inference = subprocess.run(
    ["pg-agmemory", "infer", "inspect", "--config", "/missing-synthetic-provider-config.json"],
    capture_output=True, text=True, timeout=15,
)
assert inference.returncode == 2 and "Traceback" not in inference.stderr
if profile == "core":
    assert inference.stdout == "" and "pg-agmemory[providers]" in inference.stderr
else:
    assert json.loads(inference.stdout)["error"]["code"] == "invalid_provider_configuration"
print(f"Optional adapter installation smoke passed: {profile}")
