"""Current candidate packaging and frozen M3/M4 history, not runtime qualification."""

import asyncio
import hashlib
import importlib.util
import json
import re
import sys
import tomllib
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pg_agmemory import __version__, age_graph, api, cli, database
from pg_agmemory.database import RuntimeValidationError, Settings
from pg_agmemory.deletion_history import DeletionHistory
from pg_agmemory.graph_artifact import GraphArtifact
from pg_agmemory.graph_generation import INPUT_TABLES, GraphInput, input_digest
from pg_agmemory.processing_recovery import (
    TABLES,
    ProcessingRecoverySnapshot,
    StateFingerprint,
    compare_processing_state,
)
from pg_agmemory.recovery_apply import CONTENT_TABLES, ROW_TABLES, RecoveryBundle

ROOT = Path(__file__).resolve().parents[1]
AGE_COMMIT = "72707aab7ce982bf13cad3d102bd869dab07d64b"
PROFILE_DIGEST = "1b5c047e6b32da6d301a94f950ef943b79c7f5767d76bd86cc6ee08d4f31ab3f"
M4_PROFILE_DIGEST = "cba4b77ce48090e5e675406fd3a26d6d1f4be1a8efbaa7837f4a1cd76f0aff55"
M5_V5_PROFILE_DIGEST = "ecd01f7169e2d19e2c6f46e4b4e19cb3e179f3f2d6b9e82eb05e04b5936350b6"
M3_PROFILE_DIGEST = "37b0379d66341047d2def85621feff9f949cc5a42e3826d3746f51c175e0db0d"
PRE_RELEASE_PROFILE_DIGEST = "c89ed11ad1fc31038b2e168a56309c27d01521a627f2fed2e7b4ac6852fb2212"
CASE_DIMENSIONS = (
    ("chain-small", "chain", 12, 1, "outgoing"),
    ("fanout-small", "fanout", 12, 1, "outgoing"),
    ("multiseed-small", "multiseed", 12, 4, "both"),
    ("chain-medium", "chain", 64, 1, "outgoing"),
    ("fanout-medium", "fanout", 64, 1, "outgoing"),
    ("multiseed-medium", "multiseed", 64, 4, "both"),
)
ROLE = {"rolsuper": False, "rolbypassrls": False, "owns_tables": False}


def digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def fingerprints(tables):
    return tuple(StateFingerprint(table=table, rows=0, digest="a" * 64) for table in tables)


def connection(*, role=None, versions=range(1, 24)):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(side_effect=[
        ROLE.copy() if role is None else role,
        {"extversion": "0.8.6", "nspname": "public"},
    ])
    cursor.fetchall = AsyncMock(return_value=[{"version": version} for version in versions])
    conn.execute = AsyncMock(return_value=cursor)
    return conn


@pytest.fixture
def profile():
    return json.loads((ROOT / "examples/graph-resource-profile.json").read_text())


@pytest.fixture
def benchmark():
    spec = importlib.util.spec_from_file_location(
        "m3_release_graph_benchmark", ROOT / "scripts/graph-resource-benchmark.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshot():
    return ProcessingRecoverySnapshot(
        tenant_id=UUID(int=1), lineage="b" * 64, access_epoch=1, deletion_epoch=1,
        tables=fingerprints(TABLES),
    )


def test_release_version_matches_project_and_editable_lock_root():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    roots = [package for package in lock["package"] if package["name"] == "pg-agmemory"]
    assert project["name"] == "pg-agmemory" and len(roots) == 1
    assert __version__ == project["version"] == roots[0]["version"] == "0.4.0.dev1"
    assert roots[0]["source"] == {"editable": "."}


def test_schema_twenty_three_is_a_packaged_migration_ledger_not_an_age_install(monkeypatch):
    assert database.SCHEMA_VERSION == len(database.MIGRATIONS) == 23
    assert tuple(int(name.split("_", 1)[0]) for name in database.MIGRATIONS) == tuple(range(1, 24))
    assert database.MIGRATIONS[-1] == "023_english_fts.sql"
    statements = tuple(
        files("pg_agmemory").joinpath("storage", name).read_text()
        for name in database.MIGRATIONS
    )
    assert "CREATE ROLE pgag_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;" in statements[0]
    assert not any(re.search(
        r"\bCREATE\s+EXTENSION\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?age\"?\b", statement, re.I,
    ) for statement in statements)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.fetchone.return_value = ("0.8.6", "public")
    connector = MagicMock(return_value=conn)
    boundary = MagicMock()
    monkeypatch.setattr(database.psycopg, "connect", connector)
    monkeypatch.setattr(database, "transaction", boundary)
    monkeypatch.setattr(database, "rebuild", MagicMock())
    database.migrate("unused-offline")
    connector.assert_called_once_with("unused-offline", autocommit=True)
    boundary.assert_called_once_with(conn)
    boundary.return_value.__enter__.assert_called_once_with()
    boundary.return_value.__exit__.assert_called_once_with(None, None, None)
    calls = conn.execute.call_args_list
    assert [call.args[1] for call in calls if call.args[0].startswith(
        "INSERT INTO public.pgag_schema_migration"
    )] == [(version,) for version in range(1, 24)]
    assert [call.args[0] for call in calls if call.args[0] in statements] == list(statements)


def test_runtime_requires_the_entire_schema_twenty_three_ledger(monkeypatch):
    valid = connection()
    monkeypatch.setattr(database, "connect", AsyncMock(return_value=valid))
    asyncio.run(database.validate_runtime("unused-offline"))
    queries = [call.args[0] for call in valid.execute.await_args_list]
    assert "SELECT version FROM public.pgag_schema_migration ORDER BY version" in queries
    assert database.VECTOR_QUERY in queries
    assert not any("ag_catalog" in query or "extname='age'" in query for query in queries)
    for versions in (
        range(1, 20), range(1, 21), range(1, 22), range(1, 23), range(1, 25),
        (*range(1, 22), 23), (23,),
    ):
        conn = connection(versions=versions)
        monkeypatch.setattr(database, "connect", AsyncMock(return_value=conn))
        with pytest.raises(RuntimeValidationError) as error:
            asyncio.run(database.validate_runtime("unused-offline"))
        assert error.value.code == "schema_version_mismatch"


def test_default_sql_and_strict_age_opt_in_survive_release(monkeypatch):
    for field in ("DATABASE_URL", "JWT_PUBLIC_KEY", "JWT_ISSUER", "JWT_AUDIENCE"):
        monkeypatch.setenv("PGAG_" + field, "unused-offline")
    monkeypatch.delenv("PGAG_GRAPH_BACKEND", raising=False)
    settings = Settings.from_env()
    assert settings.graph_backend == "sql"
    assert Settings("unused", "unused", "unused", "unused").graph_backend == "sql"
    for backend in ("sql", "age"):
        monkeypatch.setenv("PGAG_GRAPH_BACKEND", backend)
        assert Settings.from_env().graph_backend == backend
    for invalid in ("", "AGE", " age", "age ", "native_vle", "auto", "fallback"):
        monkeypatch.setenv("PGAG_GRAPH_BACKEND", invalid)
        with pytest.raises(ValueError, match="PGAG_GRAPH_BACKEND must be sql or age"):
            Settings.from_env()
        with pytest.raises(ValueError, match="PGAG_GRAPH_BACKEND must be sql or age"):
            replace(settings, graph_backend=invalid)


def test_api_v1_reports_m5_candidate_without_changing_backend_readiness(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    settings = Settings("unused-offline", public, "https://issuer.test", "pgag-test")
    for backend in ("sql", "age"):
        core, native = AsyncMock(), AsyncMock()
        monkeypatch.setattr(api, "validate_runtime", core)
        monkeypatch.setattr(api, "validate_age_runtime", native)
        app = api.create_app(replace(settings, graph_backend=backend))
        endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/capabilities")
        capabilities = asyncio.run(endpoint())
        assert tuple(capabilities[key] for key in (
            "api_version", "service_version", "schema_version", "stage", "graph_backend",
        )) == ("v1", "0.4.0.dev1", 23, "m5-production-candidate", backend)
        assert {"graph_expand", "entities", "structured_relations"} <= set(capabilities["features"])
        assert capabilities["request_deadline"] == {
            "scope": "v1_http", "seconds": 30.0, "error_response_reserve_seconds": 1.0,
            "commit_expiry_outcome": "unknown", "hard_realtime": False,
        }
        assert capabilities["embedding_migration_administration"] == {
            "command": "embedding-migration", "transport": "admin-cli", "read_only": True,
            "automatic_inference": False, "automatic_cutover": False,
            "production_qualified": False,
        }
        assert capabilities["monitoring_export_administration"] == {
            "command": "monitoring-export", "transport": "admin-cli", "database_read_only": True,
            "statistics_atomic": False, "automatic_remediation": False,
            "production_qualified": False,
        }
        administration = capabilities["age_projection_administration"]
        assert administration["required_age_commit"] == AGE_COMMIT
        assert administration["fallback"] == "explicit_sql_configuration_only"
        assert administration["isolated_recovery"]["automatic_reactivation"] is False
        source = capabilities["source_access_administration"]
        assert source["transport"] == "admin-cli" and source["command"] == "source-access"
        assert source["recovery"] == "exact_content_only"
        assert source["automatic_reactivation"] is False
        assert source["post_restore_revalidation"] == "explicit"
        with TestClient(app) as client:
            response = client.get("/readyz")
            assert response.status_code == 200 and response.json() == {"status": "ready"}
            assert response.headers["cache-control"] == "no-store"
            schema = client.get("/openapi.json").json()
            assert schema["info"]["version"] == "0.4.0.dev1"
            assert "/v1/graph/expand" in schema["paths"]
            assert not any("source-access" in path for path in schema["paths"])
            assert client.get("/v1/capabilities").status_code == 401
        assert core.await_count == 2
        assert native.await_count == (2 if backend == "age" else 0)


@pytest.mark.parametrize("command,module", [
    ("embedding-migration", "pg_agmemory.embedding_migration"),
    ("monitoring-export", "pg_agmemory.monitoring"),
])
def test_m5_operator_commands_dispatch_without_runtime_mutations(monkeypatch, command, module):
    handler = MagicMock()
    monkeypatch.setattr(importlib.import_module(module), "main", handler)
    monkeypatch.setattr(sys, "argv", ["pg-agmemory", command, "--help"])
    cli.main()
    handler.assert_called_once_with(["--help"])


def test_native_distribution_pins_remain_exact_not_just_matching_labels():
    manifest = json.loads((ROOT / "patches/age/source.json").read_text())
    expected = {
        "commit": AGE_COMMIT,
        "upstream_base_commit": "fa109ef1ddb1c7a945a1c340195d650000e49713",
        "archive_sha256": "8ec03c92b420e7c45db5cde853bdd5a9964a4d5bc620923afa23ec380823f285",
        "patch_sha256": "89b711f33234a304476d3164b578f8da700a527a6668782823c694695efc1653",
        "source_tree_sha256": "0ea5a00a8c73ee85d561a6c3627815f43c3f15f32608da923c07a855ad206c91",
        "distribution_stamp_sha256":
            "74e7696316dcb361e6f2c9286e4a945bc7c7a086c3d62a03462709799b0b4a6b",
        "preload_diagnostic_sha256":
            "55a03bcf23ab675fdc4512a08c7969c59a4b61f74ea33dec3ab0a9ff334c2538",
        "postgres_version_num": 180006,
        "vector_version": "0.8.6",
        "age_control_version": "1.8.0",
        "base_image": (
            "docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:"
            "2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
        ),
    }
    assert {field: manifest[field] for field in expected} == expected
    assert (database.VECTOR_VERSION, age_graph.AGE_VERSION, age_graph.AGE_COMMIT) == (
        "0.8.6", "1.8.0", AGE_COMMIT,
    )
    dockerfile = (ROOT / "Dockerfile.age-patched").read_text()
    assert [line.split()[1] for line in dockerfile.splitlines() if line.startswith("FROM ")] == [
        expected["base_image"], expected["base_image"],
    ]
    for field in ("patch", "distribution_stamp", "preload_diagnostic"):
        payload = (ROOT / manifest[field + "_file"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected[field + "_sha256"]
        assert expected[field + "_sha256"] in dockerfile
    assert "'PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2)'" in dockerfile
    assert expected["archive_sha256"] in dockerfile
    assert expected["source_tree_sha256"] in dockerfile
    stamp = (ROOT / manifest["distribution_stamp_file"]).read_text()
    identity = json.loads(stamp.split("RETURN '", 1)[1].split("'::pg_catalog.jsonb;", 1)[0])
    assert identity == {field: expected[field] for field in (
        "commit", "upstream_base_commit", "patch_sha256",
    )}


def test_pilot_profile_has_a_new_fixed_identity_not_a_relaxed_validator(profile, benchmark):
    assert (profile["name"], profile["service_version"], profile["format"]) == (
        "M5-bounded-native-graph-v7", "0.4.0.dev1", "pgag-graph-resource-profile-v1",
    )
    assert digest(profile) == benchmark.FROZEN_PROFILE_DIGEST == PROFILE_DIGEST
    assert benchmark.load_profile(ROOT / "examples/graph-resource-profile.json") == profile
    for changes in (
        {"name": "M3-bounded-native-graph-v2", "service_version": "0.2.0",
         "schema_version": 20},
        {"name": "M3-bounded-native-graph-v1", "service_version": "0.1.3"},
        {"name": "M3-bounded-native-graph-v1"}, {"service_version": "0.1.3"},
        {"name": "M5-bounded-native-graph-v5", "schema_version": 21},
        {"name": "M5-bounded-native-graph-v5"},
        {"name": "M5-bounded-native-graph-v6", "schema_version": 22},
        {"gate": {**profile["gate"], "end_to_end_p95_ms_exclusive": 1501}},
    ):
        with pytest.raises(benchmark.BenchmarkError, match="profile_not_frozen"):
            benchmark.validate_profile(profile | changes)


def test_frozen_m3_profile_retains_its_historical_identity(profile):
    historical = json.loads((ROOT / "examples/graph-resource-profile-m3-v2.json").read_text())
    assert digest(historical) == M3_PROFILE_DIGEST
    assert historical == profile | {
        "name": "M3-bounded-native-graph-v2", "service_version": "0.2.0", "schema_version": 20,
    }
    # Captured from 37f9c21; packaged tests do not need a Git checkout.
    original = historical | {"name": "M3-bounded-native-graph-v1", "service_version": "0.1.3"}
    assert digest(original) == PRE_RELEASE_PROFILE_DIGEST


def test_frozen_m4_profile_retains_its_historical_identity(profile):
    payload = (ROOT / "examples/graph-resource-profile-m4-v4.json").read_bytes()
    historical = json.loads(payload)
    assert hashlib.sha256(payload).hexdigest() == (
        "d5ecf34b6925822d1951452364cde110683af63b72cfd06253c20c9c50917fa0"
    )
    assert digest(historical) == M4_PROFILE_DIGEST
    assert historical == profile | {
        "name": "M4-bounded-native-graph-v4", "service_version": "0.3.0", "schema_version": 21,
    }


def test_frozen_m5_v5_profile_retains_its_schema21_identity(profile, benchmark):
    payload = (ROOT / "examples/graph-resource-profile-m5-v5.json").read_bytes()
    historical = json.loads(payload)
    assert hashlib.sha256(payload).hexdigest() == (
        "ca66037d172d57bf27f02b0bb672f39243d8d269bf8899476ae73f05849fd667"
    )
    assert digest(historical) == M5_V5_PROFILE_DIGEST
    assert historical == profile | {
        "name": "M5-bounded-native-graph-v5", "schema_version": 21,
    }
    with pytest.raises(benchmark.BenchmarkError, match="profile_not_frozen"):
        benchmark.validate_profile(historical)


def test_frozen_m5_v6_profile_retains_its_schema22_identity(profile, benchmark):
    payload = (ROOT / "examples/graph-resource-profile-m5-v6.json").read_bytes()
    historical = json.loads(payload)
    assert digest(historical) == (
        "9987ab2fb791948de1c5310758fd9d639d9cbe365c08cb3405838ff27ef07ab1"
    )
    assert historical == profile | {
        "name": "M5-bounded-native-graph-v6", "schema_version": 22,
    }
    with pytest.raises(benchmark.BenchmarkError, match="profile_not_frozen"):
        benchmark.validate_profile(historical)


def test_pilot_profile_preserves_workload_dimensions_and_thresholds(profile):
    assert tuple(tuple(case[field] for field in (
        "id", "shape", "nodes_per_scope", "seed_count", "direction",
    )) for case in profile["cases"]) == CASE_DIMENSIONS
    assert profile["resources"] == {
        "database": {"vcpus": 6, "memory_gib": 24},
        "application": {"vcpus": 2, "memory_gib": 8},
    }
    assert tuple(profile["sampling"][field] for field in (
        "warmup_pairs", "measured_pairs", "abort_case_on_error",
    )) == (3, 30, True)
    assert tuple(profile["fixture"][field] for field in (
        "tenant_per_case", "scopes_per_case", "readable_scopes_per_case",
        "hidden_scope_matches_topology", "revised_relations_per_scope",
        "other_tenant_decoys", "providers", "max_hops", "max_paths",
    )) == (1, 2, 1, True, 1, 0, 0, 2, 100)
    assert profile["gate"] == {
        "end_to_end_p95_ms_exclusive": 1500, "zero_errors": True,
        "exact_independent_oracle": True, "complete_sample_counts": True,
        "both_backends_every_case": True,
    }


def test_recovery_v1_schema_twenty_three_artifacts_never_authorize_restore(snapshot):
    history = DeletionHistory(
        tenant_id=snapshot.tenant_id, access_epoch=1, deletion_epoch=1, records=(),
    )
    bundle = RecoveryBundle(
        reference=snapshot, content=fingerprints(CONTENT_TABLES),
        rows={table: [] for table in ROW_TABLES}, signature="c" * 64,
    )
    for artifact, expected_format in (
        (history, "pgag-deletion-history-v1"),
        (snapshot, "pgag-processing-recovery-v1"),
        (bundle, "pgag-recovery-apply-v1"),
    ):
        model = type(artifact)
        value = artifact.model_dump(mode="json")
        parsed = model.model_validate_json(json.dumps(value))
        assert parsed == artifact and parsed.format == expected_format
        assert parsed.restore_authorized is False
        schema = value["reference"] if isinstance(artifact, RecoveryBundle) else value
        assert schema["schema_version"] == 23
        for change in ({"restore_authorized": True}, {"format": expected_format[:-1] + "2"}):
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(value | change))
        for previous_schema in (20, 21, 22):
            schema["schema_version"] = previous_schema
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(value))
    check = compare_processing_state(snapshot, snapshot)
    assert check.processing_state_matches and check.restore_authorized is False
    assert tuple(table.table for table in snapshot.tables[-3:]) == (
        "memory_ops.graph_generation", "memory_ops.graph_generation_state",
        "memory_ops.age_projection",
    )
    assert all(table.table in CONTENT_TABLES for table in snapshot.tables[-3:])


def test_graph_recovery_artifacts_keep_v1_schema_twenty_three_and_require_publication(snapshot):
    graph_input = GraphInput(
        tenant_id=snapshot.tenant_id, access_epoch=1, deletion_epoch=1,
        tables=fingerprints(INPUT_TABLES),
    )
    artifact = GraphArtifact(
        tenant_id=snapshot.tenant_id, generation_id=UUID(int=2), parent_id=None,
        profile_digest="a" * 64, input_digest="b" * 64, input_snapshot=graph_input,
        nodes=(), edge_revisions=(), signature="c" * 64,
    )
    for model, value, expected_format in (
        (GraphInput, graph_input, "pgag-graph-input-v1"),
        (GraphArtifact, artifact, "pgag-graph-artifact-v1"),
    ):
        parsed = model.model_validate_json(value.model_dump_json())
        assert parsed == value and parsed.format == expected_format and parsed.schema_version == 23
    assert artifact.serving_enabled is False and artifact.permission_filter_required is True
    for changes in ({"serving_enabled": True}, {"permission_filter_required": False}):
        with pytest.raises(ValidationError):
            GraphArtifact.model_validate_json(json.dumps(
                artifact.model_dump(mode="json") | changes,
            ))
    changed = snapshot.model_dump(mode="json")
    changed["tables"][-1]["digest"] = "d" * 64
    check = compare_processing_state(
        snapshot, ProcessingRecoverySnapshot.model_validate_json(json.dumps(changed)),
    )
    assert check.differences == ("memory_ops.age_projection",)
    assert check.processing_state_matches is False and check.restore_authorized is False


@pytest.mark.parametrize("schema", [19, 20, 21, 22])
def test_historical_graph_inputs_remain_readable_without_becoming_current(snapshot, schema):
    current = GraphInput(
        tenant_id=snapshot.tenant_id, access_epoch=1, deletion_epoch=1,
        tables=fingerprints(INPUT_TABLES),
    )
    historical = GraphInput.model_validate_json(json.dumps(
        current.model_dump(mode="json") | {"schema_version": schema},
    ))
    assert historical.schema_version == schema and current.schema_version == 23
    assert historical.tables == current.tables
    assert input_digest(historical, b"historical-test-key") != input_digest(
        current, b"historical-test-key",
    )
    assert GraphInput.model_validate_json(historical.model_dump_json()) == historical


def test_sql_runtime_still_rejects_superuser_bypassrls_and_ownership(monkeypatch):
    for field in ROLE:
        conn = connection(role=ROLE | {field: True})
        monkeypatch.setattr(database, "connect", AsyncMock(return_value=conn))
        with pytest.raises(RuntimeValidationError) as error:
            asyncio.run(database.validate_runtime("unused-offline"))
        assert error.value.code == "runtime_role_invalid"
        assert conn.execute.await_count == 2
        conn.execute.return_value.fetchall.assert_not_awaited()


def test_age_runtime_still_rejects_superuser_bypassrls_and_ownership():
    for field in ROLE:
        conn = connection(role=ROLE | {field: True})
        with pytest.raises(RuntimeValidationError) as error:
            asyncio.run(age_graph.validate_age_connection(conn))
        assert error.value.code == "runtime_role_invalid"
        conn.execute.assert_awaited_once()
