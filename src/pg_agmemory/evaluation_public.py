"""Independent adapter for the pinned MIT-declared LongMemEval oracle diagnostic."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pg_agmemory.evaluation import EvaluationDataset, EvaluationQuestion, EvaluationSource

LONGMEMEVAL_REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
LONGMEMEVAL_ORACLE_DIGEST = "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c"
LONGMEMEVAL_ORACLE_BYTES = 15388478
SELECTOR_SEED = "pg-agmemory-public-v1"


def opaque_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(("pg-agmemory-eval-source-v1", *parts)).encode()).hexdigest()


def normalize_longmemeval(records: list[dict[str, Any]]) -> EvaluationDataset:
    selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identifiers: set[str] = set()
    for record in records:
        question_id = record["question_id"]
        if not isinstance(question_id, str) or question_id in identifiers:
            raise ValueError("Question IDs must be distinct strings")
        identifiers.add(question_id)
        stratum = "abstention" if question_id.endswith("_abs") else record["question_type"]
        if stratum not in (
            "abstention",
            "knowledge-update",
            "multi-session",
            "single-session-assistant",
            "single-session-preference",
            "single-session-user",
            "temporal-reasoning",
        ):
            raise ValueError("Unsupported LongMemEval category")
        selected[stratum].append(record)
    sources = []
    questions = []
    for stratum in sorted(selected):
        ranked = sorted(
            selected[stratum],
            key=lambda record: (
                hashlib.sha256((SELECTOR_SEED + "\0" + record["question_id"]).encode()).hexdigest(),
                record["question_id"],
            ),
        )
        for record in ranked[:2]:
            question_id = record["question_id"]
            group = opaque_id("scope", question_id)
            dates, session_ids, sessions = (
                record["haystack_dates"],
                record["haystack_session_ids"],
                record["haystack_sessions"],
            )
            if (
                not isinstance(dates, list)
                or not isinstance(session_ids, list)
                or not isinstance(sessions, list)
                or len(dates) != len(session_ids)
                or len(dates) != len(sessions)
                or len(set(session_ids)) != len(session_ids)
            ):
                raise ValueError("Mismatched or duplicate history sessions")
            relevant = {}
            ordered = sorted(
                zip(dates, session_ids, sessions, strict=True),
                key=lambda session: (session[0], opaque_id(question_id, session[1])),
            )
            for date, session_id, turns in ordered:
                if not isinstance(date, str) or not isinstance(session_id, str):
                    raise ValueError("Source timestamps and session IDs must be strings")
                if not isinstance(turns, list):
                    raise ValueError("Source session must contain turns")
                for index, turn in enumerate(turns):
                    if (
                        not isinstance(turn, dict)
                        or turn.get("role") not in ("user", "assistant")
                        or not isinstance(turn.get("content"), str)
                        or type(turn.get("has_answer")) is not bool
                    ):
                        raise ValueError("Unsupported source turn")
                    source_id = opaque_id(question_id, session_id, str(index))
                    # Dates remain source text: the benchmark does not declare a timezone.
                    text = f"Source date: {date}\nRole: {turn['role']}\n{turn['content']}"
                    sources.append(
                        EvaluationSource(
                            source_id=source_id,
                            group_id=group,
                            occurred_at="timezone-unknown",
                            text=text,
                        )
                    )
                    if turn["has_answer"] and stratum != "abstention":
                        relevant[source_id] = 1
            answer = record["answer"]
            if type(answer) not in (str, int):
                raise ValueError("Unsupported gold answer type")
            if stratum != "abstention" and not relevant:
                raise ValueError("No turn-level gold annotation for an answerable question")
            question = record["question"]
            question_date = record["question_date"]
            if not isinstance(question, str) or not isinstance(question_date, str):
                raise ValueError("Question and question date must be strings")
            questions.append(
                EvaluationQuestion(
                    question_id=question_id,
                    group_id=group,
                    category=stratum,
                    language="en",
                    split="test",
                    query=f"Question date: {question_date}\n{question}",
                    relevant=relevant,
                    answer=str(answer),
                )
            )
    return EvaluationDataset(
        dataset_id="longmemeval-cleaned/oracle/pg-agmemory-public-v1",
        origin="public",
        license="MIT",
        retrieval_unit="turn",
        source_revision=LONGMEMEVAL_REVISION,
        source_file_digest=LONGMEMEVAL_ORACLE_DIGEST,
        variant="oracle-reader-diagnostic",
        sources=sources,
        questions=questions,
    )


def load_longmemeval(path: Path) -> EvaluationDataset:
    with path.open("rb") as stream:
        raw = stream.read(LONGMEMEVAL_ORACLE_BYTES + 1)
    if (
        len(raw) != LONGMEMEVAL_ORACLE_BYTES
        or hashlib.sha256(raw).hexdigest() != LONGMEMEVAL_ORACLE_DIGEST
    ):
        raise ValueError("LongMemEval file does not match the pinned oracle artifact")
    records = json.loads(raw)
    if not isinstance(records, list) or len(records) != 500:
        raise ValueError("Unexpected LongMemEval record count")
    return normalize_longmemeval(records)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pg_agmemory.evaluation_public")
    parser.add_argument("--longmemeval-oracle", required=True, type=Path)
    args = parser.parse_args()
    try:
        dataset = load_longmemeval(args.longmemeval_oracle)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        parser.exit(2, "Invalid or unsupported pinned LongMemEval oracle artifact\n")
    print(dataset.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
