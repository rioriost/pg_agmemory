"""Serial, synthetic Native API cost protocol; no workers, models, or performance gates.

The wrapper bakes this identical harness into both immutable source images. Each
repetition uses a fresh private PostgreSQL cluster and clones one prepopulated
schema-22 database, measures one clone unchanged,
and upgrades the other with the schema-23 client while no API process is running.
Administrative fixture tombstones deliberately retain payloads, unlike the separate
Native purge fixture. PostgreSQL storage is physical per relation/index; per-profile
tuple bytes are logical payload accounting, not attributed physical index storage.
"""

import argparse
import hashlib
import json
import math
import os
import random
import re
import secrets
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
import jwt
import psycopg
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from pg_agmemory.api import create_app
from pg_agmemory.database import SCHEMA_VERSION, Settings, migrate
from pg_agmemory.models import RecallResult
from pg_agmemory.service import build_context

SIMPLE = "simple-v1"
ENGLISH = "en-snowball-v1"
JAPANESE = "ja-janome-0.5.0-v1"
AT = "2026-09-01T00:00:00Z"
FUTURE = "2100-01-01T00:00:00Z"
ISSUER = "https://english-costs.invalid"
AUDIENCE = "pgag-english-costs"
FORMS = ("approval", "approved", "approve", "approving")
CANONICAL_TABLES = (
    "memory.object", "memory.episode", "memory.assertion",
    "memory.assertion_revision", "memory.provenance_edge", "memory_ops.object_tombstone",
    "memory_ops.deletion_request", "memory_ops.deletion_target",
)
RUN_CONTEXT = {"phase": "startup", "repetition": None}


class BenchmarkError(RuntimeError):
    pass


def failure_record(exc):
    code = str(exc) if isinstance(exc, BenchmarkError) else type(exc).__name__
    result = {"status": "failed", "code": code, "schema": SCHEMA_VERSION, **RUN_CONTEXT}
    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
        result["sqlstate"] = sqlstate
    return result


def require(condition, code):
    if not condition:
        raise BenchmarkError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(filename, value):
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def specification(name):
    require(name in ("modest", "smoke"), "unsupported_profile")
    small = name == "smoke"
    return {
        "format": "pgag-english-cost-spec-v1", "name": name, "seed": 20260927,
        "repetitions": 2 if small else 4,
        "episodes": 64 if small else 256, "assertions": 8 if small else 32,
        "topic_groups": 64, "retained_payload_tombstones": 4,
        "write_warmups_per_operation": 1 if small else 5,
        "write_samples_per_operation": 3 if small else 30,
        "read_warmups_per_query": 1 if small else 3,
        "read_samples_per_query": 3 if small else 20,
        "query_count": 8, "request_concurrency": 1,
        "http_timeout_seconds": 30, "recall_max_items": 16, "recall_byte_budget": 8000,
        "warmup_writes_persist": True, "automatic_processing": False,
        "cold_cache_claimed": False, "performance_gate": None,
    }


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


def distribution(values):
    require(all(type(value) is int and value >= 0 for value in values), "invalid_timing_sample")
    return {
        "samples": len(values), "sum": sum(values), "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95), "minimum": min(values) if values else None,
        "maximum": max(values) if values else None, "percentile_method": "nearest_rank",
    }


def repetition_order(repetition):
    require(type(repetition) is int and 1 <= repetition <= 8, "invalid_repetition")
    return ["schema22", "schema23"] if repetition % 2 else ["schema23", "schema22"]


def read_order(spec, repetition, warmup=False):
    count = spec["read_warmups_per_query" if warmup else "read_samples_per_query"]
    rng = random.Random(spec["seed"] + repetition * 101 + (1 if warmup else 0))
    result = []
    for _ in range(count):
        row = list(range(spec["query_count"]))
        rng.shuffle(row)
        result.extend(row)
    return result


def episode_text(index):
    return (
        f"topic{index % 64:04d} record{index:05d}. Field token: {FORMS[index // 64 % 4]}. "
        "Processes are running. Evidence quotes: silver golden."
    )


def source_identity():
    root = Path("/benchmark")
    identity = json.loads((root / "source-identity.json").read_text())
    require(identity["schema_version"] == SCHEMA_VERSION, "baked_schema_identity_mismatch")
    require(re.fullmatch(r"[0-9a-f]{40}", identity["source_sha"]) is not None,
            "invalid_source_identity")
    require(
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == identity["harness_sha256"],
        "baked_harness_mismatch",
    )
    for line in (root / "source-inputs.sha256").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "invalid_source_manifest_path")
        require(hashlib.sha256((Path("/app") / relative).read_bytes()).hexdigest() == expected,
                "baked_source_mismatch")
    return identity


def owned_url():
    run_id = os.environ["PGAG_BENCHMARK_RUN_ID"]
    require(re.fullmatch(r"[0-9a-f]{12}", run_id) is not None, "invalid_owned_run_id")
    url = os.environ["PGAG_BENCHMARK_ADMIN_URL"]
    database = conninfo_to_dict(url).get("dbname", "")
    require(re.fullmatch(rf"pgag_en_{run_id}_r[0-9]{{2}}_(seed|base|new)", database) is not None,
            "private_owned_database_required")
    return url


def verify_database(url, version, *, client=True, quiescent=False):
    require(not client or SCHEMA_VERSION == version, "client_schema_mismatch")
    with psycopg.connect(url) as conn:
        require(conn.execute("SHOW server_version_num").fetchone()[0] == "180006",
                "postgres_18_6_required")
        versions = conn.execute(
            "SELECT version FROM public.pgag_schema_migration ORDER BY version"
        ).fetchall()
        require(versions == [(i,) for i in range(1, version + 1)], "database_schema_mismatch")
        require(conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone() == ("0.8.6",), "pgvector_0_8_6_required")
        if quiescent:
            require(conn.execute(
                """SELECT count(*) FROM pg_stat_activity
                   WHERE datname=current_database() AND pid<>pg_backend_pid()
                     AND backend_type='client backend'"""
            ).fetchone()[0] == 0, "api_or_other_client_still_connected")


def provision(url, run_id):
    role = f"pgag_en_runtime_{run_id}"
    password = os.environ["PGAG_BENCHMARK_RUNTIME_PASSWORD"]
    tenant, principal, scope = [
        str(uuid5(NAMESPACE_URL, f"pgag-english-costs:{run_id}:{name}"))
        for name in ("tenant", "principal", "scope")
    ]
    subject = f"english-costs-{run_id}"
    with psycopg.connect(url) as conn:
        require(conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is None,
                "owned_runtime_role_already_exists")
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE pgag_runtime")
                     .format(sql.Identifier(role), sql.Literal(password)))
        conn.execute("INSERT INTO memory.tenant(id,dedup_secret) VALUES (%s,%s)",
                     (tenant, secrets.token_bytes(32)))
        conn.execute("INSERT INTO memory.principal VALUES (%s,%s,%s)",
                     (tenant, principal, subject))
        conn.execute("INSERT INTO memory.scope VALUES (%s,%s)", (tenant, scope))
        conn.execute(
            """INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions)
               VALUES (%s,%s,%s,ARRAY['read','write','delete'])""", (tenant, scope, principal),
        )
    return {"tenant": tenant, "principal": principal, "scope": scope, "subject": subject}


class Native:
    def __init__(self, client, token):
        self.client = client
        self.token = token

    def request(self, path, body, *, status=200, key="read"):
        headers = {"Authorization": f"Bearer {self.token}", "Idempotency-Key": key}
        started = time.perf_counter_ns()
        response = self.client.post(path, json=body, headers=headers)
        elapsed = time.perf_counter_ns() - started
        require(response.status_code == status,
                f"native_status_{response.status_code}_expected_{status}")
        return response.json(), elapsed


@contextmanager
def native_api(url, identity):
    verify_database(url, SCHEMA_VERSION)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )
    runtime_url = make_conninfo(**(conninfo_to_dict(url) | {
        "user": f"pgag_en_runtime_{os.environ['PGAG_BENCHMARK_RUN_ID']}",
        "password": os.environ["PGAG_BENCHMARK_RUNTIME_PASSWORD"],
    }))
    now = int(time.time())
    token = jwt.encode(
        {"sub": identity["subject"], "iss": ISSUER, "aud": AUDIENCE,
         "iat": now, "exp": now + 3600}, private, algorithm="RS256",
    )
    settings = Settings(runtime_url, public, ISSUER, AUDIENCE)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(
        create_app(settings), access_log=False, log_level="critical", log_config=None,
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        require(server.started, "native_startup_failed")
        with httpx.Client(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
            trust_env=False, timeout=30,
        ) as client:
            require(client.get("/readyz").status_code == 200, "native_not_ready")
            yield Native(client, token)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        require(not thread.is_alive(), "native_did_not_stop")
    verify_database(url, SCHEMA_VERSION, quiescent=True)


def observe_body(scope, label, text):
    return {
        "scope_id": scope, "source_namespace": "english-costs-v1",
        "source_event_id": label, "occurred_at": AT, "content": text,
        "consent_reference": "owned-synthetic-english-cost-fixture",
        "auto_extract": False, "auto_embed": False,
    }


def remember_body(scope, label, source):
    return {
        "scope_id": scope, "subject": label, "predicate": "status", "value": "silver",
        "explicit_intent": True, "valid_from": AT,
        "evidence": [{"memory_id": source, "quote": "silver"}],
    }


def revision_body(source):
    return {
        "expected_revision": 1, "value": "golden", "explicit_intent": True,
        "valid_from": AT, "reason": "Deterministic synthetic revision",
        "evidence": [{"memory_id": source, "quote": "golden"}],
    }


def seed_fixture(api, identity, spec):
    episodes, assertions = [], []
    for index in range(spec["episodes"]):
        text = episode_text(index)
        result, _ = api.request(
            "/v1/observe", observe_body(identity["scope"], f"seed-{index}", text),
            status=201, key=f"seed-observe-{index}",
        )
        require(result["revision"] == 1, "observe_revision")
        episodes.append({"id": result["memory_id"], "index": index, "text": text})
    historical_at = None
    for index in range(spec["assertions"]):
        source = episodes[index]["id"]
        result, _ = api.request(
            "/v1/remember", remember_body(identity["scope"], f"ledger{index:04d}", source),
            status=201, key=f"seed-remember-{index}",
        )
        assertion = result["memory_id"]
        require(result["revision"] == 1, "remember_revision")
        if index == 0:
            explained, _ = api.request("/v1/explain", {"memory_id": assertion, "revision": 1})
            historical_at = explained["assertion"]["recorded_at"]
        revised, _ = api.request(
            f"/v1/assertions/{assertion}/revisions", revision_body(source),
            status=201, key=f"seed-revise-{index}",
        )
        require(revised["memory_id"] == assertion and revised["revision"] == 2, "revision_write")
        assertions.append({"id": assertion, "index": index, "source": source})
    purge_source, _ = api.request(
        "/v1/observe", observe_body(identity["scope"], "purged", "purgetoken silver golden"),
        status=201, key="seed-purge-source",
    )
    purge_child, _ = api.request(
        "/v1/remember", remember_body(identity["scope"], "purgedledger", purge_source["memory_id"]),
        status=201, key="seed-purge-child",
    )
    api.request(
        f"/v1/assertions/{purge_child['memory_id']}/revisions",
        revision_body(purge_source["memory_id"]), status=201, key="seed-purge-revision",
    )
    purged, _ = api.request(
        "/v1/forget", {"memory_ids": [purge_source["memory_id"]], "mode": "purge",
                       "reason": "Owned synthetic fixture purge"},
        status=202, key="seed-purge",
    )
    require(purged["state"] == "active_store_purged" and purged["object_count"] == 2,
            "native_purge_fixture_incomplete")
    return {
        "identity": identity, "episodes": episodes, "assertions": assertions,
        "historical_at": historical_at,
        "tombstoned_retained_ids": [row["id"] for row in episodes[-2:] + assertions[-2:]],
        "purged_ids": [purge_source["memory_id"], purge_child["memory_id"]],
        "purge_receipt": purged,
        "logical_fixture_sha256": digest({
            "texts": [row["text"] for row in episodes], "spec": spec,
            "assertions": [(f"ledger{i:04d}", "silver", "golden")
                           for i in range(spec["assertions"])],
        }),
    }


def inject_retained_tombstones(url, fixture):
    identity = fixture["identity"]
    targets = sorted(fixture["tombstoned_retained_ids"])
    require(len(targets) == 4 and len(set(targets)) == 4, "retained_tombstone_targets")
    deletion = str(uuid5(NAMESPACE_URL, f"english-costs-suppression:{identity['tenant']}"))
    with psycopg.connect(url) as conn:
        objects = conn.execute(
            """SELECT id,scope_id FROM memory.object
               WHERE tenant_id=%s AND id=ANY(%s::uuid[]) ORDER BY id""",
            (identity["tenant"], targets),
        ).fetchall()
        require([str(row[0]) for row in objects] == targets
                and all(str(row[1]) == identity["scope"] for row in objects),
                "retained_tombstone_scope_or_object")
        epoch = conn.execute(
            """UPDATE memory.tenant SET deletion_epoch=deletion_epoch+1
               WHERE id=%s RETURNING deletion_epoch""", (identity["tenant"],),
        ).fetchone()
        require(epoch is not None, "retained_tombstone_tenant")
        conn.execute(
            """INSERT INTO memory_ops.deletion_request
               (tenant_id,id,principal_id,mode,state,object_count,deletion_epoch)
               VALUES (%s,%s,%s,'suppress','blocked_for_reads',%s,%s)""",
            (identity["tenant"], deletion, identity["principal"], len(targets), epoch[0]),
        )
        for ordinal, memory_id in enumerate(targets, 1):
            conn.execute(
                """INSERT INTO memory_ops.object_tombstone(tenant_id,object_id,scope_id)
                   VALUES (%s,%s,%s)""", (identity["tenant"], memory_id, identity["scope"]),
            )
            conn.execute(
                """INSERT INTO memory_ops.deletion_target
                   (tenant_id,deletion_id,object_id,scope_id,ordinal)
                   VALUES (%s,%s,%s,%s,%s)""",
                (identity["tenant"], deletion, memory_id, identity["scope"], ordinal),
            )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return {
        "deletion_id": deletion, "mode": "suppress", "state": "blocked_for_reads",
        "object_count": len(targets), "deletion_epoch": epoch[0],
        "payloads_retained": True,
    }


def queries(fixture, profile):
    require(profile in (SIMPLE, ENGLISH), "unsupported_read_profile")
    rows = [row for row in fixture["episodes"]
            if row["index"] % 64 == 0 and row["id"] not in fixture["tombstoned_retained_ids"]]
    all_ids = [row["id"] for row in rows]
    def literal(word):
        return [row["id"] for row in rows if FORMS[row["index"] // 64 % 4] == word]

    current = fixture["assertions"][0]["id"]
    return [
        {"name": "anchor", "query": "topic0000", "kind": "episode", "ids": all_ids},
        {"name": "literal-approval", "query": "topic0000 approval", "kind": "episode",
         "ids": all_ids if profile == ENGLISH else literal("approval")},
        {"name": "stem-approve", "query": "topic0000 approve", "kind": "episode",
         "ids": all_ids if profile == ENGLISH else literal("approve")},
        {"name": "stem-run", "query": "topic0000 run", "kind": "episode",
         "ids": all_ids if profile == ENGLISH else []},
        {"name": "stopwords", "query": "the and a", "kind": "episode", "ids": []},
        {"name": "not-found", "query": "topic0000 missingtoken", "kind": "episode", "ids": []},
        {"name": "current-revision", "query": "ledger0000 golden", "kind": "assertion",
         "ids": [current], "revision": 2},
        {"name": "historic-revision", "query": "ledger0000 silver", "kind": "assertion",
         "ids": [current], "revision": 1, "known_at": fixture["historical_at"]},
    ]


def recall_body(fixture, profile, query):
    return {
        "query": query["query"], "scope_ids": [fixture["identity"]["scope"]],
        "purpose": "owned-synthetic-english-costs", "search_profile": profile,
        "retrieval_mode": "lexical", "mode": "explicit", "as_of": FUTURE,
        "known_at": query.get("known_at", FUTURE), "max_items": 16, "token_budget": 8000,
        "filters": {"kind": query["kind"]},
    }


def validate_read(response, query, fixture, profile):
    parsed = RecallResult.model_validate(response)
    items = response["items"]
    actual = [item["memory_id"] for item in items]
    require(len(actual) == len(set(actual)) and set(actual) == set(query["ids"]),
            f"incorrect_read_sources_{query['name']}")
    require(response["search_profile"] == profile and response["retrieval_mode"] == "lexical",
            "read_profile_changed")
    require(response["embedding_model"] is None, "unexpected_embedding")
    require(response["coverage"]["retrieval_complete"]
            and not response["coverage"]["lexical_incomplete"]
            and not response["coverage"]["truncated"], "incomplete_fixed_query")
    pack, selected, omitted = build_context(parsed.items, 8000)
    require(not omitted and len(selected) == len(items) and pack == response["context_pack"],
            "read_context_changed")
    originals = {row["id"]: row["text"] for row in fixture["episodes"]}
    for item in items:
        require(item["revision"] == query.get("revision", 1), "wrong_read_revision")
        if item["memory_id"] in originals:
            require(item["content"] == originals[item["memory_id"]], "raw_evidence_changed")
        else:
            value = "silver" if query.get("revision") == 1 else "golden"
            require(item["content"] == f"ledger0000 / status: {value}", "assertion_content_changed")
            require(item["source"] == [fixture["assertions"][0]["source"]],
                    "assertion_provenance_changed")


def guard_native(api, fixture, profile):
    for query in queries(fixture, profile):
        result, _ = api.request("/v1/recall", recall_body(fixture, profile, query))
        validate_read(result, query, fixture, profile)
    source = fixture["episodes"][0]["id"]
    request = recall_body(fixture, profile, {"query": "", "kind": "episode"})
    request.update(required_memory_refs=[{"memory_id": source, "revision": 1}], max_items=1)
    result, _ = api.request("/v1/recall", request)
    require([item["memory_id"] for item in result["items"]] == [source], "retained_ref_missing")
    for memory_id in fixture["tombstoned_retained_ids"] + fixture["purged_ids"]:
        request = recall_body(fixture, profile, {"query": "", "kind": "episode"})
        request.pop("filters")
        request.update(required_memory_refs=[{"memory_id": memory_id}], max_items=1)
        api.request("/v1/recall", request, status=404)


def projection_guard(url, fixture, version):
    tenant = fixture["identity"]["tenant"]
    with psycopg.connect(url) as conn:
        canonical_rows = {}
        for table in CANONICAL_TABLES:
            rows = conn.execute(
                sql.SQL("SELECT row_to_json(t) FROM {} t WHERE tenant_id=%s "
                        "ORDER BY row_to_json(t)::text").format(sql.Identifier(*table.split("."))),
                (tenant,),
            ).fetchall()
            canonical_rows[table] = [row[0] for row in rows]
        counts = {table: len(rows) for table, rows in canonical_rows.items()}
        ja_rows = {}
        projections = {}
        for table, parent, column, text in (
            ("episode_lexical", "memory.episode", "episode_id", "p.content"),
            ("assertion_lexical", "memory.assertion", "assertion_id",
             "p.subject || ' ' || p.predicate || ' ' || r.value"),
        ):
            revision_join = (
                "JOIN memory.assertion_revision r ON r.tenant_id=p.tenant_id "
                "AND r.assertion_id=p.id AND r.revision=l.revision"
                if table == "assertion_lexical" else ""
            )
            expected_rows = counts[
                "memory.assertion_revision" if revision_join else "memory.episode"
            ]
            actual_profiles = conn.execute(
                sql.SQL("SELECT profile,count(*) FROM memory.{} WHERE tenant_id=%s "
                        "GROUP BY profile ORDER BY profile").format(sql.Identifier(table)),
                (tenant,),
            ).fetchall()
            expected_profiles = [JAPANESE] if version == 22 else [ENGLISH, JAPANESE]
            require([row[0] for row in actual_profiles] == expected_profiles, "projection_profiles")
            for profile, count in actual_profiles:
                live = conn.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {} p {} WHERE p.tenant_id=%s "
                        "AND NOT EXISTS (SELECT 1 FROM memory_ops.object_tombstone t "
                        "WHERE t.tenant_id=p.tenant_id AND t.object_id=p.id)"
                    ).format(
                        sql.Identifier(*parent.split(".")),
                        sql.SQL("JOIN memory.assertion_revision r ON r.tenant_id=p.tenant_id "
                                "AND r.assertion_id=p.id" if revision_join else ""),
                    ), (tenant,),
                ).fetchone()[0]
                require(count == (live if profile == ENGLISH else expected_rows),
                        "projection_count")
                if profile == ENGLISH:
                    tombstoned = conn.execute(
                        sql.SQL(
                            "SELECT count(*) FROM memory.{} l "
                            "JOIN memory_ops.object_tombstone t ON t.tenant_id=l.tenant_id "
                            "AND t.object_id=l.{} WHERE l.tenant_id=%s AND l.profile=%s"
                        ).format(sql.Identifier(table), sql.Identifier(column)), (tenant, profile),
                    ).fetchone()[0]
                    require(tombstoned == 0, "english_backfilled_tombstoned_payload")
                configuration = "pg_catalog.english" if profile == ENGLISH else "pg_catalog.simple"
                invalid = conn.execute(
                    sql.SQL(
                        "SELECT count(*) FROM memory.{} l JOIN {} p "
                        "ON p.tenant_id=l.tenant_id AND p.id=l.{} {} "
                        "WHERE l.tenant_id=%s AND l.profile=%s AND "
                        "(l.scope_id<>p.scope_id OR l.search_text<>to_tsvector(%s::regconfig,{}))"
                    ).format(
                        sql.Identifier(table), sql.Identifier(*parent.split(".")),
                        sql.Identifier(column), sql.SQL(revision_join), sql.SQL(text),
                    ), (tenant, profile, configuration),
                ).fetchone()[0]
                require(invalid == 0, "projection_content")
            projections[table] = dict(actual_profiles)
            ja_rows[table] = [row[0] for row in conn.execute(
                sql.SQL("SELECT row_to_json(l) FROM memory.{} l WHERE tenant_id=%s "
                        "AND profile=%s ORDER BY row_to_json(l)::text")
                .format(sql.Identifier(table)),
                (tenant, JAPANESE),
            ).fetchall()]
        require(conn.execute(
            "SELECT count(*) FROM memory_ops.model_call WHERE tenant_id=%s", (tenant,),
        ).fetchone()[0] == 0, "unexpected_model_call")
        require(conn.execute(
            "SELECT count(*) FROM memory_ops.job WHERE tenant_id=%s", (tenant,),
        ).fetchone()[0] == 0, "unexpected_background_job")
        epochs = conn.execute(
            "SELECT access_epoch,deletion_epoch FROM memory.tenant WHERE id=%s", (tenant,),
        ).fetchone()
    return {
        "counts": counts, "projections": projections, "epochs": list(epochs),
        "canonical_sha256": digest(canonical_rows), "japanese_projection_sha256": digest(ja_rows),
        "model_calls": 0, "jobs": 0,
    }


def preserved(before, after):
    for field in ("counts", "epochs", "canonical_sha256", "japanese_projection_sha256"):
        require(before[field] == after[field], f"upgrade_changed_{field}")


def storage(url):
    with psycopg.connect(url, row_factory=dict_row) as conn:
        result = conn.execute(
            "SELECT pg_database_size(current_database()) AS database_bytes,"
            "current_setting('server_version') AS postgres,"
            "(SELECT extversion FROM pg_extension WHERE extname='vector') AS pgvector"
        ).fetchone()
        result["tables"] = conn.execute(
            """SELECT schemaname,relname,pg_relation_size(relid) AS heap_main_bytes,
               pg_table_size(relid) AS table_including_toast_bytes,
               pg_indexes_size(relid) AS index_bytes,pg_total_relation_size(relid) AS total_bytes
               FROM pg_stat_user_tables WHERE schemaname IN ('memory','memory_ops')
               ORDER BY schemaname,relname"""
        ).fetchall()
        result["indexes"] = conn.execute(
            """SELECT schemaname,relname,indexrelname,pg_relation_size(indexrelid) AS bytes
               FROM pg_stat_user_indexes WHERE schemaname IN ('memory','memory_ops')
               ORDER BY schemaname,relname,indexrelname"""
        ).fetchall()
        result["logical_projection_tuple_bytes"] = {}
        for table in ("episode_lexical", "assertion_lexical"):
            result["logical_projection_tuple_bytes"][table] = conn.execute(
                sql.SQL("SELECT profile,count(*) AS rows,sum(pg_column_size(l)) AS tuple_bytes "
                        "FROM memory.{} l GROUP BY profile ORDER BY profile")
                .format(sql.Identifier(table)),
            ).fetchall()
        result["settings"] = conn.execute(
            "SELECT name,setting,unit FROM pg_settings WHERE name=ANY(%s) ORDER BY name",
            (["shared_buffers", "work_mem", "maintenance_work_mem", "autovacuum",
              "max_connections", "max_parallel_workers_per_gather", "fsync",
              "synchronous_commit", "full_page_writes"],),
        ).fetchall()
    return result


def maintain(url):
    started = time.perf_counter_ns()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("VACUUM (ANALYZE)")
    return time.perf_counter_ns() - started


def measure_writes(api, fixture, spec):
    scope = fixture["identity"]["scope"]
    samples = {operation: [] for operation in ("observe", "remember", "revision")}
    total = spec["write_warmups_per_operation"] + spec["write_samples_per_operation"]
    for index in range(total):
        label = f"write-{index:05d}"
        source, observe_ns = api.request(
            "/v1/observe", observe_body(scope, label, f"timed{index:05d} silver golden"),
            status=201, key=f"write-observe-{index}",
        )
        assertion, remember_ns = api.request(
            "/v1/remember", remember_body(scope, f"timedledger{index:05d}", source["memory_id"]),
            status=201, key=f"write-remember-{index}",
        )
        revision, revision_ns = api.request(
            f"/v1/assertions/{assertion['memory_id']}/revisions",
            revision_body(source["memory_id"]),
            status=201, key=f"write-revision-{index}",
        )
        require(source["revision"] == assertion["revision"] == 1
                and revision["revision"] == 2 and revision["memory_id"] == assertion["memory_id"],
                "write_response_incorrect")
        if index >= spec["write_warmups_per_operation"]:
            for name, elapsed in zip(samples, (observe_ns, remember_ns, revision_ns), strict=True):
                samples[name].append(elapsed)
    return {
        "order": "observe-remember-revision triplets; warmups first and retained",
        "untimed_warmups_per_operation": spec["write_warmups_per_operation"],
        "timing_boundary": "synchronous loopback HTTP POST through complete response bytes",
        "operations": {name: {"samples_ns": values, "latency_ns": distribution(values)}
                       for name, values in samples.items()},
    }


def write_count_guard(before, after, spec):
    increments = {
        "memory.object": 2, "memory.episode": 1, "memory.assertion": 1,
        "memory.assertion_revision": 2, "memory.provenance_edge": 2,
        "memory_ops.object_tombstone": 0,
        "memory_ops.deletion_request": 0, "memory_ops.deletion_target": 0,
    }
    total = spec["write_warmups_per_operation"] + spec["write_samples_per_operation"]
    for table, multiplier in increments.items():
        require(after["counts"][table] == before["counts"][table] + total * multiplier,
                f"write_count_{table}")
    require(before["epochs"] == after["epochs"], "write_changed_authority_epoch")


def measure_reads(api, fixture, spec, repetition, profile):
    definitions = queries(fixture, profile)
    require(len(definitions) == spec["query_count"], "query_count_changed")
    warmup = read_order(spec, repetition, True)
    order = read_order(spec, repetition)
    samples = [[] for _ in definitions]
    for warming, sequence in ((True, warmup), (False, order)):
        for index in sequence:
            query = definitions[index]
            result, elapsed = api.request("/v1/recall", recall_body(fixture, profile, query))
            validate_read(result, query, fixture, profile)
            if not warming:
                samples[index].append(elapsed)
    return {
        "profile": profile, "sample_order": order, "warmup_order": warmup,
        "query_order_sha256": digest({"warmup": warmup, "measured": order}),
        "queries": [
            {"name": query["name"], "query": query["query"], "kind": query["kind"],
             "known_at": query.get("known_at", FUTURE), "selected_count": len(query["ids"]),
             "selected_memory_ids": query["ids"], "id_order": "fixture_order_not_native_rank",
             "samples_ns": samples[index],
             "latency_ns": distribution(samples[index])}
            for index, query in enumerate(definitions)
        ],
    }


def seed(directory, spec, identity):
    url = owned_url()
    require(SCHEMA_VERSION == 22, "seed_requires_schema22_client")
    with psycopg.connect(url) as conn:
        require(conn.execute("SELECT to_regnamespace('memory')").fetchone()[0] is None,
                "seed_requires_empty_database")
        require(conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname='pgag_runtime'"
        ).fetchone() is None, "seed_requires_fresh_cluster_pgag_runtime_exists")
    migrate(url)
    verify_database(url, 22)
    actor = provision(url, os.environ["PGAG_BENCHMARK_RUN_ID"])
    with native_api(url, actor) as api:
        fixture = seed_fixture(api, actor, spec)
    fixture["suppression_receipt"] = inject_retained_tombstones(url, fixture)
    with native_api(url, actor) as api:
        guard_native(api, fixture, SIMPLE)
    maintain(url)
    guard = projection_guard(url, fixture, 22)
    require(guard["counts"]["memory.episode"] == spec["episodes"], "seed_episode_count")
    require(guard["counts"]["memory.assertion"] == spec["assertions"], "seed_assertion_count")
    require(guard["counts"]["memory.assertion_revision"] == 2 * spec["assertions"],
            "seed_history_count")
    require(guard["counts"]["memory.provenance_edge"] == 2 * spec["assertions"],
            "seed_provenance_count")
    require(guard["counts"]["memory.object"] == spec["episodes"] + spec["assertions"] + 2,
            "seed_object_count")
    require(guard["counts"]["memory_ops.object_tombstone"] == 6, "seed_tombstone_count")
    require(guard["counts"]["memory_ops.deletion_request"] == 2, "seed_deletion_manifest_count")
    require(guard["counts"]["memory_ops.deletion_target"] == 6, "seed_deletion_target_count")
    write_json(directory / "seed.json", {
        "format": "pgag-english-cost-seed-v1", "source": identity, "spec": spec,
        "fixture": fixture, "guard": guard, "storage": storage(url),
        "administrative_retained_payload_tombstones": True,
    })


def upgrade(directory, seeded, identity):
    url = owned_url()
    require(SCHEMA_VERSION == 23, "upgrade_requires_schema23_client")
    verify_database(url, 22, client=False, quiescent=True)
    before = projection_guard(url, seeded["fixture"], 22)
    preserved(seeded["guard"], before)
    before_storage = storage(url)
    started = time.perf_counter_ns()
    migrate(url)
    elapsed = time.perf_counter_ns() - started
    verify_database(url, 23, quiescent=True)
    after = projection_guard(url, seeded["fixture"], 23)
    preserved(before, after)
    transaction_storage = storage(url)
    maintenance_ns = maintain(url)
    with native_api(url, seeded["fixture"]["identity"]) as api:
        for profile in (SIMPLE, ENGLISH):
            guard_native(api, seeded["fixture"], profile)
    write_json(directory / "migration.json", {
        "format": "pgag-english-cost-migration-v1", "source": identity,
        "migration_from": 22, "migration_to": 23, "migration_ns": elapsed,
        "timing_boundary": "public migrate(): connection, transaction, migration23, commit",
        "old_api_quiescent_before_upgrade": True, "matching_new_api_verified": True,
        "before_guard": before, "after_guard": after,
        "before_storage": before_storage, "after_transaction_storage": transaction_storage,
        "after_maintenance_storage": storage(url), "excluded_maintenance_ns": maintenance_ns,
    })


def measure(directory, seeded, identity, repetition):
    url = owned_url()
    fixture, spec = seeded["fixture"], seeded["spec"]
    verify_database(url, SCHEMA_VERSION, quiescent=True)
    before = projection_guard(url, fixture, SCHEMA_VERSION)
    preserved(seeded["guard"], before)
    profiles = [SIMPLE] if SCHEMA_VERSION == 22 else (
        [SIMPLE, ENGLISH] if repetition % 2 else [ENGLISH, SIMPLE]
    )
    before_storage = storage(url)
    with native_api(url, fixture["identity"]) as api:
        for profile in profiles:
            guard_native(api, fixture, profile)
        writes = measure_writes(api, fixture, spec)
        after = projection_guard(url, fixture, SCHEMA_VERSION)
        write_count_guard(before, after, spec)
        after_write_storage = storage(url)
        maintenance_ns = maintain(url)
        reads = []
        for profile in profiles:
            guard_native(api, fixture, profile)
            reads.append(measure_reads(api, fixture, spec, repetition, profile))
            guard_native(api, fixture, profile)
    final = projection_guard(url, fixture, SCHEMA_VERSION)
    preserved(after, final)
    write_json(directory / f"schema{SCHEMA_VERSION}.json", {
        "format": "pgag-english-cost-measurement-v1", "source": identity,
        "repetition": repetition, "schema_version": SCHEMA_VERSION,
        "logical_fixture_sha256": fixture["logical_fixture_sha256"],
        "spec_sha256": digest(spec), "arm_order": repetition_order(repetition),
        "profile_order": profiles, "before_guard": before, "after_guard": final,
        "before_storage": before_storage, "after_write_storage": after_write_storage,
        "after_maintenance_storage": storage(url), "excluded_maintenance_ns": maintenance_ns,
        "writes": writes, "reads": reads,
        "correctness_verified_before_and_after": True, "model_calls": 0,
    })


def report(directory, spec, identity):
    repetitions = []
    migrations = []
    for repetition in range(1, spec["repetitions"] + 1):
        root = directory / f"r{repetition:02d}"
        seed_data = json.loads((root / "seed.json").read_text())
        migration = json.loads((root / "migration.json").read_text())
        baseline = json.loads((root / "schema22.json").read_text())
        current = json.loads((root / "schema23.json").read_text())
        require(seed_data["spec"] == spec, "report_spec_mismatch")
        require(baseline["logical_fixture_sha256"] == current["logical_fixture_sha256"],
                "paired_fixture_mismatch")
        paired = {}
        for operation in ("observe", "remember", "revision"):
            old = baseline["writes"]["operations"][operation]["latency_ns"]["p50"]
            new = current["writes"]["operations"][operation]["latency_ns"]["p50"]
            paired[operation] = {"schema22_p50_ns": old, "schema23_p50_ns": new,
                                 "difference_ns": new - old, "ratio": new / old if old else None}
        old_reads = next(value for value in baseline["reads"] if value["profile"] == SIMPLE)
        new_reads = next(value for value in current["reads"] if value["profile"] == SIMPLE)
        require(old_reads["query_order_sha256"] == new_reads["query_order_sha256"],
                "paired_read_order_changed")
        read_pairs = []
        for old, new in zip(old_reads["queries"], new_reads["queries"], strict=True):
            require(old["name"] == new["name"]
                    and old["selected_memory_ids"] == new["selected_memory_ids"],
                    "paired_simple_selectivity_changed")
            read_pairs.append({
                "query": old["query"], "name": old["name"], "selected_count": old["selected_count"],
                "schema22_latency_ns": old["latency_ns"], "schema23_latency_ns": new["latency_ns"],
            })
        migrations.append(migration["migration_ns"])
        repetitions.append({
            "repetition": repetition, "serial_order": repetition_order(repetition),
            "seed": seed_data, "migration": migration, "schema22": baseline, "schema23": current,
            "paired_write_medians": paired, "paired_simple_queries": read_pairs,
        })
    write_json(directory / "result.json", {
        "format": "pgag-english-profile-costs-v1", "status": "completed",
        "harness_source": identity,
        "cluster_lifecycle": "fresh_per_repetition",
        "spec": spec, "repetitions": repetitions, "migration_latency_ns": distribution(migrations),
        "native_only": True, "real_models": False, "schema22_simple_vs_schema23_simple": (
            "Paired fixed-workload observations of frozen versions, including added projections. "
            "Not a causal proof that every observed difference comes from English projection alone."
        ),
        "schema23_english": (
            "Additional fixed-query semantic/selectivity measurement; not an equal-selectivity "
            "performance comparison with simple-v1."
        ),
        "limitations": [
            "Synthetic modest data; no production capacity or performance improvement claim.",
            "One serial client; a fresh private PostgreSQL cluster per repetition.",
            "Only one database container runs at a time; host contention is possible.",
            "Autovacuum disabled; explicit untimed VACUUM ANALYZE and read warmups are recorded.",
            "No cold-cache claim, confidence interval, significance test, or latency gate.",
            "Timing excludes server startup, response validation, guards, and maintenance.",
            "Physical relation/index bytes cannot be attributed to profiles by tuple-byte ratios.",
        ],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "upgrade", "measure", "report"))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--profile", choices=("modest", "smoke"), default="modest")
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    RUN_CONTEXT.update(phase=args.command, repetition=args.repetition)
    require(sys.platform.startswith("linux"), "linux_guest_required")
    require(args.directory.is_dir() and not args.directory.is_symlink(),
            "private_directory_required")
    identity = source_identity()
    spec = specification(args.profile)
    repetition_order(args.repetition)
    if args.command == "seed":
        seed(args.directory, spec, identity)
    elif args.command == "report":
        report(args.directory, spec, identity)
    else:
        seeded = json.loads((args.directory / "seed.json").read_text())
        require(seeded["spec"] == spec, "seed_profile_mismatch")
        if args.command == "upgrade":
            upgrade(args.directory, seeded, identity)
        else:
            measure(args.directory, seeded, identity, args.repetition)
    print(canonical({"status": "completed", "schema": SCHEMA_VERSION, **RUN_CONTEXT}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not emit connection strings, credentials, or HTTP request headers.
        print(canonical(failure_record(exc)), file=sys.stderr)
        raise SystemExit(1) from None
