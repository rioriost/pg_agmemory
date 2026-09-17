# ADR 0022: 明示batch capture

[English](0022-batch-capture.md) | [契約](../STATUS-jp.md#explicit-batch-capture) | [運用](../operations/README-jp.md#explicit-batch-capture)

- 日付: 2026-09-18
- 状態: 上限付きv0.0.22/schema 10で採用。graph修正と全方向回帰はlocal/nativeで検証済み
- 拡張対象: [atomic capture](0010-atomic-capture-jp.md)、[durable job](0006-durable-jobs-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/性能/記憶品質/本番/DRの適格性確認ではない

**過去版の注記:** このADRはv0.0.22/schema 10のdocs CI失敗と、
別途検証したgraph修正/方向別revisionを記録しています。
その後の最終docs CI 35273848787は合格しました。
[過去の証拠](../STATUS-jp.md#v0022--schema-10)を参照してください。
[ADR 0023](0023-episode-query-jp.md)はv0.0.23/schema 10のread-only episode照会をdraft化し、
Native/SDK resourceは31になります。適格性確認待ちです。
以下の決定と検証境界は記録時の内容を維持します。

## 決定とrequest境界

Native JWT認証付き`POST /v1/captures/batch`にcaller所有の`Idempotency-Key`を要求します。
closedな`CaptureBatch`は`episode: Observe`と
**1〜16件**の`memories: list[CapturedMemory]`だけを持ちます。
ObserveとCapturedMemoryの既存の正規化、上限、明示intent規則を維持します。
各proposalはcallerが指定し、episodeのscopeと正規化済みepisode内の一つのliteral quoteを使い、
scope、evidence ID、identityをoverrideできません。
正規化model JSON serialization後に重複する候補は、trimで同一になるものを含め
**422 `invalid_request`**です。list順序は意味を持ちます。
LLM抽出、provider呼出し、自動capture、意味的真実の主張は追加しません。

正確な**201 `CaptureBatchResult`**は`memory_id`（episode UUID）、
`revision: 1`、request順のUUID **1〜16件**の`synthesis_job_ids`を返します。
job参照であり、assertionやjobが新規/pendingである証明ではありません。
既存`POST /v1/captures`、`Capture`、`CaptureResult`、`atomic_capture`は
`max_jobs: 1`の単一job契約を維持します。

## 原子的な受付と独立した公開

既存tenant transactionとresponse barrier内でObserveと`Jobs.enqueue`を再利用し、
operation `capture_batch`のreceipt/auditを記録します。内部合成では別の
`capture-batch-observe-v1` namespaceとindex付き
`capture-batch-job-v1:keyhash:index` keyを使い、callerが内部keyを指定することはありません。
**新規**episode、lexical projection、job、identity、receipt、auditの全書込みをまとめてcommitします。
後方candidateの不正quote、途中での**scope当たりactive job 100件**の残quota枯渇、
audit失敗は新規受付全体をrollbackし、既存行は変更しません。
quotaは新規受付jobだけに適用し、requestの16件上限は新しいglobal quotaではありません。

受付後、workerは各jobを独立して公開します。完了は原子的でなく、
request順は実行順を保証しません。既存のjob単位GET、所有job照会、取消、retryを使います。
batch job、集約status、group取消はありません。受付はworker/providerを呼び出しません。

## Identity、replay、削除

同じsourceとjob intentにfresh keyを使うと、cancelled/failed/succeededを含む元のIDを再利用し、
復活させません。fresh keyで候補を並べ替えると既存IDを並べ替えて返しますが、
同じkeyでbodyを並べ替えると**409**です。
exact-key receiptは現在のwrite権限と返す**全job**のobject livenessを再検証します。
sourceまたは一つのjobのpurge後はreceipt全体が**404**で、
部分replayや復活はありません。source purgeは依存job/assertionへの既存closureを維持します。
失敗jobの個別retryは明示操作を維持し、capture replayは元IDをretry childへ置換しません。

## SDK、上限、導入

async `capture_batch(CaptureBatch, *, idempotency_key) -> CaptureBatchResult`は
`mutation=True`で201を要求します。不確実な結果では元key/bodyを維持し、
自動retry、key置換、batch分割はしません。
実行中mutationの取消はrollbackの証明ではありません。
call時のstrict request検証と通常の**request 256 KiB / response 2 MiB**上限を維持します。
個別には有効な大きいcandidate 16件でもNative request-body上限を超えて**413**になり得ます。

Native/SDKは30 resource method、MCPの4 toolとclosedなread-only hookは不変で、
batch toolやhook fieldは追加しません。
厳密なservice 0.0.22 / API v1 / schema 10を要求します。
stage `m2-batch-capture`はfeature `atomic_batch_structured_capture`と
`atomic_batch_capture` metadataを追加します。
`endpoint: "/v1/captures/batch"`、`max_jobs: 16`、
`recipe_version: "structured-remember-v1"`、`automatic_capture: false`、
`admission_atomic: true`、`publication_atomic: false`です。
v21→v22はapplication-onlyで、schema 10/履歴1〜10、固定artifact、
依存/backend/providerは不変です。SQL migrationやversion混在保証はなく、
旧componentを停止/drainして対応版を使います。

## 検証境界

**graph修正と全方向回帰は検証済みです。**
方向別追加検証の
[`3f56c51434333428fe742bb6a464d1d3117e8e26`](https://github.com/rioriost/pg_agmemory/commit/3f56c51434333428fe742bb6a464d1d3117e8e26)は
Apple Containerのfull `./scripts/test-containers.sh`で
**780合格、warning 1件、507.09秒**でした。
[CI 35271311062](https://github.com/rioriost/pg_agmemory/actions/runs/35271311062)は完全一致SHAで、
amd64 **780合格、warning 1件、819.23秒**、arm64 **780合格、warning 1件、745.25秒**でした。
全3環境でRuff、mypy **source 19 + strict SDK consumer 1ファイル**、
全optional導入検査、全production smokeも合格しました。
`auto`/`generic`/`nested_loop` × `outgoing`/`incoming`/`both`の9組は
両adjacency branchを対象として合格し、
**780 = batch基準773 + nested-loop case 1 + direction case 6**です。
この拡張は回帰coverageの変更であり、product SQL、version、schemaは不変です。
この文書更新の最終docs CIはまだ実行していません。

**以前の774件のgraph修正適格性確認:**
修正[`bf7429327955071239fdc2f7b60d1a5d47dfff7e`](https://github.com/rioriost/pg_agmemory/commit/bf7429327955071239fdc2f7b60d1a5d47dfff7e)は
Apple Containerのfull suiteで**774合格、445.86秒**、全smokeも合格しました。
[CI 35268438022](https://github.com/rioriost/pg_agmemory/actions/runs/35268438022)は完全一致SHAで、
amd64 **774合格、779.45秒**、arm64 **774合格、682.78秒**でした。
両方でRuff、mypy **source 19 + strict SDK consumer 1ファイル**、
全導入検査、全production smokeも合格しました。
以前のtest image内の未変更の修正前sourceに対するnegative controlは**期待通り1失敗**でした。
保持したsyntheticな`graph-canonical-baseline-plan.json`は
`assertion`/`assertion_revision`が**400 loop**、endpoint-evidenceが**4 loop**を示しました。
失敗CI planや本番性能benchmarkではありません。

最終docs revision
[`af91974d091feb276db79baf838c7fa2bab8904f`](https://github.com/rioriost/pg_agmemory/commit/af91974d091feb276db79baf838c7fa2bab8904f)の
[CI 35265233011](https://github.com/rioriost/pg_agmemory/actions/runs/35265233011)は**失敗**しました。
amd64は**772合格、1失敗、631.34秒**、arm64は**773合格、701.79秒**で全smokeも合格しました。
既存100-path auto-plan graph上限caseで再び`QueryCanceled` / statement timeoutによる
**503**が発生しました。実際の失敗CI planは**未取得**です。
そのCI planでなく、保持済みの使い捨てDB診断`graph-materialized-plan.json`が
`adjacent` materialization後に残るcanonical metadataとendpointの反復scanを示します。
追加修正はこのCTEを維持し、scope/predicateで絞ったassertion、timeで絞ったrevision、
重複しない認可済み・根拠有効なendpointを別途materializeします。
事前計算ID arrayでsemijoin反転と保護されたcanonical dataの反復scanを防ぐ設計で、
外側joinはmaterialize済みの認可metadataを使います。
RLS、scope/time/根拠の意味、順序、上限、**5000 ms** timeout、
version/API/schemaと30 resource methodは維持します。

検証済み全方向回帰は`auto`、`generic`、`nested_loop`を対象とし、実際のgeneric prepared使用のassertionも維持します。
runtime roleで実際のSQLとparameterに`EXPLAIN ANALYZE`を使い、
100-pathで100行、`assertion`/`assertion_revision`のscan loop **<= 1**、
`entity`/`entity_evidence`のscan loop **<= 2**を確認しました。
mode/方向の全9組でこれらの検査に合格しましたが、本番完了は主張しません。

**初期v22実装の証拠であり、追加修正の適格性確認ではありません。** Apple Containerのfull
`./scripts/test-containers.sh`は**773合格、既存warning 1件、447.40秒**でした。
**既存730 + 新規43 case**で、contract 8、capture 18、SDK 17です。
captureは新規17 caseと既存の実応答消失テストへのbatch parameter 1件、
SDKは不正key parameter 9、mock 6、real workflow 1、
既存の実応答消失テストへのbatch parameter 1件を追加しました。
既存のactive job 100件quotaテストも後方batch rollbackを確認し、件数は増やしていません。
実装
[`75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe`](https://github.com/rioriost/pg_agmemory/commit/75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe)は
完全一致SHAの[CI 35262682028](https://github.com/rioriost/pg_agmemory/actions/runs/35262682028)に合格しました。
native amd64は**773合格、621.56秒**、arm64は**773合格、702.01秒**でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
従来の全production smokeとbatch captureのworker/replay/purgeが全3環境で合格しました。
coverageは1/16/17件境界、正規化重複、後方quote/audit/quota rollback、
principal所有権/現在write ACL、順序付きreplayと競合、従来capture/Observeとの相互運用、
独立したterminal job、source/job/result purge、全childのreplay検査、古いleaseのfenceを含みます。
実際のcommit後の応答消失はSDKの`outcome_unknown: true`となり、
明示的な同じkey/bodyでの回復と256 KiB body上限も確認しました。
初期結果は最終docs CI失敗を覆すものでも追加修正の適格性確認でもありません。
所要時間はbenchmarkではなく、M0〜M3/MVP、性能、記憶品質、本番、DR適格性確認は主張しません。
[検証済み証拠](../STATUS-jp.md#v0022--schema-10)を参照してください。
v21のdocs run失敗、検証済みcode修正、別の成功した最終docs CIは
[過去の証拠](../STATUS-jp.md#v0021--schema-10)であり、v22の適格性確認ではありません。
