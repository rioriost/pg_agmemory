# Human review preparation

[日本語](HUMAN_REVIEW-jp.md) | [Measured evidence](EVALUATION.md)

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

M2 targets remain assertion support of at least 95% with no serious fabrication,
important-claim compaction fidelity of at least 98% with exact typed-state
preservation, natural-language update correctness of at least 95%, and unsupported
assertions in answers at most 2% with no serious cases. A holistic summary rating
does not supply a claim-level retention denominator. This small Wikipedia pilot
does not establish these milestone-wide rates, and no generated report should
declare M2 qualified.

Use the pilot to clarify the rubric and diagnose failures. Before formal
acceptance, freeze a separately selected evaluation sample, sampling units,
cohorts, denominators, model/prompt/source versions and review protocol. Report
uncertainty at the session/source-group level rather than counting repeated
outputs from one article as independent evidence.
