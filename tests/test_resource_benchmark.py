import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

from pg_agmemory.providers import InferenceInput, parse_extraction_proposals

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "resource_benchmark", ROOT / "scripts/resource-benchmark.py"
)
assert SPEC is not None and SPEC.loader is not None
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def test_profile_freezes_S_and_marks_reduced_runs():
    source = ROOT / "examples/resource-profile-s.json"
    full = bench.profile(source)
    development = bench.profile(source, development=True)
    preflight = bench.profile(source, preflight=True)
    assert full["tenants"] * full["episodes_per_tenant"] == 100000
    assert full["tenants"] * full["assertions_per_tenant"] == 10000
    assert full["steady_seconds"] == 1800
    assert (
        development["parent_profile_digest"]
        == preflight["parent_profile_digest"]
        == bench.digest(full)
    )
    assert development["database_settings"]["shared_buffers"] == "1GB"
    assert preflight["episodes_per_tenant"] == full["episodes_per_tenant"]
    assert development["name"] != full["name"] != preflight["name"]


def test_selectivity_counts_and_tenant_mode_cross_product():
    spec = bench.profile(ROOT / "examples/resource-profile-s.json")
    counts = Counter(bench.scope_index(i, 10000) for i in range(10000))
    assert [counts[i] for i in range(4)] == [10, 90, 900, 9000]
    matrix = Counter(bench.cohort(spec, i) for i in range(120))
    assert len(matrix) == 120 and set(matrix.values()) == {1}


def test_vectors_and_text_are_deterministic_bounded_and_not_model_measurements():
    spec = bench.profile(ROOT / "examples/resource-profile-s.json")
    assert bench.vectors(spec) == bench.vectors(spec)
    assert len(bench.vectors(spec)) == 256
    assert all(len(row) == 768 for row in bench.vectors(spec))
    for topic in (0, 17, 255):
        text = bench.text_for(topic, 512)
        assert len(text.encode()) == 512 and text.endswith(".")
        assert f"topic{topic:03}" in text


def test_controlled_provider_wire_contract_is_not_relaxed():
    response = bench.provider_response()
    choice = response["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    data = InferenceInput(text="Synthetic source")
    result = parse_extraction_proposals(
        choice["message"]["content"],
        data,
        bench.worker_profile().settings.text_model,
    )
    assert result.candidates == []


def test_percentiles_do_not_turn_missing_samples_into_passes():
    assert bench.percentile([]) is None
    assert bench.percentile(list(range(1, 101))) == 95
    assert bench.percentile([500]) == 500


def test_profile_rejects_wrong_dimensions_before_seed(tmp_path):
    value = json.loads((ROOT / "examples/resource-profile-s.json").read_text())
    value["vector_dimensions"] = 1
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(value))
    with pytest.raises(bench.ResourceError):
        bench.profile(path)
