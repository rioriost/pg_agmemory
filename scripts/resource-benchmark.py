"""Controlled Native HTTP/worker resource measurement; never a model-quality score."""

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import multiprocessing
import os
import random
import secrets
import signal
import sys
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import jwt
import psycopg
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pg_agmemory import __version__
from pg_agmemory.api import create_app
from pg_agmemory.database import SCHEMA_VERSION, Settings, migrate
from pg_agmemory.jobs import job_transaction
from pg_agmemory.lexical import JAPANESE_PROFILE, segment
from pg_agmemory.models import (
    EmbeddingModel,
    Evidence,
    Observe,
    PutEmbedding,
    RecallResult,
    Remember,
    VectorQuery,
)
from pg_agmemory.providers import ProviderSettings
from pg_agmemory.synthesis_policy import SynthesisPolicy, SynthesisPolicyRequest, synthesis_policy
from pg_agmemory.worker import process_model
from pg_agmemory.worker_profile import WorkerProfile

MODEL = EmbeddingModel(name="resource-fixed-v1", revision="seeded-dense-768-v1")
CONSENT = "synthetic-resource-fixture"
AT = datetime(2026, 9, 1, tzinfo=UTC)
ISSUER = "https://resource.invalid"
AUDIENCE = "pgag-resource"
PROVIDER_PORT = 18435


class ResourceError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ResourceError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def profile(path, development=False, preflight=False):
    raw = path.read_bytes()
    require(len(raw) <= 16384, "profile too large")
    value = json.loads(raw)
    require(value["format"] == "pgag-resource-profile-v1", "unknown profile")
    require(
        value["tenants"] == 10
        and value["episodes_per_tenant"] == 10000
        and value["assertions_per_tenant"] == 1000
        and value["steady_seconds"] == 1800
        and value["workers"] == 2
        and value["vector_dimensions"] == 768,
        "unexpected S reference shape",
    )
    if development:
        value = value | {
            "name": "development-v1",
            "parent_profile_digest": digest(value),
            "tenants": 2,
            "episodes_per_tenant": 1000,
            "assertions_per_tenant": 100,
            "steady_seconds": 30,
            "warmup_seconds": 5,
            "database_memory_mib": 4096,
            "application_memory_mib": 2048,
            "database_settings": value["database_settings"] | {"shared_buffers": "1GB"},
        }
    elif preflight:
        value = value | {
            "name": "S-preflight-v1",
            "parent_profile_digest": digest(value),
            "steady_seconds": 30,
            "warmup_seconds": 15,
        }
    return value


def uid(label):
    return uuid5(NAMESPACE_URL, "pgag-resource-v1:" + label)


def scope_index(index, count):
    return (
        0
        if index < count // 1000
        else (1 if index < count // 100 else 2 if index < count // 10 else 3)
    )


def cohort(spec, index):
    tenant = index % spec["tenants"]
    selectivity = (index // spec["tenants"]) % 4
    mode = spec["recall_modes"][(index // (4 * spec["tenants"])) % len(spec["recall_modes"])]
    return tenant, selectivity, mode


def text_for(topic, size):
    value = f"topic{topic:03} is the synthetic resource tag."
    return (value + " Retain this approved synthetic memory fixture." * 32)[: size - 1] + "."


def vectors(spec):
    rng = random.Random(spec["seed"])
    return [[rng.uniform(-1, 1) for _ in range(768)] for _ in range(spec["vector_templates"])]


def worker_profile():
    return WorkerProfile(
        ProviderSettings(
            backend="local_http",
            endpoint=f"http://127.0.0.1:{PROVIDER_PORT}/v1",
            text_model={"name": "controlled-resource-text", "revision": "empty-extraction-v1"},
            embedding_model={"name": MODEL.name, "revision": MODEL.revision},
            max_output_tokens=128,
            timeout_seconds=2,
        )
    )


def provider_response():
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"candidates":[]}'},
            }
        ]
    }


def copy_rows(conn, table, columns, rows):
    with conn.cursor().copy(
        sql.SQL("COPY {} ({}) FROM STDIN").format(
            sql.Identifier(*table.split(".")),
            sql.SQL(",").join(map(sql.Identifier, columns)),
        )
    ) as copy:
        for row in rows:
            copy.write_row(row)


def seed(directory, spec):
    url = os.environ["PGAG_ADMIN_DATABASE_URL"]
    with psycopg.connect(url) as conn:
        require(
            conn.execute("SELECT current_database()").fetchone()[0] == "pgag_resource",
            "dedicated pgag_resource database required",
        )
        require(
            conn.execute(
                "SELECT count(*) FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema')"
            ).fetchone()[0]
            == 0,
            "fresh empty database required",
        )
    started = time.monotonic()
    migrate(url)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    runtime_password = secrets.token_urlsafe(32)
    with psycopg.connect(url) as conn:
        conn.execute(
            sql.SQL(
                "CREATE ROLE pgag_resource_runtime LOGIN PASSWORD {} IN ROLE pgag_runtime"
            ).format(sql.Literal(runtime_password))
        )
    runtime_url = make_conninfo(
        **(
            conninfo_to_dict(url)
            | {
                "user": "pgag_resource_runtime",
                "password": runtime_password,
            }
        )
    )
    values = vectors(spec)
    literals = [VectorQuery(model=MODEL, values=v).vector_literal() for v in values]
    texts = [text_for(i, spec["episode_bytes"]) for i in range(len(values))]
    segments = [segment(text) for text in texts]
    identities = []
    for tenant_number in range(spec["tenants"]):
        tenant, actor = uid(f"tenant/{tenant_number}"), uid(f"actor/{tenant_number}")
        scopes = [uid(f"scope/{tenant_number}/{i}") for i in range(6)]
        subject = f"resource-subject-{tenant_number}"
        secret = secrets.token_bytes(32)

        def mac(text, secret=secret):
            return hmac.new(secret, text.encode(), hashlib.sha256).hexdigest()

        objects, episodes, assertions, revisions, edges = [], [], [], [], []
        episode_vectors, assertion_vectors, events, journal, audit = [], [], [], [], []
        episode_lex, assertion_lex = [], []
        for index in range(spec["episodes_per_tenant"]):
            topic = index % len(values)
            memory_id = uid(f"episode/{tenant_number}/{index}")
            scope = scopes[scope_index(index, spec["episodes_per_tenant"])]
            text = texts[topic]
            observe = Observe(
                scope_id=scope,
                source_namespace="resource-seed",
                source_event_id=str(index),
                occurred_at=AT,
                content=text,
                consent_reference=CONSENT,
            )
            objects.append((tenant, memory_id, scope, "episode", AT))
            episodes.append((tenant, memory_id, scope, AT, text, CONSENT, "resource-seed"))
            input_digest = hashlib.sha256(text.encode()).hexdigest()
            episode_vectors.append(
                (
                    tenant,
                    memory_id,
                    1,
                    scope,
                    MODEL.name,
                    MODEL.revision,
                    input_digest,
                    literals[topic],
                )
            )
            episode_lex.append((tenant, memory_id, scope, segments[topic]))
            events.append(
                (
                    tenant,
                    scope,
                    mac(json.dumps(["resource-seed", str(index)])),
                    mac(observe.model_dump_json()),
                    memory_id,
                )
            )
            journal.append(
                (
                    tenant,
                    actor,
                    "observe",
                    mac(f"seed-observe-{index}"),
                    mac(observe.model_dump_json()),
                    Jsonb(
                        {
                            "memory_id": str(memory_id),
                            "revision": 1,
                            "synthesis_job_id": None,
                        }
                    ),
                )
            )
            projected = PutEmbedding(
                memory_id=memory_id, input_digest=input_digest, model=MODEL, values=values[topic]
            )
            journal.append(
                (
                    tenant,
                    actor,
                    "embedding",
                    mac(f"seed-embedding-{index}"),
                    mac(projected.model_dump_json()),
                    Jsonb({"memory_id": str(memory_id), "revision": 1}),
                )
            )
            audit.extend(
                [(tenant, actor, "observe", memory_id), (tenant, actor, "embedding", memory_id)]
            )
            if index % 10 == 0:
                assertion = uid(f"assertion/{tenant_number}/{index}")
                name, value = f"item{index}", f"topic{topic:03}"
                remembered = Remember(
                    scope_id=scope,
                    subject=name,
                    predicate="resource_tag",
                    value=value,
                    explicit_intent=True,
                    valid_from=AT,
                    evidence=[Evidence(memory_id=memory_id, quote=value)],
                )
                objects.append((tenant, assertion, scope, "assertion", AT))
                assertions.append((tenant, assertion, scope, name, "resource_tag"))
                revisions.append((tenant, assertion, scope, 1, value, f"[{AT.isoformat()},)", True))
                edges.append((tenant, assertion, 1, memory_id, scope, value))
                rendered = f"{name} / resource_tag: {value}"
                sha = hashlib.sha256(rendered.encode()).hexdigest()
                assertion_vectors.append(
                    (tenant, assertion, 1, scope, MODEL.name, MODEL.revision, sha, literals[topic])
                )
                assertion_lex.append(
                    (tenant, assertion, scope, segment(f"{name} resource_tag {value}"))
                )
                journal.append(
                    (
                        tenant,
                        actor,
                        "remember",
                        mac(f"seed-remember-{index}"),
                        mac(remembered.model_dump_json()),
                        Jsonb(
                            {
                                "memory_id": str(assertion),
                                "revision": 1,
                                "epistemic_status": "reported",
                            }
                        ),
                    )
                )
                projected = PutEmbedding(
                    memory_id=assertion, input_digest=sha, model=MODEL, values=values[topic]
                )
                journal.append(
                    (
                        tenant,
                        actor,
                        "embedding",
                        mac(f"seed-assert-embedding-{index}"),
                        mac(projected.model_dump_json()),
                        Jsonb({"memory_id": str(assertion), "revision": 1}),
                    )
                )
                audit.extend(
                    [
                        (tenant, actor, "remember", assertion),
                        (tenant, actor, "embedding", assertion),
                    ]
                )
        with psycopg.connect(url) as conn:
            conn.execute(
                "INSERT INTO memory.tenant(id,dedup_secret) VALUES (%s,%s)", (tenant, secret)
            )
            conn.execute("INSERT INTO memory.principal VALUES (%s,%s,%s)", (tenant, actor, subject))
            for scope in scopes:
                conn.execute("INSERT INTO memory.scope VALUES (%s,%s)", (tenant, scope))
                conn.execute(
                    "INSERT INTO memory.scope_member "
                    "(tenant_id,scope_id,principal_id,permissions) "
                    "VALUES (%s,%s,%s,ARRAY['read','write','delete'])",
                    (tenant, scope, actor),
                )
            copy_rows(
                conn,
                "memory.object",
                ["tenant_id", "id", "scope_id", "kind", "created_at"],
                objects,
            )
            copy_rows(
                conn,
                "memory.episode",
                [
                    "tenant_id",
                    "id",
                    "scope_id",
                    "occurred_at",
                    "content",
                    "consent_reference",
                    "source_namespace",
                ],
                episodes,
            )
            copy_rows(
                conn,
                "memory.assertion",
                ["tenant_id", "id", "scope_id", "subject", "predicate"],
                assertions,
            )
            copy_rows(
                conn,
                "memory.assertion_revision",
                [
                    "tenant_id",
                    "assertion_id",
                    "scope_id",
                    "revision",
                    "value",
                    "valid_time",
                    "explicit_intent",
                ],
                revisions,
            )
            copy_rows(
                conn,
                "memory.provenance_edge",
                ["tenant_id", "child_id", "child_revision", "parent_id", "scope_id", "quote"],
                edges,
            )
            for kind, rows in (("episode", episode_vectors), ("assertion", assertion_vectors)):
                copy_rows(
                    conn,
                    "memory." + kind + "_embedding",
                    [
                        "tenant_id",
                        kind + "_id",
                        "revision",
                        "scope_id",
                        "model_name",
                        "model_revision",
                        "input_digest",
                        "embedding",
                    ],
                    rows,
                )
            conn.execute(
                "CREATE TEMP TABLE resource_lex "
                "(tenant_id uuid,id uuid,scope_id uuid,tokens text) ON COMMIT DROP"
            )
            for kind, rows in (("episode", episode_lex), ("assertion", assertion_lex)):
                copy_rows(conn, "resource_lex", ["tenant_id", "id", "scope_id", "tokens"], rows)
                extra_column, extra_value = (",revision", ",1") if kind == "assertion" else ("", "")
                conn.execute(
                    sql.SQL(
                        "INSERT INTO memory.{} (tenant_id,{},scope_id,profile,search_text"
                        + extra_column
                        + ") SELECT tenant_id,id,scope_id,%s,to_tsvector('simple',tokens)"
                        + extra_value
                        + " FROM resource_lex"
                    ).format(sql.Identifier(kind + "_lexical"), sql.Identifier(kind + "_id")),
                    (JAPANESE_PROFILE,),
                )
                conn.execute("TRUNCATE resource_lex")
            copy_rows(
                conn,
                "memory_ops.source_event",
                ["tenant_id", "scope_id", "event_digest", "request_digest", "object_id"],
                events,
            )
            copy_rows(
                conn,
                "memory_ops.idempotency",
                [
                    "tenant_id",
                    "principal_id",
                    "operation",
                    "key_digest",
                    "request_digest",
                    "result",
                ],
                journal,
            )
            copy_rows(
                conn,
                "memory_ops.audit_event",
                ["tenant_id", "principal_id", "action", "target_id"],
                audit,
            )
        with synthesis_policy(
            url,
            SynthesisPolicyRequest(
                operation="set",
                tenant_id=tenant,
                scope_id=scopes[4],
                expected_access_epoch=1,
                policy=SynthesisPolicy(
                    enabled=True,
                    profile_digest=worker_profile().digest,
                    kinds=["extract"],
                    consent_references=[CONSENT],
                    max_pending_jobs=spec["synthesis_max_pending_per_scope"],
                    max_input_bytes=1024,
                    max_output_tokens=128,
                    max_calls=spec["synthesis_max_calls_per_scope"],
                ),
            ),
        ):
            pass
        now = int(time.time())
        identities.append(
            {
                "tenant_id": str(tenant),
                "subject": subject,
                "scopes": list(map(str, scopes)),
                "token": jwt.encode(
                    {
                        "sub": subject,
                        "iss": ISSUER,
                        "aud": AUDIENCE,
                        "iat": now,
                        "exp": now + 21600,
                    },
                    private,
                    algorithm="RS256",
                ),
            }
        )
        print(json.dumps({"seeded_tenants": tenant_number + 1}), flush=True)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("ANALYZE")
    write_json(directory / "profile.json", spec)
    write_json(
        directory / "runtime.json",
        {
            "database_url": runtime_url,
            "jwt_public_key": public,
            "jwt_issuer": ISSUER,
            "jwt_audience": AUDIENCE,
        },
    )
    write_json(directory / "clients.json", identities)
    write_json(
        directory / "seed.json",
        {
            "profile_digest": digest(spec),
            "elapsed_seconds": time.monotonic() - started,
            "episodes": spec["tenants"] * spec["episodes_per_tenant"],
            "assertions": spec["tenants"] * spec["assertions_per_tenant"],
            "vectors": spec["tenants"]
            * (spec["episodes_per_tenant"] + spec["assertions_per_tenant"]),
            "native_metadata_seeded": True,
            "administrative_bulk_fixture": True,
        },
    )


class Journal:
    def __init__(self, path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(fd, "w", buffering=1)

    def record(self, value):
        self.stream.write(canonical(value) + "\n")

    def close(self):
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()


def worker(directory, number, stop):
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    settings = Settings(**json.loads((directory / "runtime.json").read_text()))
    identities = json.loads((directory / "clients.json").read_text())
    journal = Journal(directory / f"worker-{number}.jsonl")
    configured = worker_profile()

    async def loop():
        index = number
        while not stop.is_set():
            subject = identities[index % len(identities)]["subject"]
            index += 1
            async with job_transaction(settings.database_url, subject) as jobs:
                claim = await jobs.claim(profile_digest=configured.digest)
                row = (
                    None
                    if not claim
                    else await (
                        await jobs.conn.execute(
                            "SELECT extract(epoch FROM (updated_at-created_at))*1000 AS lag "
                            "FROM memory_ops.job WHERE tenant_id=%s AND id=%s",
                            (jobs.tenant, claim["job_id"]),
                        )
                    ).fetchone()
                )
            if not claim:
                await asyncio.sleep(0.02)
                continue
            require(claim["state"] == "running", "unexpected worker claim")
            start = time.perf_counter()
            result = await process_model(settings.database_url, subject, claim, configured)
            journal.record(
                {
                    "outcome": result["outcome"],
                    "queue_ms": float(row["lag"]),
                    "processing_ms": (time.perf_counter() - start) * 1000,
                }
            )

    try:
        asyncio.run(loop())
    finally:
        journal.close()


def serve(directory):
    spec = json.loads((directory / "profile.json").read_text())
    journal = Journal(directory / "server.jsonl")
    provider_journal = Journal(directory / "provider.jsonl")
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < length <= 65536:
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(length))
            if body.get("model") != "controlled-resource-text" or body.get("max_tokens") != 128:
                self.send_error(400)
                return
            time.sleep(spec["controlled_provider_delay_ms"] / 1000)
            encoded = json.dumps(provider_response()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            with lock:
                provider_journal.record(
                    {"status": 200, "input_bytes": length, "output_bytes": len(encoded)}
                )

    server = ThreadingHTTPServer(("127.0.0.1", PROVIDER_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    workers = [
        context.Process(target=worker, args=(directory, i, stop)) for i in range(spec["workers"])
    ]
    sampler = context.Process(target=sample, args=(directory, "application"))
    sampler.start()
    for process in workers:
        process.start()
    settings = Settings(**json.loads((directory / "runtime.json").read_text()))
    app = create_app(settings, timing_sink=lambda event: journal.record(asdict(event)))
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, log_level="warning")
    finally:
        stop.set()
        for process in workers:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join()
        server.shutdown()
        server.server_close()
        sampler.terminate()
        sampler.join(5)
        require(sampler.exitcode == 0, "application sampler exited abnormally")
        journal.close()
        provider_journal.close()
        require(all(p.exitcode == 0 for p in workers), "worker exited abnormally")


async def load(directory, base):
    spec = json.loads((directory / "profile.json").read_text())
    identities = json.loads((directory / "clients.json").read_text())
    space = vectors(spec)
    journal = Journal(directory / "client.jsonl")
    authorized = {}
    for t in range(spec["tenants"]):
        for i in range(spec["episodes_per_tenant"]):
            scope = identities[t]["scopes"][scope_index(i, spec["episodes_per_tenant"])]
            authorized[str(uid(f"episode/{t}/{i}"))] = scope
            if i % 10 == 0:
                authorized[str(uid(f"assertion/{t}/{i}"))] = scope
    inflight = set()
    task_failures = []
    sequence = {"recall": 0, "observe": 0}
    timeout = httpx.Timeout(spec["request_timeout_seconds"])
    database_samples = Journal(directory / "database-resources.jsonl")
    sampling = True

    def database_sample():
        with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"], row_factory=dict_row) as conn:
            row = conn.execute("""SELECT pg_read_file('/proc/stat',0,65536) AS cpu,
                pg_read_file('/proc/meminfo',0,65536) AS memory,
                (SELECT count(*) FROM memory_ops.job
                 WHERE state IN ('pending','running')) AS pending,
                (SELECT max(n) FROM (SELECT count(*) n FROM memory_ops.job
                  WHERE state IN ('pending','running')
                  GROUP BY tenant_id,scope_id) q) AS max_scope_pending
                """).fetchone()
            memory = {
                line.split()[0].rstrip(":"): int(line.split()[1]) * 1024
                for line in row["memory"].splitlines()
                if line.startswith(("MemTotal:", "MemAvailable:"))
            }
            return {
                "monotonic": time.monotonic(),
                "cpu_ticks": list(map(int, row["cpu"].splitlines()[0].split()[1:])),
                "clock_ticks": os.sysconf("SC_CLK_TCK"),
                **memory,
                "pending": row["pending"],
                "max_scope_pending": row["max_scope_pending"] or 0,
            }

    async def sampling_loop():
        while sampling:
            database_samples.record(await asyncio.to_thread(database_sample))
            await asyncio.sleep(1)

    sampler = asyncio.create_task(sampling_loop())
    async with httpx.AsyncClient(
        base_url=base,
        timeout=timeout,
        trust_env=False,
        limits=httpx.Limits(
            max_connections=spec["max_client_inflight"],
            max_keepalive_connections=spec["max_client_inflight"],
        ),
    ) as client:
        ready = await client.get("/readyz")
        require(ready.status_code == 200, "API not ready")
        caps = await client.get(
            "/v1/capabilities",
            headers={
                "Authorization": "Bearer " + identities[0]["token"],
            },
        )
        require(
            caps.status_code == 200 and caps.json()["schema_version"] == SCHEMA_VERSION,
            "capability mismatch",
        )

        async def request(operation, index, phase, scheduled):
            tenant, selectivity, mode = cohort(spec, index)
            actor = identities[tenant]
            scopes = actor["scopes"][: 4 - selectivity]
            topic_count = min(256, spec["episodes_per_tenant"] // (10**selectivity))
            topic = (index // len(identities)) % max(1, topic_count)
            if operation == "recall":
                body = {
                    "scope_ids": scopes,
                    "purpose": "synthetic-resource-measurement",
                    "mode": "implicit",
                    "token_budget": spec["recall_token_budget"],
                    "max_items": spec["recall_max_items"],
                    "retrieval_mode": mode,
                    "query": "" if mode == "vector" else f"topic{topic:03}",
                }
                if mode != "lexical":
                    body["vector_query"] = {"model": MODEL.model_dump(), "values": space[topic]}
            else:
                mode = "observe"
                body = {
                    "scope_id": actor["scopes"][4],
                    "source_namespace": "resource-load",
                    "source_event_id": f"{phase}-{index}",
                    "occurred_at": AT.isoformat(),
                    "content": text_for(index % 256, spec["episode_bytes"]),
                    "consent_reference": CONSENT,
                    "auto_extract": True,
                }
            start = time.perf_counter()
            row = {
                "phase": phase,
                "operation": operation,
                "mode": mode,
                "selectivity_percent": spec["selectivity_percent"][selectivity],
                "schedule_lag_ms": (start - scheduled) * 1000,
                "request_id": None,
                "status": None,
                "valid": False,
                "error": None,
            }
            try:
                response = await client.post(
                    "/v1/" + operation,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + actor["token"],
                        "Idempotency-Key": str(uuid4()),
                    },
                )
                row.update(
                    status=response.status_code, request_id=response.headers.get("x-request-id")
                )
                if response.status_code != (200 if operation == "recall" else 201):
                    row["error"] = "unexpected_http_status"
                elif operation == "recall":
                    result = RecallResult.model_validate_json(response.content)
                    row["valid"] = (
                        bool(result.items)
                        and all(
                            authorized.get(str(item.memory_id)) in scopes for item in result.items
                        )
                        and result.context_pack.byte_count <= spec["recall_token_budget"]
                    )
                    if not row["valid"]:
                        row["error"] = "recall_contract_failed"
                else:
                    value = response.json()
                    row["valid"] = bool(value.get("memory_id") and value.get("synthesis_job_id"))
            except httpx.HTTPError:
                row["error"] = "transport_error"
            except ValueError:
                row["error"] = "response_contract_failed"
            finally:
                row["client_ms"] = (time.perf_counter() - start) * 1000
                journal.record(row)

        def finished(task):
            inflight.discard(task)
            if not task.cancelled() and task.exception() is not None:
                task_failures.append(type(task.exception()).__name__)

        try:
            for phase, seconds in (
                ("warmup", spec["warmup_seconds"]),
                ("steady", spec["steady_seconds"]),
            ):
                started = time.perf_counter()
                plan = sorted(
                    (i / rate, operation)
                    for operation, rate in (
                        ("recall", spec["recall_per_second"]),
                        ("observe", spec["observe_per_second"]),
                    )
                    for i in range(seconds * rate)
                )
                for offset, operation in plan:
                    require(not task_failures, "load task failed")
                    due = started + offset
                    await asyncio.sleep(max(0, due - time.perf_counter()))
                    index = sequence[operation]
                    sequence[operation] += 1
                    if len(inflight) >= spec["max_client_inflight"]:
                        _, selectivity, mode = cohort(spec, index)
                        journal.record(
                            {
                                "phase": phase,
                                "operation": operation,
                                "mode": mode if operation == "recall" else "observe",
                                "selectivity_percent": spec["selectivity_percent"][selectivity],
                                "error": "scheduled_drop",
                                "valid": False,
                                "request_id": None,
                            }
                        )
                        continue
                    task = asyncio.create_task(request(operation, index, phase, due))
                    inflight.add(task)
                    task.add_done_callback(finished)
                await asyncio.sleep(max(0, started + seconds - time.perf_counter()))
                if inflight:
                    await asyncio.gather(*list(inflight))
                require(not task_failures, "load task failed")
                require(time.perf_counter() - started >= seconds - 1, "load window truncated")
        finally:
            sampling = False
            await sampler
            database_samples.close()
            journal.close()


def footprint(directory, name):
    with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"], row_factory=dict_row) as conn:
        size = conn.execute("""SELECT pg_database_size(current_database()) AS database_bytes,
            pg_current_wal_lsn()::text AS wal_lsn,
            current_setting('server_version_num') AS postgres,
            (SELECT extversion FROM pg_extension WHERE extname='vector') AS pgvector""").fetchone()
        size["relations"] = conn.execute("""SELECT schemaname,relname,
            pg_total_relation_size(relid) AS total_bytes,pg_indexes_size(relid) AS index_bytes
            FROM pg_stat_user_tables ORDER BY schemaname,relname""").fetchall()
        size["jobs"] = conn.execute(
            "SELECT state,error_code,count(*) AS count FROM memory_ops.job "
            "GROUP BY state,error_code"
        ).fetchall()
        size["calls"] = conn.execute(
            "SELECT outcome,billing_unknown,count(*) AS count FROM memory_ops.model_call "
            "GROUP BY outcome,billing_unknown"
        ).fetchall()
        size["settings"] = conn.execute(
            "SELECT name,setting,unit FROM pg_settings WHERE name=ANY(%s)",
            (
                [
                    "shared_buffers",
                    "work_mem",
                    "maintenance_work_mem",
                    "max_connections",
                    "max_parallel_workers_per_gather",
                ],
            ),
        ).fetchall()
        write_json(directory / f"{name}.json", size)


def sample(directory, label):
    journal = Journal(directory / f"{label}-resources.jsonl")
    stop = False

    def stopping(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, stopping)
    try:
        while not stop:
            memory = {
                row.split()[0].rstrip(":"): int(row.split()[1]) * 1024
                for row in Path("/proc/meminfo").read_text().splitlines()
                if row.startswith(("MemTotal:", "MemAvailable:"))
            }
            cpu = list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:]))
            journal.record(
                {
                    "monotonic": time.monotonic(),
                    "cpu_ticks": cpu,
                    "clock_ticks": os.sysconf("SC_CLK_TCK"),
                    **memory,
                }
            )
            time.sleep(1)
    finally:
        journal.close()


def percentile(values, fraction=0.95):
    return None if not values else sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def report(directory, sha):
    spec = json.loads((directory / "profile.json").read_text())
    clients = [json.loads(line) for line in (directory / "client.jsonl").read_text().splitlines()]
    servers = [json.loads(line) for line in (directory / "server.jsonl").read_text().splitlines()]
    by_id = {row["request_id"]: row for row in servers}
    steady = [row for row in clients if row["phase"] == "steady"]
    errors = [row for row in steady if not row["valid"]]
    missing = [row for row in steady if row["request_id"] not in by_id]
    measurements = {}
    for operation in ("recall", "observe"):
        rows = [row for row in steady if row["operation"] == operation]
        timings = [by_id[row["request_id"]] for row in rows if row["request_id"] in by_id]
        measurements[operation] = {
            "attempts": len(rows),
            "client_p95_ms": percentile([r["client_ms"] for r in rows if "client_ms" in r]),
            "schedule_lag_p95_ms": percentile(
                [r["schedule_lag_ms"] for r in rows if "schedule_lag_ms" in r]
            ),
            **{
                field + "_p95": percentile([r[field] for r in timings if r[field] is not None])
                for field in ("transaction_ms", "connection_barrier_ms", "handler_ms", "commit_ms")
            },
        }
    strata = {}
    for mode in spec["recall_modes"]:
        for selectivity in spec["selectivity_percent"]:
            rows = [
                r
                for r in steady
                if r["operation"] == "recall"
                and r["mode"] == mode
                and r["selectivity_percent"] == selectivity
            ]
            strata[f"{mode}:{selectivity}"] = {
                "attempts": len(rows),
                "transaction_p95_ms": percentile(
                    [
                        by_id[r["request_id"]]["transaction_ms"]
                        for r in rows
                        if r["request_id"] in by_id
                        and by_id[r["request_id"]]["transaction_ms"] is not None
                    ]
                ),
            }
    workers = [
        json.loads(line)
        for path in sorted(directory.glob("worker-*.jsonl"))
        for line in path.read_text().splitlines()
    ]
    resources = {}
    for label in ("database", "application"):
        rows = [
            json.loads(line)
            for line in (directory / f"{label}-resources.jsonl").read_text().splitlines()
        ]
        require(len(rows) >= 2, "resource samples incomplete")
        cpu = [b - a for a, b in zip(rows[0]["cpu_ticks"], rows[-1]["cpu_ticks"], strict=True)]
        resources[label] = {
            "sample_count": len(rows),
            "sampled_seconds": rows[-1]["monotonic"] - rows[0]["monotonic"],
            "cpu_busy_seconds": sum(cpu[i] for i in (0, 1, 2, 5, 6)) / rows[0]["clock_ticks"],
            "guest_total_bytes": rows[0]["MemTotal"],
            "guest_nonavailable_sampled_peak_bytes": max(
                r["MemTotal"] - r["MemAvailable"] for r in rows
            ),
        }
        if label == "database":
            resources[label]["sampled_max_scope_pending"] = max(
                r["max_scope_pending"] for r in rows
            )
    before = json.loads((directory / "before.json").read_text())
    after = json.loads((directory / "after.json").read_text())

    def lsn(value):
        high, low = value.split("/")
        return int(high, 16) * 2**32 + int(low, 16)

    gates = {
        "schedule_lag": all(
            "schedule_lag_ms" in r and r["schedule_lag_ms"] <= spec["max_schedule_lag_ms"]
            for r in steady
        ),
        "complete_schedule": not any(r["error"] == "scheduled_drop" for r in steady)
        and all(
            measurements[op]["attempts"] == spec[op + "_per_second"] * spec["steady_seconds"]
            for op in ("recall", "observe")
        ),
        "no_invalid_responses": not errors,
        "complete_timings": not missing and len(by_id) == len(servers),
        "observe_latency": measurements["observe"]["transaction_ms_p95"] is not None
        and measurements["observe"]["transaction_ms_p95"] < spec["observe_transaction_p95_ms"],
        "recall_latency": all(
            row["transaction_p95_ms"] is not None
            and row["transaction_p95_ms"] < spec["recall_transaction_p95_ms"]
            for row in strata.values()
        ),
        "workers_drained": not any(r["state"] in ("pending", "running") for r in after["jobs"]),
        "worker_success": all(r["outcome"] == "succeeded" for r in workers)
        and len(workers) == sum(r["operation"] == "observe" and r["valid"] for r in clients),
    }
    result = {
        "format": "pgag-resource-result-v1",
        "implementation_sha": sha,
        "build_identity": json.loads((directory / "build-identity.json").read_text()),
        "service_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "profile": spec,
        "profile_digest": digest(spec),
        "measurements": measurements,
        "recall_strata": strata,
        "resources": resources,
        "steady_gates": gates,
        "footprint": {
            "before_database_bytes": before["database_bytes"],
            "after_database_bytes": after["database_bytes"],
            "after_index_bytes": sum(r["index_bytes"] for r in after["relations"]),
            "wal_growth_bytes": lsn(after["wal_lsn"]) - lsn(before["wal_lsn"]),
            "logical_backup_bytes": (directory / "final.dump").stat().st_size,
        },
        "invalid_responses": len(errors),
        "missing_timings": len(missing),
        "worker_outcomes": {
            state: sum(r["outcome"] == state for r in workers)
            for state in sorted({r["outcome"] for r in workers})
        },
        "job_states": after["jobs"],
        "call_states": after["calls"],
        "worker_queue_p95_ms": percentile([r["queue_ms"] for r in workers]),
        "worker_processing_p95_ms": percentile([r["processing_ms"] for r in workers]),
        "resource_qualified": False,
        "m2_qualified": False,
        "unmeasured": [
            "small-forget-barrier",
            "large-purge",
            "concurrent-limit-probes",
            "physical-cold-cache",
            "provider-billing",
        ],
    }
    write_json(directory / "report.json", result)
    print(canonical(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation", choices=("seed", "serve", "load", "footprint", "sample", "report")
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--base-url")
    parser.add_argument("--name")
    parser.add_argument("--sha")
    args = parser.parse_args()
    require(sys.platform == "linux", "Linux container required")
    if args.operation == "seed":
        require(not (args.development and args.preflight), "conflicting measurement modes")
        seed(args.directory, profile(args.profile, args.development, args.preflight))
    elif args.operation == "serve":
        serve(args.directory)
    elif args.operation == "load":
        asyncio.run(load(args.directory, args.base_url))
    elif args.operation == "footprint":
        footprint(args.directory, args.name)
    elif args.operation == "sample":
        sample(args.directory, args.name)
    else:
        report(args.directory, args.sha)


if __name__ == "__main__":
    try:
        main()
    except (ResourceError, OSError, psycopg.Error, ValueError, KeyError) as exc:
        print(f"Resource measurement failed ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
