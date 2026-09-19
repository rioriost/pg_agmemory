# Human review preparation

[日本語](HUMAN_REVIEW-jp.md) | [Measured evidence](EVALUATION.md)

**2026-09-19: the requested human-review work is paused and no longer required
for M2.** The [revised plan](PG_AGMEMORY_IMPLEMENTATION_PLAN.md) qualifies memory
contracts, not a model's summarization/judgment ability. This guide and the
unrated artifacts are retained as an optional provider diagnostic, not an active
project acceptance workstream. Do not fill the forms or run more models to
unblock implementation. Frozen packet instructions/report gate names reflect the
old plan; keep their hashes and results intact rather than editing them into a pass.

## Purpose and boundary

This workflow prepares an **unrated development pilot**, not M2 acceptance.
People compare original evidence with generated claims, summaries and answers.
The tooling preserves provenance, presents the comparisons, validates submitted
ratings and counts missing or conflicting judgments. It cannot verify that a
person actually performed a review or turn caller adoption into human approval.

Wikipedia is useful for source-supported factual claims and summaries. Its text
is not an independent guarantee of real-world truth. Encyclopedic articles do
not establish realistic preference updates, unfinished-task preservation,
permission to act, or 20 executed task replays. Those require separate approved
task material. Provider summaries are not working-memory compaction experiments;
unpublished extraction proposals are not adopted or automatically published facts.

## Sources and licensing

The initial selection is fixed in
[`examples/wikipedia-review-plan.json`](../examples/wikipedia-review-plan.json):
English/Japanese pairs for PostgreSQL, the Solar System and atomic clocks.
This is a small, deliberately selected pilot, not a representative random sample.
No images, article-history dumps or private conversations are requested.

Keep each article's title, language, exact revision, contributor/history link,
license link, retrieval time, content hashes and transformation description with
the text. A rendered old revision may include templates rendered at retrieval
time; the captured bytes, not every historical template dependency, are pinned.
Revision publication time is not the occurrence time of every fact in an article.
Do not treat revision differences as semantic corrections without human review.

Wikipedia-derived text is **CC BY-SA 4.0**, not this repository's MIT-licensed
code. Preserve attribution to Wikipedia contributors, links to the exact revision
and history, the [license](https://creativecommons.org/licenses/by-sa/4.0/), and
notices of text extraction, excerpting and model-generated adaptations.
When redistributing adapted article text, retain the applicable share-alike terms.
Check article-specific notices; the general text license does not automatically
cover third-party quotations or media. Do not bundle source payloads in git.
Use the ignored, private `.review-artifacts/` directory for local packets.

Collection must follow the [Wikimedia reuse terms](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content),
[User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)
and [API etiquette](https://www.mediawiki.org/wiki/API:Etiquette).
Identify the project, use serial bounded requests, and stop on denial or rate
limiting rather than spoofing a browser or bypassing a restriction.

## Archived local pilot: 2026-09-18

**No review is currently requested.** If independently choosing to use these
diagnostic tools, do not regenerate the frozen outputs. Assign two reviewers to
`reviewer-a` and `reviewer-b`. In this checkout, first open
`.review-artifacts/wikipedia-pilot/review/sources.html` and fill the corresponding
`source-reviewer-1.json` / `source-reviewer-2.json`. Only afterward open
`outputs.html` and fill `reviewer-1.json` / `reviewer-2.json` in the same directory.
The initial `pending-report/report.html` is one directory above `review/`.
All four forms are pending, with no model-generated important-claim inventories
or human verdicts. These private local artifacts are deliberately not in git.

Collection and generation used immutable code
`1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6`, Ollama 0.34.1 and
`qwen2.5:7b` revision
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
The six fixed sources produced six excerpts totaling 3,301 UTF-8 bytes:

| Article | Language | Revision | Excerpt bytes |
|---|---|---|---:|
| PostgreSQL | en | 1373697757 | 632 |
| PostgreSQL | ja | 109498755 | 639 |
| Solar System | en | 1372787813 | 460 |
| 太陽系 | ja | 110861363 | 863 |
| Atomic clock | en | 1374241509 | 336 |
| 原子時計 | ja | 111035897 | 371 |

Exactly **24 local text calls**, zero embedding calls, and no retries produced
24 review records: **5 summaries, 11 QA records (5 answers, 6 abstentions), and
8 generation failures**. No extraction claim passed the existing contract, so
assertion support cannot be measured from this pilot. All failures are retained:
one extraction and one summary reached the 512-token limit; five extraction
responses had exact source quotes but nonliteral subjects and/or values; one
definition answer supplied an invalid citation ID. This is mechanical diagnosis,
not semantic grading. Do not repair rejected outputs or silently omit failures.
The valid outputs still require human judgments of support, omissions and
abstention appropriateness. No acceptance rate or M2 qualification is claimed.

Provenance digests:

- Corpus: `012ef8d1dd7d562662ddaf5d39825d9f3d627c09b2dd310413adb3cd0087ba01`
- Profile: `842964cb10f6c28f858544355989f4b481eedb3a6fde8134229eaad2d744381c`
- Review packet: `865f61431652560295201c4aae5c66b8fab0ea2fbfaeb055a063bbac5a20979e`

The first collector at `340bb1b` stopped after one HTTP 200 metadata response
because it incorrectly rejected a normal revision-history continuation token.
Its failed manifest/response remain in
`.review-artifacts/wikipedia-corpus-340bb1b-failed/`; no model calls occurred in
that attempt. The corrected collector accepts only that bounded, well-formed
metadata cursor, never follows it, and still rejects warnings, unknown cursors
and parse continuations. The subsequent complete corpus is in
`.review-artifacts/wikipedia-corpus/`; raw API responses remain JSON-only.

## Preparing a packet

Run repository Python commands in the supported Linux container environment.
Use an immutable archive of the chosen commit, and record its full SHA; the CLI
records the supplied identity but does not authenticate the checkout or model
weights. The operator must verify both. Choose new output directories: existing
artifacts are never overwritten or resumed automatically.

```sh
python -m pg_agmemory.evaluation_wikipedia \
  --plan examples/wikipedia-review-plan.json \
  --output .review-artifacts/wikipedia-corpus

python -m pg_agmemory.review_pilot \
  --corpus .review-artifacts/wikipedia-corpus/corpus.json \
  --profile /private/local-profile.json \
  --implementation-sha FULL_40_CHARACTER_COMMIT_SHA \
  --output .review-artifacts/wikipedia-pilot \
  --reviewer reviewer-a --reviewer reviewer-b \
  --max-calls 24 --allow-local-model-calls
```

Create `.review-artifacts/` as an owner-private directory first. The collector
makes no model calls. It captures up to six complete lead paragraphs per article,
bounded to 12,000 UTF-8 bytes. The pilot selects the first nonempty complete
paragraph, at most 1,600 UTF-8 bytes, and rejects an oversized first paragraph
rather than silently skipping it or cutting a sentence.

Each of the six excerpts has four planned local text-model calls: extraction,
provider summary, definition QA and next-revision-date QA. The latter question
probes abstention; neither question has an automatically supplied human verdict
or gold answer. Extraction and summary use the existing provider-default
sampling; QA uses temperature zero and seed 17. This is not a deterministic
reproducibility claim. No embedding calls, Native ingestion, adoption or worker
jobs occur. The reusable QA profile requires a pinned embedding model identity
even though this pilot never invokes it.

`journal.jsonl` retains reservations and untrusted requests/responses without
authorization headers. `measurements.json` binds the corpus, excerpts, profile,
question recipe, call matrix and failures. Invalid outputs remain visible; other
provider/transport failures stop the run with the partial journal retained.
Never delete a failed run to disguise it or automatically retry an unknown call.
`review/` contains the escaped offline HTML, digest-bound packet, two source-only
forms and two **unfilled** output-rating forms. `readiness.json` records pending
coverage, not quality acceptance. Source payloads and model adaptations retain
their provenance/license notices; do not distribute them as MIT code.

## Human procedure

1. **Read sources before model outputs.** Confirm the material is usable and
   record important claims or constraints from the source-only view. An empty
   inventory is not evidence of perfect summary fidelity. Keep the finalized
   source inventory separate from output ratings.
2. **Review the frozen outputs independently.** Use separate reviewer identifiers
   and forms. Read the surrounding source, not merely matching keywords. Check
   negation, uncertainty, entities, dates, numbers and scope of each claim.
3. **Record a reason and severity.** Mark uncertain cases as uncertain instead of
   guessing. A fabricated approval or authority, reversed prohibition, or material
   entity/time/number substitution is a candidate for severe-error adjudication;
   freeze the domain-specific severity rubric before formal acceptance.
4. **Resolve disagreements explicitly.** Keep both original ratings. Do not
   silently majority-vote, overwrite one reviewer, remove a difficult case or
   change the source/output after it has been rated.

Reviewer identifiers are declarations, not authenticated human identities.
An operator must approve reviewer eligibility and the evaluation protocol.
Completed human reviews do not update the service's candidate-adoption flags,
publish memories, or authorize actions.

### Files to edit and offline commands

Start with `review/sources.html` and your `source-reviewer-N.json`. For each
annotation, add human-written `important_claims` with `claim_id`, `text` and
`grounding` (`source_id` plus an exact `quote` or `start`/`end` span), then mark
`status` as `complete`. Offsets are zero-based Unicode code points, end-exclusive.
Keep the empty inventory if not yet annotated; it is not a successful retention
measurement. Save the preannotation before opening `outputs.html` or `packet.json`.
The files cannot enforce viewing order or reviewer independence.

Next, compare `outputs.html` against `sources.html` and edit only the `outcome`,
`severity`, `rationale` and `source_references` fields in your `reviewer-N.json`.
`instructions.txt` lists the allowed labels for each kind. Every completed
rating, including `uncertain` or a generation failure, requires a nonblank reason,
severity (`none`, `minor`, `major`, `critical`) and at least a matching source ID
in `source_references`. Quotes/spans are optional for output ratings but must be
exact if supplied. Leave pending rows entirely unfilled. Do not modify IDs,
digests, kind, source text or model outputs. HTML is read-only; edit JSON with a
text editor.

```sh
python -m pg_agmemory.human_review validate-source \
  --packet .review-artifacts/wikipedia-pilot/review/packet.json \
  --form .review-artifacts/wikipedia-pilot/review/source-reviewer-1.json \
  --out .review-artifacts/source-review-1
# Repeat for reviewer 2, with a different new output directory.

python -m pg_agmemory.human_review score \
  --packet .review-artifacts/wikipedia-pilot/review/packet.json \
  --form .review-artifacts/wikipedia-pilot/review/reviewer-1.json \
  --form .review-artifacts/wikipedia-pilot/review/reviewer-2.json \
  --out .review-artifacts/review-report
```

These commands are offline and make no model/database calls. Validation checks
bindings and form structure, not semantic truth or completion of the human
procedure. Scoring preserves both reviewers and disagreements. Source-only
inventories are not yet connected to per-claim summary-retention ratings, so
important-claim retention remains `NOT_MEASURED`, even after holistic ratings.

## What can and cannot be concluded

Report generation failures, empty extraction results, abstentions, pending and
uncertain ratings, and disagreements alongside any rate. Define the denominator
before rating; missing judgments are not successful cases. Zero unsupported
answers achieved by refusing to answer everything is not useful answer quality.

Keep automatic inferred publication, explicit caller adoption, and untrusted
proposals in different cohorts. Do not pool a well-performing low-impact cohort
with another cohort merely to reach a threshold.

The former semantic thresholds (95% assertion support, 98% important-claim
retention, 95% natural-language updates, 2% unsupported answers) are no longer
M2+ gates. Exact typed-state preservation remains a core software contract.
A holistic rating cannot supply a claim-retention denominator, and removing a
gate does not create a measurement. No report from this tool qualifies M2.

If someone elects to conduct semantic research outside project acceptance,
first freeze the evaluation sample, sampling units,
cohorts, denominators, model/prompt/source versions and review protocol. Report
uncertainty at the session/source-group level rather than counting repeated
outputs from one article as independent evidence.
