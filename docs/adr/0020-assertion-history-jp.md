# ADR 0020: Assertion metadata履歴

[English](0020-assertion-history.md) | [契約](../STATUS-jp.md#assertion-metadata-history) | [運用](../operations/README-jp.md#assertion-metadata-history)

- 日付: 2026-09-18
- 状態: 上限付きv0.0.20/schema 10で採用・検証済み。localと両native architectureで確認
- 拡張対象: [assertion revision](0002-assertion-revisions-jp.md)、[relational graph](0005-relational-graph-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/性能/本番/DRの適格性確認ではない

## 決定とrequest境界

Native JWT認証付きread-only `POST /v1/assertions/history`を追加し、
現在のread権限を要求しますが、`Idempotency-Key`やwrite権限は不要です。
closedな`AssertionHistory`は必須UUID `memory_id`、strict整数`max_items`
（1〜100、既定20）、nullableなstrict整数`before_revision`（1〜1001、既定null）を受け付けます。
不正UUID/範囲、整数へのboolean/float、追加fieldは**422 `invalid_request`**です。
現在読取り可能な通常assertionとcanonical relation assertionだけが対象です。
不在、非公開、kind違いは汎用**404 `not_found`**です。
identity/scope override、`as_of`、`known_at`、過去ACL、offset、watch、
full-value selectorは追加しません。

## 現在の認可下でのexclusive ordinal page

revision降順でexclusiveな`revision < before_revision`を使います。
省略/nullは最新から開始し、`before_revision: 1`は空pageです。
`1001`は既存最大revision 1000までの現在headを含み、書込み上限は変えません。
`max_items + 1`件を取得し、最大`max_items`件を返します。
overflowの場合だけ、lookahead行でなく**最後に返すordinal**から`next_before_revision`を作ります。
それ以外はnullです。nullは継続の終端ですが、新たにnullで要求すれば再開します。

位置は権限、snapshot、receipt、cache、retention objectではありません。
各pageで現在ACL/source/削除guardと既存tenant response-delivery/drain barrierを適用し、
過去の権限は使いません。page間の新revisionには位置なしでの再開が必要です。
以前のheadの`known_until`は閉じることがあり、page間snapshotはありません。
`current_revision`は照会時点のheadで、CAS予約や結果不明mutationがcommitした証拠ではありません。
そのmutationの元のidempotency key/bodyで照合し、expected revision/keyを自動置換しないでください。

選択metadataの欠落/番号gap、または読取り可能な根拠がない場合は
**409 `assertion_invalidated`**で**全page失敗**です。
canonical relation endpoint不在は**409 `relation_invalidated`**です。
部分成功、黙ったskip、fallbackはありません。
連続性はlookahead ordinalを含む上限付き取得範囲を対象とし、
根拠とrelationの検査は返すpage itemを対象とします。

## Metadataは内容を含まないという意味ではない

正確な**200 `AssertionHistoryPage`**は`memory_id`、`scope_id`、`subject`、
`predicate`、`current_revision`、最大100件の`revisions`、nullableな
`next_before_revision`、`consistency`（`access_epoch`、`deletion_epoch`）を返します。
revision metadataは`revision`、nullableな`valid_from`/`valid_to`、
`recorded_at`（system-time下限）、nullableな`known_until`（上限）、
nullableな`correction_reason`、`epistemic_status: "reported"`、
revision 1の正確なepisode `evidence_refs`最大32件、
`source_entity`/`target_entity` UUIDを含む`relation`またはnullです。
itemは`AssertionRevisionMetadata`で、SDK parsingもrevision最大100件と
根拠参照1〜32件を検証します。

完全なvalueやevidence quoteを取得せず、上限付きSQL metadata queryを使います。
subject、predicate、correction reasonは依然として人間のtextです。
**value/quoteを取得しないことは、応答が内容を含まないことを意味しません。**
evidenceとして扱い、信頼済み指示や現在の真実とせず、private metadataを開示しないでください。
全内容には既存`Explain`へ正確な`memory_id`とrevisionを渡し、新しい検査を受けます。
revision省略は引き続き**latestでなく1**です。
recallのtime意味論、evidence identity、purge動作は不変です。

## SDKと導入

async `get_assertion_history(AssertionHistory) -> AssertionHistoryPage`は内部必須の
`mutation=False`、call時request検証、通常の**request 256 KiB / response 2 MiB**上限、
sanitizedなread-only `outcome_unknown: false`を使い、自動pagination/retryはありません。
SDKは`assertion_invalidated`を認識し、`relation_invalidated`も維持します。
MCP/hookのsafe-error allowlistは変えません。
mutation、cache、provider呼出し、watch、retention objectは追加しません。
Native/SDKは28 resource method、MCPは既存4 tool、closed hookも不変で、
history tool/fieldやSDKの管理/worker機能は追加しません。

全adapterで厳密なservice 0.0.20 / API v1 / schema 10を要求します。
stage `m2-assertion-history`は`assertion_history` metadataを追加します。
`endpoint: "/v1/assertions/history"`、`order: "revision_desc"`、
`pagination: "exclusive_revision"`、`max_items: 100`、`includes_values: false`、
`includes_evidence_quotes: false`です。
v19→v20はapplication-onlyで、project版以外にSQL migration、依存/provider/artifact固定値変更はありません。
履歴1〜10とschema 10は不変です。旧componentを停止/drainして対応版を起動し、混在versionは保証しません。

## 検証境界

**最終localとnativeの実装検証は合格しました。** Apple Containerのfull
`./scripts/test-containers.sh`は**695合格、既存warning 1件、359.74秒**でした。
**既存663 + 新規32 case**で、contract 16、revision-history 7、
graph endpoint不在 1、SDK 7（mock 6 + real 1）、
既存safe-code parameterizationへの`assertion_invalidated`追加 1です。
実装
[`6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97`](https://github.com/rioriost/pg_agmemory/commit/6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97)は
完全一致SHAの[CI 35247519977](https://github.com/rioriost/pg_agmemory/actions/runs/35247519977)に合格しました。
native amd64は**695合格、527.99秒**、arm64は**695合格、615.07秒**でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
従来の全production smokeとassertion historyが全3環境で合格しました。
既存の正確な1000-revision coverageを拡張し、通常assertionとcanonical relation assertionの両方で
**100件ずつ明示的に10 page**を走査しました。既存temporal relationとpurge caseも拡張して合格しました。
所要時間はbenchmarkではなく、MVP、性能、記憶品質、本番、DR適格性確認は主張しません。
これは実装結果であり、最終docs CI結果ではありません。
[検証済み証拠](../STATUS-jp.md#v0020--schema-10)を参照してください。
v19実装と別の最終docs CIは[過去の証拠](../STATUS-jp.md#v0019--schema-10)であり、
v20の適格性確認ではありません。
