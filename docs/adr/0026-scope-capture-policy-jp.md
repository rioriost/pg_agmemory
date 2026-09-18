# ADR 0026: 管理者によるscope capture受付制御

[English](0026-scope-capture-policy.md) | [現在の状態](../STATUS-jp.md#scope-capture-policy) | [運用](../operations/README-jp.md#scope-capture-administration)

- 日付: 2026-09-18
- 状態: v0.0.26/schema 11の実装`c07630009ff4dcc34542e3ea80064d4f10c4d8b5`はlocal・native適格性確認済み
- 拡張: [ADR 0013: Scope access](0013-scope-access-jp.md)、[ADR 0022: Batch capture](0022-batch-capture-jp.md)
- 境界: 将来のepisode受付であり、同意の証明や自動抽出ではない

## 背景

scope ACLは誰が読書きできるかを決めますが、新episode captureの有効/無効、
許可するsource/consent label、本文sizeを表現しません。
次の限定M2 sliceではobserve、単一capture、batch captureと各replay経路で共有する
永続的な管理者設定が必要です。caller申告の同意labelを検証済み同意と扱ったり、
認可を迂回したり、providerを自動実行したりしてはいけません。

## 決定

**service 0.0.26 / API v1 / schema 11**、stage `m2-scope-capture-policy`を使います。
`PGAG_ADMIN_DATABASE_URL`を使う管理CLI
`pg-agmemory scope-capture get|set`だけを追加します。
memory REST route、SDK resource、MCP toolは追加せず、Native/SDKは
**31 memory resource**、MCPは**4 tool**、hookは不変です。
capabilitiesはadmin transport、tenant access-epoch CAS、4 field、3受付経路、
replay再検査、未設定時のlegacy動作を示します。
`auto_synthesis`とglobal `model_inference.live_provider_qualified`はfalseのままです。

### Patchではない完全policy

全4 fieldを必須とし、未知fieldを拒否します。

| Field | 契約 |
| --- | --- |
| `enabled` | strict boolean |
| `source_namespaces` | `null`または相異なる最大64正規化文字列 |
| `consent_references` | `null`または相異なる最大64正規化文字列 |
| `max_content_bytes` | strict整数1〜262144 |

listの文字列は空白除去後1〜256文字、C0 controlなしの有効UTF-8、
正規化後に相異なり、正規sortします。`null`は制限なし、`[]`はすべて拒否です。
比較は大文字小文字を区別する完全一致で、wildcardや外部同意lookupはありません。
byte上限は正規化した`Observe.content`のUTF-8 byte数であり、
serializeしたJSONやraw requestのsizeではありません。
既存の本文65,536文字上限とNative body 256 KiB上限も適用します。
CLI policy fileは64 KiB上限です。

保存rowなしは完全なlegacy policyを意味します。

```json
{
  "enabled": true,
  "source_namespaces": null,
  "consent_references": null,
  "max_content_bytes": 262144
}
```

`get`は有効policy、tenant `access_epoch`、最後のpolicy変更
`policy_access_epoch`（rowなしならnull）、`configured`、`changed`を返します。
`set`は現在tenant epochと完全policyを要求します。同値setもCASを検査しますが、
row作成/更新、epoch増加、auditはありません。特に未設定scopeへのdefault設定はno-opです。
完全なlegacy policyへ戻しても以前のrowを削除せず、reset/delete commandはありません。
epoch 9223372036854775807でも正しいepochのno-opは可能ですが、
実変更は`access_epoch_exhausted`です。

### 認可、transaction、storage

共有`admin.py`接続/error helperは既存scope-accessのimport/error互換性、
厳密なschema/pgvector検証、特権role検査、上限付き接続/statement/lock待機、
tenant **session** advisory barrierを維持します。
SQL transactionだけでなく、commitから**管理出力deliveryまで**保持します。
`pg_agmemory.scope_access.ScopeAccessError`は
`pg_agmemory.admin.AdminError`のaliasとしてimport互換性を維持し、
既存`MAX_EPOCH`/`Epoch`のimportも残します。

実変更は`memory.scope_capture_policy`の更新、tenant access epochの
**一度だけ**の増加、完全な変更前後policyと`database_role`を含む
`memory_ops.capture_policy_event`挿入を原子的に実行します。
他scopeの変更とmembership mutationも同じtenant CASを共有します。
config tableはFORCE RLSで現在読取り可能なscopeへのruntime SELECTのみです。
private auditはFORCE RLS、runtime policy/grantなしです。
runtime roleはどちらのtableも管理できません。

管理errorは`{error: {code, outcome_unknown}}`のみで、引数errorも静的です。
生SQL/DSN診断は公開しません。応答失敗/喪失の前にcommit済みの場合があります。
`get`でreadbackし、現在policy/epochと必要に応じprivate auditを整合確認してから、
追加変更に現在CASを使います。blind retryはしません。

### Replayより先にpolicy

現在のscope **readとwrite**認可をpolicyより先に確認します。
不存在/未認可scopeは**404 `not_found`**を維持します。
observe、capture、batch captureはidempotency/source-event dedupより先に
現在policyを検査します。したがって制限後は過去の完全一致replayでも
**403 `capture_policy_denied`**となり、書込みません。
不正な保存設定は**503 `capture_policy_invalid`**でfail-closed、
capture本文の不正UTF-8は既存Pydantic検証の**422 `invalid_request`**であり、新codeではありません。
OpenAPIは型付き403を宣言します。既存request形状/size検証は不変です。
SDKは両policy codeを保持し、mutationの403は
`retryable: false` / `outcome_unknown: false`です。
503はpolicyがfail-closedでも保守的に`true` / `true`を維持し、自動retryは追加しません。

policyは将来の受付制御であり、遡及purge/cancelではありません。
既存内容はACLに従って読取り可能で、明示remember/jobは既存episodeを使えます。
実変更は古いcontext/claim epochを無効化し、claim済みjobをfenceしますが、
workerは新epochで復旧できます。capture無効化はqueued publicationを
永久停止させる約束ではありません。

## 代替案と影響

- runtime書込み可能policyやREST管理endpointは権限範囲を拡大するため、
  管理資格情報をruntime/adapterの外に保ちます。
- principal別allowlistや別policy epochは既存tenant fence/CASモデルを分断するため、
  scope設定と既存tenant access epochを使います。
- 初回insertだけの検査ではcapture無効化後の古い完全一致replayを許してしまうため、
  両dedup経路の前で再検査します。
- default復元時のrow削除は有効policyと設定履歴を曖昧にするため、
  明示全置換でrow/audit履歴を残します。
- 不正保存policyでfail-openにすると制限が黙って外れるため、
  寛容なfallbackを作らずfail-closedにします。

このpolicyは同意検証、secret/PII検出、provider egress認可、
自動capture/抽出/合成、compaction**ではありません**。
labelや管理snapshot自体も機微情報になり得るため、
backup/private auditとともに管理者の運用制御で保護します。

## Upgradeと適格性確認

既存migrationに続き`011_capture_policy.sql`を適用します。
固定PostgreSQL **18.6**と**`public`内の`vector` 0.8.6**を維持し、
厳密なledger **1〜11**を要求します。backup後、旧版・新版API、
schema 10 writer、worker、replica、adapter、caller、管理操作を停止/drainしてmigrationします。
対応schema 11 service/adapterだけを再起動します。
schema 10 binaryはschema 11を拒否し、code revertだけではDBを戻せません。
downgrade commandはありません。forward fix、または隔離環境で完全backupを
対応旧componentとともに復元し、現在ACL/削除/policy判断を整合確認します。
[運用手順](../operations/README-jp.md#schema-11-scope-capture-policy-upgrade)を参照してください。

**Localと完全一致SHAのnative適格性確認は合格しました。**
実装`c07630009ff4dcc34542e3ea80064d4f10c4d8b5`のApple Containerは
1083合格、opt-in live skip 5件、既存warning 1件 / 499.15秒で、
全lint/type/導入/non-root smokeも合格しました。live model/cloud resourceは使用していません。
[Native CI 35308638587](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587)も
完全一致実装で合格し、Docker Linux **amd64 558.30秒**、**arm64 784.48秒**、
各**1083合格 / skip 5件 / warning 1件**、capture policyを含む全check/non-root smokeも合格です。
live呼出しはありません。job linkは[v26証拠](../STATUS-jp.md#v0026--schema-11)を参照してください。
後続の文書専用publication commitは検査済み実装SHAではありません。
過去v25のCA/CIとv24 live provider結果はschema 11の適格性を示しません。
M2/MVP/production、性能、検索品質、DR、full-erasureの受入は主張しません。

v26後の次の限定再開点は**M2の自動synthesis、embedding統合、compaction**と、
残る**品質/task-replay gate**です。承認済みepisodeのopt-in抽出/publication workflowを別途設計し、
明示的な同意/provider egress承認、untrusted proposal review、根拠検証、
安定したintent/replay処理、EN/JA品質/費用評価を自動化より先に定義します。
compactionとtask-replay受入にも個別の上限付き証拠が必要です。
これらは未実装であり、capture受付は有効化もprovider egress認可もしません。
