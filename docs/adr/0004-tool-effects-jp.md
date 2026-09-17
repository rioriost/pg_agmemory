# ADR 0004: Tool-effect ledgerとrun全体の再開境界

[English](0004-tool-effects.md) | [現在の契約](../STATUS-jp.md#tool-effect-ledger) | [運用](../operations/README-jp.md#tool-effectの運用)

- 日付: 2026-09-16
- 状態: v0.0.4/schema 4を実装済み。ローカルとnative Dockerの検査は合格。M1全体は未完了
- 置換対象: [ADR 0003](0003-checkpoints-jp.md)のledger延期とsnapshot-onlyの再開規則
- 命名: ローカルdirectory/package/serviceは`pg_agmemory`、公開repositoryは`rioriost/pg_agmemory`

過去の範囲: 以下のschema 4要件と証拠はv4時点のものです。
[ADR 0005](0005-relational-graph-jp.md)がentity参照とschema 5を追加します。
更新には本ADRの過去schema要件ではなく、現在の運用手順を使ってください。

## 決定と範囲

上限付きのdurableな外部tool-effect intent/outcome台帳をPostgreSQLへ保存します。
callerの意図と報告を記録するもので、サービスがprovider実行を観測した記録ではありません。
tool実行、承認サービス、provider receipt照会、自動再試行、外部exactly-once保証はありません。

先にcheckpointでscope内のrunを作る必要があります。
operation identityはHTTP `Idempotency-Key`とは独立したtenant/scope/run/operation内です。
新run/operation IDは実世界actionの意味的重複抑止ではありません。
権限、承認、dispatchの協調、actionの正規化、外部照合はhostの責任です。

## Identity、privacy、lifecycle

`POST /v1/tool-effects`は既存scope/run、caller operation UUID、tool名、
必須の小文字64桁hex `action_hash`、同一scopeの正確なepisode/assertion参照を最大100件受け付けます。
全memory依存を宣言してください。省略sourceをsemantic scannerが発見することはありません。
生のaction hashや引数は永続化せず、tenant-HMACの`action_fingerprint`と安定した
64桁hex `external_idempotency_key`を保存し、GETで返します。

同一operation identityで正規化plan bodyが一致すれば、HTTP keyが異なっても
head変更後も元のrevision 1参照を再利用します。intent変更は衝突し、
purge済みidentityは再作成できません。
HTTP再送記録は結果参照/revision/statusとkeyed request digestのみで、
payload、event reason、receiptのコピーは含みません。
plan/遷移応答は過去revisionの参照であり、現在状態のsnapshotや実行許可ではありません。
現在の認可の下で、生存effectはrun封鎖後も旧参照を再送できますが、
新たなdispatchは拒否します。purge済みeffectの完全一致再送は引き続き`404`です。

`POST /v1/tool-effects/{memory_id}/transitions`はCAS付きeventを追記します。
DBが時刻/actorを付与し、次のFSMを強制します。

```text
planned    -> dispatched | unknown
dispatched -> unknown | confirmed | failed
unknown    -> confirmed | failed
confirmed / failed (terminal; 以後の遷移なし)
```

`planned → unknown`はlegacy/protocol外の試行に関する不確実性を記録し、
実行は許可しません。`unknown → dispatched`は禁止です。
terminal eventは1〜256文字のreceipt参照と、
`provider_receipt`または`operator_review` sourceを要求し、他状態ではreceiptを禁止します。
receipt参照は**caller申告であり、サービス検証済みではありません**。
failedという結果も自動再試行を許可しません。

runの存続期間中の上限はterminalを含め100 effect、
各effectは最大4件の不変eventと100件の宣言済み参照です。
GETは最新状態/履歴、参照、HMAC識別子、現在の`run_invalidated`を返します。
RLS、tenant/scope外部key、遅延history/reference検査、head lock/CAS、
tenantのcommit/send lockを維持します。特権runtime helperは導入しません。

harnessは外部呼出し前にdispatchを永続記録し、providerが対応する場合は
安定した外部keyを使う必要があります。旧遷移応答の再送は新たな送信/盲目的再送の許可ではありません。
DBのCASは、このprotocol外で行われる外部actionの防止や取消しはできません。

## Checkpointの再開とrestore

checkpoint GET/restoreは、別branchやsnapshot後に追加したものも含め、
runの全生存effectを参照します。`tool_effects`は現在のsummaryで、
保存checkpoint checksumの一部ではありません。
tracked confirmed/failedはそのoperationの旧hintを解決し、
dispatched/unknownはsnapshot hintがなくても再開を阻止します。

**plannedを含む未追跡snapshot hintはすべて再開を阻止**し、
`untracked_effects`と`requires_reconciliation`に列挙します。
unknown/dispatched hintとtracked planned記録の組合せも阻止します。
plan登録だけで不確実性の証拠を消してはいけません。
保存stateを書き換えず、旧動作を意図的に厳格化します。
blockerがあれば`resume_allowed: false`、`automatic_reexecution`は常にfalseです。
再開可能という表示は権限や外部完了の証明ではありません。

restoreは新branch作成前に、runの全dispatched effectへ
`origin: "checkpoint_restore"`の`unknown`を原子的に追記します。
新revisionは古いledger writeを拒否しますが、進行中の外部呼出しは取り消しません。
元branchは変更せず、完全一致restore再送はjournal eventを重複作成せず既存forkを返し、
live summaryを再計算します。保存assertion参照は正確な過去revisionを維持し、
外部事実を自動更新しません。

## Purgeとrun封鎖

依存closureを宣言済みepisode/assertion sourceからeffectへ、
effectから古い空snapshotを含む**同一scope/runの全checkpoint**へ拡張します。
既存のassertion履歴・parent/fork lineageの保守的な追跡は維持します。
上限は引き続き要求rootに加えて依存物全体で10,000件です。

どのeffectでもpurgeすると影響する全checkpoint payloadを削除し、
`effects_invalidated`を永続設定します。新effect plan、dispatch、checkpoint、再開は禁止です。
独立した生存effectは同じrunというだけでpurgeせず、
GETと`unknown → confirmed/failed`を含む許可済み照合遷移は可能ですが、dispatchは再開できません。

tenantのread/drain barrier下で、同じtransaction内のtombstone挿入前に
effect payload、参照、event reason/receipt参照をSQL削除します。
opaque operation identity、run flag、object anchor/tombstone、HMAC再送metadataは
tenantの存続期間中残します。WAL、backup、物理媒体、配信済みdataの消去ではなく、
operator-managed backupの制限は維持します。

## Schemaと適格性確認

追加的な`004_tool_effects.sql`で`memory.tool_effect`、
`memory.tool_effect_revision`、`memory.tool_effect_reference`、
`memory_ops.tool_effect_identity`、checkpoint-run失効flagを導入します。
migration 001〜003と旧保存checkpoint hashは変えません。
v0.0.4の要求ledgerは厳密に`[1, 2, 3, 4]`です。全API trafficを停止し、
backup、未適用migrationの原子的適用後に対応APIだけを起動します。
旧版とのrolling共存や自動downgradeは非対応です。
[保守手順](../operations/README-jp.md#v004の保守migration)に従ってください。

v0.0.4の実装
[4a7d3f8](https://github.com/rioriost/pg_agmemory/commit/4a7d3f8)は、
Apple Containerとnative Docker amd64/arm64で、それぞれ73テスト（既存warning 2件）、
Ruff、strict mypy（source 8ファイル）、production HTTP health smokeが合格しました。
[CI run 35098507356](https://github.com/rioriost/pg_agmemory/actions/runs/35098507356)と
[検証証拠](../STATUS-jp.md#検証証拠)を参照してください。
SQL/graph oracle、worker、MCP、harness連携、別のworking snapshot compaction、
自動backup/DRは将来課題であり、M1全体の完了ではありません。
