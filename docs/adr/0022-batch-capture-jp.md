# ADR 0022: 明示batch capture

[English](0022-batch-capture.md) | [契約](../STATUS-jp.md#explicit-batch-capture) | [運用](../operations/README-jp.md#explicit-batch-capture)

- 日付: 2026-09-18
- 状態: 上限付きv0.0.22/schema 10で採用・検証済み。localと両native architectureで確認
- 拡張対象: [atomic capture](0010-atomic-capture-jp.md)、[durable job](0006-durable-jobs-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/性能/記憶品質/本番/DRの適格性確認ではない

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

**最終localとnativeの実装検証は合格しました。** Apple Containerのfull
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
これは実装結果であり、最終docs CIはまだ開始していません。
所要時間はbenchmarkではなく、M0〜M3/MVP、性能、記憶品質、本番、DR適格性確認は主張しません。
[検証済み証拠](../STATUS-jp.md#v0022--schema-10)を参照してください。
v21のdocs run失敗、検証済みcode修正、別の成功した最終docs CIは
[過去の証拠](../STATUS-jp.md#v0021--schema-10)であり、v22の適格性確認ではありません。
