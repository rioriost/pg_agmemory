# ADR 0017: 構造化recallの完全一致filter

[English](0017-recall-filters.md) | [契約](../STATUS-jp.md#exact-structured-recall-filters) | [運用](../operations/README-jp.md#exact-structured-recall-filters)

- 日付: 2026-09-17
- 状態: 上限付きv0.0.17/schema 10で採用・検証済み。localと両native architectureで確認
- 拡張対象: [初期recall](0001-initial-slice-jp.md)、[revision選択](0002-assertion-revisions-jp.md)、[pgvector retrieval](0011-pgvector-retrieval-jp.md)、[required context](0016-required-context-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/本番/意味品質/性能/DR/完全消去の適格性確認ではない

**過去版の注記:** このADRは検証済みv0.0.17/schema 10を記録します。
[ADR 0018](0018-checkpoint-head-jp.md)はschema 10のv0.0.18で正確なbranchの
read-only head照会を記録し、Native/SDK resourceは26です。localと両native architectureで検証済みです。
別の最終v0.0.17 docs CI 35232139680は実装CIとは別に
[過去の証拠](../STATUS-jp.md#v0017--schema-10)へ記録します。

## 決定とrequest境界

既存Native requestへ`Recall.filters: RecallFilters | None = None`を追加します。
共有の型付きnested modelは未知fieldを拒否し、fieldは次の三つだけで、すべて既定nullです。

| Field | 契約 |
|---|---|
| `kind` | `"episode"`、`"assertion"`、nullのいずれか |
| `subject` | `ShortText`またはnull。既存の前後空白除去後1〜256文字 |
| `predicate` | `^[a-z][a-z0-9_]{0,63}$`に一致するstringまたはnull |

episode kindとnon-null subject/predicateの組合せは不正です。
不正形式、値、組合せ、未知fieldは**422 `invalid_request`**です。
field alias、任意SQL、推論selector、range、array形式filterは追加しません。
省略、null、`{}`、全field nullは全3 retrieval modeの既存結果を維持します。

## 完全一致選択とranking

non-null fieldをすべてANDで結合します。subject/predicateはrelationを含むassertion候補を意味し、
assertion kindだけでもrelationを含み、episode kindは全assertionを除外します。
subject/predicateは通常の`Contract` string trim後、`C` collationで
大文字小文字を区別して完全一致比較します。
部分文字列、FTS、Unicode正規化、fuzzy一致、entity解決は行いません。
空queryのlexical recallはfilter後の候補をbrowseし、通常の非空lexical queryには一致が必要です。
filterはqueryを迂回しません。

top-K後の処理でなく、共有materialized候補の内部で
lexical/vector/hybridのranking、coverage、required参照の適格性より**前**にfilterを適用します。
vector/RRFを含むranking計算はfilter後の適格候補集合を使います。
固定`as_of`/`known_at`、要求scope、現在RLS、根拠可視性/完全性、削除gateを維持します。
lexical/vector欠落coverageはこのfilter後の集合を反映し、
除外object、query関連性、最終item上限に基づくものではありません。
`coverage.jobs_pending`は意図的にscope単位のsignalを維持し、
job payloadとの構造化一致ではありません。

## Required context、error、権限

required参照はlexical専用で、既定revisionは**最新でなく1**、
別契約のkeyword迂回とrequest順prefixを維持します。
filter一致も必須で、一つでも不一致ならIDや部分contextを返さず、
汎用の**request全体の404 `not_found`**となります。
revision、時間、scope、filterの迂回はありません。
既存compact `ContextPack`全体のUTF-8 byte計算、implicit 2,000-byte上限、
optional除外/truncation、requiredの**422 `budget_exhausted`**、
`budget_too_small`、成功時の空理由を維持します。
filterはcaller選択条件であり、policy権限、検証済み内容、信頼する指示、
自動推論した意図ではありません。

## Surfaceと導入

既存の型付きSDK `recall(Recall)`とMCP `memory_recall`で追加fieldを公開し、
Native/SDK resource method 25とMCP tool 4を維持します。
routeやsafe error codeは追加しません。
SDK recallはread-only、sanitized call時error、`outcome_unknown: false`、自動retryなしを維持します。
hook入力は`filters`を拒否し、内部`Recall.filters`は既定`None`です。
eventごとのoverrideを追加せず、信頼する起動時境界を維持します。

全adapterで厳密なservice 0.0.17 / API v1 / schema 10を要求します。
v16→v17はapplication-onlyで、SQL migration、依存/provider/index変更、永続priority、cacheはありません。
API/worker/readinessは厳密な履歴1〜10と既存role/pgvector検査を維持します。
旧processを停止/drainして対応componentを使い、古いschemaには既存offline migrationを適用します。
stageは`m2-structured-recall`です。capabilitiesに`recall_filters`を追加し、
`fields: ["kind", "subject", "predicate"]`、`match: "exact"`、`combination: "and"`、
`retrieval_modes: ["lexical", "vector", "hybrid"]`を設定します。

## 検証境界

**v0.0.17実装全体のlocalとnative適格性確認は合格しました。**
Apple Containerと両native Docker architectureで各**604テスト、既存warning 1件**、
**既存566 + 新規38**が合格しました。
内訳は**`tests/test_recall_filters.py`の37 case + 実hook CLIのfilter拒否1 case**です。
localの`./scripts/test-containers.sh`はexit 0で**321.56秒（5:21）**、
native amd64は**638.66秒**、arm64は**544.33秒**でした。
[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)は完全一致の実装
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894)で合格しました。
Ruff、strict mypy **source 19ファイル + strict SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、全non-root production smokeが全3環境で合格しました。
全3 modeのranking前/coverage filter、required参照不一致、
後から追加した不正形式/kind-only overflow 5 case、Unicodeの合成済み/分解済み表現を正規化しない完全一致、
OpenAPI assertion、明示`RecallFilters` consumer構築を含みます。
共有`Predicate` aliasは`Remember`/`CapturedMemory`のfield順とsemanticsを維持します。
新SDK smokeはobserve/remember、`max_items: 1`でtruncationなしの3 field完全一致選択、
required source/filter衝突（`404 not_found`、`outcome_unknown: false`）、
`object_count: 2`を返すsource purge、filter付き空readで合格しました。
project版は0.0.17へ進め、schema 10、依存版、artifact固定値は不変です。所要時間は性能benchmarkではありません。
これは実装の結果であり、後続の最終docs CI結果ではありません。
[検証済み証拠](../STATUS-jp.md#v0017--schema-10)を参照してください。
検証済みv16実装と別の最終docs CIは[過去の証拠](../STATUS-jp.md#v0016--schema-10)であり、
v17の適格性確認ではありません。
