import asyncio
import json
import math

import pytest

from pg_agmemory.providers import InferenceInput

pytestmark = pytest.mark.live

SAMPLES = [
    pytest.param(
        "Synthetic test: Project Cedar is paused. Deployment has not been approved. "
        "The next review is on September 20.",
        id="en",
    ),
    pytest.param(
        "合成テスト：Cedar計画は一時停止中です。デプロイは承認されていません。"
        "次回のレビューは9月20日です。",
        id="ja",
    ),
]


@pytest.mark.parametrize("text", SAMPLES)
def test_live_summary_contract(live_provider, text):
    if live_provider.settings.text_model is None:
        pytest.skip("The selected profile has no text model")
    data = InferenceInput(text=text)
    result = asyncio.run(live_provider.summarize(data))
    assert result.input_digest == data.digest()
    assert result.status == "untrusted"
    assert result.model == live_provider.settings.text_model
    assert result.summary.strip()
    print(result.model_dump_json())


@pytest.mark.parametrize("text", SAMPLES)
def test_live_embedding_contract(live_provider, text):
    if live_provider.settings.embedding_model is None:
        pytest.skip("The selected profile has no embedding model")
    data = InferenceInput(text=text)
    result = asyncio.run(live_provider.embed(data))
    assert result.input_digest == data.digest()
    assert result.model == live_provider.settings.embedding_model
    assert len(result.values) == 768
    assert all(math.isfinite(value) for value in result.values)
    assert math.isfinite(math.hypot(*result.values)) and math.hypot(*result.values) > 0
    print(
        json.dumps(
            {
                "input_digest": result.input_digest,
                "model": result.model.model_dump(),
                "dimensions": len(result.values),
                "finite_nonzero": True,
            }
        )
    )
