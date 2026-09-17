# ADR 0006: Durableな構造化publication jobとleaseで制御するworker

[English](0006-durable-jobs.md) | [現在の契約](../STATUS-jp.md#durable-job) | [運用](../operations/README-jp.md#durable-jobとworkerの運用)

- 日付: 2026-09-17
- 状態: v0.0.6/schema 6を実装済み。ローカルとnative Dockerの検査は合格。M0/M1/M2/M3全体は未完了
- 拡張対象: 明示assertion publicationと依存purge。
  [ADR 0005](0005-relational-graph-jp.md)のgraph/checkpoint/effect履歴を維持
- 命名: ローカルdirectory/package/serviceは`pg_agmemory`、公開repositoryは`rioriost/pgag_memory`

## 決定と範囲

明示要求された非同期の**構造化記憶publication**をPostgreSQLに保存し、
固定principal workerで処理します。自動synthesis、自然言語抽出、LLM/provider処理、
embedding、compaction、tool実行ではありません。
`observe`は同期観測のままで自動enqueueせず、`synthesis_job_id: null`です。
同期`remember`の契約とlegacy正規化JSON/HMAC順を維持します。

初期profileはglobal multi-tenant schedulerや適格性確認済みの公平性/cost pool設計ではありません。
M0/M1/M2/M3、MVP/本番、性能、記憶品質、完全消去、backup/DRの受入は未完了です。

## 明示job identityと安全なstatus

`POST /v1/jobs`は`Idempotency-Key`と
`{kind: "structured_remember", memory: <変更しないRemember request>}`を要求します。
現在のscope read/write権限、明示intent、同一scopeの読取り可能で重複しないepisode ID
1〜32件と各1〜4,096文字の原文quoteが必要です。recipeは`structured-remember-v1`だけで、
`202`は`{job_id, kind, recipe_version}`を返し、assertion完成ではありません。

`memory_ops.job`はkind `job`のobject anchorと、不変episode revision 1の`job_input`依存を
持ちます。保持`job_identity`はtenant/principal/scopeのHMAC identityを保存します。
canonical intent/recipeでHTTP keyをまたいで重複抑止し、job dedup用に根拠をsortします。
同じHTTP keyには同じ正規化requestが必要です。別principalは独立したjobを投入でき、
異なるsource identityの意味的重複は抑止しません。
scope当たりpending/runningは100件、job当たり最大5試行です。

`GET /v1/jobs/{job_id}`は現在のscope read権限を使い、同一scopeの他principalのjobも
対象です。state（`pending`、`running`、`succeeded`、`failed`）、試行回数/上限、
scheduling/lease時刻、安全なerror code、入力参照、retry parent、
nullableな元のrevision 1結果を返します。後のassertion訂正でも結果参照は変更しません。
request payload、lease token、owner principalは公開しません。
terminal成功/失敗でrequest JSONを消去し、不変入力ID参照はpurgeまで保持します。

jobはcheckpoint/effect参照kindやrecall/explain itemではありません。
recallは要求scopeの読取り可能なpending/running jobを`coverage.jobs_pending`で示し、
`synthesis_pending: false`と`graph_used: false`を維持します。
SQL graphを自動展開したり、synthesis完了を主張したりしません。

## Terminal変更でなく明示retry

`POST /v1/jobs/{job_id}/retry`はkeyと元の`EnqueueJob` body全体を受け付けます。
failed requestは消去済みだからです。failed parentのownerだけが、
現在の権限/根拠とHMAC intent検査の下でretryできます。
intent変更は`409 job_intent_conflict`、failed以外のparentは`409 job_retry_conflict`、
非ownerは`404`です。

一つのfailed parentにはHTTP keyをまたいでも一つの重複抑止済みretry childを対応付けます。
childには新たな5試行枠を与え、旧terminalは不変です。
childが失敗したらそのIDをretryして別の明示cycleを開始します。
任意のrecipe resetは非対応です。retry lineageは依存purgeのため保持します。

## Claimと原子的publication

`pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT [--once]`は
制限付き`PGAG_DATABASE_URL`で動作します。subjectは設定issuer内でprovision済みの
一つのprincipalを示す信頼する配置設定であり、HTTP偽装機能ではありません。
JWT署名/公開鍵やadmin URLは不要です。APIの起動role/schema検査がworkerも保護し、
superuser、table owner/owner所属、`BYPASSRLS`資格情報を拒否します。
そのprincipalのjobだけをclaim/publishできます。

APIとworkerは`principal_connection`のlookup、`bind_identity`の再検査、
同一tenantの**session** advisory lockを共有します。
APIは引き続きcommitとHTTP応答送出までlockを保持し、worker transactionは短命です。

1. `FOR UPDATE SKIP LOCKED`で実行時刻に達したjobを一つclaimし、試行回数を増やし、
   現在のaccess/deletion epochを保存して新UUID tokenと30秒leaseを付与します。
   現在の権限と完全な不変入力を確認してcommitします。
   内部の1〜300秒claim上限は制御されたテスト用で、CLI設定ではありません。
2. transaction外で保存済み構造化requestを検査/準備します。
   決定的processorは外部呼出しをしません。
3. identityを再bindし、現在のscope、完全なepisode入力、準備した元bodyとの完全一致、
   token/lease/期限、epochを再検査します。共有assertion-publication helperで結果assertion、
   provenance、job成功、auditを同時commitします。最後の更新でも期限を再検査し、
   publication途中で期限切れなら全outputをrollbackします。

assertionのrecorded/system timeはenqueueでなくworker publication時に始まります。
job作成時刻はassertion採用時刻ではなく、callerのvalid boundはserver管理のsystem履歴と独立です。

lease失効/引継ぎは古いwriterを拒否します。内部heartbeatはleaseとepochを確認して
30秒leaseを更新しますが、HTTP endpointはありません。
公開claim/publish APIや、このprocessor用の長時間heartbeat taskはありません。
at-least-once試行でjob当たり最大一つのcommit済み結果を作り、外部exactly-once保証ではありません。

再試行可能errorは最大5試行で`2^attempt + [0,1)`秒のbackoff/jitterを使います。
再試行不能な不正入力は即時失敗です。5回目のclaimが期限切れなら
`attempt_limit`でfailedとなり、6回目はありません。
log/statusはpayloadでなく安全なcode（`dependency_unavailable`、`stale_context`、
`invalid_input`、`attempt_limit`）を使います。terminal失敗からの回復は明示retryです。

継続modeはidle時1秒ごとにpollし、一時的DB loop障害後は2秒待ちます。
`--once`は実行時刻に達したjobを最大一つ処理し、JSONの
`idle`/`succeeded`/`pending`/`failed`/`lease_lost`と該当opaque ID/結果参照を返して終了します。
他commandではこのoptionを拒否します。
stdout/logのoutcomeはopaqueな過去参照であり、現在のread許可ではありません。
job GETと正確なrevisionのexplainが現在のアクセス権/削除状態を検査します。

## Purgeの方向と互換性

依存closureはepisode入力 → job、result assertion → job、parent job → retry子孫を
既存の10,000依存上限内へ追加します。
**job/control記録やfailed parentのretry chainを削除しても、公開済みの独立assertion出力や
source episodeは消えません。** output assertionには自身の直接episode provenanceがあるため、
factを消すにはoutput/sourceを明示purgeします。
どのrevision-sourceの削除でもassertion全体と依存jobをpurgeします。
job → resultの依存cycleはありません。

job request/入力行をassertion/episode行とtombstoneより先にtenant barrier下で原子的に削除します。
purgeは実行中publisherを拒否し、その後のjob GET/再送は`404`です。
保持job identityはexact jobの復活を防ぎます。opaque anchor、job dedup、HTTP receiptは
tenantの存続期間中保持します。backup/WAL/replica/配信済みdataの消去やDRは保証しません。

追加的な`006_durable_jobs.sql`は001〜005を変更しません。
forced RLS、同一scope外部key、入力完全性、限定したlifecycle UPDATE権限、
job遷移guardがintent、lease、試行回数、terminal不変性を保護します。
既存typed graph/effect/checkpoint履歴とlegacy冪等性を維持し、
jobはcheckpoint/effect参照kindになりません。

**旧版・新版の全APIとworker**を停止/drainし、backup、`pg-agmemory migrate`、
厳密な履歴`[1, 2, 3, 4, 5, 6]`の確認後、対応するAPI/worker版だけを起動します。
rolling共存やdowngradeは非対応です。旧v0.0.1には起動schema guardがなく停止を維持します。
復元DBは削除/ACL状態の再適用までAPI/workerから隔離します。

## 証拠の境界

実装commit
[a4aa7f6](https://github.com/rioriost/pgag_memory/commit/a4aa7f6c8a9ccc52f906619c64e70a8d00eae0d8)について、
Apple Containerとnative Docker amd64/arm64の各環境で114テスト（既存warning 2件）、
Ruff、strict mypy（source 11ファイル）、non-root production API HTTPと
実CLI worker smokeの両方が合格しました。
[run 35168437396](https://github.com/rioriost/pgag_memory/actions/runs/35168437396)
の両CI jobはこのSHAと完全一致し、実logで結果を確認しています。

job identity/retry/上限、lease引継ぎ/期限切れrollback、現在の認可/epoch、
publicationの原子性、依存purge、過去互換性を検査しています。
worker smokeは使い捨てprincipalとruntime専用資格情報で
`pg-agmemory worker --subject ... --once`を実行し、`{"outcome":"idle"}`を確認して
`Production worker smoke passed`をlogに記録しました。idle結果自体はqueue済みpublicationを
証明しません。所要時間と範囲は[STATUS](../STATUS-jp.md#検証証拠)を参照してください。
過去のv5証拠は[ADR 0005](0005-relational-graph-jp.md)で区別して保持します。
検査合格はM0/M1/M2/M3全体の完了や、自動synthesis、性能、記憶品質、
MVP/本番、完全消去、backup/DRの適格性を示しません。
