# 初期sliceの運用

[English](README.md) | [プロジェクトREADME](../../README-jp.md) | [現在の契約](../STATUS-jp.md)

**本番runbookや検証済み災害復旧手順ではありません。**
この初期releaseでは、許可済み・除去処理済みの使い捨てtest dataを使用してください。
purge訓練、schema reset、restore実験を含む破壊的操作は、
使い捨てtest DBだけを対象とし、業務DBや実userの履歴には実行しないでください。

## 初期設定とrole分離

PostgreSQL 18と、repositoryの`Dockerfile`から構築したimageを使用します。
CLI名は`pg-agmemory`、import package名は`pg_agmemory`です。

| 設定 | 利用者 | 用途 |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | 管理CLIのみ | migrationとprivate tenant/principal/scopeの作成 |
| `PGAG_DATABASE_URL` | API runtime | `pgag_runtime`に所属する専用の制限付きlogin |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | 2048 bit以上の静的PEM RSA検証公開鍵。署名用秘密鍵は渡さない |
| `PGAG_JWT_ISSUER` | API runtime | 信頼するissuerの完全一致値 |
| `PGAG_JWT_AUDIENCE` | API runtime | 本サービスのaudienceの完全一致値 |

1. admin URLが、意図した空の使い捨てMemory DBを指すことを確認します。
   アプリimageから`pg-agmemory migrate`を実行します。migrationはtransactionalで、
   `public.pgag_schema_migration`に版を記録し、適用済み版は再実行時にskipします。
   一般的なupgrade/rollback frameworkとして扱わないでください。
2. schemaは`src/pg_agmemory/storage/001_initial.sql`として同梱され、
   インストール済みpackage resourceから読み込まれます。
   実装プランの例示DDLで代用したり、別の生成済みmigration fileを想定したりしないでください。
3. 別の管理者で`NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime`の専用runtime
   loginを作り、passwordを安全に設定します。table所有権もmigration owner roleへの
   所属も付与しません。このloginにはrole/database作成権限を与えないでください。
4. admin URLで`pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT`を実行します。
   private tenant、principal、scopeを作成してIDを返します。
   provisionはendpointでもmembership更新コマンドでもありません。
   設定した信頼するissuerが発行したsubjectを使ってください。
5. runtime設定のみを渡して`pg-agmemory serve`を実行します。
   起動時にsuperuser、RLS bypass、アプリtable ownerとしての接続を拒否します。
   owner role経由の所属も対象です。

admin URL、署名用秘密鍵、token、tenant HMAC secretをsource管理、issue、
logへ残さず、不要なものをruntime環境へ渡さないでください。
runtime DB資格情報をagentへ渡して任意SQL入口にしてはいけません。
固定queryと信頼されたidentity contextも認可境界の一部です。

## 認証、通信、health

検証器はRS256と必須claimの`sub`、`iss`、`aud`、`iat`、`exp`を使い、
署名/issuer/audience/時刻を検証してPostgreSQL上でexternal subjectを解決します。
JWKS refresh、複数鍵を重ねるrotation workflow、delegated identityはありません。
issuerの変更にはsubject mappingの確認が必要です。
DBは複数issuerのnamespaceでprincipalを分けていません。

APIはHTTP port 8000で待受けます。信頼できるreverse proxyでTLSを終端し、
runtime portを信頼できないnetworkへ直接公開しないでください。
組込みcredentialや認証回避設定はありません。
`/docs`と`/openapi.json`はruntime生成のschema表示で、配置の認可設定ではありません。

`GET /healthz`は起動検証後のprocess livenessです。成功しても現在のDB接続、
認可の正しさ、本番readinessを保証しません。DB/lock障害は`503`になり得ます。
mutationの結果が不明なら、新しいkeyを作らず同じkey・同じpayloadで再送してください。
commit済みでもHTTP応答だけ失われる場合があります。

## Membership変更とrequest drain

**管理者の権限変更もAPIと同じlockに協調する必要があります。**
runtimeのmembership管理endpointはありません。APIはrequestごとに短命connectionを
開き、正規化したUUID textで
`pg_advisory_lock(hashtextextended(tenant_uuid::text, 0))`というsession advisory lockを
取得し、commitとbuffer済み応答の送出が終わるまで保持します。

一つの専用管理connection上で次の順に実行します。

1. membershipを変更する前に同一tenantの**session** lockを取得します。
2. transactionを開始し、権限/membershipを更新して、
   同じtransactionでtenantの`access_epoch`を増やします。
3. 意図したtenant/scope/principalと更新行数を確認してcommitします。
4. commit後にのみlockを解放するかconnectionを閉じます。
   失敗時はrollbackしてから解放し、lockを保持したsessionをpoolへ戻さないでください。

次の`psql`例は使い捨てtest DBで既存membershipをread-onlyへ縮小します。
provision済みtest記録の`tenant_uuid`、`scope_uuid`、`principal_uuid`を
`psql`変数として指定してください。全操作を同じ管理connectionで実行し、
`COMMIT`前に更新行数を確認します。
UUID castによりruntimeと同じlock keyになるようtextを正規化します。

```sql
\set ON_ERROR_STOP on
SELECT pg_advisory_lock(hashtextextended(:'tenant_uuid'::uuid::text, 0));
BEGIN;
UPDATE memory.scope_member
SET permissions = ARRAY['read']::text[]
WHERE tenant_id = :'tenant_uuid'::uuid
  AND scope_id = :'scope_uuid'::uuid
  AND principal_id = :'principal_uuid'::uuid;
UPDATE memory.tenant
SET access_epoch = access_epoch + 1
WHERE id = :'tenant_uuid'::uuid;
COMMIT;
SELECT pg_advisory_unlock(hashtextextended(:'tenant_uuid'::uuid::text, 0));
```

transactionだけのadvisory lock、異なるhash/seed、commit前のunlock、
lockなしのACL更新はdrain protocolを**満たしません**。
そのような管理操作の競合は保証対象外です。tenant全体を直列化するため、
遅い応答は同一tenantの無関係なrequestも遅延させ得ます。性能は未測定です。
配信済みcontextやnetworkへ渡したbyteをこのprotocolで失効・回収することはできません。

## Purgeと保持記録

明示IDを使い、破壊的なテストの前に`preview`を確認してください。
previewは対象を固定せず、purge時に認可と依存関係を再評価します。
内部schema constraintに将来mode名があっても、
受け付けるmodeは`preview`と`purge`のみです。

purgeは現在のepisode → assertion closureを最大10,000件の派生assertionまで
同期処理します。稼働DBのepisode/assertion本文と根拠引用を先に削除し、
同じtransactionでscopeに束縛された時刻付きmarkerを`memory_ops.object_tombstone`へ
挿入します。objectのSELECT RLSがそのanchorを非公開にし、
`memory.object`へのsoft-deleteの`deleted_at`更新や特権削除helperは使いません。
barrier/receiptをcommitしてから応答します。workerへのenqueueはしません。
source削除の影響を受ける複数sourceのassertionは再構築せず削除します。
receiptの`active_store_purged`は完全消去ではありません。

opaque objectとそのobject tombstone、audit/receipt metadata、tenant-keyed HMACの
source/idempotency tombstoneはtenantの存続期間中残します。
purgeを「完了」させようとして手動削除したり`dedup_secret`を変更したりしないでください。
再送保護が機能しなくなる可能性があります。削除済みsource identityやmemory結果の
完全一致再送は`404`、payload衝突は引き続き`409`です。
自動的なtenant完全消去手順はありません。

## Backup、restore、release証拠

削除receiptは`backup_status: "operator_managed"`、
`backup_retention_deadline: null`を返します。SQL行削除は物理媒体の消去、
WAL/replica/backupからの除去、配信済みcontextの消去を証明しません。
backup保持期限の強制、自動restore replay、HA/PITR workflow、
検証済みRPO/RTOは実装されていません。

必要なrestore境界は次のとおりですが、**自動手順としては未実装です**。

1. 復元DBを隔離し、API・agent・userからアクセスさせません。
2. backupと一緒に巻き戻されていないsourceから、最新の削除台帳とACL失効記録を
   取得します。古いbackup内の記録だけでは不十分です。
3. 公開を検討する前に、該当epochを含む削除と権限を適用します。
   tenantの重複抑止状態を維持してください。
4. 復元状態で削除対象がなく、未認可アクセスができないことを検証します。
   最新記録を取得できない、または安全に適用できない場合は隔離を続けます。
   これらを自動実行する対応済みコマンドはありません。

restore訓練は使い捨て環境に限定してください。このchecklistやhealth probeを根拠に
DRや本番complianceを主張してはいけません。実行したcommand、環境、architecture、
結果を、計画上の未測定目標とは分けて記録してください。
Apple Containerとnative両architectureのDocker CI検証は
[貢献方法](../../CONTRIBUTING-jp.md)を参照してください。
