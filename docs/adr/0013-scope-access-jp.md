# ADR 0013: 特権scope-access管理

[English](0013-scope-access.md) | [契約](../STATUS-jp.md#scope-access-administration) | [運用](../operations/README-jp.md#scope-access-administration)

- 日付: 2026-09-17
- 状態: 上限付きv0.0.13/schema 9で採用・検証済み。localと両native architectureで確認
- 拡張対象: [identity/永続化境界](0001-initial-slice-jp.md)、[durable job](0006-durable-jobs-jp.md)、[SDK境界](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/本番/性能/品質/DR/完全消去の完了ではない

**過去版の注記:** このADRはstage `m2-scope-access`と起動版の決定を含む
検証済みv0.0.13/schema 9の記録です。
[ADR 0014](0014-runtime-readiness-jp.md)は同じschema 9とscope-access契約を維持する
application-only v0.0.14 runtime readinessの記録です。localと両native検査は合格しました。
別の最終v0.0.13 docs CI 35198499967は
[過去の証拠](../STATUS-jp.md#v0013--schema-9)に記録します。

## 決定と権限

信頼する管理者が**既存の同一tenant**のtenant/scope/principal UUIDを管理する
`pg-agmemory scope-access get|set|revoke`を追加します。tenant/scope/principalは作成しません。
`PGAG_ADMIN_DATABASE_URL`のみを使い、DB roleは`rolsuper`または`rolbypassrls`と
適切なSQL権限を要求します。`get`でもruntime資格情報を拒否します。
`get`は`FOR UPDATE`を使わず、非owner `BYPASSRLS` inspectorも`memory`のUSAGEと
schema履歴/tenant/scope/principal/`scope_member`のSELECTで照会できます。
変更には追加でtenant UPDATE、該当membership DML、`memory_ops` USAGE、
audit INSERTが必要です。RLS bypass roleは引き続き必須です。
JWT、`--subject`、`--once`、runtime URL fallback、取得本文からのidentity選択はありません。
HTTP/MCP/SDK管理methodはなく、public memory resourceは維持します。

過去の手書きSQL runbookよりこの上限付きCLIを優先します。
操作前にrole、厳密なschema履歴1〜9、`public`内の`vector` 0.8.6を検査します。
PostgreSQL 18.6/pgvector 0.8.6固定imageとPython依存版は維持します。
stageは`m2-scope-access`となり、capabilitiesに`scope_access_administration`を追加します。
`transport: "admin-cli"`、`command: "scope-access"`、
`compare_and_swap: "tenant_access_epoch"`、`audit: "database_role"`であり、
新endpointではありません。

## 明示全置換とtenant-wide CAS

`get`は`--tenant-id`、`--scope-id`、`--principal-id`だけを受け付けます。
両変更は**1〜9223372036854775807**の`--expected-access-epoch`を要求します。
lock下でtenant全体counterを**no-op判定前**に比較します。
stdout喪失後や無関係scope変更後も古いCASは常に競合し、
idempotency keyやreplay receiptではありません。

`set`はpermission全置換とexpiryの明示選択を必須とします。
未来のtimezone-aware `--expires-at`または`--no-expiry`を選びます。
write-only/delete-onlyを含む重複しないread/write/delete、または`admin`単独を許可し、
重複やadmin混在を拒否します。DB flagはNative action/read要件を上書きしません。
permission順序変更は同等です。naive timestampとlock後のDB clock以下のexpiryを拒否します。
expiryだけの変更もepochを進め、省略による暗黙の無期限grantはありません。
`revoke`は行を削除しpermission/expiry optionを受け付けません。
現在のexpected epochで既に不在ならno-opです。

成功はwrapperなしJSON 1行で、`operation`、`tenant_id`、`scope_id`、`principal_id`、
`access_epoch`、`changed`、`membership_exists`、`permissions`、`expires_at`、
`effective_permissions`、`evaluated_at`を含みます。
membership不在はfalse/空/nullであり、既存legacy空permission行と区別します。
flagはread/write/delete/admin順で、有効adminは全4 flagへ、期限切れは空へ展開します。
DB `evaluated_at`時点のmembership状態であり、Native認可全体や将来accessの保証ではありません。
自然期限切れはepochや保持payload/auditを変えず、処理中応答もdrainしません。
強いdrainには明示revoke/barrierが必要です。

## Durable auditのためのschema 9

`009_scope_access.sql`と特権専用`memory_ops.scope_access_event`を追加し、
forced RLSとruntime policy/grantなしを適用します。
`(tenant_id, access_epoch)`を主keyとし、同一tenantのscope/principal FKを持ちます。
`set`/`revoke`、変更前後flag/expiry、DB clockの`recorded_at`、`current_user`由来の`database_role`を
保存し、平文memory本文、external subject、DSNは保存しません。
`evaluated_at`はresponse専用でaudit fieldではありません。
`database_role`は実行roleであり、申告されたend-user actorではありません。
既存ACL行を維持し、過去の手動変更へのaudit backfillや暗黙ownership/purge semantics変更は行いません。
実変更だけがmembership変更、tenant epoch増加、event追記を原子的に行います。
no-op/get/conflictはevent/epoch増加を作らず、変更途中の失敗は3者をまとめてrollbackします。
epoch上限はwrapせず拒否します。

durable管理auditにはこのschema変更が必要ですが、特権管理者はDBを変更できます。
改ざん耐性の証明、独立revocation復旧台帳、DR solutionではありません。
grantでpurge済みdataは復活せず、現在ACL/削除記録のrestoreは手動のままです。

## Barrierと結果不明

**connect/statement/lock各5秒timeout**の専用同期autocommit admin connectionを使います。
`get`/no-opも含め、API/workerと同じ正規化tenantの**session** advisory lockを取得します。
transaction commit**とCLI JSON stdout flushまで**保持し、すべての経路でcloseして解放します。
connectionをpoolせず、transactionだけのlockにも置き換えません。
先行する遅い応答は管理操作を遅らせ、lock timeoutは何も変更しません。
協調する同じ版のAPI clientは管理操作中onlineを維持できます。
migrationは引き続き旧API/worker/adapter/hook/SDK callerの停止/drainが必要です。
以後は厳密なservice 0.0.13 / API v1 / schema 9を要求し、rolling共存は認めません。

syntax/model/config errorは固定sanitized stderr、exit 2、JSONなしです。
DB/domain errorはstdout `{error: {code, outcome_unknown}}`、exit 1で、
raw DB error/DSN/資格情報/dataを含めません。
[catalog](../STATUS-jp.md#scope-access-administration)はrole/privilege/schema/extension、
not-found/CAS/expiry/上限、DB障害を区別します。
commit通信失敗は保守的に結果不明で、commit試行前の失敗は不明ではありません。
cancel、kill、stdout喪失でも変更結果は不明になり得ます。
新しい`get`と特権auditを確認してから、新CAS操作を明示承認します。
自動retry、Idempotency-Key、admin変更receipt、queueはありません。
barrierは配信済みcontextを撤回しません。

## 検証境界

**2026-09-17 JSTにv0.0.13最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**464テスト、既存warning 1件**、
Ruff、strict mypy（source 19 + SDK consumer 1ファイル）、真のcore/hook/sdk-only導入、
scope-accessを含むnon-root production全smokeが合格しました。
内訳は既存426 + unit 22 + integration 16テスト（新規38）です。
schema 8→9のDDL後のledger記録失敗とrollback/retry、以前のmigration、
role/CLI/CAS/expiry/audit、response/flush barrier、結果不明commit cleanup、
既存resource/adapterを検査しました。実装は
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413)
で公開済みです。[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)は
この完全一致SHAで合格し、native実logで件数、検査、smokeを確認しました。
所要時間は**local 297.52秒 / amd64 539.86秒 / arm64 460.73秒**です。
これは実装の結果であり、その後の最終docs CI runではありません。
[検証証拠](../STATUS-jp.md#v0013--schema-9)を参照してください。所要時間は性能benchmarkではありません。
別々の検証済みv0.0.12実装/最終docs runは
[過去の証拠](../STATUS-jp.md#v0012--schema-8)であり、schema 9検証ではありません。
