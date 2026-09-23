"""Bounded enabled-AGE disaster recovery; only for the disposable shell harness.

Private fixture IDs and signed operational rows survive removal of the source
cluster. No API starts on the restored cluster before explicit isolated apply.
The retained report contains counts and keyed fingerprints, never fixture text.
The backup deliberately discards AGE extension/catalog and physical projections;
fresh AGE comes from the trusted image, not from a restored extension catalog.
"""

import asyncio
import hashlib
import hmac
import json
import os
import platform
import re
import stat
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.rows import dict_row

import pg_agmemory
from pg_agmemory.age_graph import AGE_COMMIT, AgeGraph
from pg_agmemory.graphs import SqlGraph
from pg_agmemory.models import (
    CreateEntity,
    CreateRelation,
    Evidence,
    ExpandGraph,
    Forget,
    GraphResult,
    Observe,
    ReviseRelation,
)
from pg_agmemory.processing_recovery import (
    ProcessingRecoverySnapshot,
    capture_processing_state,
    compare_processing_state,
)
from pg_agmemory.recovery_apply import RecoveryBundle, secret_for
from pg_agmemory.service import MemoryError, MemoryService, bind_identity, principal_connection

FORMAT = "pgag-isolated-age-recovery-v1"
PROFILE = hashlib.sha256(b"synthetic-age-recovery-v1").hexdigest()
BACKUP_MODE = "canonical-only-projection-discard"
DUMP_EXCLUSIONS = [
    "--exclude-extension=age", "--exclude-schema=ag_catalog", "--exclude-schema=pgag_age_*",
]
OPERATOR = "synthetic-age-recovery-operator"
READER = "synthetic-age-recovery-reader"
START = datetime(2026, 9, 1, tzinfo=UTC)
SPLIT = datetime(2026, 9, 4, tzinfo=UTC)
CURRENT = datetime(2026, 9, 5, tzinfo=UTC)
PROJECTION = "memory_ops.age_projection"
ACCOUNTING = (
    "memory_ops.model_call", "memory_ops.job", "memory.scope_synthesis_policy",
    "memory_ops.source_access_state", "memory_ops.source_access_event",
)


class DrillError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise DrillError(code)


def cli(arguments, *, request=None, error=None):
    result = subprocess.run(
        ["pg-agmemory", *arguments], input="" if request is None else json.dumps(request),
        capture_output=True, text=True, timeout=90, check=False,
    )
    require(not result.stderr.strip(), "unexpected_cli_diagnostics_" + arguments[0])
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise DrillError("invalid_cli_json") from None
    require(isinstance(value, dict), "invalid_cli_object")
    if error is not None:
        require(result.returncode == 1 and value.get("error", {}).get("code") == error,
                "unexpected_cli_refusal")
    else:
        code = value.get("error", {}).get("code", "unknown")
        require(isinstance(code, str) and code.replace("_", "").isalnum(), "invalid_cli_error")
        require(result.returncode == 0 and "error" not in value,
                "cli_failed_" + arguments[0] + "_" + code)
    return value


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_private(path):
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_uid == os.geteuid(), "private_fixture_required")
    return json.loads(path.read_bytes())


def validate_archive_manifest(text):
    entries = [line for line in text.splitlines() if line and not line.startswith(";")]
    require(entries and all(
        "ag_catalog" not in line and "pgag_age_" not in line
        and re.search(r"\bEXTENSION (?:- )?age(?:\s|$)", line) is None
        for line in entries
    ), "projection_not_excluded_from_archive")
    for schema, table in (
        ("memory", "tenant"), ("memory", "episode"), ("memory", "relation_revision"),
        ("memory_ops", "age_projection"), ("memory_ops", "graph_generation"),
        ("memory_ops", "graph_generation_state"), ("memory_ops", "recovery_key"),
        ("memory_ops", "source_access_state"), ("memory_ops", "source_access_event"),
        ("public", "pgag_schema_migration"),
    ):
        require(any(f" TABLE DATA {schema} {table} " in line for line in entries),
                "canonical_table_missing_from_archive")
    require(any(re.search(r"\bEXTENSION - vector(?:\s|$)", line) for line in entries),
            "vector_extension_missing_from_archive")
    return {
        "backup_mode": BACKUP_MODE, "dump_exclusions": DUMP_EXCLUSIONS,
        "archive_manifest_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "archive_manifest_entries": len(entries), "archive_projection_entries": 0,
        "full_age_catalog_restore_qualified": False,
        "prior_full_age_rebuild_failure": "ag_graph_graphid_index",
        "extension_recreated_from_trusted_image": True,
    }


async def manifest(directory):
    result = validate_archive_manifest((directory / "archive.list").read_text())
    write_private(directory / "backup.json", result)
    return {"status": "archive_verified", **result}


@asynccontextmanager
async def service(subject=OPERATOR):
    async with principal_connection(os.environ["PGAG_DATABASE_URL"], subject) as (conn, identity):
        async with conn.transaction():
            await bind_identity(conn, subject, identity)
            yield MemoryService(conn, identity)


def generation(tenant, operation="get", **fields):
    result = cli(["graph-generation"], request={
        "operation": operation, "tenant_id": tenant, **fields,
    })
    require(not result["artifact_verified"] and not result["serving_enabled"],
            "generation_must_not_activate")
    return result


def prepare_generation(fixture, directory, filename):
    tenant = fixture["tenant_id"]
    current = generation(tenant)
    identifier = str(uuid4())
    begun = generation(
        tenant, "begin", expected_revision=current["revision"], generation_id=identifier,
        expected_input_digest=current["current_input_digest"], profile_digest=PROFILE,
    )
    path = directory / filename
    args = [
        "--tenant-id", tenant, "--generation-id", identifier,
        "--expected-revision", str(begun["revision"]), "--file", str(path),
    ]
    exported = cli(["graph-artifact", "export", *args])
    checked = cli(["graph-artifact", "check", *args])
    require(exported["artifact_digest"] == checked["artifact_digest"]
            and checked["artifact_verified"] and not checked["serving_enabled"],
            "artifact_check_failed")
    recorded = generation(
        tenant, "record", expected_revision=begun["revision"], generation_id=identifier,
        expected_input_digest=current["current_input_digest"],
        artifact_digest=exported["artifact_digest"],
    )
    return recorded, path


def publish(fixture, recorded, path, revision, *, rebuild_missing=False):
    return cli([
        "age-projection", "publish", "--tenant-id", fixture["tenant_id"],
        "--expected-revision", str(revision), "--generation-id", recorded["head"]["id"],
        "--expected-generation-revision", str(recorded["revision"]), "--file", str(path),
        *(["--rebuild-missing"] if rebuild_missing else []),
    ])


def graph_request(fixture, *, historical=False, deleted=False):
    return ExpandGraph(
        scope_ids=[UUID(fixture["scope_id"])],
        seeds=[UUID(fixture["nodes"][3 if deleted else 0])],
        relation_types=["depends_on", "part_of"],
        purpose="Synthetic isolated AGE recovery verification",
        max_hops=2, max_paths=100, as_of=START if historical else CURRENT,
        known_at=datetime.fromisoformat(
            fixture["historical_known_at" if historical else "known_at"],
        ),
    )


async def graph_read(fixture, backend, *, subject=OPERATOR, **options):
    async with service(subject) as memory:
        result = await backend(memory).expand(graph_request(fixture, **options))
        return GraphResult.model_validate(result).model_dump(mode="json")


async def native_equal(fixture, generation_id, *, paths):
    for historical, count in ((False, paths), (True, 1)):
        expected = await graph_read(fixture, SqlGraph, historical=historical)
        actual = await graph_read(fixture, AgeGraph, historical=historical)
        require(len(expected["paths"]) == count,
                "sql_historical_paths_missing" if historical else "sql_current_paths_missing")
        require(actual == {
            **expected, "backend": "age", "projection_watermark": generation_id,
        }, "native_sql_ordered_paths_mismatch")


def snapshot(fixture):
    tenant = UUID(fixture["tenant_id"])
    with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL timezone='UTC'")
        require(conn.execute(
            "SELECT version FROM public.pgag_schema_migration ORDER BY version",
        ).fetchall() == [{"version": version} for version in range(1, 22)], "schema21_required")
        extensions = conn.execute(
            "SELECT extname,extversion FROM pg_extension "
            "WHERE extname IN ('age','vector') ORDER BY extname",
        ).fetchall()
        require(extensions == [{"extname": "age", "extversion": "1.8.0"},
                               {"extname": "vector", "extversion": "0.8.6"}],
                "extension_identity_mismatch")
        build = conn.execute(
            "SELECT ag_catalog.pgag_age_build() AS build,"
            "ag_catalog.pgag_age_preloaded() AS preloaded,"
            "current_setting('server_version_num')::int AS postgres",
        ).fetchone()
        require(build["build"]["commit"] == AGE_COMMIT and build["preloaded"]
                and build["postgres"] == 180006, "patched_age_identity_mismatch")
        tables = {
            f"memory.{row['tablename']}": "" for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='memory' ORDER BY tablename",
            )
        }
        tables.update(dict.fromkeys((
            "memory_ops.job", "memory_ops.job_input", "memory_ops.job_identity",
            "memory_ops.extraction_candidate", "memory_ops.model_call", "memory_ops.source_event",
            "memory_ops.source_access_state", "memory_ops.source_access_event",
        ), ""))
        require(len(tables) == 37, "canonical_table_coverage_changed")
        require(conn.execute("SELECT id FROM memory.tenant").fetchall() == [{"id": tenant}],
                "single_synthetic_tenant_required")
        secret = secret_for(conn, tenant)
        def fingerprint(table):
            values = [row["data"] for row in conn.execute(sql.SQL(
                'SELECT to_jsonb(t) AS data FROM {} t ORDER BY to_jsonb(t)::text COLLATE "C"'
            ).format(sql.Identifier(*table.split("."))))]
            digest = hmac.new(secret, table.encode() + b":" + json.dumps(
                values, sort_keys=True, separators=(",", ":"),
            ).encode(), hashlib.sha256).hexdigest()
            return {"table": table, "rows": len(values), "digest": digest}
        fingerprints = [fingerprint(table) for table in tables]
        authoritative = [fingerprint(row["schema"] + "." + row["table"]) for row in conn.execute(
            """SELECT schemaname AS schema,tablename AS table FROM pg_tables
               WHERE schemaname IN ('memory','memory_ops','public')
               ORDER BY schemaname,tablename""",
        ).fetchall()]
        def rows(query):
            return [row["data"] for row in conn.execute(
                "SELECT to_jsonb(t) AS data FROM (" + query + ") t", (tenant,),
            )]
        result = {
            "canonical": fingerprints,
            "authoritative": authoritative,
            "registry": rows("SELECT * FROM memory_ops.age_projection WHERE tenant_id=%s"),
            "generations": rows("SELECT * FROM memory_ops.graph_generation "
                                "WHERE tenant_id=%s ORDER BY id"),
            "objects": rows("SELECT id,kind,scope_id FROM memory.object "
                            "WHERE tenant_id=%s ORDER BY id"),
            "tombstones": rows("SELECT object_id,scope_id FROM memory_ops.object_tombstone "
                               "WHERE tenant_id=%s ORDER BY object_id"),
            "provenance": rows("SELECT * FROM memory.provenance_edge "
                               "WHERE tenant_id=%s ORDER BY parent_id,child_id"),
            "extensions": extensions,
            "age_build": build,
        }
    result["processing"] = capture_processing_state(
        os.environ["PGAG_ADMIN_DATABASE_URL"], tenant,
    ).model_dump(mode="json")
    return result


def verify_physical_scope(fixture, *, missing):
    with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"]) as conn:
        graphs = {row[0] for row in conn.execute("SELECT name::text FROM ag_catalog.ag_graph")}
        schemas = {row[0] for row in conn.execute(
            "SELECT nspname::text FROM pg_namespace WHERE nspname ~ '^pgag_age_'",
        )}
        labels = conn.execute("SELECT count(*) FROM ag_catalog.ag_label").fetchone()[0]
        expected = set() if missing else {"pgag_age_" + UUID(fixture["old_generation"]).hex}
        require(graphs == schemas == expected and labels == (0 if missing else 4),
                "unexpected_or_partial_physical_graphs")


def validate_fixture(fixture, before, latest):
    require(fixture["format"] == FORMAT and len(set(fixture["nodes"])) == 4,
            "invalid_fixture")
    require(before["objects"] == latest["objects"], "canonical_ids_changed")
    require(before["generations"] == latest["generations"], "old_generation_changed")
    require(before["registry"] == latest["registry"] and len(latest["registry"]) == 1,
            "enabled_registry_changed")
    require(latest["registry"][0]["enabled"] and latest["registry"][0]["revision"] == 1,
            "enabled_revision_one_required")
    require(not before["tombstones"] and
            {row["object_id"] for row in latest["tombstones"]} == set(fixture["purged"]),
            "purge_closure_mismatch")
    for state in (before, latest):
        require(len(state["canonical"]) == 37, "incomplete_canonical_fingerprints")
        tables = {row["table"]: row for row in state["processing"]["tables"]}
        require(tables["memory_ops.model_call"]["rows"] == tables["memory_ops.job"]["rows"] == 0,
                "unexpected_model_or_worker_activity")
    old = {row["table"]: row for row in before["processing"]["tables"]}
    new = {row["table"]: row for row in latest["processing"]["tables"]}
    require(all(table in old and table in new and old[table] == new[table] for table in ACCOUNTING),
            "accounting_changed")
    require(latest["provenance"] and len(before["provenance"]) > len(latest["provenance"]),
            "source_provenance_not_exercised")


async def seed(directory):
    fixture = cli(["provision", "--subject", OPERATOR])
    fixture.update(format=FORMAT, reader_id=str(uuid4()))
    with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
            (fixture["tenant_id"], fixture["reader_id"], READER),
        )
        epoch = conn.execute("SELECT access_epoch FROM memory.tenant WHERE id=%s",
                             (fixture["tenant_id"],)).fetchone()[0]
    cli(["scope-access", "set", "--tenant-id", fixture["tenant_id"],
         "--scope-id", fixture["scope_id"], "--principal-id", fixture["reader_id"],
         "--expected-access-epoch", str(epoch), "--permissions", "read", "--no-expiry"])
    sources, nodes = [], []
    scope = UUID(fixture["scope_id"])
    async with service() as memory:
        for branch in ("control", "purged"):
            content = "Synthetic AGE recovery " + branch + " evidence."
            source = await memory.observe(Observe(
                scope_id=scope, source_namespace="synthetic-age-recovery",
                source_event_id=branch, occurred_at=START, content=content,
                consent_reference="isolated-age-drill-only",
            ), "source-" + branch)
            sources.append((source["memory_id"], [Evidence(
                memory_id=UUID(source["memory_id"]), quote=content,
            )]))
        for index in range(4):
            node = await SqlGraph(memory).create_entity(CreateEntity(
                scope_id=scope, entity_type="component",
                canonical_label=f"SYNTHETIC_AGE_RECOVERY_{index}",
                evidence=sources[index == 3][1], explicit_intent=True,
            ), f"node-{index}")
            nodes.append(node["memory_id"])
        edge = await SqlGraph(memory).create_relation(CreateRelation(
            scope_id=scope, source_entity=UUID(nodes[0]), target_entity=UUID(nodes[1]),
            predicate="depends_on", evidence=sources[0][1], explicit_intent=True,
            valid_from=START, valid_to=SPLIT,
        ), "control-relation")
        branch = await SqlGraph(memory).create_relation(CreateRelation(
            scope_id=scope, source_entity=UUID(nodes[2]), target_entity=UUID(nodes[3]),
            predicate="part_of", evidence=sources[1][1], explicit_intent=True,
            valid_from=SPLIT,
        ), "deletable-relation")
    async with service() as memory:
        await SqlGraph(memory).revise_relation(UUID(edge["memory_id"]), ReviseRelation(
            expected_revision=1, target_entity=UUID(nodes[2]), evidence=sources[0][1],
            explicit_intent=True, valid_from=SPLIT, reason="Synthetic relation revision",
        ), "revise-control")
    fixture.update(
        nodes=nodes, control_source=sources[0][0], purge_source=sources[1][0],
        control_relation=edge["memory_id"],
        purged=[sources[1][0], nodes[3], branch["memory_id"]],
    )
    with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"]) as conn:
        historical, current = conn.execute(
            """SELECT lower(system_time)+(upper(system_time)-lower(system_time))/2,
                      clock_timestamp()
               FROM memory.assertion_revision
               WHERE tenant_id=%s AND assertion_id=%s AND revision=1""",
            (fixture["tenant_id"], fixture["control_relation"]),
        ).fetchone()
    fixture.update(historical_known_at=historical.isoformat(), known_at=current.isoformat())
    recorded, path = prepare_generation(fixture, directory, "old-graph.json")
    installed = publish(fixture, recorded, path, 0)
    require(installed["revision"] == 1 and installed["serving_enabled"]
            and installed["projection"]["node_count"] == 4
            and installed["projection"]["edge_revision_count"] == 3, "initial_publish_failed")
    fixture["old_generation"] = recorded["head"]["id"]
    verify_physical_scope(fixture, missing=False)
    await native_equal(fixture, fixture["old_generation"], paths=2)
    require((await graph_read(fixture, AgeGraph, subject=READER))["paths"],
            "reader_initial_access_missing")
    before = snapshot(fixture)
    require(len(before["objects"]) == 8 and not before["tombstones"], "seed_object_counts")
    write_private(directory / "fixture.json", fixture)
    write_private(directory / "before.json", before)
    return {"status": "seeded", "nodes": 4, "relations": 2, "edge_revisions": 3}


async def later(directory):
    fixture = read_private(directory / "fixture.json")
    verify_physical_scope(fixture, missing=False)
    async with service() as memory:
        receipt = await memory.forget(Forget(
            memory_ids=[UUID(fixture["purge_source"])], reason="Synthetic post-backup purge",
        ), "source-purge")
        require(receipt["object_count"] == 3, "source_purge_closure")
    state = capture_processing_state(
        os.environ["PGAG_ADMIN_DATABASE_URL"], UUID(fixture["tenant_id"]),
    )
    cli(["scope-access", "revoke", "--tenant-id", fixture["tenant_id"],
         "--scope-id", fixture["scope_id"], "--principal-id", fixture["reader_id"],
         "--expected-access-epoch", str(state.access_epoch)])
    latest = snapshot(fixture)
    validate_fixture(fixture, read_private(directory / "before.json"), latest)
    require(generation(fixture["tenant_id"])["head"]["source_matches"] is False,
            "old_generation_not_stale")
    try:
        await graph_read(fixture, AgeGraph)
    except MemoryError as exc:
        require(exc.code == "graph_projection_stale", "stale_projection_wrong_error")
    else:
        raise DrillError("stale_projection_served")
    cli(["recovery-apply", "export", "--tenant-id", fixture["tenant_id"],
         "--bundle", str(directory / "bundle.json")])
    bundle = RecoveryBundle.model_validate_json(json.dumps(read_private(directory / "bundle.json")))
    require(bundle.reference.model_dump(mode="json") == latest["processing"],
            "source_bundle_snapshot_mismatch")
    write_private(directory / "latest.json", latest)
    return {"status": "exported", "purged_objects": 3, "reader_revoked": True}


@contextmanager
def api_server(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    environment = {name: value for name, value in os.environ.items()
                   if name in ("PATH", "LANG", "HOME", "SSL_CERT_FILE", "PGAG_DATABASE_URL")}
    environment.update(PGAG_JWT_PUBLIC_KEY=public, PGAG_JWT_ISSUER="age-recovery",
                       PGAG_JWT_AUDIENCE="age-recovery", PGAG_GRAPH_BACKEND="age")
    def headers(subject):
        now = int(time.time())
        token = jwt.encode({"sub": subject, "iss": "age-recovery", "aud": "age-recovery",
                            "iat": now, "exp": now + 600}, key, algorithm="RS256")
        return {"Authorization": "Bearer " + token}
    with (directory / "api-private.log").open("wb") as log:
        process = subprocess.Popen(["pg-agmemory", "serve"], env=environment,
                                   stdout=log, stderr=log)
        try:
            with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
                for _ in range(150):
                    require(process.poll() is None, "api_startup_failed")
                    try:
                        if client.get("/readyz").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    raise DrillError("api_not_ready")
                yield client, headers
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


async def canonical_guards(fixture):
    async with service() as memory:
        for target in fixture["purged"]:
            try:
                await memory.object(UUID(target))
            except MemoryError as exc:
                require(exc.code == "not_found", "purged_object_wrong_error")
            else:
                raise DrillError("purged_object_visible")
        await memory.object(UUID(fixture["control_source"]))
    async with service(READER) as memory:
        try:
            await memory.object(UUID(fixture["control_source"]))
        except MemoryError as exc:
            require(exc.code == "not_found", "revoked_reader_wrong_error")
        else:
            raise DrillError("revoked_reader_visible")
    require(not (await graph_read(fixture, SqlGraph, deleted=True))["paths"],
            "sql_deleted_paths_visible")
    require(not (await graph_read(fixture, SqlGraph, subject=READER))["paths"],
            "sql_revoked_paths_visible")
    require(len((await graph_read(fixture, SqlGraph))["paths"]) == 1, "sql_control_missing")


async def recover(directory):
    fixture = read_private(directory / "fixture.json")
    before, latest = (read_private(directory / name) for name in ("before.json", "latest.json"))
    validate_fixture(fixture, before, latest)
    require(os.environ.get("PGAG_AGE_SOURCE_REMOVED") == "true", "source_not_removed")
    backup = read_private(directory / "backup.json")
    require(backup == validate_archive_manifest((directory / "archive.list").read_text()),
            "archive_manifest_changed")
    verify_physical_scope(fixture, missing=True)
    require(snapshot(fixture) == before, "old_dump_restore_mismatch")
    bundle = RecoveryBundle.model_validate_json(json.dumps(read_private(directory / "bundle.json")))
    require(bundle.reference.model_dump(mode="json") == latest["processing"],
            "latest_bundle_evidence_mismatch")
    requests = bundle.rows["memory_ops.deletion_request"]
    require(len(requests) == 1 and requests[0]["mode"] == "purge"
            and requests[0]["object_count"] == 3, "bounded_purge_receipt_required")
    targets = bundle.rows["memory_ops.deletion_target"]
    require({row["object_id"] for row in targets} == set(fixture["purged"]),
            "signed_purge_targets_mismatch")
    async with service() as memory:
        replayed = await memory.forget(Forget(
            memory_ids=[UUID(row["object_id"]) for row in targets],
            reason="Isolated signed latest-ledger purge replay",
        ), "isolated-recovery-purge")
        require(replayed["object_count"] == requests[0]["object_count"]
                and replayed["deletion_epoch"] == requests[0]["deletion_epoch"],
                "purge_replay_mismatch")
    expected = capture_processing_state(
        os.environ["PGAG_ADMIN_DATABASE_URL"], UUID(fixture["tenant_id"]),
    )
    write_private(directory / "expected.json", expected.model_dump(mode="json"))
    args = [
        "recovery-apply", "apply", "--tenant-id", fixture["tenant_id"],
        "--bundle", str(directory / "bundle.json"), "--expected", str(directory / "expected.json"),
        "--isolated",
    ]
    cli(args, error="recovery_active_graph_projection")
    require(capture_processing_state(
        os.environ["PGAG_ADMIN_DATABASE_URL"], UUID(fixture["tenant_id"]),
    ) == expected, "default_refusal_mutated_state")
    applied = cli([*args, "--disable-age-projection"])
    require(
        applied["status"] == "applied" and applied["processing_state_matches"] is False
        and applied["operational_state_restored"] is True
        and applied["age_projection_disabled"] is True
        and applied["projection_rebuild_required"] is True
        and applied["differences"] == [PROJECTION], "apply_result_mismatch",
    )
    restored = snapshot(fixture)
    comparison = compare_processing_state(
        ProcessingRecoverySnapshot.model_validate_json(json.dumps(restored["processing"])),
        bundle.reference,
    )
    require(not comparison.processing_state_matches and comparison.differences == (PROJECTION,),
            "applied_operational_state_mismatch")
    for key in ("canonical", "objects", "tombstones", "provenance", "generations",
                "extensions", "age_build"):
        require(restored[key] == latest[key], "latest_" + key + "_mismatch")
    disabled = restored["registry"][0]
    require(not disabled["enabled"] and disabled["revision"] == 2
            and {k: v for k, v in disabled.items()
                 if k not in ("enabled", "revision", "updated_at")}
            == {k: v for k, v in before["registry"][0].items()
                if k not in ("enabled", "revision", "updated_at")}, "disabled_receipt_changed")
    await canonical_guards(fixture)
    with api_server(directory) as (client, headers):
        body = graph_request(fixture).model_dump(mode="json")
        response = client.post("/v1/graph/expand", json=body, headers=headers(OPERATOR))
        require(response.status_code == 409
                and response.json()["code"] == "graph_projection_unavailable",
                "disabled_http_not_refused")
        recorded, path = prepare_generation(fixture, directory, "rebuilt-graph.json")
        require(recorded["head"]["id"] != fixture["old_generation"]
                and recorded["head"]["parent_id"] == fixture["old_generation"]
                and recorded["head"]["source_matches"], "new_current_generation_required")
        still_disabled = cli(["age-projection", "get", "--tenant-id", fixture["tenant_id"]])
        require(not still_disabled["serving_enabled"] and still_disabled["revision"] == 2,
                "artifact_automatically_activated")
        installed = publish(fixture, recorded, path, 2, rebuild_missing=True)
        require(installed["revision"] == 3 and installed["serving_enabled"]
                and installed["rebuilt_missing_projection"] is True
                and installed["projection"]["node_count"] == 3
                and installed["projection"]["edge_revision_count"] == 2,
                "rebuilt_publish_mismatch")
        await native_equal(fixture, recorded["head"]["id"], paths=1)
        for subject, deleted in ((OPERATOR, False), (OPERATOR, True), (READER, False)):
            expected_paths = await graph_read(fixture, SqlGraph, subject=subject, deleted=deleted)
            response = client.post(
                "/v1/graph/expand",
                json=graph_request(fixture, deleted=deleted).model_dump(mode="json"),
                headers=headers(subject),
            )
            require(response.status_code == 200 and response.json() == {
                **expected_paths, "backend": "age", "projection_watermark": recorded["head"]["id"],
            }, "rebuilt_http_sql_mismatch")
            require(bool(response.json()["paths"]) == (subject == OPERATOR and not deleted),
                    "rebuilt_negative_guard_failed")
    final = snapshot(fixture)
    require(final["canonical"] == latest["canonical"], "rebuild_modified_canonical")
    old_rows = [row for row in final["generations"] if row["id"] == fixture["old_generation"]]
    require(old_rows == before["generations"], "rebuild_modified_old_generation")
    final_processing = {row["table"]: row for row in final["processing"]["tables"]}
    for row in restored["processing"]["tables"]:
        if row["table"] not in (
            PROJECTION, "memory_ops.graph_generation", "memory_ops.graph_generation_state",
        ):
            require(final_processing[row["table"]] == row, "rebuild_modified_operational_state")
    report = {
        "status": "passed", "scope": "bounded-isolated-enabled-age", "m3_qualified": False,
        "schema_version": 21, "service_version": pg_agmemory.__version__,
        "source_authority_recovery": "exact_content_only",
        "source_authority_revalidation": "explicit",
        "automatic_source_grant_refresh": False,
        "platform": platform.machine(), "age_commit": AGE_COMMIT,
        **backup, "source_cluster_removed_before_restore": True,
        "fresh_matching_image_restore": True, "nonroot_packaged_runtime": True,
        "authoritative_old_snapshot_exact": True, "recovery_hmac_keys_preserved": True,
        "missing_previous_projection_rebuilt": True,
        "fresh_age_runtime_schema_usage_granted": True,
        "registry_revisions": [1, 2, 3], "old_generation_unchanged": True,
        "canonical_tables": 37, "canonical_ids_retained": len(final["objects"]),
        "nodes_before": 4, "nodes_after": 3, "relation_revisions_before": 3,
        "relation_revisions_after": 2, "purged_objects": 3,
        "ordered_native_sql_equal": True, "historical_paths_equal": True,
        "disabled_http_status": 409, "purged_targets_invisible": True, "reader_revoked": True,
        "automatic_activation": False, "automatic_model_calls": 0, "worker_started": False,
        "processing_state_matches": False, "operational_state_restored": True,
        "differences_after_apply": [PROJECTION], "accounting_exact": True,
        "source_latest_canonical": latest["canonical"],
        "restored_canonical": restored["canonical"],
        "source_latest_processing": latest["processing"]["tables"],
        "restored_processing": restored["processing"]["tables"],
        "build_identity": json.loads(os.environ["PGAG_AGE_RECOVERY_BUILD_IDENTITY"]),
    }
    write_private(directory / "report.json", report)
    return report


def main():
    try:
        require(sys.platform == "linux" and os.geteuid() != 0, "nonroot_linux_required")
        require(pg_agmemory.__version__ == "0.3.0", "service_0_3_0_required")
        directory = Path(os.environ["PGAG_AGE_RECOVERY_DIRECTORY"]).resolve()
        require(directory.is_dir() and stat.S_IMODE(directory.stat().st_mode) == 0o700,
                "private_directory_required")
        identity = json.loads(os.environ["PGAG_AGE_RECOVERY_BUILD_IDENTITY"])
        require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                == identity["files_sha256"]["scripts/smoke-age-recovery.py"],
                "smoke_changed_after_build_identity")
        require(sys.argv[1:] in (["seed"], ["manifest"], ["later"], ["recover"]), "invalid_phase")
        phases = {"seed": seed, "manifest": manifest, "later": later, "recover": recover}
        phase = phases[sys.argv[1]]
        result = asyncio.run(phase(directory))
    except (DrillError, OSError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError, psycopg.Error, httpx.HTTPError, MemoryError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc) if isinstance(exc, DrillError)
                          else type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
