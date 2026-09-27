import importlib.util
import json
import subprocess
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
import pytest

from pg_agmemory.models import MemoryItem, Observe, Remember, ReviseAssertion
from pg_agmemory.service import build_context

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "english_profile_benchmark", ROOT / "scripts/english-profile-benchmark.py",
)
assert SPEC is not None and SPEC.loader is not None
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def identity(label):
    return str(uuid5(NAMESPACE_URL, "english-cost-test:" + label))


def fixture_data():
    episodes = [{"id": identity(f"episode-{i}"), "index": i, "text": bench.episode_text(i)}
                for i in range(256)]
    assertions = [{"id": identity(f"assertion-{i}"), "index": i, "source": episodes[i]["id"]}
                  for i in range(32)]
    return {
        "identity": {"scope": identity("scope"), "tenant": identity("tenant"),
                     "principal": identity("principal")},
        "episodes": episodes, "assertions": assertions,
        "historical_at": "2026-09-01T00:00:00.000001Z",
        "tombstoned_retained_ids": [row["id"] for row in episodes[-2:] + assertions[-2:]],
        "purged_ids": [identity("purged-source"), identity("purged-child")],
    }


def response(fixture, query, profile):
    by_id = {row["id"]: row for row in fixture["episodes"]}
    items = []
    for memory_id in query["ids"]:
        episode = by_id.get(memory_id)
        value = "silver" if query.get("revision") == 1 else "golden"
        items.append(MemoryItem(
            memory_id=memory_id, revision=query.get("revision", 1), type=query["kind"],
            content=episode["text"] if episode else f"ledger0000 / status: {value}",
            recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
            source=[] if episode else [fixture["assertions"][0]["source"]],
        ))
    pack, selected, omitted = build_context(items, 8000)
    assert len(selected) == len(items) and not omitted
    return {
        "items": [item.model_dump(mode="json") for item in items], "context_pack": pack,
        "coverage": {"retrieval_complete": True, "synthesis_pending": False,
                     "projection_pending": False, "jobs_pending": False,
                     "lexical_incomplete": False, "vector_incomplete": False,
                     "graph_used": False, "truncated": False},
        "consistency": {"access_epoch": 1, "deletion_epoch": 2},
        "search_profile": profile, "retrieval_mode": "lexical", "embedding_model": None,
        "empty_reason": None if items else "not_found",
    }


class ReadAPI:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []

    def request(self, path, body, *, status=200, key="read"):
        assert path == "/v1/recall"
        self.calls.append(deepcopy(body))
        assert body["scope_ids"] == [self.fixture["identity"]["scope"]]
        assert body["retrieval_mode"] == "lexical" and body["as_of"] == bench.FUTURE
        if status == 404:
            assert body["required_memory_refs"][0]["memory_id"] in (
                self.fixture["tombstoned_retained_ids"] + self.fixture["purged_ids"]
            )
            return {"code": "not_found"}, 1
        if body.get("required_memory_refs"):
            query = {"name": "required", "kind": "episode",
                     "ids": [body["required_memory_refs"][0]["memory_id"]]}
        else:
            query = next(query for query in bench.queries(self.fixture, body["search_profile"])
                         if query["query"] == body["query"])
        return response(self.fixture, query, body["search_profile"]), 100


class WriteAPI:
    def __init__(self):
        self.calls = []

    def request(self, path, body, *, status=200, key="read"):
        self.calls.append((path, body, key))
        assert status == 201
        if path == "/v1/observe":
            Observe.model_validate(body)
            return {"memory_id": identity(key), "revision": 1}, 10
        if path == "/v1/remember":
            Remember.model_validate(body)
            return {"memory_id": identity(key), "revision": 1}, 20
        ReviseAssertion.model_validate(body)
        return {"memory_id": path.split("/")[3], "revision": 2}, 30


def test_fixed_profiles_have_explicit_modest_cost_and_no_model_permission():
    spec = bench.specification("modest")
    assert (spec["episodes"], spec["assertions"], spec["repetitions"]) == (256, 32, 4)
    assert spec["request_concurrency"] == 1 and spec["automatic_processing"] is False
    assert spec["performance_gate"] is None and spec["cold_cache_claimed"] is False
    assert bench.specification("smoke")["repetitions"] == 2
    with pytest.raises(bench.BenchmarkError, match="unsupported_profile"):
        bench.specification("production")


@pytest.mark.parametrize("invalid", [0, 9, -1, True, "1"])
def test_repetition_order_is_bounded_and_strict(invalid):
    with pytest.raises(bench.BenchmarkError, match="invalid_repetition"):
        bench.repetition_order(invalid)


def test_serial_arm_order_alternates_without_changing_read_workload():
    assert bench.repetition_order(1) == ["schema22", "schema23"]
    assert bench.repetition_order(2) == ["schema23", "schema22"]
    spec = bench.specification("modest")
    order = bench.read_order(spec, 2)
    assert order == bench.read_order(spec, 2)
    assert order != bench.read_order(spec, 1)
    assert Counter(order) == {index: 20 for index in range(8)}
    assert Counter(bench.read_order(spec, 2, True)) == {index: 3 for index in range(8)}


def test_literal_and_stemmed_query_expectations_are_not_equal_selectivity():
    fixture = fixture_data()
    simple = bench.queries(fixture, bench.SIMPLE)
    english = bench.queries(fixture, bench.ENGLISH)
    assert [query["query"] for query in simple] == [query["query"] for query in english]
    assert [len(query["ids"]) for query in simple] == [4, 1, 1, 0, 0, 0, 1, 1]
    assert [len(query["ids"]) for query in english] == [4, 4, 4, 4, 0, 0, 1, 1]
    assert all(query["query"] for query in english)
    assert english[-1]["known_at"] == fixture["historical_at"]
    assert english[-1]["revision"] == 1 and english[-2]["revision"] == 2
    assert bench.episode_text(0) != bench.episode_text(64)


def test_native_guards_check_history_purge_and_retained_payload_tombstones():
    fixture = fixture_data()
    api = ReadAPI(fixture)
    bench.guard_native(api, fixture, bench.ENGLISH)
    assert len(api.calls) == 8 + 1 + 6
    assert all(call["known_at"] for call in api.calls)
    assert len([call for call in api.calls if call["query"] == ""]) == 7
    assert all(call["max_items"] == 1 for call in api.calls if call["query"] == "")


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "content", "revision",
    "profile", "truncated", "incomplete", "embedding", "context",
])
def test_read_guards_abort_instead_of_accepting_bad_timings(mutation):
    fixture = fixture_data()
    query = bench.queries(fixture, bench.ENGLISH)[0]
    value = response(fixture, query, bench.ENGLISH)
    if mutation == "missing":
        value["items"].pop()
    elif mutation == "duplicate":
        value["items"].append(value["items"][0])
    elif mutation == "content":
        value["items"][0]["content"] = "different evidence"
    elif mutation == "revision":
        value["items"][0]["revision"] = 2
    elif mutation == "profile":
        value["search_profile"] = bench.SIMPLE
    elif mutation == "truncated":
        value["coverage"]["truncated"] = True
    elif mutation == "incomplete":
        value["coverage"]["lexical_incomplete"] = True
    elif mutation == "context":
        value["context_pack"]["text"] = "replacement"
    else:
        value["embedding_model"] = {"name": "not-permitted", "revision": "v1"}
    with pytest.raises(bench.BenchmarkError):
        bench.validate_read(value, query, fixture, bench.ENGLISH)


def test_read_measurement_records_each_fixed_query_count_and_identical_seed_order():
    fixture, spec = fixture_data(), bench.specification("smoke")
    old = bench.measure_reads(ReadAPI(fixture), fixture, spec, 1, bench.SIMPLE)
    new = bench.measure_reads(ReadAPI(fixture), fixture, spec, 1, bench.ENGLISH)
    assert old["query_order_sha256"] == new["query_order_sha256"]
    assert [row["selected_count"] for row in old["queries"]] != [
        row["selected_count"] for row in new["queries"]
    ]
    assert all(row["samples_ns"] == [100] * 3 for row in new["queries"])


def test_writes_are_new_nonreplayed_triplets_with_explicit_warmups():
    api, spec = WriteAPI(), bench.specification("smoke")
    result = bench.measure_writes(api, fixture_data(), spec)
    assert len(api.calls) == 3 * (1 + 3)
    assert len({key for _, _, key in api.calls}) == len(api.calls)
    assert result["operations"]["observe"]["samples_ns"] == [10, 10, 10]
    assert result["operations"]["remember"]["samples_ns"] == [20, 20, 20]
    assert result["operations"]["revision"]["samples_ns"] == [30, 30, 30]
    assert all(not body["auto_extract"] and not body["auto_embed"]
               for route, body, _ in api.calls if route == "/v1/observe")


def test_write_count_guard_includes_both_revision_specific_provenance_edges():
    spec = bench.specification("smoke")
    before = {"counts": dict.fromkeys(bench.CANONICAL_TABLES, 10), "epochs": [1, 2]}
    after = deepcopy(before)
    for table, per_triplet in zip(bench.CANONICAL_TABLES, [2, 1, 1, 2, 2, 0, 0, 0], strict=True):
        after["counts"][table] += per_triplet * 4
    bench.write_count_guard(before, after, spec)
    after["counts"]["memory.provenance_edge"] -= 1
    with pytest.raises(bench.BenchmarkError, match="write_count"):
        bench.write_count_guard(before, after, spec)


@pytest.mark.parametrize("field", [
    "counts", "epochs", "canonical_sha256", "japanese_projection_sha256",
])
def test_upgrade_preservation_does_not_allow_history_or_japanese_rebuild(field):
    before = {"counts": {"rows": 12}, "epochs": [1, 2], "canonical_sha256": "canonical",
              "japanese_projection_sha256": "japanese", "projections": {bench.JAPANESE: 12}}
    after = deepcopy(before)
    after["projections"][bench.ENGLISH] = 10
    bench.preserved(before, after)
    after[field] = "changed"
    with pytest.raises(bench.BenchmarkError, match="upgrade_changed"):
        bench.preserved(before, after)


def test_percentiles_keep_empty_unknown_and_have_no_latency_pass_threshold():
    assert bench.distribution([])["p95"] is None
    assert bench.distribution(list(range(1, 21)))["p95"] == 19
    assert bench.distribution([10**12])["p50"] == 10**12
    with pytest.raises(bench.BenchmarkError):
        bench.distribution([True])
    with pytest.raises(bench.BenchmarkError):
        bench.distribution([-1])


def test_owned_database_validation_rejects_shared_or_injected_names(monkeypatch):
    monkeypatch.setenv("PGAG_BENCHMARK_RUN_ID", "012345abcdef")
    monkeypatch.setenv("PGAG_BENCHMARK_ADMIN_URL", "postgresql://localhost/shared")
    with pytest.raises(bench.BenchmarkError, match="private_owned_database"):
        bench.owned_url()
    expected = "postgresql://localhost/pgag_en_012345abcdef_r01_new"
    monkeypatch.setenv("PGAG_BENCHMARK_ADMIN_URL", expected)
    assert bench.owned_url() == expected
    monkeypatch.setenv("PGAG_BENCHMARK_RUN_ID", "not-a-run-id")
    with pytest.raises(bench.BenchmarkError, match="invalid_owned_run_id"):
        bench.owned_url()


def test_schema_mismatch_is_rejected_before_connecting(monkeypatch):
    monkeypatch.setattr(bench, "SCHEMA_VERSION", 23)

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("No client may connect with a mismatched schema identity")

    monkeypatch.setattr(bench.psycopg, "connect", forbidden_connect)
    with pytest.raises(bench.BenchmarkError, match="client_schema_mismatch"):
        bench.verify_database("unused", 22)


def test_next_seed_rejects_surviving_cluster_role_before_migration(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, query):
            self.query = query
            return self

        def fetchone(self):
            if "to_regnamespace" in self.query:
                return (None,)
            assert self.query == "SELECT 1 FROM pg_roles WHERE rolname='pgag_runtime'"
            return (1,)

    def forbidden_migration(_):
        raise AssertionError("An existing cluster-global role must not be ignored")

    monkeypatch.setattr(bench, "SCHEMA_VERSION", 22)
    monkeypatch.setattr(bench, "owned_url", lambda: "unused")
    monkeypatch.setattr(bench.psycopg, "connect", lambda _: Connection())
    monkeypatch.setattr(bench, "migrate", forbidden_migration)
    with pytest.raises(bench.BenchmarkError, match="seed_requires_fresh_cluster"):
        bench.seed(Path("."), bench.specification("smoke"), {})


def test_provision_does_not_silently_reuse_a_preexisting_login_role(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, query, parameters):
            assert query == "SELECT 1 FROM pg_roles WHERE rolname=%s"
            assert parameters == ("pgag_en_runtime_012345abcdef",)
            return self

        def fetchone(self):
            return (1,)

    monkeypatch.setenv("PGAG_BENCHMARK_RUNTIME_PASSWORD", "synthetic-test-only")
    monkeypatch.setattr(bench.psycopg, "connect", lambda _: Connection())
    with pytest.raises(bench.BenchmarkError, match="owned_runtime_role_already_exists"):
        bench.provision("unused", "012345abcdef")


def test_failure_records_identify_phase_repetition_schema_and_sqlstate_without_credentials(
    monkeypatch,
):
    monkeypatch.setattr(bench, "RUN_CONTEXT", {"phase": "seed", "repetition": 2})
    monkeypatch.setattr(bench, "SCHEMA_VERSION", 22)
    failure = bench.failure_record(psycopg.errors.DuplicateObject("sensitive must-not-log"))
    assert failure == {
        "status": "failed", "code": "DuplicateObject", "schema": 22,
        "phase": "seed", "repetition": 2, "sqlstate": "42710",
    }
    assert "must-not-log" not in bench.canonical(failure)


def test_upgrade_requires_quiescence_and_does_not_terminate_other_clients(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, query):
            self.query = query
            assert "terminate" not in query
            return self

        def fetchone(self):
            if "server_version_num" in self.query:
                return ("180006",)
            if "pg_extension" in self.query:
                return ("0.8.6",)
            assert "pg_stat_activity" in self.query
            return (1,)

        def fetchall(self):
            return [(version,) for version in range(1, 23)]

    monkeypatch.setattr(bench.psycopg, "connect", lambda _: Connection())
    with pytest.raises(bench.BenchmarkError, match="api_or_other_client_still_connected"):
        bench.verify_database("unused", 22, client=False, quiescent=True)


@pytest.mark.parametrize("version", [22, 23])
def test_retained_tombstones_use_one_guarded_manifest_transaction(monkeypatch, version):
    fixture = fixture_data()
    calls = []

    class Connection:
        def __enter__(self):
            calls.append(("begin", None))
            return self

        def __exit__(self, exception_type, *_):
            assert exception_type is None
            assert calls[-1][0] == "SET CONSTRAINTS ALL IMMEDIATE"
            calls.append(("commit", None))

        def execute(self, query, parameters=None):
            calls.append((" ".join(query.split()), parameters))
            return self

        def fetchall(self):
            return [(memory_id, fixture["identity"]["scope"])
                    for memory_id in sorted(fixture["tombstoned_retained_ids"])]

        def fetchone(self):
            return (3,)

    monkeypatch.setattr(bench, "SCHEMA_VERSION", version)
    monkeypatch.setattr(bench.psycopg, "connect", lambda _: Connection())
    receipt = bench.inject_retained_tombstones("unused", fixture)
    manifest = [(query, parameters) for query, parameters in calls
                if query.startswith("INSERT INTO memory_ops.deletion_request")]
    targets = [parameters for query, parameters in calls
               if query.startswith("INSERT INTO memory_ops.deletion_target")]
    tombstones = [parameters for query, parameters in calls
                  if query.startswith("INSERT INTO memory_ops.object_tombstone")]
    assert len(manifest) == 1 and len(targets) == len(tombstones) == 4
    assert "'suppress','blocked_for_reads'" in manifest[0][0]
    assert manifest[0][1] == (
        fixture["identity"]["tenant"], receipt["deletion_id"],
        fixture["identity"]["principal"], 4, 3,
    )
    assert [row[-1] for row in targets] == [1, 2, 3, 4]
    assert {row[2] for row in targets} == set(fixture["tombstoned_retained_ids"])
    assert receipt["deletion_epoch"] == 3 and receipt["payloads_retained"]
    assert calls[0][0] == "begin" and calls[-1][0] == "commit"
    assert not any("DISABLE" in query or "recovery_apply" in query for query, _ in calls)


def test_wrapper_bakes_both_archives_and_never_mounts_source_or_prunes_shared_resources():
    wrapper = (ROOT / "scripts/measure-english-profile-costs.sh").read_text()
    assert "3bb0ee2cb79aa476208ea6446eaa47345305cd0b" in wrapper
    assert "b8d775d2d9bd1e14ebf9cea4b978a11dce06346c" in wrapper
    assert 'git archive "$ref"' in wrapper
    assert 'COPY benchmark/ /benchmark/' in wrapper
    assert '-v "$directory:/artifacts"' in wrapper
    assert ":/app" not in wrapper and ":/work" not in wrapper and ":ro" not in wrapper
    assert 'container image rm "$name"' in wrapper and 'container rm --force "$db"' in wrapper
    for forbidden in ("prune", "builder stop", "system stop", "docker run", "podman", "sudo"):
        assert forbidden not in wrapper
    assert "CREATE DATABASE" in wrapper and "WITH TEMPLATE" in wrapper
    assert "repetition % 2" in wrapper and "180006" in wrapper


def test_wrapper_starts_and_removes_a_distinct_cluster_inside_each_serial_repetition():
    wrapper = (ROOT / "scripts/measure-english-profile-costs.sh").read_text()
    loop = wrapper.split("for ((repetition=1;", 1)[1]
    assert loop.index('start_database "$label"') < loop.index('run_phase "$image22" seed')
    assert loop.index('stop_database\n') > loop.index('DROP DATABASE')
    assert 'db="pgag-en-${run_id}-${repetition_label}-db"' in wrapper
    assert 'cluster_lifecycle:"fresh_per_repetition"' in wrapper
    assert "maximum_concurrent_database_containers:1" in wrapper
    assert "DROP ROLE" not in wrapper
    assert '"code":"container_phase_failed","phase":"%s","repetition":%d' in wrapper


@pytest.mark.parametrize(("mode", "prior_status", "exit_status", "failures"), [
    ("removed", 0, 0, 0),
    ("already-absent-step", 7, 7, 0),
    ("already-absent-images", 0, 0, 0),
    ("database-removal-fails", 0, 1, 1),
    ("image-removal-fails", 7, 7, 1),
    ("successful-removal-still-present", 0, 1, 1),
    ("inventory-unavailable", 0, 1, 2),
    ("report-write-fails", 0, 1, None),
])
def test_cleanup_verifies_owned_absence_and_preserves_failure_status(
    mode, prior_status, exit_status, failures,
):
    wrapper = (ROOT / "scripts/measure-english-profile-costs.sh").read_text()
    functions = wrapper.split("cleanup_remove() {", 1)[1].split("trap cleanup EXIT\n", 1)[0]
    # Execute the exact cleanup functions, replacing only the container CLI and
    # persistence boundary. No files, container resources, or external tools are used.
    script = "set -Eeuo pipefail\ncleanup_remove() {" + functions + r'''
own_step=true
own_db=true
step=owned-step
db=owned-db
owned_images=(owned-image22 owned-image23)
directory=private-output
run_complete=true
removals=0
unexpected_calls=0
container() {
    case "$*" in
        "rm --force owned-step")
            removals=$((removals + 1))
            [[ "$MODE" != already-absent-step ]]
            ;;
        "rm --force owned-db")
            removals=$((removals + 1))
            [[ "$MODE" != database-removal-fails ]]
            ;;
        "image rm owned-image22"|"image rm owned-image23")
            removals=$((removals + 1))
            if [[ "$MODE" == already-absent-images ]]; then return 1; fi
            if [[ "$MODE" == image-removal-fails && "$3" == owned-image23 ]]; then return 1; fi
            return 0
            ;;
        "list --all --quiet")
            if [[ "$MODE" == inventory-unavailable ]]; then return 9; fi
            printf 'buildkit\nunrelated-user-container\n'
            if [[ "$MODE" == database-removal-fails ]]; then printf 'owned-db\n'; fi
            return 0
            ;;
        "image list --quiet")
            printf 'unrelated-user-image\n'
            if [[ "$MODE" == image-removal-fails ||
                  "$MODE" == successful-removal-still-present ]]; then
                printf 'owned-image23\n'
            fi
            return 0
            ;;
        *)
            unexpected_calls=$((unexpected_calls + 1))
            return 97
            ;;
    esac
}
persist_cleanup_report() {
    [[ "$removals" == 4 && "$unexpected_calls" == 0 ]] || return 98
    if [[ "$MODE" == report-write-fails ]]; then return 31; fi
    cleanup_report
}
trap cleanup EXIT
exit "$PRIOR_STATUS"
'''
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        env={"PATH": "", "MODE": mode, "PRIOR_STATUS": str(prior_status)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == exit_status, result.stderr
    assert ("Completed private" in result.stdout) == (exit_status == 0)
    if failures is None:
        assert "Could not persist cleanup.json" in result.stderr
        assert not result.stdout
        return
    report = json.loads(result.stdout.splitlines()[0])
    assert report["run_exit_status"] == prior_status
    assert report["exit_status"] == exit_status
    assert report["cleanup_failures"] == failures
    assert report["cleanup_complete"] is (failures == 0)
    assert report["only_run_owned_resources_removed"] is (failures == 0)
    assert report["buildkit_preserved"] is True
    assert len(report["resources"]) == 4
    by_name = {resource["name"]: resource for resource in report["resources"]}
    if mode == "already-absent-step":
        assert by_name["owned-step"]["outcome"] == "already_absent"
        assert by_name["owned-step"]["removal_exit_status"] == 1
    if mode == "already-absent-images":
        assert by_name["owned-image22"]["outcome"] == "already_absent"
        assert by_name["owned-image23"]["outcome"] == "already_absent"
    if mode == "inventory-unavailable":
        assert by_name["owned-step"]["outcome"] == "absence_unverified"
        assert by_name["owned-db"]["inventory_exit_status"] == 9
    if mode in ("image-removal-fails", "successful-removal-still-present"):
        assert by_name["owned-image23"]["outcome"] == "still_present"
    if mode == "database-removal-fails":
        assert by_name["owned-db"]["outcome"] == "still_present"


@pytest.mark.parametrize("option", ["--help", "-h"])
def test_wrapper_help_needs_no_external_commands_or_container(option):
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/measure-english-profile-costs.sh"), option],
        env={"PATH": ""}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout and "--smoke" in result.stdout
    assert not result.stderr


@pytest.mark.parametrize("arguments", [
    [], ["--unknown"], ["--smoke"], ["--help", "--smoke"], [""],
    ["not-a-private-destination"], ["/outside-repository/result"],
    [".review-artifacts/../escaped"], [".review-artifacts/"],
    [".review-artifacts/new-name", ""],
    [".review-artifacts/new-name", "--unsupported"],
    [".review-artifacts/new-name", "--smoke", "extra"],
])
def test_wrapper_rejects_invalid_arguments_before_external_commands(arguments):
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/measure-english-profile-costs.sh"), *arguments],
        env={"PATH": ""}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "command not found" not in result.stderr
    assert "illegal option" not in result.stderr


@pytest.mark.integration
def test_real_native_fixture_queries_history_and_projection_guards(env):
    class EnvironmentAPI:
        def request(self, path, body, *, status=200, key="read"):
            result = env.client.post(path, json=body, headers=env.headers(key=key))
            assert result.status_code == status, result.text
            return result.json(), 0

    actor = {"tenant": str(env.tenants[0]), "principal": str(env.principals[0]),
             "scope": str(env.scopes[0]), "subject": env.subjects[0]}
    api = EnvironmentAPI()
    fixture = bench.seed_fixture(api, actor, bench.specification("smoke"))
    receipt = bench.inject_retained_tombstones(env.admin_url, fixture)
    with psycopg.connect(env.admin_url) as conn:
        manifest = conn.execute(
            """SELECT mode,state,object_count,deletion_epoch,target_manifest_version
               FROM memory_ops.deletion_request WHERE tenant_id=%s AND id=%s""",
            (actor["tenant"], receipt["deletion_id"]),
        ).fetchone()
        assert manifest == ("suppress", "blocked_for_reads", 4, receipt["deletion_epoch"], 1)
        assert receipt["deletion_epoch"] == fixture["purge_receipt"]["deletion_epoch"] + 1
        assert conn.execute(
            "SELECT deletion_epoch FROM memory.tenant WHERE id=%s", (actor["tenant"],),
        ).fetchone() == (receipt["deletion_epoch"],)
        targets = conn.execute(
            """SELECT object_id,scope_id,ordinal FROM memory_ops.deletion_target
               WHERE tenant_id=%s AND deletion_id=%s ORDER BY ordinal""",
            (actor["tenant"], receipt["deletion_id"]),
        ).fetchall()
        assert [(str(memory_id), str(scope), ordinal) for memory_id, scope, ordinal in targets] == [
            (memory_id, actor["scope"], ordinal)
            for ordinal, memory_id in enumerate(sorted(fixture["tombstoned_retained_ids"]), 1)
        ]
        assert conn.execute(
            "SELECT count(*) FROM memory.episode WHERE tenant_id=%s", (actor["tenant"],),
        ).fetchone() == (64,)
        assert conn.execute(
            "SELECT count(*) FROM memory.assertion_revision WHERE tenant_id=%s", (actor["tenant"],),
        ).fetchone() == (16,)
    # This fixture was ingested by schema23, not upgraded from schema22. Its existing
    # English tombstone projections must be rejected, then removed to emulate backfill.
    with pytest.raises(bench.BenchmarkError, match="projection_count"):
        bench.projection_guard(env.admin_url, fixture, 23)
    with psycopg.connect(env.admin_url) as conn:
        for table, column in (("episode_lexical", "episode_id"),
                              ("assertion_lexical", "assertion_id")):
            conn.execute(
                psycopg.sql.SQL("DELETE FROM memory.{} WHERE tenant_id=%s AND profile=%s "
                               "AND {}=ANY(%s::uuid[])")
                .format(psycopg.sql.Identifier(table), psycopg.sql.Identifier(column)),
                (actor["tenant"], bench.ENGLISH, fixture["tombstoned_retained_ids"]),
            )
    verified = bench.projection_guard(env.admin_url, fixture, 23)
    assert verified["projections"]["episode_lexical"] == {bench.ENGLISH: 62, bench.JAPANESE: 64}
    assert verified["projections"]["assertion_lexical"] == {bench.ENGLISH: 12, bench.JAPANESE: 16}
    for profile in (bench.SIMPLE, bench.ENGLISH):
        bench.guard_native(api, fixture, profile)
