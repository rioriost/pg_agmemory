# ADR 0003: Typed checkpointと保守的なlineage

[English](0003-checkpoints.md) | [現在の契約](../STATUS-jp.md#checkpointの契約) | [運用](../operations/README-jp.md#checkpointの運用)

- 日付: 2026-09-16
- 状態: v0.0.3/schema 3を実装済み。ローカル/native Docker検査は合格。M1全体は未完了
- 拡張対象: [ADR 0001](0001-initial-slice-jp.md)、[ADR 0002](0002-assertion-revisions-jp.md)
- 命名: ローカルdirectory/package/serviceは`pg_agmemory`、
  公開repositoryは`rioriost/pg_agmemory`

**v0.0.3当時の記録です。** [ADR 0004](0004-tool-effects-jp.md)は下記の
ledger延期とsnapshot-onlyの照合/再開判断を置き換えます。
v0.0.4はlive run ledgerを参照し、全未追跡hintを阻止してeffect purge後のrunを封鎖します。
保存checkpoint checksumは変わらず、下記の決定と検証証拠はv0.0.3当時の範囲に限定します。

## 決定と範囲

scope内のrun/branch identity、正確な宣言済みmemory参照、HMAC完全性検査、
branch-head compare-and-swapを備えたtyped・不変checkpoint payloadをPostgreSQLへ保存します。
state-envelope APIであって、harness adapter、実行engine、
durable外部副作用ledger、災害復旧システムではありません。
assertion/episodeのrecallとexplainは分離したままです。

state schemaはversion 1のみで、`goal`、`constraints`、`completed_actions`、
`decisions`、`unresolved_questions`、`next_actions`、typedな`pending_effects`を持ちます。
未知/任意objectや実行可能なserializationは受け付けません。
pending effectは`operation_id`、短いdescription、
`planned / dispatched / unknown`のstatusを持ちます。
caller申告のsnapshot hintであり、サービスが確認したaction結果ではありません。

## 作成、読取り、完全性

| 操作 | 決定 |
|---|---|
| `POST /v1/checkpoints` | scope/run/branch、harness ID/version、typed state、非負event watermark、明示`expected_head`（空branchはnull）を要求。server UUID、sequence、parent、checksumを返す |
| `GET /v1/checkpoints/{checkpoint_id}` | 現在の認可、checksum、state schema、可視参照を再検査し、recall itemでなくenvelopeを返す |
| `POST /v1/checkpoints/restore` | harness/version/schemaの完全一致と未使用target branchを要求。元のscope/runに新checkpointを作る |

重複しない`(memory_id, revision)`参照を最大100件まで許可します。
同一scopeで読取り可能なepisode（revision 1）または存在するassertion revisionを指します。
checkpointは`memory_refs`のsourceにできず、
checkpoint間の依存は自動記録するparent lineageで表します。

サービスとDBがbranch headをlockします。sequenceは1から始まり、
採用成功時だけ増加し、watermarkは減少できません。
parentは同一scope/runの過去checkpointで、restore時は別branch上のparentも許可します。
DB制約が参照件数、有効なhead、parent順序、取消不能なbranch失効を強制します。
runtimeは特権helperを使いません。

tenant session lockとbuffer済み応答のcommit-before-send境界を維持します。
checkpoint作成は1 MiB、他endpointは256 KiBのbodyまで受け付けます。
tenant-keyed `hmac-sha256-v1` checksumは保存state envelope、
identity、参照、保存時epochを対象とします。
サービスの信頼境界内の完全性を確認するもので、意味的な真実やaction発生の証明ではありません。
保存時/現在のaccess・deletion epochは説明metadataであり、現在の認可の代わりにはなりません。

同じkey/payloadの再送はcommit済みcheckpoint参照を維持し、
idempotency記録にはstateを含めません。
restore再送は新branchを作らず現在の検査で既存forkを読み込み、
現在のepoch metadataは変わり得ます。purge済みcheckpointは再送で復元できません。

## Restoreは実行ではない

restoreはstate/参照を新branchのsequence 1へコピーし、元checkpointをparentとして残します。
元branchを変更したり巻き戻したりしません。
保存済みassertion参照は正確な過去revisionを維持し、
最新revisionや自動更新した外部事実へ置き換えません。
harness ID、version、state schemaの不一致を拒否し、
既存target branchは失効済みであっても再利用できません。

restore時にdispatched pending effectをunknownへ変更します。
GET/restoreともdispatched/unknownのoperation IDを`requires_reconciliation`へ列挙し、
一つでも残れば`resume_allowed: false`です。`automatic_reexecution`は常にfalseです。
`resume_allowed: true`でもsnapshot hintにすぎず、送金・送信・削除などの副作用を
再実行する認可ではありません。照合や実行はcallerが行い、
receipt照会やeffect ledgerはここでは実装しません。

## 宣言済み依存と削除

**callerはmemory stateの依存をすべて宣言しなければなりません。**
未宣言コピーのsemantic scanner、同意台帳、PII/secret自動除去はありません。
型が定まっていても任意の本文が安全になるわけではありません。

purgeは全revisionのepisode-to-assertion根拠、
episode/assertionからcheckpointへの直接参照、
forkを含む全parent-to-child checkpoint linkを辿ります。
子孫自身の`memory_refs`が祖先sourceを省略しても、
保守的に完全なparent lineageを引き継ぎます。
したがって旧assertion revisionだけのsourceでも、assertion全履歴と
影響する全checkpoint payloadを削除します。

上限はclosure全体で要求rootに加えて依存物10,000件です。超過は原子的に失敗します。
purgeは影響するbranch headを永続失効させ、同じtransactionでpayload/参照を
tombstoneより先に削除し、既存tenant lockで先行応答をdrainします。
branch IDは再開できません。opaque run/branch/object anchorとHMAC再送metadataは
tenantの存続期間中残すため、完全消去ではありません。
backup保持と隔離復元の要件は変わりません。

## Schemaと適格性確認

追加的な`003_checkpoints.sql`で`memory.checkpoint_run`、
`memory.checkpoint_branch`、`memory.checkpoint`、`memory.checkpoint_reference`を
導入し、RLS・複合key・採用/失効検査を設けます。
`001_initial.sql`、`002_assertion_revisions.sql`は変更しません。
v0.0.3の要求schema ledgerは厳密に`[1, 2, 3]`です。

旧版/新版API trafficを停止し、backup、未適用migrationの原子的適用を行って、
対応する新APIのみ起動します。旧APIとのrolling共存やdowngradeは非対応で、
v0.0.1にはschema guardがありません。
[保守手順](../operations/README-jp.md#v003の保守migration)に従ってください。
checkpoint restoreはPostgreSQL backupからの復旧ではありません。

v0.0.3のcommit [8adb40a](https://github.com/rioriost/pg_agmemory/commit/8adb40a)は、
Apple Containerとnative Dockerのlinux/amd64・linux/arm64で、
それぞれ54テスト、Ruff、strict mypy（source 7ファイル）、
production HTTP health smokeが合格しました。
[CI run 35088907082](https://github.com/rioriost/pg_agmemory/actions/runs/35088907082)と、
報告された[検証証拠](../STATUS-jp.md#検証証拠)を参照してください。
別のworking snapshot/compaction、durable effect ledger、harness adapter、
worker、vector/graph検索、M1全体/DR適格性確認はこのsliceの対象外です。
