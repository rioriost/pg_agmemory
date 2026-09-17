# ADR 0019: 所有jobの照会とpagination

[English](0019-job-query.md) | [契約](../STATUS-jp.md#owned-job-query-and-pagination) | [運用](../operations/README-jp.md#owned-job-query-and-pagination)

- 日付: 2026-09-18
- 状態: 上限付きv0.0.19/schema 10で採用・検証済み。localと両native architectureで確認
- 拡張対象: [durable job](0006-durable-jobs-jp.md)、[job取消](0015-job-cancellation-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/性能/本番/DRの適格性確認ではない

## 決定とrequest境界

認証付きread-only `POST /v1/jobs/query`を追加し、`Idempotency-Key`やwrite権限は不要です。
closedな`QueryJobs`は必須の重複しない`scope_ids`（UUID 1〜32件）、
重複しない`states`（最大5件、既定`[]`は全状態）、strict整数`max_items`（1〜100、既定20）、
`before: JobCursor | None`（既定null）を受け付けます。
`JobState`は`pending`、`running`、`succeeded`、`failed`、`cancelled`をそのまま再利用し、
lifecycleを変えません。closedな`JobCursor`はtimezone付き`created_at` timestampとUUID `job_id`が必須です。
不正形式、重複、上限、timestamp/UUID、追加fieldは**422 `invalid_request`**です。
owner/principal/tenant/kind/payload-query filter、offset、watch、`after`、過去ACL selectorは受け付けません。

## 所有権と全page検証

現在tenantと要求scope内で、現在のRLS/source/削除可視性と任意stateを満たす
現在callerの所有jobだけを選びます。scope-admin権限でも所有権をbypassしません。
既知IDの`GET /v1/jobs/{job_id}`はより広く、他principal所有の同一scopeの
現在読取り可能jobも引き続き読めますが、queryでの発見はできません。
未知/読取り不可scopeはrecall同様に結果へ寄与しません。
一致なしは**200**と`jobs: []`、`next_cursor: null`で、
非公開理由、total、別principalのjob発見signalは返しません。

選択された各page itemを`Jobs.get`で再読取りし、参照件数とresult生存検査を維持します。
実際に不正な選択jobがあれば既存**404 `not_found` / 409 `job_invalidated`**動作で全pageを失敗させ、
黙ったskipや部分成功にはしません。現在権限、削除検査、
tenant response-delivery/drain lockをpageごとに適用します。
claim、取消、state変更、worker/provider呼出し、自動retryはありません。

## Snapshot権限を持たないexclusive位置

順序は`created_at DESC, id DESC`で、exclusiveな
`(created_at, id) < (before.created_at, before.job_id)`境界を使います。
`max_items + 1`件を取得し、最大`max_items`件を返します。
overflowがある場合だけlookahead行でなく**最後に返すitem**から`next_cursor`を作ります。
省略/nullの`before`は最新から開始し、nullの`next_cursor`はこの継続の終端です。

cursorは署名なしの透明な位置情報であり、権限、receipt、cache、snapshot、retention objectではありません。
既存jobを指す必要もなく、偽造/古い/削除済みcursor位置も現在認可された所有行を限定するだけです。
state遷移でも元の`created_at`を維持し、`updated_at`順ではありません。
各pageは照会時点の結果で、state/アクセス/削除変更をまたぐ構成固定を保証しません。
古いcursorより新しいjobには`before`なしで明示再開します。
total count、snapshot watermark、global LSN、永続cursor、自動paginationはありません。

## 型付き応答とSDK

正確な**200 `JobPage`**は`jobs: list[ListedJob]`、
`next_cursor: JobCursor | None`、既存`Consistency`
（`access_epoch`、`deletion_epoch`）を含み、snapshot保証ではありません。
response modelは`jobs`を100件に制限し、SDKも同じ上限を検証します。
共有`MemoryService.epochs`はcheckpointのepoch readerをcheckpoint、recall、job queryで再利用し、
永続化、schema、mutation hashを変えません。
`ListedJob`は既存の完全な`JobDetail`へUUID `scope_id`を加え、GET形式は不変です。
参照、state/時刻、安全なerror、`retry_of`、元のresult revision 1を含み、
保存payload、evidence quote、`lease_token`、intent digestは含みません。

async `query_jobs(QueryJobs) -> JobPage`は内部必須の`mutation=False`、
call時model検証、通常の**request 256 KiB / response 2 MiB**上限、
sanitizedなread-only `outcome_unknown: false`を使い、自動retry/paginationはありません。
既存safe `job_invalidated`を再利用し、新error codeは追加しません。
Native/SDK resource methodは27で、MCPの4 toolは不変です。
job toolやhook field、SDKの管理/worker機能は追加しません。

## 導入と検証境界

全adapterで厳密なservice 0.0.19 / API v1 / schema 10を要求します。
stage `m2-job-query`は`job_query`を追加します。
`endpoint: "/v1/jobs/query"`、`ownership: "caller"`、
`order: ["created_at_desc", "job_id_desc"]`、`pagination: "exclusive_keyset"`、
`max_items: 100`です。
v18→v19はapplication-onlyで、project版以外にSQL migration、依存/provider/artifact固定値変更はありません。
旧componentを停止/drainして対応版を使い、厳密な履歴1〜10を維持します。
古いschemaには既存offline migrationが必要です。

**最終localとnativeの実装検証は合格しました。** Apple Containerの
`./scripts/test-containers.sh`は**exit 0**で、**663合格、既存warning 1件、
366.14秒（6:06）**でした。**既存630 + 新規33テスト**（`tests/test_contract.py`が16、
`tests/test_jobs.py`が10、`tests/test_sdk.py`が7）です。
実装
[`e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf`](https://github.com/rioriost/pg_agmemory/commit/e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf)は
完全一致SHAの[CI 35242118110](https://github.com/rioriost/pg_agmemory/actions/runs/35242118110)に合格しました。
native実logで各**663合格、warning 1件**、**amd64 638.06秒 / arm64 586.58秒**を確認しました。
Ruff、strict mypy **source 19 + SDK consumer 1ファイル**、真のcore/hook/sdk-only導入、
全non-root production smokeが全3環境で合格しました。
full suiteは実100-item page/101行overflow、同時刻UUID境界、全5状態と変更、
削除/偽造cursor、現在ACL/scope-adminの所有権境界、全page検証、read-only SQL、
明示的なtyped SDKの3 page、payload非開示、自動paginationなしを含みます。
新SDK production sequenceはsource + job三つ → pendingの最初の1件page →
middle jobを明示取消 → 次pageは最古jobだけ → cancelled jobを照会 →
source purge `object_count: 4` → pending照会が空、の順で合格しました。
所要時間はbenchmarkではありません。これは実装結果であり、後続最終docs CI結果ではありません。
[検証済み証拠](../STATUS-jp.md#v0019--schema-10)を参照してください。
v18実装と別の最終docs CIは[過去の証拠](../STATUS-jp.md#v0018--schema-10)であり、
v19の適格性確認ではありません。
