import hashlib
import json
from pathlib import Path

from test_processing_live import CALL_LIMIT, EMBEDDING_MODEL, TEXT_MODEL

from pg_agmemory.worker_profile import WorkerProfile

ROOT = Path(__file__).resolve().parents[1]


def test_reference_profile_matches_pinned_live_recipe(monkeypatch):
    monkeypatch.setenv("PGAG_M2_RELAY_TOKEN", "synthetic-offline-validation-only")
    result = json.loads((ROOT / "examples/reference-memory-result.json").read_text())
    profile = WorkerProfile.parse((ROOT / result["recipe"]["profile_path"]).read_bytes())
    assert profile.digest == result["recipe"]["profile_digest"]
    assert profile.settings.backend == "local_http"
    assert profile.settings.text_model.model_dump() == TEXT_MODEL
    assert profile.settings.embedding_model.model_dump() == EMBEDDING_MODEL
    assert profile.settings.max_output_tokens == 512
    assert result["recipe"]["external_model_calls_limit"] == CALL_LIMIT
    assert result["recipe"]["automatic_retries"] == 0


def test_reference_result_retains_recipe_identity_and_measurement_limits():
    result = json.loads((ROOT / "examples/reference-memory-result.json").read_text())
    recipe = ROOT / result["recipe"]["path"]
    assert hashlib.sha256(recipe.read_bytes()).hexdigest() == result["recipe"]["sha256"]
    assert result["implementation"]["current_release_rerun"] is False
    assert result["latency"]["samples"] == 1 and result["latency"]["p95_seconds"] is None
    assert result["latency"]["per_request_seconds"] is None
    assert result["footprint"]["database_bytes"] is None
    assert result["footprint"]["process_rss_bytes"] is None
    assert result["footprint"]["actual_billing"] is None
    assert result["prior_failures"]["total_model_calls"] == 4
    assert result["prior_failures"]["included_in_successful_case"] is False
    assert len(result["observations"]["model_calls"]) == CALL_LIMIT
    assert result["observations"]["reservations_retained_after_purge"] == CALL_LIMIT
