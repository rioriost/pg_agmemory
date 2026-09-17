# ADR 0021: 完全一致entity照会とpagination

[English](0021-entity-query.md) | [契約](../STATUS-jp.md#exact-entity-query-and-pagination) | [運用](../operations/README-jp.md#exact-entity-query-and-pagination)

- 日付: 2026-09-18
- 状態: 上限付きv0.0.21/schema 10で採用。追加修正はlocalと両native architectureで検証済み
- 拡張対象: [entityとgraph](0005-relational-graph-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/性能/identity解決/記憶品質/本番/DRの適格性確認ではない

## 決定とrequest境界

Native JWT認証付きread-only `POST /v1/entities/query`を追加し、
現在のread権限を要求しますが、write権限や`Idempotency-Key`は不要です。
closedな`QueryEntities`は必須の重複しないUUID `scope_ids`（1〜32件）、
nullableな`entity_type`（`EntityType`、既定null）、nullableな`canonical_label`
（通常の空白除去後1〜256文字の`ShortText`、既定null）、
strict整数`max_items`（1〜100、既定20）、`before: EntityCursor | None`（既定null）を受け付けます。
closedな`EntityCursor`はtimezone付き`recorded_at`とUUID `memory_id`が必須です。
不正形式/type、重複、空/空白だけのlabel、範囲、UUID/timestamp、
追加fieldは**422 `invalid_request`**です。
owner/principal override、offset、watch、過去ACL、任意queryは追加しません。

## 完全一致filterと明示identity選択

`EntityType`は`person`、`organization`、`project`、`component`、
`incident`、`task`、`decision`、`other`だけを維持します。
任意のtypeとlabelのfilterは**LIMIT前にAND**で組み合わせます。
labelは既存の空白除去後に`C` collationで大文字小文字を区別する完全一致です。
filter省略/nullは要求scope内の現在読取り可能な全entityを対象にします。
alias、fuzzy/substring/wildcard/Unicode正規化match、embedding、
merge、自動identity選択は導入しません。
同じlabelの一致もpageを通じてすべて別IDを維持し、一つの解決済みidentityにはしません。
callerは既存`GET /v1/entities/{memory_id}` / SDK `get_entity`で根拠を確認し、
graph seedを明示選択します。query自体はgraph展開を行いません。

## 現在の可視性と全page失敗

現在tenant/scope/source RLSを適用し、**所有権filterはありません**。
caller所有jobだけの照会と異なり、読取り可能な共有scopeのentityも含みます。
未知/非公開scopeは寄与せず、一致なしは**200**、`entities: []`、
`next_cursor: null`と現在epochで、非公開理由/件数ではありません。
各pageに現在のtenant response-delivery/drain barrierを適用します。
選択して返す全itemの`entity_evidence JOIN episode`による可視根拠件数は
保存済み`reference_count`と一致する必要があり、不一致なら
**409 `entity_invalidated`**で**全page失敗**です。
黙ったskip、部分成功、fallbackはありません。
SQLはquoteでなくmetadata/件数を取得し、lookahead行はoverflow用だけです。

## Exclusive位置と型付きmetadata

`object.created_at DESC, entity.id DESC`順を
`recorded_at DESC, memory_id DESC`として公開し、exclusiveな
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`を使います。
`max_items + 1`件を取得し、最大`max_items`件を返します。
overflowの場合だけlookaheadでなく**最後に返すitem**から`next_cursor`を作ります。
省略/nullの`before`は最新から開始し、nullの`next_cursor`は継続の終端です。
署名なしの透明な位置は権限、snapshot、receipt、cache、retention objectではありません。
既存objectを指す必要はなく、古い/削除済み/偽造cursorも現在認可された行を限定するだけです。
各pageは照会時点の結果で、アクセス/source/削除で構成は変わり得ます。
古い境界より新しいentityには`before`なしの明示再開が必要です。

正確な**200 `EntityPage`**は`entities: list[EntitySummary]`（最大100件）、
`next_cursor: EntityCursor | None`、`consistency`（`access_epoch`、`deletion_epoch`）を返します。
既存`EntitySummary`は`memory_id`、`revision: 1`、`scope_id`、
`entity_type`、`canonical_label`、`recorded_at`（object作成で、source event timeではない）を含みます。
evidence quote、source ID、total countはなく、既知IDの`EntityDetail`は不変です。
labelは人間のtextであり、内容を含まないdata、信頼済み指示、検証済み真実ではありません。

## SDKと導入

async `query_entities(QueryEntities) -> EntityPage`は内部`mutation=False`、
call時request検証、通常の**request 256 KiB / response 2 MiB**上限、
sanitizedなread-only `outcome_unknown: false`を使い、自動pagination/retryはありません。
SDK parsingもresponse modelの100-entity上限を検証します。
既存`entity_invalidated`を再利用し、MCP/hook safe-error動作は不変です。
Native/SDKは29 resource method、MCPの4 toolは不変、
closed hookもentity tool/fieldを追加しません。SDKの管理/worker機能、
mutation、provider呼出し、cache、自動identity選択は追加しません。
全adapterで厳密なservice 0.0.21 / API v1 / schema 10を要求します。
stage `m2-entity-query`は`entity_query`を追加します。
`endpoint: "/v1/entities/query"`、`match: "exact"`、
`order: ["recorded_at_desc", "memory_id_desc"]`、`pagination: "exclusive_keyset"`、
`max_items: 100`です。
v20→v21はapplication-onlyで、schema 10/履歴1〜10と固定artifactを維持し、
SQL migration、依存/provider、AGE変更はありません。
旧componentを停止/drainして対応版を使い、混在versionは保証しません。

## 検証境界

**現在のgraph query追加修正はlocalと両native architectureで検証済みです。**
修正[`956b232f38caeeb7d0421a2d6fd3d8340206bcbc`](https://github.com/rioriost/pg_agmemory/commit/956b232f38caeeb7d0421a2d6fd3d8340206bcbc)は
別の使い捨てfixtureの強制generic prepared planで再現した
`relation_revision`の反復scanを`adjacent` CTEのmaterializeで除去します。
RLS、時刻/scope/根拠検証、順序、上限はすべて維持し、
timeout引上げ、JIT無効化、RLS緩和は行いません。
追加回帰は既存100/101-path fixtureを使い、custom planの結果だけでなく
`pg_prepared_statements.generic_plans > 0`を確認します。
version/schema/APIと29 resource methodは不変です。
Apple Containerのfull `./scripts/test-containers.sh`は
**730合格、既存warning 1件、430.12秒**（**既存729 + generic-plan case 1件**）でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
全production smokeもlocalで合格しました。
nativeの[CI 35257534254](https://github.com/rioriost/pg_agmemory/actions/runs/35257534254)は
この修正の完全一致SHAで、amd64 **730合格、warning 1件、748.37秒**、
arm64 **730合格、warning 1件、654.52秒**でした。
両native runで同じ検査、optional導入、全production smokeも合格しました。
失敗したdocs revisionの再実行成功ではなく、新しいcode修正の適格性確認です。
再生成した最終docsのCIはまだ実行していません。

以前の最終docs revision
[`4d97e92ec08d845ebd2c969819a999e9f0fd6f83`](https://github.com/rioriost/pg_agmemory/commit/4d97e92ec08d845ebd2c969819a999e9f0fd6f83)の
[CI 35254318489](https://github.com/rioriost/pg_agmemory/actions/runs/35254318489)は**失敗**しました。
amd64は**728合格、1失敗、549.51秒**、arm64は**729合格、626.14秒**でした。
既存`test_exact_graph_path_seed_and_entity_evidence_limits`の100-path展開で
`QueryCanceled` / statement timeoutによる**503**が発生しました。
実際のCI planは未取得で、別の再現はそのplanの証明や本番性能benchmarkではありません。

**初期v21実装の過去の証拠であり、修正の適格性確認ではありません。** Apple Containerのfull
`./scripts/test-containers.sh`は**729合格、既存warning 1件、390.27秒**でした。
**既存695 + 新規34 case**で、contract 20、graph-query 7、SDK 7（mock 6 + real 1）です。
実装
[`a34477d7511f22202f0bd981772f51408634a9af`](https://github.com/rioriost/pg_agmemory/commit/a34477d7511f22202f0bd981772f51408634a9af)は
完全一致SHAの[CI 35252290223](https://github.com/rioriost/pg_agmemory/actions/runs/35252290223)に合格しました。
native amd64は**729合格、764.95秒**、arm64は**729合格、614.98秒**でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
従来の全production smokeとentity照会が全3環境で合格しました。
coverageは正確な**101-entity fixtureでの同時刻とUUID順序**、全8 type、
日本語/句読点/大文字小文字/Unicodeの区別、他ownerの共有scope読取り可視性、
ACL変更/現在epoch、削除/偽造cursor、**32-scope/100-item page上限**、
quoteなし、明示graph-seed選択を含みます。
所要時間はbenchmarkではなく、MVP、性能、identity解決、記憶品質、
本番、DR適格性確認は主張しません。
初期結果とdocs runの失敗は追加修正の適格性確認と区別して維持します。
[検証状況と証拠](../STATUS-jp.md#v0021--schema-10)を参照してください。
v20実装と別の最終docs CIは[過去の証拠](../STATUS-jp.md#v0020--schema-10)であり、
v21の適格性確認ではありません。
