import json
import random
import re
import string
import subprocess
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime

import pytest

from pg_agmemory import evaluation_fixtures as fixtures
from pg_agmemory.evaluation import EvaluationDataset


@pytest.fixture(scope="module")
def corpus():
    return fixtures.synthetic_dataset()


def test_exact_sample_group_split_and_bilingual_counts(corpus):
    assert len(corpus.sources) == 1920
    assert len(corpus.questions) == 720
    assert len({source.group_id for source in corpus.sources}) == 60
    assert Counter(question.split for question in corpus.questions) == {"dev": 120, "test": 600}
    assert Counter(question.language for question in corpus.questions) == {"en": 360, "ja": 360}
    assert Counter(
        (question.split, question.language) for question in corpus.questions
    ) == {("dev", "en"): 60, ("dev", "ja"): 60, ("test", "en"): 300, ("test", "ja"): 300}
    groups = {
        split: {question.group_id for question in corpus.questions if question.split == split}
        for split in ("dev", "test")
    }
    assert len(groups["dev"]) == 10 and len(groups["test"]) == 50
    assert not groups["dev"] & groups["test"]
    assert groups["dev"] | groups["test"] == {source.group_id for source in corpus.sources}
    assert set(Counter(question.group_id for question in corpus.questions).values()) == {12}
    assert set(Counter(source.group_id for source in corpus.sources).values()) == {32}
    categories = Counter(question.category for question in corpus.questions)
    assert categories == dict.fromkeys(fixtures.CATEGORIES, 60)
    strata = Counter(
        (question.category, question.language, question.split) for question in corpus.questions
    )
    assert strata == {
        (category, language, split): count
        for category in fixtures.CATEGORIES
        for language in ("en", "ja")
        for split, count in (("dev", 5), ("test", 25))
    }


def test_identifiers_are_opaque_unique_and_question_gold_stays_outside_sources(corpus):
    sources = {source.source_id: source for source in corpus.sources}
    question_ids = {question.question_id for question in corpus.questions}
    group_ids = {source.group_id for source in corpus.sources}
    assert len(sources) == 1920 and len(question_ids) == 720
    assert set(sources).isdisjoint(question_ids)
    assert set(sources).isdisjoint(group_ids) and question_ids.isdisjoint(group_ids)
    assert all(re.fullmatch(r"s_[0-9a-f]{32}", source_id) for source_id in sources)
    assert all(re.fullmatch(r"q_[0-9a-f]{32}", question_id) for question_id in question_ids)
    assert all(re.fullmatch(r"g_[0-9a-f]{32}", group_id) for group_id in group_ids)
    for source in corpus.sources:
        assert set(source.model_dump()) == {"source_id", "group_id", "occurred_at", "text"}
        assert source.source_id not in source.text and source.group_id not in source.text
        assert all(category not in source.source_id for category in fixtures.CATEGORIES)
        assert "question_id" not in source.text and "relevant" not in source.text
    for question in corpus.questions:
        for source_id, relevance in question.relevant.items():
            assert source_id in sources
            assert sources[source_id].group_id == question.group_id
            assert type(relevance) is int and 1 <= relevance <= 3
            assert source_id not in question.query
        assert all(question.query not in source.text for source in corpus.sources)


def test_seed_is_deterministic_distinct_and_does_not_mutate_global_rng(corpus):
    before = random.getstate()
    repeat = fixtures.synthetic_dataset(seed=42)
    assert random.getstate() == before
    assert repeat == corpus and repeat.digest() == corpus.digest()
    other = fixtures.synthetic_dataset(seed=43)
    assert other.digest() != corpus.digest()
    assert {source.source_id for source in other.sources}.isdisjoint(
        source.source_id for source in corpus.sources
    )
    assert [source.text for source in other.sources] != [source.text for source in corpus.sources]
    assert len(other.sources) == 1920 and len(other.questions) == 720
    assert Counter(question.language for question in other.questions) == {"en": 360, "ja": 360}


@pytest.mark.parametrize("seed", [-1, 2147483648, True, False, 1.0, "42", None])
def test_invalid_seed_is_rejected_without_coercion(seed):
    with pytest.raises(ValueError, match="Seed must be an integer"):
        fixtures.synthetic_dataset(seed)


@pytest.mark.parametrize("seed", [0, 2147483647])
def test_seed_boundary_values_are_supported(seed):
    data = fixtures.synthetic_dataset(seed)
    assert len(data.questions) == 720 and data.origin == "synthetic"
    assert data.dataset_id.endswith(f"-{seed}")


def test_source_corpus_does_not_depend_on_question_or_gold_rendering(monkeypatch, corpus):
    queries = deepcopy(fixtures._QUERY_TEMPLATES)
    answers = deepcopy(fixtures._ANSWER_TEMPLATES)
    queries["same_name"]["en"] = ("Changed synthetic query only?",) * 3
    answers["same_name"]["en"] = ("Changed gold only.",)
    evidence = dict(fixtures._CATEGORY_RECORDS)
    evidence["same_name"] = ("reference",)
    monkeypatch.setattr(fixtures, "_QUERY_TEMPLATES", queries)
    monkeypatch.setattr(fixtures, "_ANSWER_TEMPLATES", answers)
    monkeypatch.setattr(fixtures, "_CATEGORY_RECORDS", evidence)
    changed = fixtures.synthetic_dataset()
    assert changed.sources == corpus.sources
    assert changed.questions != corpus.questions
    assert changed.digest() != corpus.digest()
    assert all("Changed gold" not in source.text for source in changed.sources)
    assert all("Changed synthetic query" not in source.text for source in changed.sources)


def test_scoped_candidate_pools_exceed_recall_cutoff_without_cross_group_padding(corpus):
    sources_by_group = defaultdict(list)
    for source in corpus.sources:
        sources_by_group[source.group_id].append(source)
    for question in corpus.questions:
        visible = [
            source
            for source in sources_by_group[question.group_id]
            if question.as_of is None
            or datetime.fromisoformat(source.occurred_at) <= datetime.fromisoformat(question.as_of)
        ]
        assert len(visible) > 20
        assert len({source.source_id for source in visible} - question.relevant.keys()) >= 20


def test_all_dates_are_aware_and_historical_gold_obeys_actual_boundaries(corpus):
    sources = {source.source_id: source for source in corpus.sources}
    for source in corpus.sources:
        assert datetime.fromisoformat(source.occurred_at).utcoffset() is not None
    by_group = defaultdict(dict)
    for question in corpus.questions:
        by_group[question.group_id][question.category] = question
        if question.as_of is not None:
            boundary = datetime.fromisoformat(question.as_of)
            assert boundary.utcoffset() is not None
            assert all(
                datetime.fromisoformat(sources[source_id].occurred_at) <= boundary
                for source_id in question.relevant
            )
    assert sum(question.as_of is not None for question in corpus.questions) == 120
    for group_id, questions in by_group.items():
        historical = questions["temporal_history"]
        current = questions["temporal_current"]
        assert historical.language == current.language
        assert len(historical.relevant) == len(current.relevant) == 1
        old = sources[next(iter(historical.relevant))]
        new = sources[next(iter(current.relevant))]
        assert old.source_id != new.source_id
        assert old.group_id == new.group_id == group_id
        assert (
            datetime.fromisoformat(old.occurred_at)
            <= datetime.fromisoformat(historical.as_of)
            < datetime.fromisoformat(new.occurred_at)
            <= datetime.fromisoformat(current.as_of)
        )
        assert historical.answer != current.answer
        old_value = re.match(r"\d+", historical.answer).group()
        new_value = re.match(r"\d+", current.answer).group()
        assert old_value in old.text
        assert new_value in new.text and old_value in new.text
        assert old.source_id not in current.relevant and new.source_id not in historical.relevant
        assert any(
            source.group_id == group_id
            and datetime.fromisoformat(source.occurred_at)
            > datetime.fromisoformat(historical.as_of)
            for source in corpus.sources
        )


def test_unanswerable_questions_abstain_with_empty_gold_and_no_fabricated_answer(corpus):
    questions = [question for question in corpus.questions if question.category == "unanswerable"]
    assert len(questions) == 60
    assert all(question.relevant == {} and question.answer == "" for question in questions)
    assert all(
        question.relevant and question.answer
        for question in corpus.questions
        if question.category != "unanswerable"
    )


def test_same_name_questions_cannot_use_another_groups_role(corpus):
    questions = [question for question in corpus.questions if question.category == "same_name"]
    sources = {source.source_id: source for source in corpus.sources}
    for language, name in (("en", "Alex"), ("ja", "葵")):
        cohort = [question for question in questions if question.language == language]
        assert len(cohort) == 30
        assert len({question.group_id for question in cohort}) == 30
        assert len({question.answer for question in cohort}) >= 2
        for question in cohort:
            assert name in question.query
            source = sources[next(iter(question.relevant))]
            assert name in source.text and question.answer in source.text
            assert source.group_id == question.group_id
        groups_by_answer = defaultdict(set)
        for question in cohort:
            groups_by_answer[question.answer].add(question.group_id)
        assert all(
            groups.isdisjoint(other_groups)
            for answer, groups in groups_by_answer.items()
            for other_answer, other_groups in groups_by_answer.items()
            if answer != other_answer
        )


def test_reference_queries_cover_exact_ids_paths_and_versions_without_dominating(corpus):
    cohort = [question for question in corpus.questions if question.category == "exact_reference"]
    assert len(cohort) == 60 < len(corpus.questions) // 2
    for language in ("en", "ja"):
        answers = [question.answer for question in cohort if question.language == language]
        assert sum(bool(re.fullmatch(r"v\d+\.\d+\.\d+", answer)) for answer in answers) == 10
        assert sum(
            answer.startswith("services/") and answer.endswith(".toml") for answer in answers
        ) == 10
        assert sum(bool(re.fullmatch(r"R-[0-9a-f]{8}", answer)) for answer in answers) == 10
    non_reference = [
        question for question in corpus.questions if question.category != "exact_reference"
    ]
    assert not any(
        re.search(r"R-[0-9a-f]{8}|services/|s_[0-9a-f]{32}", question.query)
        for question in non_reference
    )


def test_source_corrections_keep_both_records_but_mark_the_new_one_as_primary(corpus):
    sources = {source.source_id: source for source in corpus.sources}
    cohort = [question for question in corpus.questions if question.category == "source_update"]
    for question in cohort:
        assert sorted(question.relevant.values()) == [1, 3]
        old = sources[next(key for key, grade in question.relevant.items() if grade == 1)]
        new = sources[next(key for key, grade in question.relevant.items() if grade == 3)]
        assert datetime.fromisoformat(old.occurred_at) < datetime.fromisoformat(new.occurred_at)
        old_path = re.search(r"docs/legacy/[a-z]+/recovery\.md", question.answer).group()
        new_path = re.search(r"runbooks/[a-z]+/restore-v\d+\.md", question.answer).group()
        assert old_path in old.text and new_path not in old.text
        assert old_path in new.text and new_path in new.text
        assert any(
            word in new.text for word in ("superseded", "corrected", "moved", "訂正", "移動")
        )
        assert question.answer.startswith(
            "The current location is " if question.language == "en" else "現行の場所は"
        )


def test_negation_uncertainty_and_injection_gold_do_not_invent_authority(corpus):
    sources = {source.source_id: source for source in corpus.sources}
    for question in corpus.questions:
        if question.category not in ("negation", "uncertainty", "quoted_injection"):
            continue
        source = sources[next(iter(question.relevant))]
        if question.category == "negation":
            assert question.answer.startswith(
                "Not approved" if question.language == "en" else "未承認"
            )
            assert any(
                marker in source.text
                for marker in (
                    "did not approve",
                    "without approval",
                    "withheld approval",
                    "承認していない",
                    "承認は得られず",
                    "承認を明示的に見送った",
                )
            )
        elif question.category == "uncertainty":
            assert (
                "unconfirmed hypothesis" if question.language == "en" else "未確認の仮説"
            ) in question.answer
            assert any(
                marker in source.text
                for marker in ("might", "unconfirmed", "tentative", "仮説", "可能性", "暫定")
            )
        else:
            assert question.answer.startswith(
                "None:" if question.language == "en" else "権限はない"
            )
            assert any(
                marker in source.text
                for marker in (
                    "not an approval",
                    "quote has no authority",
                    "not a valid decision",
                    "承認や権限付与ではない",
                    "引用に権限はなく",
                    "有効な判断や権限変更ではない",
                )
            )


def test_multi_session_next_steps_require_failed_and_later_unfinished_plan(corpus):
    sources = {source.source_id: source for source in corpus.sources}
    questions_by_group = defaultdict(dict)
    for question in corpus.questions:
        questions_by_group[question.group_id][question.category] = question
    for questions in questions_by_group.values():
        failed = questions["failed_approach"]
        followup = questions["next_steps"]
        assert failed.language == followup.language
        assert sorted(followup.relevant.values()) == [2, 3]
        old_id = next(key for key, grade in followup.relevant.items() if grade == 2)
        new_id = next(key for key, grade in followup.relevant.items() if grade == 3)
        assert set(failed.relevant) == {old_id}
        assert (
            datetime.fromisoformat(sources[old_id].occurred_at)
            < datetime.fromisoformat(sources[new_id].occurred_at)
        )
        expected = "not completed" if followup.language == "en" else "まだ完了していない"
        assert expected in followup.answer
        assert any(
            marker in sources[new_id].text
            for marker in (
                "not been performed",
                "not claimed",
                "not a completed",
                "まだ実施",
                "完了",
            )
        )


def template_pattern(template):
    return re.compile(
        "".join(
            re.escape(literal) + (".+?" if field is not None else "")
            for literal, field, _, _ in string.Formatter().parse(template)
        )
        + r"\Z",
        re.DOTALL,
    )


@pytest.mark.parametrize("category", fixtures.CATEGORIES)
@pytest.mark.parametrize("language", ["en", "ja"])
def test_every_language_category_uses_multiple_source_and_query_templates(
    corpus, category, language
):
    cohort = [
        question
        for question in corpus.questions
        if question.category == category and question.language == language
    ]
    query_templates = fixtures._QUERY_TEMPLATES[category][language]
    assert len(set(query_templates)) == 3
    assert sum(
        any(template_pattern(template).fullmatch(question.query) for question in cohort)
        for template in query_templates
    ) == 3
    group_ids = {question.group_id for question in cohort}
    for record in fixtures._CATEGORY_RECORDS[category]:
        source_templates = fixtures._SOURCE_TEMPLATES[record][language]
        assert len(set(source_templates)) == 3
        assert sum(
            any(
                template_pattern(template).fullmatch(source.text)
                for source in corpus.sources
                if source.group_id in group_ids
            )
            for template in source_templates
        ) == 3


def test_synthetic_limitations_are_explicit_and_dataset_round_trips(corpus):
    assert corpus.origin == "synthetic" and corpus.license == "MIT"
    assert corpus.retrieval_unit == "source"
    assert corpus.source_revision == "internal-synthetic-templates-v1"
    assert corpus.source_file_digest is None
    assert "template gold only, not human assessment or real-task replay" in corpus.variant
    assert any("20 real-task" in limitation for limitation in fixtures.LIMITATIONS)
    restored = EvaluationDataset.model_validate_json(corpus.model_dump_json())
    assert restored == corpus and restored.digest() == corpus.digest()


def test_cli_default_writes_only_dataset_json(capsys, corpus):
    fixtures.main([])
    captured = capsys.readouterr()
    assert captured.err == ""
    restored = EvaluationDataset.model_validate_json(captured.out)
    assert restored == corpus
    assert json.loads(captured.out)["origin"] == "synthetic"


def test_module_cli_accepts_explicit_seed_without_network_or_provider_dependencies():
    completed = subprocess.run(
        [sys.executable, "-m", "pg_agmemory.evaluation_fixtures", "--seed", "43"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0 and completed.stderr == ""
    result = EvaluationDataset.model_validate_json(completed.stdout)
    assert result.digest() == fixtures.synthetic_dataset(43).digest()


def test_module_import_does_not_load_httpx_or_inference_providers():
    code = """
import builtins
import sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "httpx" or name.startswith("httpx.") or name in (
        "pg_agmemory.providers", "pg_agmemory.azure_inference"
    ):
        raise AssertionError("Fixture import must not require provider extras")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from pg_agmemory.evaluation_fixtures import synthetic_dataset
assert "httpx" not in sys.modules
assert "pg_agmemory.providers" not in sys.modules
assert len(synthetic_dataset().questions) == 720
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0 and completed.stdout == completed.stderr == ""


@pytest.mark.parametrize("seed", ["-1", "2147483648"])
def test_cli_rejects_seed_outside_bound(seed, capsys):
    with pytest.raises(SystemExit) as failure:
        fixtures.main(["--seed", seed])
    captured = capsys.readouterr()
    assert failure.value.code == 2 and captured.out == ""
    assert "--seed must be between" in captured.err
