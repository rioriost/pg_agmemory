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
v0.0.7 opt-in日本語lexical FTS milestoneにはschema 7が必要です。
Apple Containerとnative Docker amd64/arm64の各環境で**144テスト**（既存warning 2件）、
Ruff、strict mypy（source 12ファイル）、non-root productionの日本語tokenizer、
API HTTP、実worker CLI `--once` idle実行という全3種のsmokeが合格しました。
最終SHA、CI log、所要時間は[検証証拠](../STATUS-jp.md#検証証拠)を参照してください。

既存package-feed registryを維持した最終lockを使ってください。
全36 packageのversion、依存metadata、artifact hashはテスト済みPyPI解決lockと
byte単位で同一です。v6との差分はJanome 0.5.0の追加とprojectのv0.0.7へのversion更新だけで、
無関係なupgradeやregistry移行はありません。native CIはこのretained-registry lockから
buildしました。

| 設定 | 利用者 | 用途 |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | 管理CLIのみ | migration、offline全tenant lexical rebuild、private tenant/principal/scopeの作成 |
| `PGAG_DATABASE_URL` | API/worker runtime | `pgag_runtime`に所属する専用の制限付きlogin |
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
   `src/pg_agmemory/storage/003_checkpoints.sql`、
   `src/pg_agmemory/storage/004_tool_effects.sql`、
   `src/pg_agmemory/storage/005_relational_graph.sql`、
   `src/pg_agmemory/storage/006_durable_jobs.sql`に続き、追加的な
   `src/pg_agmemory/storage/007_japanese_fts.sql`をpackage resourceとして
   同梱します。計画の例示DDLで代用したり、生成済みfileを想定したりしないでください。
   管理者はsuperuser、または必要な所有権/DDL・role/schema作成・`btree_gist`
   extension導入権限を持つ適格な`BYPASSRLS` roleである必要があります。
   bypassだけではDDL権限を与えません。migration 007のPython rebuildを含むbackfillの
   `row_security = off`は、行がRLSでfilterされる場合にfail-closedにする設定で、
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
   owner role経由の所属も対象です。またschema ledgerが厳密に`[1, 2, 3, 4, 5, 6, 7]`であることを
   要求し、欠落・旧版・将来版・不完全な履歴は拒否します。
   workerもこのrole/schema検査を使いますが、APIのJWT設定は不要です。

admin URL、署名用秘密鍵、token、tenant HMAC secretをsource管理、issue、
logへ残さず、不要なものをruntime環境へ渡さないでください。
runtime DB資格情報をagentへ渡して任意SQL入口にしてはいけません。
固定queryと信頼されたidentity contextも認可境界の一部です。

<a id="v003の保守migration"></a>
<a id="v004の保守migration"></a>
<a id="v005の保守migration"></a>
<a id="v006の保守migration"></a>

## v0.0.7の保守migration

**旧版/新版API/workerのrolling共存やdowngradeは非対応です。**
upgradeの予行は使い捨てtest DBに限定してください。
migrationテストの合格は、本番upgradeや災害復旧の適格性を示すものではありません。
次の保守protocolに従ってください。

1. replica、worker継続loop、自動再起動を含め、**旧版・新版の全APIとworkerを停止/drain**します。
   migration advisory lockはAPI trafficやworker claim/publication停止の代わりにはなりません。
2. backupを取得し、旧application/schema版を記録します。
   restore隔離の要件に従い、最新削除台帳とACL失効を独立して保全してください。
   唯一のmigration前backupを上書きしてはいけません。
3. 特権migration管理者と新imageで`pg-agmemory migrate`を実行します。
   migration lock下で未適用script、Python lexical backfill、ledger更新を
   一つのtransactionで適用します。
   lock timeoutは5秒で、無期限に待たず中断します。traffic停止を維持して競合を調査します。
4. migration 007は`memory.episode_lexical`と`memory.assertion_lexical`を追加し、
   forced RLS、同一scope canonical外部key、cascade削除を適用します。
   runtime権限は`SELECT`/`INSERT`だけで、`UPDATE`や直接`DELETE`は付与しません。
   canonical parent purgeは子tableのDELETE権限なしでcascadeします。
   Python backfillは全保持episodeと全assertion revisionを対象にし、
   tombstoneをskipしてschema 7記録前に完了します。
   backfill完了後の失敗でもprojection DDL/dataとschema ledgerをまとめてrollbackし、
   schema 6からのupgradeは6のままです。
   migration 001〜006は変更せず、旧DBへ未適用版を順に適用します。
   graph/job/assertion/effect履歴、checkpoint checksum、canonical ID/system time、
   source-event/idempotency receipt、`Remember` JSON/HMAC順を維持してください。
   固定したJanome 0.5.0依存と同梱辞書を使用します。
   v4台帳の厳格な再開規則は維持し、未追跡hintはplannedでも再開を阻止します。
5. 厳密な履歴`[1, 2, 3, 4, 5, 6, 7]`を確認してから、制限付きruntime資格情報と
   意図した固定worker subjectで**対応するv7 API/workerだけを起動**します。
   traffic再開前にcapabilities/schema、既定/opt-in recallとprojection coverage、
   過去revision選択、認可、purge、原子的publication、互換性を検査してください。
   health応答だけではこれらを検証できません。migration/rebuildの時間・resource使用量は
   適格性未確認です。
6. 失敗時はAPI/worker停止を維持します。変更済みschemaへ旧imageを接続したり、
   downgradeがあると想定したりしないでください。
   backup restoreも最新削除/ACL状態の再適用・検証まで隔離します。

**旧v0.0.1 APIには新しいschema互換性guardがありません。**
不整合なschemaでも起動し得るため、運用側で停止を維持する必要があります。
新runtimeによるschema不一致の拒否は、旧processを保護しません。

## Lexical profileとreindexの運用

recallの既定は`search_profile: "simple-v1"`です。
日本語scriptのsurface/wakati分割は`"ja-janome-0.5.0-v1"`を明示指定し、
応答は選択profileを返します。Janome 0.5.0はJanome追加語付きの同梱
mecab-ipadic-2.7.0-20070801を使用します。ASCII識別子/英語はsegmenterをそのまま通過し、
PostgreSQLが引き続きlexical処理を行います。Unicode/全半角正規化、原形化/stemming、
同義語照合、分割/recall品質の適格性確認はありません。漢字処理は中国語文字にも及びますが、
中国語recallを適格としません。vector/hybrid検索、外部model/provider、
fileベースのmemory indexではなく、context予算は別の`utf8-bytes-v1`契約を維持します。

対応container build profileを使ってください。test/runtime buildは辞書moduleを含む
**静的Janome package bytecodeだけ**を逐次事前compileします。
package codeでありmemory index/cacheやcompile済みuser入力ではありません。
Janomeは日本語script連続部分がある場合だけlazy importし、英語だけの操作ではloadしません。
`max_cached_word_len=0`でmatcher入力prefix cacheを無効化し、同梱辞書resource cacheだけを
保持します。辞書codeを事前compileしていないcold host installationでは初期化peakが
大幅に増える可能性があります。fresh Linux subprocessのguardは初期化peak RSS
**256 MiB未満**と、英語だけの操作でJanomeをimportしないことを要求します。
このtest閾値を配置時のmemory上限に使ってはいけません。request処理、並行性、
migration/rebuild、resource sizingの適格性は未確認です。
限定的な診断観測値は[ADR 0007](../adr/0007-japanese-fts-jp.md#runtime初期化の境界)を参照してください。

日本語profileでは、現在認可済み・要求scope内・時間条件内のprojectionが欠けると、
query関連性やjob状態とは無関係に`coverage.lexical_incomplete: true`と
`coverage.retrieval_complete: false`を返します。利用可能な一致結果は返せ、
空queryはflagがあってもcanonical itemをbrowseします。候補なしでprojection欠落があれば
`empty_reason: "index_incomplete"`、予算で候補を除外した場合は`"budget_exhausted"`です。
simple profileへの黙ったfallbackや修復workerはありません。
simple検索の選択は日本語projectionを修復しません。
破損辞書logは入力textを含まない`japanese_dictionary_error`へ除去処理します。
Janomeの`SystemExit`はtokenizer-unavailableへ変換し、`index_incomplete`ではなく
APIの`503 dependency_unavailable`となります。
workerは入力をechoせず既存の上限付き依存障害retryを使います。

reindexはworkerのprincipal/scope単位でなく、**選択DBの全tenant**を再構築します。
`--subject`は範囲を狭めるoptionではなく明示拒否し、`--once`もworker専用として拒否します。
既存schema 7 DBをcanonical dataから再構築する手順:

1. 自動再起動を含む**全API/workerを停止/drain**し、migration同様にbackupします。
   offline保守であり、稼働中の管理APIではありません。
2. 対応するv7 imageと**`PGAG_ADMIN_DATABASE_URL`**を使用し、forced RLS bypassと
   必要なtable権限を持つ管理者で次を実行します。

   ```bash
   pg-agmemory reindex-lexical
   ```

3. commandは厳密な履歴`[1, 2, 3, 4, 5, 6, 7]`を要求し、5秒のlock timeoutで
   migration advisory lockを取得して、両projection tableを一つのtransactionで置換します。
   tombstoneを除く全保持episode/assertion revisionを分割し、headだけに限定しません。
   canonical ID、system time、根拠、receipt、同期request hashは変えません。
   JSON出力は`profile: "ja-janome-0.5.0-v1"`と整数の
   `episodes`/`assertion_revisions`件数だけで、source本文/tokenは含みません。
4. 一部置換後の失敗でも旧schema 7のprojectionを維持します。
   traffic停止を維持し、schema、権限、lock競合を調査します。
   index修復のためruntime bypassを付与したり、canonical本文、timestamp、receiptを
   編集したりしてはいけません。
5. 対応するv7 API/workerだけを再起動します。traffic再開前に許可済みtest dataで
   profile/coverageと認可された現在/過去recallを確認してください。
   正確な`known_at`境界にはhost/VMのwall-clock値でなく、
   serverが返すassertionの`recorded_at`を使います。
   件数だけでは関連性、世界知識の完全性、性能を認定できず、自動復旧/DR保証もありません。

同梱辞書はcode依存で、projectionの保存先はPostgreSQLだけです。
image再配布時はJanome Apache-2.0 licenseと同梱IPADIC copyright/license noticeを保持します。
[依存ライセンス](../../README-jp.md#依存ライセンス)と
[ADR 0007](../adr/0007-japanese-fts-jp.md)を参照してください。
M0/M1/M2/M3とMVP/本番の受入は未完了です。

## Durable jobとworkerの運用

workerはcaller指定の構造化assertionを公開し、自動synthesis/自然言語抽出/LLM/provider、
embedding、compaction、tool-effect実行は行いません。subjectを事前provisionしてから、
制限付き`PGAG_DATABASE_URL`資格情報と信頼する配置identityだけで実行します。

```bash
pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT --once
```

subjectは1〜256文字で、設定issuer内のprovision済みprincipalと一致させます。
caller指定のHTTP偽装ではありません。agentへruntime DB資格情報やworker subject選択権限を
与えてはいけません。workerにJWT署名/公開鍵やadmin URLは不要です。
superuser、table owner/owner所属、`BYPASSRLS`資格情報は使わないでください。
起動時はAPIのrole/schema検査を共有し、そのprincipalが現在write可能なjobだけをclaimします。
同一scope readerはGETできますが、他principalのjobを実行/retryできません。

`--once`省略時は継続実行し、idle pollは1秒、一時的DB loop障害後は2秒待ちます。
`--once`は実行時刻に達したjobを最大一つ処理し、JSON `outcome`
（`idle`、`succeeded`、`pending`、`failed`、`lease_lost`）と該当opaque ID/結果参照を返します。
queue全体やretry cycleの完了は待たず、他commandでの`--once`は拒否します。
起動/回復不能errorは失敗であり、成功したidle結果ではありません。
固定principal profileであり、global schedulerや公平性/cost poolの適格な実装ではありません。
worker stdout/logはopaqueな過去outcome参照を含み、現在のread許可やlive snapshotではありません。
以前のCLI outcomeを信用せず、現在のアクセス権/削除状態を検査するjob GET/explainで読んでください。

1. HTTP keyと`{kind: "structured_remember", memory: <元のRemember body>}`を
   `POST /v1/jobs`へ送ります。現在のscope read/write権限、明示intent、
   正確な同一scope episode引用を使います。`202`は固定recipe `structured-remember-v1`の
   commit済みjob参照であり、公開完了ではありません。
   明示retry用に元requestを安全に保管し、投入前に機密情報を除去してください。
2. 不明なenqueue結果は同じ正規化request/keyで再送します。
   canonical intent/recipeは同一principal/scope内でkeyをまたいでも重複抑止し、
   根拠順の正規化はjob identityだけに適用します。
   別principalや異なるsource identityの意味的重複は抑止しません。
   scopeのpending/running上限100件を守り、別identityで回避してはいけません。
3. job GETでstate、試行回数（最大5）、scheduling/lease時刻、安全なerror code、
   不変episode入力参照、retry parent、resultを確認します。
   GETはrequest JSON、owner principal、lease tokenを返しません。
   terminal成功/失敗ではrequestを消去し、成功resultは後の訂正後もrevision 1です。
   explainには結果の正確なassertion revisionを使います。
   assertionのrecorded/system timeはjob enqueueでなくworker publication時に始まります。
   jobの`created_at`をassertionの採用時刻として使ってはいけません。
4. 自動retry可能な失敗は`2^attempt + [0,1)`秒のbackoff/jitterを使います。
   `invalid_input`は即時失敗で、期限切れの5回目claimは`attempt_limit`となり6回目はありません。
   payloadをlogへ出さず、安全な`dependency_unavailable`/`stale_context` codeで診断します。
   `lease_lost`は古い準備bodyからの再publication許可ではありません。
5. 所有するterminal failed jobには、元の`EnqueueJob` body全体とkeyを
   `/v1/jobs/{job_id}/retry`へPOSTします。現在の根拠/権限とHMAC intentを再検査し、
   intent変更は`409 job_intent_conflict`、failed以外のparentは`409 job_retry_conflict`、
   非ownerは`404`です。同じparentの再試行はkeyをまたいでも一つのchildを再利用します。
   childが失敗したらそのIDで別の明示5試行cycleを開始し、terminal行/recipeをSQLでresetしません。

claimは`FOR UPDATE SKIP LOCKED`下でcommitしてからtransaction外でpayloadを準備します。
既定leaseは新tokenと現在epoch付きの30秒で、内部1〜300秒上限は制御されたテスト用、
運用設定ではありません。publicationは現在のidentity/権限、入力/正確なbody、
lease/token/期限、epochを再検査し、output/provenance/job成功/auditを同時commitします。
最後の更新時に期限切れならoutputをrollbackします。内部heartbeatはlease/epochを検査しますが、
決定的processorにbackground heartbeat taskや外部呼出しは不要です。
公開claim/publish/heartbeat endpointはありません。
at-least-once試行でjob当たり最大一つのcommit済み結果を作り、外部exactly-once実行ではありません。

`observe`は自動enqueueせず、同期`remember`とlegacy JSON/HMACは変更しません。
recallの`jobs_pending`は要求scopeの読取り可能なpending/running jobを対象にし、
`synthesis_pending`と`graph_used`はfalseのままです。
jobはrecall/explain itemやcheckpoint/effect参照kindではありません。
[契約](../STATUS-jp.md#durable-job)と[ADR 0006](../adr/0006-durable-jobs-jp.md)を参照してください。
M0/M1/M2/M3、MVP/本番、性能、品質、DRの受入は未完了です。

## Entityとgraphの運用

1. `POST /v1/entities`で許可済みの同一scope episode引用、allowlist内のtype、
   長さ制限付きcanonical label、`explicit_intent: true`から明示entityを作成します。
   返されたrevision 1 UUIDを保存してください。label/typeはcaller申告であり、
   信頼できる指示や検証済みfactではありません。metadata/根拠にはentity GETを使い、
   recall/explainは使いません。alias/merge/名前解決やlabel訂正endpointはなく、
   新HTTP keyは同名の別identityを作成し得ます。不明な作成結果は元のkey/bodyで再送します。
2. relationは`POST /v1/relations`だけで作成し、同一scopeのsource/target entity UUIDと
   episode根拠を指定します。返却IDはcanonical assertionであり、第二のrelation objectでは
   ありません。一致するfree-text `remember`もuntypedのままです。
   allowlist内の全predicateは複数値を許すreportedな申告です。
3. `POST /v1/relations/{memory_id}/revisions`で正確なexpected revision、target UUID、
   置換根拠/valid bound、明示intent、reasonを指定して訂正します。source/predicateは固定で、
   bound省略は無限端となりinterval全体を置換します。汎用assertion訂正は
   `409 relation_revision_required`です。過去の正確なrevisionをexplainで確認し、
   省略時は最新ではなく1です。typed link/valueをSQLで編集してはいけません。
4. 認証付き読取り専用`POST /v1/graph/expand`へ明示した重複のないscope、entity seed、
   predicate、purposeを送ります。`Idempotency-Key`は不要です。
   上限は32 scope、16 seed、5 predicate、1〜2 hop、1〜100 pathです。
   実効`as_of`/`known_at`、coverage、epochを確認してください。prefixも数え、
   cycleでもpath内でnodeは反復しません。incoming/bothは探索方向であり逆向きtruthの推論ではありません。
   非公開seedは返さず、可視の孤立seedはpathなしでも返り得ます。空/上限付き結果は不在の証明ではありません。
5. `409 graph_invalidated`は失効した読取り、DB `503`は障害として扱い、空graphと
   みなしてはいけません。canonical PostgreSQL joinなのでAGE/SQL/PGQ導入、
   graph projection再構築、lag/watermark操作は不要です。
   `backend: "sql"`、`projection_watermark: null`を返し、
   動的graph SQL/Cypher/label入力はありません。recallはFTSで`graph_used: false`のままです。
6. graph由来を含むコピーした全entity revision 1または正確なassertion revisionを
   checkpoint/effectの`memory_refs`へ宣言します。entity GET/relation explainが根拠を返し、
   展開nodeはquoteを省略します。pathやcanonical labelをactionの実行許可として扱ってはいけません。

[契約](../STATUS-jp.md#entityとsql-graph-oracle)と
[ADR 0005](../adr/0005-relational-graph-jp.md)を参照してください。
上限付きの正しさの基準であり、graph有用性/性能の証拠、M0/M1/M3全体、MVP、
本番/DR適格性ではありません。

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

purgeはepisode/entity/assertion履歴、job依存/retry lineage、宣言済みcheckpoint/effect参照、完全なparent lineageを介した
全子孫/fork checkpointを辿ります。上限は要求rootに加えて依存物全体で10,000件です。
旧assertion revisionだけのsourceでもassertion全履歴と影響する全checkpoint stateを削除します。
episode根拠からentity、さらにそのentityをsourceまたは**過去のどのtargetとしてでも**
使うrelation全履歴へ伝播します。entityの直接purgeも同じrelation依存を辿り、
checkpoint/effectへの直接entity参照も対象です。relationが消えただけで他の生存entityは消しません。
entityはepisodeだけに依存するため、意味的relation cycleはprovenance cycleではありません。
job closureは同じ10,000依存上限内でepisode入力 → job、result assertion → job、
parent job → retry子孫を辿ります。
**jobやfailed parentのretry chainを削除しても、公開済みの独立assertionやsource episodeは
消えません。** factを消すにはoutput/sourceを明示purgeします。
output assertionは自身の直接episode provenanceを維持し、どのrevision-sourceの削除でも
assertion全体と依存jobを消します。job → resultの依存cycleはありません。
headが影響を受けるbranchは永続失効するため、同じIDの再開やlineage除去による回避を
試みないでください。どのeffectでもpurgeすると、古い空snapshotを含む
**同一scope/runの全checkpoint payload**を削除し、`effects_invalidated`を永続設定します。
新plan、dispatch、checkpoint、再開を禁止しますが、同じrunというだけで
独立effectまでpurgeせず、生存記録の照合は可能です。
job request/入力行をassertion/episode行とtombstoneより先に同じtenant barrierで削除し、
実行中publisherを拒否します。purge済みjobのGET/再送は`404`となり、
保持job identityがexact jobの復活を防ぎます。
canonical episode/assertion削除は同じbarrierで対応する全lexical revisionへcascadeし、
tombstone commit前に消去します。これらは派生payloadであり別memory/provenance vertexでは
ありません。rebuildはtombstoneをskipし、purge済みsource本文を復元できません。
payload、entity根拠、typed link、引用、参照、reason/receipt参照を含むeffect eventをactive tableから先にSQL削除し、
同じtransactionでscopeに束縛された時刻付きmarkerを`memory_ops.object_tombstone`へ
挿入します。objectのSELECT RLSがそのanchorを非公開にし、
`memory.object`へのsoft-deleteの`deleted_at`更新や特権削除helperは使いません。
barrier/receiptをcommitしてから応答します。
tenant session lockがclosure、run/branch失効、read drainを覆います。
workerへのenqueueや影響本文の再構築は行いません。
receiptの`active_store_purged`は完全消去ではありません。

opaque job identity、operation registry/run flag、run/branch metadata、objectとtombstone、
audit/receipt metadata、tenant-keyed HMACの
source/idempotency tombstoneはtenantの存続期間中残します。
purgeを「完了」させようとして手動削除したり`dedup_secret`を変更したりしないでください。
再送保護が機能しなくなる可能性があります。削除済みsource identityやmemory結果の
完全一致再送は`404`、payload衝突は引き続き`409`です。
自動的なtenant完全消去手順はありません。
過去参照/再送からpurge済みentity label、relation value、receiptは復元できません。
下記のbackup制限は変わりません。

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

1. 復元DBを隔離し、API・worker・agent・userからアクセスさせません。
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

`scripts/test-containers.sh`はproduction API HTTP smokeに加え、
non-root production image内の実worker CLI smokeを実行します。
使い捨てprincipalをprovisionし、runtime専用資格情報で`worker --subject ... --once`を
実行して`{"outcome":"idle"}`を検査し、`Production worker smoke passed`をlogに出します。
non-root runtime image内で`東京都` → `東京` / `都`の分割も検査し、
成功時に`Production Japanese tokenizer smoke passed`を出力します。
同梱tokenizerの初期化/分割の検査であり、end-to-end recallや品質の評価ではありません。
CI step名は`Test containers and smoke-test production API and worker`です。
idle-worker検査はpublicationテストや本番/DR適格性確認ではありません。
最終v7実装
[678ba24](https://github.com/rioriost/pgag_memory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)は、
local Apple Containerと完全一致SHAのnative Docker
[CI run 35173023029](https://github.com/rioriost/pgag_memory/actions/runs/35173023029)で合格し、
各環境で全3種のproduction smokeも合格しました。
詳細は[STATUS](../STATUS-jp.md#検証証拠)に記録していますが、本番/DR受入の主張ではありません。
