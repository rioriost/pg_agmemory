# ADR 0015: 明示job取消

[English](0015-job-cancellation.md) | [契約](../STATUS-jp.md#explicit-job-cancellation) | [運用](../operations/README-jp.md#explicit-job-cancellation)

- 日付: 2026-09-17
- 状態: 上限付きv0.0.15/schema 10で採用・検証済み。localと両native architectureで確認
- 拡張対象: [durable job](0006-durable-jobs-jp.md)、[capture](0010-atomic-capture-jp.md)、[SDK](0012-python-sdk-jp.md)、[runtime readiness](0014-runtime-readiness-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/本番/性能/品質/DR/完全消去の適格性確認ではない

**過去版の注記:** このADRは検証済みv0.0.15/schema 10のjob取消を記録します。
[ADR 0016](0016-required-context-jp.md)は同じschemaと25-resource surfaceで
v0.0.16 required-context recallを記録し、v15→v16はapplication-only導入です。
v0.0.16はlocalと両native architectureで検証済みです。別の最終v0.0.15 docs CI 35218254940は
[過去の証拠](../STATUS-jp.md#v0015--schema-10)に記録します。

## 決定と権限

認証付き`POST /v1/jobs/{job_id}/cancel`を追加し、caller管理の`Idempotency-Key`と
`CancelJob`を要求します。fieldは`expected_state`（`pending`/`running`）と
strict整数0〜5の`expected_attempt`だけで、runningは1以上を要求します。
追加field、reason/private自由文、provider/lease-token parameter、任意state強制を拒否します。
callerはjob GETからstate/attempt CASを明示選択し、tenant access epochや外部tool state/versionは使いません。

認証中のcurrent owner（`principal_id`）、scope read/write権限、source可視性/完全性を要求します。
同一scopeの`admin` readerでも他ownerのjobは取消できません。runtime `job_update` RLSは不変です。
不在/private/cross-tenant/non-job/purge済みtargetは404、
参照失効は`409 job_invalidated`です。
原子的UPDATEでexpected stateとattemptを照合し、成功は**HTTP 200**の`JobReceipt`
（`job_id`、固定kind `structured_remember`、固定recipe `structured-remember-v1`）です。
GETでcancelledを確認します。commit済み取消であり、202取消queueやworker killではありません。

## Terminal遷移と競合

有効/期限切れleaseを含むpending/runningから`cancelled`への遷移を許可します。
保存job payload、lease token/deadline、errorを消去し、resultはありません。
同じjob ID、attempt、source/evidence/opaque intent参照、retry parent、`created_at`を保持し、
DB `updated_at`で完了を記録します。
遷移、`job_cancelled` audit、HMAC保護request/keyのidempotency receiptは
原子的にcommitするか全rollbackします。保存replay結果は`{job_id}`だけです。
access/deletion epochは進めません。

worker claim/publicationも使う既存tenant session advisory lockをHTTP配信完了まで保持します。
古い準備処理が続いてもcancelledはrunningでないため、publish/heartbeat/failは
`job_lease_conflict`となり、worker outcomeは`lease_lost`です。
取消が先ならそのjobからresultは公開されません。
publicationが先にcommitしたら取消は競合し、succeeded resultを保持します。
provider abort、外部compensation、公開撤回は保証しません。

## Replayと保持

state/attempt不一致やterminalは`409 job_cancel_conflict`です。
既にcancelledの作業を新keyで取消する場合も含みます。
成功した同key/bodyのreplayは現在owner/write/access/liveness検査下で元receiptを返し、
同keyでbodyまたはjob IDを変えると`409 idempotency_conflict`です。
配信結果不明時は同じkey/bodyか現在job GETを使い、自動retry、新key、
expected値の盲目的更新を行いません。

cancelledはterminalで、failed専用retryは使えません（`409 job_retry_conflict`）。
同intentのenqueue/captureは復活させずそのjobへdedupし、capture replayは元episode/job pairを保持します。
recipe版やintent keyを作ってdedupを迂回しません。active-job上限と`jobs_pending`はcancelledを除外します。
取消は`forget`でなく、canonical episode/evidence、intent/dedup anchor、
worker準備memory、WAL、backupを消去しません。
source forgetは依存jobをpurgeして取消replayを拒否し、後のgrantでもpurge済みpayloadは復活しません。
data除去には引き続き明示forgetを使います。

## Schemaとadapter

新`010_job_cancellation.sql`はjob state/payload制約とguard triggerを変更し、tableは追加しません。
cancelledは不変で復活/書換えを拒否し、payload/result/lease/errorのnullを要求します。
新jobは引き続きpending/attempt 0です。他の既存stateはsemanticsを維持します。
schema 9→10は停止/drainと対応migration toolingを必要とし、
v13→v14 application-only手順は過去のものです。
PostgreSQL 18.6、pgvector 0.8.6、依存/provider選択、artifact pinは維持します。

非同期SDK `cancel_job(UUID, CancelJob, *, idempotency_key) -> JobReceipt`を追加し、
call時再検証、正確なHTTP 200、safe `job_cancel_conflict`を使います。
SDK内部POSTは成功statusでなくread/mutationを明示分類します。
200の取消にもkey検証と保守的な変更結果不明の扱いを適用し、
`forget` previewも保守的なmutation扱いを維持します。
Native memory resource/SDKは25 methodとなり、MCPの4 toolとread-only hookは変更しません。
Python taskのcancelはこのendpointを呼びません。
adapterの厳密なservice 0.0.15 / API v1 / schema 10とreadiness履歴1〜10を要求します。
stageは`m2-job-cancellation`で、capabilitiesへ`job_cancellation` metadataを追加します。
`endpoint: "/v1/jobs/{job_id}/cancel"`、`compare_and_swap: ["state", "attempt"]`、
`terminal_state: "cancelled"`、`provider_interruption: false`です。

## 検証境界

**v0.0.15実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)は
完全一致SHAの[CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)に合格しました。
各環境**535テスト、既存warning 1件**が合格しました。
内訳は**既存495 + 新規40**で、unit/integration別の件数は主張しません。
local Apple Containerは**298.29秒（4:58）**、native実logで確認した所要時間は
**amd64 406.88秒 / arm64 490.57秒**でした。性能benchmarkではありません。
Ruff、strict mypy **source 19ファイル + strict SDK consumer 1ファイル**、
真のoptional導入、全production smokeは全3環境で合格しました。
DDL後のschema 9→10 ledger失敗は以前のguard function、制約、schema 9履歴を復元してretryに成功し、
既存v6 jobのstate/attempt/payloadは不変でした。
実際のcommit済み取消応答喪失は`outcome_unknown: true`となり、同一replayは成功、
別keyは`409 job_cancel_conflict`と`outcome_unknown: false`でした。
production SDKのenqueue/cancel/同key replay/GET cancelled/worker `--once`
idle/source purgeのsmokeが合格し、そのfixtureは`object_count: 2`を返しました。
後続の最終文書公開/CI結果は主張しません。
[実装証拠](../STATUS-jp.md#v0015--schema-10)を参照してください。
別々の検証済みv0.0.14実装CI 35201615965と最終docs CI 35202931424は
[過去の証拠](../STATUS-jp.md#v0014--schema-9)として維持します。
