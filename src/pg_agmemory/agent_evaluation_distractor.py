"""Frozen synthetic long histories with relevant evidence among durable distractors.

The twenty target scenarios and their six local context facts are separately
authored. Eighteen additional durable facts and completed one-shot checks use
deterministic shared templates, scoped to each scenario's fictional projects.
No inference, private traces, retrieval outcomes or previous answers author labels.
"""

from typing import Annotated, Literal

from pydantic import Field

from pg_agmemory.agent_evaluation import AgentMemoryCase, Event, Identifier

COHORT_ID = "distractor-synthetic-v1"
COHORT_VERSION = 1
COHORT_DESCRIPTION = (
    "Twenty project-aware synthetic histories of exactly thirty-two chronological events: "
    "ten English, ten Japanese; sixteen scalar answers and four explicit-forget abstentions. "
    "Each history has twenty-four durable near-topic distractors that must remain retained. "
    "Four histories require two-event evidence chains, four have two corrections, and "
    "exactly four answerable histories have all required evidence in their last two events."
)
COHORT_DISCLAIMER = (
    "Project-aware coding-assistant authorship, with separately authored target scenarios "
    "and local context plus shared deterministic synthetic distractor templates. "
    "First-evaluation-use only: these histories are unsubmitted before their first evaluation "
    "and known after that run; subsequent runs are reuse. Policies are frozen: unchanged "
    "retention, planner, reader and scoring contracts; freeze and review data before inference. "
    "External qualification is false. This is not independent, blinded, human-authored, public "
    "or representative evidence, and not a claim about training-data exclusion or release "
    "qualification. Structural tests do not establish semantic correctness or usefulness."
)
COHORT_METADATA = {
    "cohort_id": COHORT_ID,
    "version": COHORT_VERSION,
    "description": COHORT_DESCRIPTION,
    "provenance": (
        "project-aware coding assistant; separately authored synthetic target scenarios "
        "and six local facts per case, plus eighteen shared deterministic distractor templates"
    ),
    "unseen_to_eval_model_before_first_run": True,
    "first_evaluation_use_only": True,
    "known_after_first_run": True,
    "policies_frozen": True,
    "external_qualification": False,
    "independently_human_authored": False,
    "blinded": False,
    "model_output_derived": False,
    "disclaimer": COHORT_DISCLAIMER,
}

# Offline audit roles only; these positions are not included in any model prompt.
DURABLE_DISTRACTOR_POSITIONS = (
    2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 16,
    18, 19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30,
)
_SCENARIO_POSITIONS = (1, 5, 9, 13, 17, 21, 31, 32)


class DistractorMemoryCase(AgentMemoryCase):
    events: Annotated[tuple[Event, ...], Field(min_length=32, max_length=32)]
    expected_keep_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=32)]


def _durable_details(
    project: str, peer: str, topic: str, language: Literal["en", "ja"],
) -> tuple[str, ...]:
    if language == "en":
        return (
            f"For {project} {topic}, future checklists must name the owning team.",
            f"For {project} {topic}, always show units beside quantities in reference notes.",
            f"For {project} {topic}, preserve source item identifiers across document revisions.",
            f"For {project} {topic}, archive finalized review notes with their batch identifier.",
            f"For {project} {topic}, never merge reference records belonging to distinct owners.",
            f"For {project} {topic}, always put text labels beside colored status indicators.",
            f"For {project} {topic}, printed reference appendices must retain their page numbers.",
            f"For {project} {topic}, future reference diagrams must include a symbol legend.",
            f"For {project} {topic}, glossary entries must spell out abbreviations on first use.",
            f"The separate {peer} {topic} team always keeps table headers visible in its editor.",
            f"The separate {peer} {topic} team must keep practice and approved materials apart.",
            f"The separate {peer} {topic} team always assigns a number to each review checklist.",
            f"The separate {peer} {topic} team must name an owner for every open action.",
            f"The separate {peer} {topic} team always preserves timestamps in its activity log.",
            f"The separate {peer} {topic} team must put each decision in a separate log entry.",
            f"The separate {peer} {topic} team always labels missing values explicitly in tables.",
            f"The separate {peer} {topic} team must use a separate review folder for each season.",
            f"The separate {peer} {topic} team always dates its revised cover sheet.",
        )
    return (
        f"{project}の{topic}では、今後も点検表に担当班の名前を記載してください。",
        f"{project}の{topic}では、今後も参考資料の数量に単位を添えてください。",
        f"{project}の{topic}では、今後も文書改訂時に元の項目識別子を引き継いでください。",
        f"{project}の{topic}では、今後も確定した確認記録に作業組の識別子を付けて保存してください。",
        f"{project}の{topic}では、今後も所有者の異なる参考記録を統合しないでください。",
        f"{project}の{topic}では、今後も状態を色だけで示さず文字の説明を添えてください。",
        f"{project}の{topic}では、今後も印刷する参考付録のページ番号を残してください。",
        f"{project}の{topic}では、今後も参考図に記号の凡例を付けてください。",
        f"{project}の{topic}では、今後も用語集で略語を初めて使う際に正式名称を添えてください。",
        f"別企画の{peer}の{topic}班は、今後も編集画面で表の見出しを固定してください。",
        f"別企画の{peer}の{topic}班は、今後も練習用と承認済みの資料を分離してください。",
        f"別企画の{peer}の{topic}班は、今後も確認用の点検表ごとに番号を付けてください。",
        f"別企画の{peer}の{topic}班は、今後も未完了の作業ごとに担当者名を記録してください。",
        f"別企画の{peer}の{topic}班は、今後も活動記録の時刻を省略しないでください。",
        f"別企画の{peer}の{topic}班は、今後も一つの決定を一つの記録欄に書いてください。",
        f"別企画の{peer}の{topic}班は、今後も表の欠損値を明示してください。",
        f"別企画の{peer}の{topic}班は、今後も季節ごとに確認資料の保管先を分けてください。",
        f"別企画の{peer}の{topic}班は、今後も表紙に改訂日を記載してください。",
    )


def _case(
    number: int,
    category: str,
    language: Literal["en", "ja"],
    *,
    project: str,
    peer: str,
    topic: str,
    facts: tuple[str, ...],
    rows: tuple[tuple[int, str], ...],
    question: str,
    answer: str | None,
    keep: tuple[int, ...],
    sources: tuple[int, ...] = (),
    forbidden: tuple[str, ...] = (),
) -> DistractorMemoryCase:
    if len(facts) != 6 or len(dict(rows)) != len(rows):
        raise ValueError("Each scenario requires six local facts and unique scenario positions")
    if not {position for position, _ in rows} <= set(_SCENARIO_POSITIONS):
        raise ValueError("Scenario rows cannot overwrite durable distractors")
    case_id = f"distractor-{number:02d}"
    texts = dict(zip(
        DURABLE_DISTRACTOR_POSITIONS,
        (*facts, *_durable_details(project, peer, topic, language)),
        strict=True,
    ))
    for position in _SCENARIO_POSITIONS:
        if language == "en":
            texts[position] = (
                f"{project} {topic}: the one-off check of practice slip "
                f"{number:02d}/{position:02d} is complete; no continuing task remains."
            )
        else:
            texts[position] = (
                f"{project}の{topic}で、練習票{number:02d}/{position:02d}の一度限りの"
                "確認依頼は完了しました。継続する作業も指示もありません。"
            )
    texts.update(rows)
    return DistractorMemoryCase(
        case_id=case_id,
        category=category,
        language=language,
        events=tuple(
            Event(
                event_id=f"{case_id}-e{position}",
                occurred_at=(
                    f"2026-09-{number + (position - 1) // 16:02d}"
                    f"T09:{(position - 1) % 16:02d}:00Z"
                ),
                text=texts[position],
            )
            for position in range(1, 33)
        ),
        question=question,
        expected_answer=answer,
        expected_keep_ids=tuple(
            f"{case_id}-e{position}"
            for position in sorted((*DURABLE_DISTRACTOR_POSITIONS, *keep))
        ),
        required_source_ids=tuple(f"{case_id}-e{position}" for position in sources),
        forbidden_answers=forbidden,
    )


def distractor_cases() -> tuple[DistractorMemoryCase, ...]:
    """Return frozen fixtures; gold relevance never changes the retention policy.

    All twenty-four distractors per case remain current and gold keep, even though
    they do not answer the question. Only completed one-shots, obsolete corrections
    and explicitly revoked values are gold forget. Revocations omit removed values.
    """
    return (
        _case(
            1, "cross_session_preference", "en",
            project="Trelliscope", peer="Trelliscope Studio", topic="dispatch review",
            facts=(
                "For Trelliscope dispatch review, I always want unanswered flags shown.",
                "Trelliscope dispatch review must keep courier names in a separate column.",
                "For Trelliscope, future dispatch summaries must include the route identifier.",
                "For Trelliscope dispatch review, always hide cancelled items from the active tab.",
                "Trelliscope Studio dispatch cards must always sort by courier surname.",
                "Trelliscope Studio dispatch review must always show the destination district.",
            ),
            rows=((1, "Across sessions, sort my Trelliscope dispatch cards by "
                      '"pickup slot ascending". This is my ongoing preference.'),),
            question="Which sort order do I prefer for Trelliscope dispatch cards?",
            answer="pickup slot ascending", keep=(1,), sources=(1,),
        ),
        _case(
            2, "cross_session_preference", "en",
            project="Bramblefolio", peer="Bramblefolio Annex", topic="catalog proof",
            facts=(
                "Bramblefolio catalog proofs must always display the illustration credit.",
                "For Bramblefolio catalog proofs, I always want page margins visible.",
                "Bramblefolio catalog proofs must keep item numbers stable across editions.",
                "For Bramblefolio, always place catalog proof comments beside the affected page.",
                "Bramblefolio Annex must always use Accordion board for catalog proof notes.",
                "Bramblefolio Annex catalog proofs must always include a contents page.",
            ),
            rows=((13, "For all future Bramblefolio catalog proof sessions, put my "
                       'annotation notes in "Quill register".'),),
            question="Where should my Bramblefolio catalog proof annotation notes go?",
            answer="Quill register", keep=(13,), sources=(13,),
        ),
        _case(
            3, "cross_session_preference", "ja",
            project="菫時計舎", peer="菫時計舎分室", topic="校正相談",
            facts=(
                "菫時計舎の校正相談では、今後も図の番号を本文と同じ欄に表示してください。",
                "菫時計舎の校正相談は、今後も未回答の相談に印を付けてください。",
                "菫時計舎では、今後も校正相談の図版と本文を別々に確認してください。",
                "菫時計舎の校正相談では、今後も改訂箇所に下線を付けてください。",
                "菫時計舎分室の校正相談は、今後も電話で受けてください。",
                "菫時計舎分室の校正相談では、今後も受付担当者を記録してください。",
            ),
            rows=(
                (5, "今後の菫時計舎の校正相談には、私の希望する窓口「若紫受付」を使ってください。"),
                (21, "継続する窓口案内です。「若紫受付」の連絡方法は「手書き連絡箱」です。"),
            ),
            question="菫時計舎の校正相談で私が希望する連絡方法は？",
            answer="手書き連絡箱", keep=(5, 21), sources=(5, 21),
        ),
        _case(
            4, "cross_session_preference", "ja",
            project="凪絵工房", peer="凪絵工房別館", topic="展示案内",
            facts=(
                "凪絵工房の展示案内では、今後も作品番号を省略しないでください。",
                "凪絵工房の展示案内では、今後も部屋ごとに段落を分けてください。",
                "凪絵工房では、今後も展示案内の挿絵に短い説明を添えてください。",
                "凪絵工房の展示案内では、今後も入口を示す地図を付けてください。",
                "凪絵工房別館の展示案内は、今後も作者紹介から説明を始めてください。",
                "凪絵工房別館では、今後も展示案内の注意事項を囲み枠にしてください。",
            ),
            rows=((31, "今後の凪絵工房の展示案内では、私は「鑑賞の順路」から"
                       "説明を聞きたいです。"),),
            question="凪絵工房の展示案内では、私に何から説明しますか？",
            answer="鑑賞の順路", keep=(31,), sources=(31,),
        ),
        _case(
            5, "project_constraint", "en",
            project="Velvetbeam", peer="Velvetbeam Archive", topic="pattern export",
            facts=(
                "Velvetbeam pattern exports must always separate indoor and outdoor patterns.",
                "Velvetbeam pattern exports must always include a swatch identifier.",
                "Velvetbeam pattern exports must preserve leading zeros in pattern numbers.",
                "Velvetbeam pattern exports must always include a column legend.",
                "Velvetbeam Archive pattern exports have a standing limit of 410 tiles per file.",
                "Velvetbeam Archive pattern exports must always include a collection year.",
            ),
            rows=((9, "The standing file-size constraint for Velvetbeam pattern export "
                      "is a maximum of 640 tiles per file."),),
            question="What per-file cap, including the unit, applies to Velvetbeam pattern export?",
            answer="640 tiles", keep=(9,), sources=(9,),
        ),
        _case(
            6, "project_constraint", "en",
            project="Morrowquay", peer="Morrowquay Yard", topic="crate inspection",
            facts=(
                "Morrowquay crate inspection must always record the crate's painted identifier.",
                "Morrowquay crate inspection must always use a checklist for missing fasteners.",
                "Morrowquay crate inspection must always record the inspector's team.",
                "Morrowquay crate inspection must keep wood and canvas observations separate.",
                "Morrowquay Yard crate inspection must always be approved by Courtyard desk.",
                "Morrowquay Yard crate inspection must always attach the packing diagram.",
            ),
            rows=(
                (1, "Morrowquay crate inspection must always obtain approval through "
                    "the Heron review route."),
                (17, "The standing directory assigns the Heron review route to Lantern desk."),
            ),
            question="Which desk must approve Morrowquay crate inspection?",
            answer="Lantern desk", keep=(1, 17), sources=(1, 17),
        ),
        _case(
            7, "project_constraint", "ja",
            project="琥珀刷房", peer="琥珀刷房資料室", topic="版画梱包",
            facts=(
                "琥珀刷房の版画梱包では、今後も作品番号を外箱に記載してください。",
                "琥珀刷房の版画梱包では、今後も紙質の異なる作品を別包みにしてください。",
                "琥珀刷房の版画梱包では、今後も向きを示す矢印を付けてください。",
                "琥珀刷房の版画梱包では、今後も包みごとに検品者を記録してください。",
                "琥珀刷房資料室の版画梱包は、今後も一包み18枚までにしてください。",
                "琥珀刷房資料室の版画梱包では、今後も緩衝紙の枚数を記録してください。",
            ),
            rows=((21, "琥珀刷房の版画梱包は、今後も一包み36枚を上限としてください。"),),
            question="琥珀刷房の版画梱包で、一包みの上限は単位も含めていくつですか？",
            answer="36枚", keep=(21,), sources=(21,),
        ),
        _case(
            8, "project_constraint", "ja",
            project="青簾造形", peer="青簾造形分館", topic="模型審査",
            facts=(
                "青簾造形の模型審査では、今後も縮尺を申請書に記載してください。",
                "青簾造形の模型審査では、今後も台座と本体を別々に撮影してください。",
                "青簾造形の模型審査では、今後も材料一覧を添付してください。",
                "青簾造形の模型審査では、今後も可動部を図面で明示してください。",
                "青簾造形分館の模型審査は、今後も南棟造形係が担当してください。",
                "青簾造形分館の模型審査では、今後も収納時の寸法を記録してください。",
            ),
            rows=(
                (31, "青簾造形の模型審査は、今後も必ず「空輪審査経路」を通してください。"),
                (32, "継続する担当表では、「空輪審査経路」の担当部署は「北棟工芸係」です。"),
            ),
            question="青簾造形の模型審査を担当する部署は？",
            answer="北棟工芸係", keep=(31, 32), sources=(31, 32),
        ),
        _case(
            9, "corrected_current_value", "en",
            project="Rillweave", peer="Rillweave Gallery", topic="catalog edition",
            facts=(
                "Rillweave catalog editions must always include a contributor index.",
                "Rillweave catalog editions must always keep figures beside their captions.",
                "Rillweave catalog editions must always preserve archival item identifiers.",
                "Rillweave catalog editions must always mark foldout pages in the contents.",
                "Rillweave Gallery's standing catalog edition label is gallery-rill-4.",
                "Rillweave Gallery catalog editions must always include an exhibition index.",
            ),
            rows=(
                (1, "The ongoing Rillweave catalog edition label is weave-amber-3."),
                (9, "Correction: the current Rillweave catalog edition label is weave-ivory-6. "
                    "The earlier edition label is obsolete."),
                (21, "Final correction: the current Rillweave catalog edition label is "
                     "weave-cobalt-9. All earlier edition labels are obsolete."),
            ),
            question="What is the current Rillweave catalog edition label?",
            answer="weave-cobalt-9", keep=(21,), sources=(21,),
            forbidden=("weave-amber-3", "weave-ivory-6"),
        ),
        _case(
            10, "corrected_current_value", "en",
            project="Pollenarc", peer="Pollenarc Workshop", topic="exhibition opening",
            facts=(
                "Pollenarc exhibition opening plans must always include an entrance sketch.",
                "Pollenarc exhibition opening plans must always name the greeting team.",
                "Pollenarc exhibition opening plans must always separate setup from visitor tasks.",
                "Pollenarc exhibition opening plans must always include a sign placement list.",
                "Pollenarc Workshop's exhibition opening remains scheduled for 2028-11-06.",
                "Pollenarc Workshop exhibition opening plans must always include a seating map.",
            ),
            rows=(
                (5, "Pollenarc's exhibition opening date is scheduled for 2028-10-04."),
                (17, "Correction: Pollenarc's current exhibition opening date is 2028-10-12. "
                     "The previous opening date is obsolete."),
                (32, "Final correction: Pollenarc's current exhibition opening date is 2028-10-19. "
                     "All earlier opening dates are obsolete."),
            ),
            question="What is Pollenarc's current exhibition opening date?",
            answer="2028-10-19", keep=(32,), sources=(32,),
            forbidden=("2028-10-04", "2028-10-12"),
        ),
        _case(
            11, "corrected_current_value", "ja",
            project="山繭文庫", peer="山繭文庫分室", topic="索引整備",
            facts=(
                "山繭文庫の索引整備では、今後も巻番号を見出しの横に付けてください。",
                "山繭文庫の索引整備では、今後も読み仮名を独立した欄に記載してください。",
                "山繭文庫の索引整備では、今後も別名を補助欄に残してください。",
                "山繭文庫の索引整備では、今後も欠巻を明示してください。",
                "山繭文庫分室の索引整備に使う現行の分類票コードは分室ろ七です。",
                "山繭文庫分室の索引整備では、今後も棚番号を末尾に付けてください。",
            ),
            rows=(
                (1, "山繭文庫の索引整備では、継続して分類票コード「こよみ一式」を使ってください。"),
                (13, "訂正です。山繭文庫の索引整備の現在の分類票コードは「こよみ二式」です。"
                     "以前の分類票コードは無効です。"),
                (21, "最終訂正です。山繭文庫の索引整備の現在の分類票コードは「こよみ五式」です。"
                     "それ以前の分類票コードはすべて無効です。"),
            ),
            question="山繭文庫の索引整備に使う現在の分類票コードは？",
            answer="こよみ五式", keep=(21,), sources=(21,),
            forbidden=("こよみ一式", "こよみ二式"),
        ),
        _case(
            12, "corrected_current_value", "ja",
            project="月砂製本", peer="月砂製本別舎", topic="見本回覧",
            facts=(
                "月砂製本の見本回覧では、今後も表紙の素材名を記載してください。",
                "月砂製本の見本回覧では、今後も紙厚を単独の欄に記載してください。",
                "月砂製本の見本回覧では、今後も角の加工方法を図示してください。",
                "月砂製本の見本回覧では、今後も受取確認を記録してください。",
                "月砂製本別舎の見本回覧では、継続して梅棚受取台を受取場所にしてください。",
                "月砂製本別舎の見本回覧では、今後も返却予定を別欄に記載してください。",
            ),
            rows=(
                (5, "月砂製本の見本回覧では、継続して受取場所を「柳棚受取台」にしてください。"),
                (9, "訂正です。月砂製本の見本回覧の現在の受取場所は「楡棚受取台」です。"
                    "前の受取場所は無効です。"),
                (17, "最終訂正です。月砂製本の見本回覧の現在の受取場所は「桂棚受取台」です。"
                     "それ以前の受取場所はすべて無効です。"),
            ),
            question="月砂製本の見本回覧における現在の受取場所は？",
            answer="桂棚受取台", keep=(17,), sources=(17,),
            forbidden=("柳棚受取台", "楡棚受取台"),
        ),
        _case(
            13, "explicit_forget", "en",
            project="Thistledock", peer="Thistledock Classroom", topic="rehearsal board",
            facts=(
                "Thistledock rehearsal boards must always keep scene numbers visible.",
                "Thistledock rehearsal boards must always mark scenery tasks separately.",
                "Thistledock rehearsal boards must always identify the stage lead.",
                "Thistledock rehearsal boards must always separate approved and draft sketches.",
                "Thistledock Classroom's ongoing rehearsal board mascot is paper otter.",
                "Thistledock Classroom rehearsal boards must always include a prop inventory.",
            ),
            rows=(
                (1, "Remember Thistledock's ongoing rehearsal board mascot: velvet puffin."),
                (21, "Forget Thistledock's remembered rehearsal board mascot. Retain this "
                     "revocation: its mascot is unknown until I explicitly name a new one."),
            ),
            question="What is Thistledock's rehearsal board mascot?",
            answer=None, keep=(21,), forbidden=("velvet puffin",),
        ),
        _case(
            14, "explicit_forget", "en",
            project="Saffronspool", peer="Saffronspool Annex", topic="sample exchange",
            facts=(
                "Saffronspool sample exchanges must always record fabric dimensions.",
                "Saffronspool sample exchanges must always keep dye lots separate.",
                "Saffronspool sample exchanges must always attach a care instruction card.",
                "Saffronspool sample exchanges must always record each sample's return owner.",
                "Saffronspool Annex's ongoing sample exchange nickname is spindle picnic.",
                "Saffronspool Annex sample exchanges must always include a texture note.",
            ),
            rows=(
                (9, "Remember the ongoing Saffronspool sample exchange nickname: bobbin parade."),
                (31, "Forget the Saffronspool sample exchange nickname. Retain this revocation: "
                     "the nickname is unknown until I explicitly provide a new one."),
            ),
            question="What is the Saffronspool sample exchange nickname?",
            answer=None, keep=(31,), forbidden=("bobbin parade",),
        ),
        _case(
            15, "explicit_forget", "ja",
            project="花礫模型", peer="花礫模型学習室", topic="試作展示",
            facts=(
                "花礫模型の試作展示では、今後も部品の材質を一覧にしてください。",
                "花礫模型の試作展示では、今後も縮尺を台座に記載してください。",
                "花礫模型の試作展示では、今後も持ち運び可能な部分を図示してください。",
                "花礫模型の試作展示では、今後も制作班の名前を表示してください。",
                "花礫模型学習室の試作展示の飾り札名は、継続して桃色の風見です。",
                "花礫模型学習室の試作展示では、今後も収納箱の番号を記録してください。",
            ),
            rows=(
                (5, "花礫模型の試作展示の飾り札名は「水玉の羅針」です。"
                    "今後も覚えておいてください。"),
                (17, "花礫模型の試作展示の飾り札名を忘れてください。この取り消し指示は保持し、"
                     "新しい名前を明示するまでは不明として扱ってください。"),
            ),
            question="花礫模型の試作展示の飾り札名は？",
            answer=None, keep=(17,), forbidden=("水玉の羅針",),
        ),
        _case(
            16, "explicit_forget", "ja",
            project="宵藍工芸", peer="宵藍工芸分室", topic="見本相談会",
            facts=(
                "宵藍工芸の見本相談会では、今後も素材名を相談票に記載してください。",
                "宵藍工芸の見本相談会では、今後も試作品と完成品を別の台に置いてください。",
                "宵藍工芸の見本相談会では、今後も持ち主の班名を記録してください。",
                "宵藍工芸の見本相談会では、今後も欠けた部品を図示してください。",
                "宵藍工芸分室の見本相談会の呼び名は、継続して小枝の市です。",
                "宵藍工芸分室の見本相談会では、今後も修理案を別の紙に記載してください。",
            ),
            rows=(
                (13, "宵藍工芸の見本相談会の呼び名は「銀糸の集い」です。今後も覚えてください。"),
                (32, "宵藍工芸の見本相談会の呼び名を忘れてください。この取り消し指示は保持し、"
                     "新しい呼び名を明示するまでは不明として扱ってください。"),
            ),
            question="宵藍工芸の見本相談会の呼び名は？",
            answer=None, keep=(32,), forbidden=("銀糸の集い",),
        ),
        _case(
            17, "workflow_failure_lesson", "en",
            project="Flintpetal", peer="Flintpetal Annex", topic="stencil alignment",
            facts=(
                "Flintpetal stencil alignment must always record the sheet orientation.",
                "Flintpetal stencil alignment must always keep an unprinted margin reference.",
                "Flintpetal stencil alignment must always label the upper edge.",
                "Flintpetal stencil alignment must always separate paper and metal templates.",
                "Flintpetal Annex stencil alignment must always begin with a corner measurement.",
                "Flintpetal Annex stencil alignment must always record the backing thickness.",
            ),
            rows=(
                (5, "Flintpetal stencil alignment failed when the sheet slipped. Our verified "
                    "ongoing prevention lesson is to use the Anchor sequence before printing."),
                (17, "The standing procedure register defines the Anchor sequence as "
                     '"clamp before tracing".'),
            ),
            question="Which procedure prevents the observed Flintpetal stencil alignment failure?",
            answer="clamp before tracing", keep=(5, 17), sources=(5, 17),
        ),
        _case(
            18, "workflow_failure_lesson", "en",
            project="Orchardlilt", peer="Orchardlilt Studio", topic="cue playback",
            facts=(
                "Orchardlilt cue playback must always display the cue number.",
                "Orchardlilt cue playback must always separate rehearsal and performance lists.",
                "Orchardlilt cue playback must always label silent intervals.",
                "Orchardlilt cue playback must always record the operator's team.",
                "Orchardlilt Studio cue playback must always validate the volume preset first.",
                "Orchardlilt Studio cue playback must always keep a printed cue list available.",
            ),
            rows=((31, "Orchardlilt cue playback skipped its first chime when started mid-list. "
                       'Our verified ongoing prevention procedure is "rewind before arming".'),),
            question="Which procedure prevents the observed Orchardlilt cue playback failure?",
            answer="rewind before arming", keep=(31,), sources=(31,),
        ),
        _case(
            19, "workflow_failure_lesson", "ja",
            project="霞帆製図", peer="霞帆製図別室", topic="型紙転写",
            facts=(
                "霞帆製図の型紙転写では、今後も布目の向きを記載してください。",
                "霞帆製図の型紙転写では、今後も縫い代を別の線で示してください。",
                "霞帆製図の型紙転写では、今後も部品番号を省略しないでください。",
                "霞帆製図の型紙転写では、今後も表裏を明示してください。",
                "霞帆製図別室の型紙転写は、今後も左端合わせで始めてください。",
                "霞帆製図別室の型紙転写では、今後も道具の点検結果を記録してください。",
            ),
            rows=((9, "霞帆製図の型紙転写で左右の位置ずれが発生しました。"
                      "検証済みの再発防止手順は「中心印の先合わせ」です。今後もこの手順を使います。"),),
            question="霞帆製図の型紙転写で起きた位置ずれを防ぐ手順は？",
            answer="中心印の先合わせ", keep=(9,), sources=(9,),
        ),
        _case(
            20, "workflow_failure_lesson", "ja",
            project="露笛工作", peer="露笛工作別班", topic="仕切り組立",
            facts=(
                "露笛工作の仕切り組立では、今後も板ごとに部品番号を表示してください。",
                "露笛工作の仕切り組立では、今後も木目の向きを図示してください。",
                "露笛工作の仕切り組立では、今後も接合部の位置を記録してください。",
                "露笛工作の仕切り組立では、今後も完成品の高さを確認票に記載してください。",
                "露笛工作別班の仕切り組立は、今後も横板から組んでください。",
                "露笛工作別班の仕切り組立では、今後も塗装面を別欄で確認してください。",
            ),
            rows=((21, "露笛工作の仕切り組立で板のねじれが発生しました。検証済みの再発防止手順は"
                       "「対角仮留め」です。今後もこの手順を使います。"),),
            question="露笛工作の仕切り組立で起きたねじれを防ぐ手順は？",
            answer="対角仮留め", keep=(21,), sources=(21,),
        ),
    )
