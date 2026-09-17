# ADR 0016: Required-context recall

[English](0016-required-context.md) | [契約](../STATUS-jp.md#required-context-recall) | [運用](../operations/README-jp.md#required-context-recall)

- 日付: 2026-09-17
- 状態: 上限付きv0.0.16/schema 10で採用・検証済み。localと両native architectureで確認
- 拡張対象: [初期recall](0001-initial-slice-jp.md)、[revision選択](0002-assertion-revisions-jp.md)、[日本語lexical recall](0007-japanese-fts-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/本番/意味品質/性能/DR/完全消去の適格性確認ではない

**過去版の注記:** このADRは検証済みv0.0.16/schema 10を記録します。
[ADR 0017](0017-recall-filters-jp.md)は同じschemaと25-resource surfaceで
v0.0.17の構造化recall完全一致filterを記録し、localと両native architectureで検証済みです。
required参照もfilterを満たす必要があります。別の最終v0.0.16 docs
CI 35226313891は[過去の証拠](../STATUS-jp.md#v0016--schema-10)に記録します。

## 決定とrequest境界

任意の`Recall.required_memory_refs`を追加し、既定は`[]`、最大16件の
`MemoryReference`とします。`memory_id` UUIDとrevision 1〜1000を使い、
既定revisionは**最新でなく1**です。revision違いでもIDは一意で、件数は`max_items`以内です。
空でない参照は`retrieval_mode: "lexical"`を要求し、
不正な組合せ/件数/重複/形式は**422 `invalid_request`**です。
explicit/implicit両recallに対応し、既存implicit 2,000-byte上限を維持します。
空または省略した参照は全3 retrieval modeと既存の順序/結果/pack semanticsを維持します。

## 適格性、順序、権限

通常recallと同じ現在認可済み・materializedなepisode/assertion候補relationを使い、
要求`scope_ids`と固定`as_of`/`known_at`を適用します。
scope拡張、ownership/ACL override、時間適格性迂回、latest置換、別revisionへのfallbackはしません。
既存bitemporal選択では、ある`known_at`でobject当たり一つのrevisionを対象にします。
適格なrelation assertionはentity UUIDとcitationのbyte overheadを保持し、
entity object自体は異なるkindの参照のままです。
不在/読取り不可/purge済み/異なるkind/要求scope外/時間条件外/異なるrevisionの参照が
一つでもあれば、部分contextや欠落参照名を返さず、
**request全体を汎用404 `not_found`**にします。

required参照はkeyword一致とranking cutoffを迂回しますが、認可は迂回しません。
required itemを**request順**で先頭に置き、required IDを除いた通常lexical順位のoptional itemを続けます。
両方が`max_items`以内で、optional overflowの追加1候補により`coverage.truncated`を維持します。
`simple-v1`と`ja-janome-0.5.0-v1`は、日本語projection欠落を含めcanonicalな正確参照に対応します。
既存`lexical_incomplete` metadataを維持し、indexは修復しません。

caller指定参照はpolicy権限、検証済み承認、信頼する指示、現在事実の保証ではありません。
memoryは既存のquote、引用、warning/`refresh_required` markerを持つ根拠のままです。
必須制約の自動検出、永続priority metadata、推論、write、idempotency、
provider呼出し、cacheは追加しません。

## Prefix全体の予算とtransport

compact `ContextPack` JSON全体をUTF-8 byteで予算化し、正確なmodel token数とはしません。
required itemが一つでも丸ごと収まらなければ、
**422 `budget_exhausted`**（`ErrorBody.code`）を返します。
空/部分/required省略の成功contextにはしません。
予算64では空envelope自体が収まらない場合でも発生し得ます。
optional itemはgreedyなitem単位除外とtruncation metadataを維持します。
required参照なしでは空envelopeが収まらない**422 `budget_too_small`**と、
optional候補が収まらないときの別物である成功時の空理由
`empty_reason: "budget_exhausted"`を維持します。

既存の型付きSDK `recall(Recall)`とMCP `memory_recall`で追加fieldを使います。
共有safe-code catalogへ`budget_exhausted`を追加し、SDK/MCPが伝播します。
SDK recallはread-onlyで、`outcome_unknown: false`、自動retryなしを維持します。
hookは`required_memory_refs`を追加入力として拒否し、内部`Recall`は既定`[]`のままです。
host pinningは追加しません。Native/SDK resource method 25とMCP tool 4を維持し、
routeは追加しません。

## 導入と検証

厳密なservice 0.0.16 / API v1 / schema 10を要求します。
API/worker/readinessは厳密な履歴1〜10を維持し、
v15→v16はmigration、依存upgrade、固定image変更のないapplication-only更新です。
旧processを停止/drainして対応componentを使い、古いschemaには既存offline migrationを適用します。
stageは`m2-required-context`です。capabilitiesに`required_context`を追加し、
`retrieval_modes: ["lexical"]`、`max_refs: 16`、`order: "request_order"`、
`budget_policy: "all_required_or_error"`を設定します。

**v0.0.16実装全体のlocalとnative適格性確認は合格しました。**
Apple Containerと両native Docker architectureで各**566テスト、既存warning 1件**、
**既存535 + 新規31**が合格しました。
localの`./scripts/test-containers.sh`はexit 0で**298.15秒（4:58）**、
native amd64は**601.73秒**、arm64は**565.34秒**でした。
[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)は完全一致の実装
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57)で合格しました。
新規caseは**`test_required_context` 29 + 実hook拒否1 + SDK safe-code parameter 1**です。
Ruff、strict mypy **source 19ファイル + strict SDK consumer 1ファイル**、
真のoptional導入、全non-root production smokeが全3環境で合格しました。
SQL参照上限、正確なbyte境界、scope/ACL/時刻/revision適格性、purge、
日本語projection欠落、Native/SDK/MCPの一致/error伝播、relation/citation保持、
実hook CLIのfield拒否が合格しました。
production sequenceはSDK後・取消前に、`max_items: 1`とtruncationのquery迂回、
explicit予算64での`budget_exhausted`と`outcome_unknown: false`、
続くsource purgeの`object_count: 2`で合格しました。
project版だけを変更し、schema 10、依存、artifactは不変です。所要時間は性能benchmarkではありません。
これは実装の結果であり、後続の最終docs CI結果ではありません。
[検証済み証拠](../STATUS-jp.md#v0016--schema-10)を参照してください。
検証済みv0.0.15実装と別の最終docs CIは[過去の証拠](../STATUS-jp.md#v0015--schema-10)として維持します。
