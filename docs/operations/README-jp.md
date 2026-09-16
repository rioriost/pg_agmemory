# 初期sliceの運用

[English](README.md) | [プロジェクトREADME](../../README-jp.md) | [現在の契約](../STATUS-jp.md)

**本番runbookや検証済み災害復旧手順ではありません。**
この初期releaseでは、許可済み・除去処理済みの使い捨てtest dataを使用してください。
purge訓練、schema reset、restore実験を含む破壊的操作は、
使い捨てtest DBだけを対象とし、業務DBや実userの履歴には実行しないでください。

## 初期設定とrole分離

PostgreSQL 18と、repositoryの`Dockerfile`から構築したimageを使用します。
CLI名は`pg-agmemory`、import package名は`pg_agmemory`です。
ローカルcheckoutは`pg_agmemory`、GitHubは引き続き`rioriost/pgag_memory`です。
v0.0.4 tool-effect milestoneにはschema 4が必要です。
Apple Containerとnative Docker amd64/arm64で、それぞれ73テスト、
Ruff、strict mypy、production HTTP health smokeが合格しました。
[検証証拠](../STATUS-jp.md#検証証拠)を参照してください。

| 設定 | 利用者 | 用途 |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | 管理CLIのみ | migrationとprivate tenant/principal/scopeの作成 |
| `PGAG_DATABASE_URL` | API runtime | `pgag_runtime`に所属する専用の制限付きlogin |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | 2048 bit以上の静的PEM RSA検証公開鍵。署名用秘密鍵は渡さない |
| `PGAG_JWT_ISSUER` | API runtime | 信頼するissuerの完全一致値 |
| `PGAG_JWT_AUDIENCE` | API runtime | 本サービスのaudienceの完全一致値 |

1. admin URLが、意図した空の使い捨てMemory DBを指すことを確認します。
   アプリimageから`pg-agmemory migrate`を実行します。migrationはtransactionalで、
   `public.pgag_schema_migration`に版を記録します。migration loopは対応する
   連続した履歴のみを受け付け、適用済み版は再実行時にskipします。
   lock取得timeoutは5秒です。既存DBの更新には下記の保守手順が必要です。
2. 変更しない`src/pg_agmemory/storage/001_initial.sql`、
   `src/pg_agmemory/storage/002_assertion_revisions.sql`、
   `src/pg_agmemory/storage/003_checkpoints.sql`に続き、追加的な
   `src/pg_agmemory/storage/004_tool_effects.sql`をpackage resourceとして
   同梱します。計画の例示DDLで代用したり、生成済みfileを想定したりしないでください。
   管理者はsuperuser、または必要な所有権/DDL・role/schema作成・`btree_gist`
   extension導入権限を持つ適格な`BYPASSRLS` roleである必要があります。
   bypassだけではDDL権限を与えません。migration 002の`row_security = off`は、
   backfillがRLSでfilterされる場合にfail-closedにする設定で、
   それ自体がforced RLSをbypassするものではありません。
3. 別の管理者で`NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime`の専用runtime
   loginを作り、passwordを安全に設定します。table所有権もmigration owner roleへの
   所属も付与しません。このloginにはrole/database作成権限を与えないでください。
4. admin URLで`pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT`を実行します。
   private tenant、principal、scopeを作成してIDを返します。
   provisionはendpointでもmembership更新コマンドでもありません。
   設定した信頼するissuerが発行したsubjectを使ってください。
5. runtime設定のみを渡して`pg-agmemory serve`を実行します。
   起動時にsuperuser、RLS bypass、アプリtable ownerとしての接続を拒否します。
   owner role経由の所属も対象です。またschema ledgerが厳密に`[1, 2, 3, 4]`であることを
   要求し、欠落・旧版・将来版・不完全な履歴は拒否します。

admin URL、署名用秘密鍵、token、tenant HMAC secretをsource管理、issue、
logへ残さず、不要なものをruntime環境へ渡さないでください。
runtime DB資格情報をagentへ渡して任意SQL入口にしてはいけません。
固定queryと信頼されたidentity contextも認可境界の一部です。

<a id="v003の保守migration"></a>

## v0.0.4の保守migration

**旧版/新版APIのrolling共存やdowngradeは非対応です。**
upgradeの予行は使い捨てtest DBに限定してください。
migrationテストの合格は、本番upgradeや災害復旧の適格性を示すものではありません。
次の保守protocolに従ってください。

1. replicaと自動再起動を含め、**旧版・新版の全API traffic/processを停止・drain**します。
   migration advisory lockはAPI traffic停止の代わりにはなりません。
2. backupを取得し、旧application/schema版を記録します。
   restore隔離の要件に従い、最新削除台帳とACL失効を独立して保全してください。
   唯一のmigration前backupを上書きしてはいけません。
3. 特権migration管理者と新imageで`pg-agmemory migrate`を実行します。
   migration lock下で未適用scriptとledger更新を一つのtransactionで適用します。
   lock timeoutは5秒で、無期限に待たず中断します。traffic停止を維持して競合を調査します。
4. migration 004はeffect payload/履歴/参照、opaque operation registry、
   run失効flagを追加します。migration 001〜003は変更せず、旧DBへ未適用版を順に適用します。
   保存checkpoint checksumは変わりませんが、live ledgerの再開規則は意図的に厳格化し、
   未追跡hintはplannedでも再開を阻止します。
   assertion履歴、source-event/idempotency記録、timestampを維持してください。
5. ledgerの版が厳密に`[1, 2, 3, 4]`であることを確認してから、
   制限付きruntime資格情報で**新APIだけを起動**します。
   capabilities/schemaを確認し、traffic再開前にmilestoneのmigration・effect FSM/CAS・
   restore fencing・legacy hint・run purgeの検査を実行してください。
   health応答だけではこれらを検証できません。
6. 失敗時はtraffic停止を維持します。変更済みschemaへ旧imageを接続したり、
   downgradeがあると想定したりしないでください。
   backup restoreも最新削除/ACL状態の再適用・検証まで隔離します。

**旧v0.0.1 APIには新しいschema互換性guardがありません。**
不整合なschemaでも起動し得るため、運用側で停止を維持する必要があります。
新runtimeによるschema不一致の拒否は、旧processを保護しません。

## Checkpointの運用

1. 機密情報を除去したschema 1のstateだけを保存します。
   コピーした全memory sourceを正確なrevisionとともに`memory_refs`へ宣言してください。
   未宣言コピーをsemantic scannerが発見することはありません。
2. 意図したscope/run/branchに`expected_head`を明示して作成し、
   nullは新branchだけに使います。headを黙ってresetせず、
   head/watermark/harnessの`409`競合を解決して返されたcheckpoint IDを保存します。
3. recall/explainでなくcheckpoint GETで読み込みます。
   checksum/参照検査の失敗は失効として扱い、検査の回避や保存payloadの編集をしないでください。
4. harness ID/versionとstate schemaを完全一致させ、未使用のtarget branchへrestoreします。
   元branchは変えません。保存済みassertion参照は正確な過去revisionを維持し、
   最新revisionへの変更や外部事実の自動更新は行いません。
   harnessへstateを渡す前に`tool_effects`、`untracked_effects`、
   `requires_reconciliation`、`resume_allowed`を確認してください。
   snapshot時点だけでなくrunの全生存effectを含みます。未追跡planned hintも阻止対象で、
   unknown hintとtracked planned effectの矛盾には不確実性/receiptの照合が必要です。
   restoreはfork作成前にledgerのdispatchedを原子的にunknownにします。
   CASは古いledger writerを拒否しますが、進行中の外部呼出しは止めません。
   `automatic_reexecution`は常にfalseで、provider receipt照会やコード実行は行いません。
5. 結果が不明なwriteは同じkey/payloadで再送します。
   idempotency記録にはstateでなく元の結果参照のみを保存します。
   現在の認可/checksum検査を適用し、purge済みcheckpointは`404`です。

保存時epochや`resume_allowed: true`は承認や外部副作用receiptではありません。
typed pending effectはsnapshot hintであり、durable ledgerは別に管理します。
作成bodyは1 MiB、他endpointは256 KiBまでです。
[契約](../STATUS-jp.md#checkpointの契約)と
[ADR 0004](../adr/0004-tool-effects-jp.md)を参照してください。本番/DR適格性は主張しません。

## Tool-effectの運用

1. effect planの前に、意図したscope内のrunをcheckpointで初期化します。
   hostの正規化actionの安定した小文字64桁hex hashを計算し、
   operation UUID、tool名、hash、全memory依存の正確な参照をPOSTします。
   サービスは引数や生hashを保存せず、外部呼出しの内容を検証しません。
   tool名、reason、receipt参照、stateの機密情報を除去してください。
2. 返された`memory_id`を保存し、GETで安定した`external_idempotency_key`、
   `run_invalidated`を含む最新記録を取得します。identityはtenant/scope/run/operation内です。
   新run/operation IDは同じ実世界actionを重複抑止しません。
   runの存続期間中の上限はterminalを含め100 effectです。
3. hostは権限/承認を検査し、外部呼出し**前**にCASで`dispatched`を永続記録します。
   providerが対応する場合は安定した外部keyを使ってください。
   実行の協調はhostの責任であり、旧dispatch応答の再送は新たな送信/盲目的再送の許可ではありません。
4. 結果が不明なら`unknown`を記録し、サービス外でproviderと照合します。
   `planned → unknown`はlegacy/protocol外の試行を記録できますが、実行許可ではありません。
   `unknown → dispatched`は禁止です。terminalの`confirmed`/`failed`は、
   長さ制限付きreceipt参照と`provider_receipt`または`operator_review`を要求します。
   どちらもcaller申告で、検証済みではありません。terminalは不変で自動再試行を許可しません。
5. 不明なledger writeは同じkey/bodyで再送します。intent変更は衝突し、
   同じintentを新keyで送っても元のrevision 1参照を返します。
   plan/dispatch応答は過去revisionの参照であり、現在状態のsnapshotや実行許可ではありません。
   現在の認可の下で、生存effectの再送はrun封鎖後も成功し得ますが、新たなdispatchは許可しません。
   旧応答を信用せずGETで現在状態を読んでください。照合を回避するためhintを消したり、
   不確実性を逃れるためIDを使い直したりしてはいけません。
   未追跡hintはhostが明示的に解決する必要があります。
6. effect purgeで封鎖されたrunでは新intent作成、dispatch、checkpoint、再開を行いません。
   独立した生存effectはGETと許可済み照合遷移が可能で、
   `unknown → confirmed/failed`も有効です。opaque operation registry、run flag、
   tombstoneを維持してください。

これは台帳であり、worker、harness adapter、provider照会client、承認サービス、
外部exactly-once機構ではありません。[契約](../STATUS-jp.md#tool-effect-ledger)と
[ADR 0004](../adr/0004-tool-effects-jp.md)を参照してください。

## Revisionの運用

訂正は全置換revisionの追加であり、subject、predicate、scopeは不変です。
結果が不明な訂正を再送するときは、`Idempotency-Key`、対象ID、bodyを維持します。
再送成功時は新しいrevisionが存在していても元のrevision参照を返し、
headの読取りにはなりません。`409 revision_conflict`では、
暗黙上書きせず古いexpected headを解決してください。上限はassertionあたり全1000 revisionです。

`explain`のrevision省略は、最新ではなく引き続きrevision 1を意味します。
mutation/recall結果を調べる場合は、その結果の正確なrevisionを指定してください。
過去読取りにも現在のACLとtombstoneを適用します。
未来日付の置換では、新valid intervalの開始前に旧値を維持しません。
[revision契約](../STATUS-jp.md#assertion-revisionの契約)と
[ADR 0002](../adr/0002-assertion-revisions-jp.md)を参照してください。

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

purgeはepisode/assertion履歴、宣言済みcheckpoint/effect参照、完全なparent lineageを介した
全子孫/fork checkpointを辿ります。上限は要求rootに加えて依存物全体で10,000件です。
旧assertion revisionだけのsourceでもassertion全履歴と影響する全checkpoint stateを削除します。
headが影響を受けるbranchは永続失効するため、同じIDの再開やlineage除去による回避を
試みないでください。どのeffectでもpurgeすると、古い空snapshotを含む
**同一scope/runの全checkpoint payload**を削除し、`effects_invalidated`を永続設定します。
新plan、dispatch、checkpoint、再開を禁止しますが、同じrunというだけで
独立effectまでpurgeせず、生存記録の照合は可能です。
payload、引用、参照、reason/receipt参照を含むeffect eventをactive tableから先にSQL削除し、
同じtransactionでscopeに束縛された時刻付きmarkerを`memory_ops.object_tombstone`へ
挿入します。objectのSELECT RLSがそのanchorを非公開にし、
`memory.object`へのsoft-deleteの`deleted_at`更新や特権削除helperは使いません。
barrier/receiptをcommitしてから応答します。
tenant session lockがclosure、run/branch失効、read drainを覆います。
workerへのenqueueや影響本文の再構築は行いません。
receiptの`active_store_purged`は完全消去ではありません。

opaque operation registry/run flag、run/branch metadata、objectとtombstone、
audit/receipt metadata、tenant-keyed HMACの
source/idempotency tombstoneはtenantの存続期間中残します。
purgeを「完了」させようとして手動削除したり`dedup_secret`を変更したりしないでください。
再送保護が機能しなくなる可能性があります。削除済みsource identityやmemory結果の
完全一致再送は`404`、payload衝突は引き続き`409`です。
自動的なtenant完全消去手順はありません。

## Backup、restore、release証拠

checkpoint restoreはMemory DB内のtyped stateのコピーであり、DB backupからの復旧、
別のworking snapshot compactionシステム、災害復旧ではありません。
DB backupはeffect状態も巻き戻し得ます。外部実行を停止したままprovider結果を別途照合してください。
ledgerが安全な復旧を自動化するものではありません。

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
