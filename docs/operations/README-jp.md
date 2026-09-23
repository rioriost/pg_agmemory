# 初期sliceの運用

[English](README.md) | [プロジェクトREADME](../../README-jp.md) | [現在の契約](../STATUS-jp.md)

**本番runbookや検証済み災害復旧手順ではありません。**
この初期releaseでは、許可済み・除去処理済みの使い捨てtest dataを使用してください。
purge訓練、schema reset、restore実験を含む破壊的操作は、
使い捨てtest DBだけを対象とし、業務DBや実userの履歴には実行しないでください。

## M4 durable source-access coordinator

開発identityは**0.3.0.dev1 / API v1 / schema 21**、
stageは`m4-integration-pilot`です。v0.3 releaseやM4全体の認定ではありません。
`pg-agmemory source-access`は、**信頼する上流coordinator**用の管理者専用local commandです。
認証済みのsource判断を受け取り、公開webhook、署名検証器、source照会client、
snapshot metadataからの権限推測は提供しません。
管理DSNとtenant/scope/principal対応はsource message、reader、plannerから分離します。

migration 021は不変のsource binding、単調な通知cursor、追記型の適用記録を
非公開`memory_ops` tableに保存します。runtime roleは読み書きできません。
bindingの管理対象は一つのscope内の一つのreader principalです。
他memberの一括失効や、同じ外部datasetを使う全principalの発見ではありません。
capture/削除は別の限定maintenance identityに残します。
そのidentityと他のgrantも監査し、専用scopeという配置境界を省略しないでください。

```bash
# 信頼する管理設定。source本文から選ばせない。
pg-agmemory source-access bind \
  --tenant-id "$TENANT_ID" --scope-id "$SOURCE_SCOPE_ID" --principal-id "$READER_ID" \
  --expected-access-epoch "$ACCESS_EPOCH" \
  --source-system "$SOURCE_SYSTEM" --dataset-id "$DATASET_ID" \
  --source-subject "$SOURCE_SUBJECT"
pg-agmemory source-access get \
  --tenant-id "$TENANT_ID" --scope-id "$SOURCE_SCOPE_ID" --principal-id "$READER_ID"
pg-agmemory source-access apply \
  --tenant-id "$TENANT_ID" --scope-id "$SOURCE_SCOPE_ID" --principal-id "$READER_ID" \
  --expected-access-epoch "$ACCESS_EPOCH" --notice-file "$VERIFIED_NOTICE_FILE"
```

bindは拒否状態から始め、既存read-only grantを原子的に除去します。
write/delete/admin権限を持つprincipalの転用は拒否します。
同じidentityの再bindはcursorをresetせず、新しい有効grantを取り消しません。
identityの変更も拒否します。bind後の対象には通常`scope-access set`を許可せず、
`get`と緊急`revoke`だけを残します。無関係なscopeのgrantは変更しません。

上限付きJSON通知のformatは`pgag-source-access-notice-v1`です。
`source`（`source_system`、`dataset_id`、`source_subject`）、
正整数`sequence`、`decision`（`allow`/`deny`）、`reason`、
任意`acl_version`/`verified_at`/`valid_until`を含みます。
allowにはreason `authorized`と認可の3 fieldすべてが必要です。
denyは`revoked`/`unavailable`/`deleted`でlease時刻を持ちません。
source identityはbindingと一致させます。
source connectorはbinding単位のsequenceを永続管理し、
配送retryのためだけに新sequenceを生成してはいけません。

allowにはDB時刻で
`verified_at <= now < valid_until <= verified_at + 300 seconds`を要求します。
grantは**readだけ**で、期限をそのまま適用します。
無効なlease時刻は既存grantを残すのでなく、拒否を永続記録してfail closedにします。
sequenceの欠落も拒否として記録してcursorを進め、
未受信通知から許可を推測しません。
未削除bindingは、その後の連続した新しい認可確認で明示再開できます。
source削除はgapや後続denyをまたいでもterminalです。
読取は止めますが、payloadの物理purge完了とは扱いません。
source-memory対応を保持して既存Native削除workflowを実行してください。

最新通知の正規化payloadが完全一致する再送はno-opです。
grant付与、lease延長、epoch更新、audit重複は行わず、
満了や緊急manual revokeを含む現行effective permissionsを返します。
古いsequence、同sequenceの異なるpayload、identity不一致、
新eventの古いaccess-epoch CASは拒否します。
membership、通常access audit、source state/auditはtenant barrier内で同一transactionにし、
途中失敗で認可変更の半分だけを公開しません。
commit結果不明時は状態を確認し、同じ検証済み通知だけを再送します。
未配送と決めつけたり、新cursorで無条件retryしたりしてはいけません。

実際のmembership変更は既存のtenant access epochを使います。
通常ACL編集と同様、epochに依存するprocessing、working snapshot、AGE投影を失効させ得ますが、
このcommandが黙ってrefresh/再実行することはありません。
metadataだけの通知ではaccess epochを増やさない場合があります。

`source_authorization_verified:false`は意図的です。
pg_agmemoryが確認するのはlocal契約・順序・lease上限で、
source systemのcredentialや署名ではありません。
lease満了でcoordinator停止時のstalenessを制限しますが、未配送の上流失効が即時反映されるわけではありません。
実際の上流認証/通知経路とtarget mappingを接続・認定するまで、
shared business dataを自動長期保存しないでください。

### Registered dataset readers

`pg-agmemory source-dataset`は、tenant/source-system/datasetが完全一致する
登録済みreaderを発見し、緊急失効する**管理者専用**commandです。
schema21の既存binding/grantを利用し、migration、背景fanout、
source認証器、Native reader用endpointは追加しません。

```bash
pg-agmemory source-dataset get \
  --tenant-id "$TENANT_ID" --source-system "$SOURCE_SYSTEM" --dataset-id "$DATASET_ID"
# 全targetを確認し、応答のaccess_epochとtarget_digestを使用します。
pg-agmemory source-dataset revoke \
  --tenant-id "$TENANT_ID" --source-system "$SOURCE_SYSTEM" --dataset-id "$DATASET_ID" \
  --expected-access-epoch "$ACCESS_EPOCH" --expected-target-digest "$TARGET_DIGEST"
```

`PGAG_ADMIN_DATABASE_URL`と信頼する設定を使い、promptや未認証source通知に
targetを選ばせません。bindingと同じ空白正規化・大文字小文字を区別する一致規則です。
同datasetに登録した全subject/scopeをscope/principal順で返し、上限は**100 bindings**です。
0件は`source_dataset_not_bound`、101件以上は`source_dataset_target_limit`とし、
切捨てや部分的な変更を行いません。未登録readerや全snapshotの発見は保証しません。

target digestはtenant、dataset、順序付きの全binding identityを表し、
現在時刻やpermission snapshotではありません。拒否状態の新bindingは
access epochを変えずに追加できるため、変更前に**digestとepochの両方**を照合します。
不一致時は再確認が必要で、自動retryはしません。digestは整合性照合用であり、
署名や認可credentialではありません。
membership削除、targetごとのaccess監査、epoch更新は同一transactionと
tenant response barrier内で処理します。削除したmembershipごとにepochを一つ進め、
元から存在しない場合は進めません。期限切れでも設定が残るgrantは削除します。
後続targetの失敗やepoch枯渇でも、先行targetを含む全変更をrollbackします。

対象は登録済みreader membershipのみです。maintenance identity、未登録member、
別dataset/tenantは変更しないため、それらのgrantも別途監査してください。
sourceのdecision/cursor/event履歴は**変更しません**。
保存済みdecisionが`allow`でも現行effective permissionsは空になり得ます。
応答は`coverage:"registered_readers_only"`、`source_notices_changed:false`、
`physical_purge:false`、`durable_dataset_block:false`、
`source_authorization_verified:false`を明示します。
直前通知の完全一致再送では再付与しませんが、次の連続した有効allowでは再付与できます。
有効leaseを持つ配送中の通知も含むため、拒否を持続させる必要があれば
上流coordinatorを停止してください。本commandはその停止やterminalな削除通知の生成を行わず、
新規bindingも禁止しません。

commit結果不明時は現行membershipと既存access監査を照合してから、
次の明示操作を判断します。batch receipt/retry protocolや分散transactionではありません。
sourceに対応する全memoryの発見、datasetのterminal削除/purge fanout、
認証済み上流通知transportは別途必要で、shared business dataの自動長期保存は無効です。

### Schema 21 upgradeと復元境界

migration前にAPI/worker/coordinatorを停止・drainし、旧source/component identityと保護backupを保持します。
新管理CLIでledger 1–21全体を適用し、新しい同一versionのcomponentを使います。
sourceだけ戻してもDBは降格しません。SQLは引き続き既定です。
schema19/20のgraph世代履歴は読めますがstaleになり、
schema20のAGE receipt/artifactはschema21でのservingを許可しません。
旧投影を明示disableし、現行schema21 artifactをbuild/record/publishしてからAGEを再選択します。
公開済みM3 tag、旧migration、過去の認定artifactは変更しません。

processing/recovery fingerprintにsource-access両tableを含めます。
このincrementは完全なstate/historyを復元時の一致必須canonical contentとして扱い、
**backup後に変更されたsource authorityを`recovery-apply`で取り込みません**。
cursor/binding/適用記録が異なる旧backupは、認可sequenceを後退させず拒否します。
不変状態は既存の隔離復元で往復できますが、自動起動や上流lease更新の承認にはなりません。
現行source認可と削除義務を別途照合するまでservingを停止してください。
汎用source authority履歴replayは別の作業です。

schema21 graph資源recipeは新しい開発identityで、新たな認定を主張しません。
固定M3 v2 recipeは`examples/graph-resource-profile-m3-v2.json`に別保存し、
旧計測は旧commit/schema/profileに属したままで、新buildへ読み替えません。

## M4 external-source snapshot pilot

任意Python SDK adapter `pg_agmemory.external_source`は、
**明示的な過去の観測**を保存し、現行business値とは扱いません。
既存`[sdk]` extraだけを使い、新endpoint、migration、source provider、
credential store、background collector、自動model呼出しは追加しません。
source接続はcaller所有で、postgresem connectorの実装ではありません。

信頼する`(source_system, dataset_id, source_subject)`ごとに専用scopeを使います。
`SourceBinding`は起動設定であり、promptやsource結果から選びません。
`ExternalSnapshot`にはsemantic revision、query ID、観測時刻、ACL version、
除去処理済みsnapshot本文とSHA-256 digestを記録します。本文の空白も有意です。
version付き`pgag-external-snapshot-v1` envelopeを通常episodeのcanonical JSONとして保存し、
既存のprovenance・削除・論理backup契約を適用します。
evidence quoteは復元した非escape本文ではなく、escapeを含む保存JSONに完全一致させます。
新しいserver側metadata indexではありません。
digestはpayload不整合の検出であり、**署名・sourceの権威・grant・上流ACL確認の証明ではありません**。
callerは秘密を含まないopaque identifierを渡し、
capture前に同意確認とsecret/PII除去を行ってください。

全snapshotは`requires_refresh:true`、authorityは`external_observation`です。
Memory読取leaseが有効でも、現行値の回答には現行source principalで再照会します。
`read_snapshot`はNative認可後に宣言envelope/binding/digestを確認しますが、
上流の権限を独立確認しません。
Native episode explanationにはscope fieldがなく、
envelopeの自己申告scopeは実際の配置scopeの証明になりません。
token自体を専用scopeへ制限してください。
通常Native recallでは、履歴JSONを非信頼episode本文として返します。

### Source認可leaseと失効

snapshot-only deployment profileは、client側checkだけでなく、
**serverが強制する期限付きscope membership**を再利用します。
信頼するcoordinatorがsource認可を検証し、Memory principalのread-only grantに対応付けます。
capture/削除には別の限定maintenance identityを使い、
そのtokenや管理DSNをserving reader/plannerへ渡しません。
専用scope内の全grantを監査し、readerの無期限・過大grantを除去してください。
無関係なtask memoryをsource専用scopeに混在させません。

**未bind**のsnapshot-only対象では、既存の手動手順を使います。

```bash
# 特権coordinator専用。ID/epoch/expiryは信頼する設定から渡す。
pg-agmemory scope-access get \
  --tenant-id "$TENANT_ID" --scope-id "$SOURCE_SCOPE_ID" --principal-id "$READER_ID"
pg-agmemory scope-access set \
  --tenant-id "$TENANT_ID" --scope-id "$SOURCE_SCOPE_ID" --principal-id "$READER_ID" \
  --expected-access-epoch "$ACCESS_EPOCH" --permissions read \
  --expires-at "$SOURCE_LEASE_END"
```

schema21で管理するbindingには上記`source-access apply`を使います。
通常`scope-access set`は意図的に拒否します。

`SOURCE_LEASE_END`は検証済みsource認可の期限とoperatorの短いfreshness上限を超えないようにします。
adapterは全体TTLの強制、source ACL versionの認証、grant更新、source通知受信を行いません。
coordinatorはcache/未確認ACLからleaseを延長してはいけません。
source拒否・失効・認可を確認できない場合は、現行`--expected-access-epoch`で
`scope-access revoke`を明示実行します。
CAS競合は再取得・source再確認を要求し、無条件retryしません。
coordinator自体が停止した場合は既存lease満了で読取を停止します。
これはstalenessの上限を設ける方式で、**上流との即時同期ではありません**。
直接Native SDK/MCP/hookと派生memoryも同じRLS grantを使います。
失効後、既に渡したcontextはhost側で破棄してください。

source削除には、保存成功receiptの`memory_id`をmaintenance identityのNative `forget`へ渡します。
削除receiptで完了を確認するまで、正本の通知とtarget mappingを保持します。
不明targetの推測、receipt/closure上限の迂回、timeout時の完了扱いをしてはいけません。
assertion/checkpointは元episodeをprovenance参照して削除を伝播させます。
参照なしのtext copyには関連付けによる保護はありません。
scope revokeは全memoryを隠しますが、purgeしたとは扱いません。
復元後は現行source認可の再確認と必要な削除の照合前にsource scopeを再開しないでください。

### Capture結果とretry

`ExternalSourceMemory.capture`は成功済み・caller確認済みのsource観測を受け取り、
business queryを実行・認定しません。
`source_query_status:"succeeded"`と、
`memory_capture_status:"stored"` / `"failed"` / `"outcome_unknown"`を分離します。
Native保存receiptがあるのは`stored`だけです。
その他はSDKの安全なerror metadataを含み、Memory成功でもbusiness query失敗でもありません。
不正なlocal契約はnetwork I/O前に例外を返し、無関係な例外は握り潰しません。
自動retry/extraction/embeddingは行いません。

明示retry/照合のために正確なsnapshot、consent reference、idempotency keyを保持します。
source event IDはsource system・dataset・subject・query IDに固定し、
同じquery IDでsnapshotを変えれば競合します。
本当に新しい観測には新query IDを使いますが、結果不明captureの迂回に使ってはいけません。
digest/ACL versionはpurge済みsourceの再公開許可ではありません。
capture停止時もsource照会結果は返せますが、Memory結果を未完/失敗として明示します。
通常task memoryはsource systemの稼働に依存しません。

[`examples/external_source_memory.py`](../../examples/external_source_memory.py)は
明示同意した合成fixtureとNative SDKだけを使用します。
file記載の信頼する`PGAG_*` binding、永続保持したquery/time/ACL/key入力、
`PGAG_SYNTHETIC_CONSENT=yes`を設定し、
`uv run --frozen --extra sdk python examples/external_source_memory.py`で実行します。
未保存の結果は明示表示して非zero終了し、retryや本文/token出力は行いません。
信頼する上流認可/通知coordinatorの接続・認定までは、
shared business dataの自動長期保存を**無効**に保ちます。
provider固有同期、dataset全体のtarget発見、即時失効保証、
M4全体の認定をこの限定snapshot adapterが提供するわけではありません。

## M4 LangGraph safe-boundary pilot

開発branchに任意の**LangGraph 1.2.11**連携を追加します。
serviceの新releaseやM4完了ではありません。
このcheckoutで`uv sync --frozen --extra langgraph`を実行します。
通常service/runtimeとcore SDKにはLangGraphを導入せず、
Native API v1/schema 20、SQL/AGEの選択も変更しません。

`pg_agmemory.langgraph.LangGraphMemory`はopen済み`AsyncMemoryClient`を、
信頼する一つのscope/run/branchへ固定します。
Native API originと委譲bearer tokenは起動時に渡し、
prompt、recall本文、graph stateから取得しません。
`observe`は許可・除去処理済みdataの明示captureで、会話の自動保存ではありません。
`recall`は固定scopeだけを指定し、Nativeの予算・認可・coverage・非信頼context契約を保ちます。

`build_turn_graph(memory, planner)`は実LangGraphの
`recall -> plan -> checkpoint`をcompileします。
callerが`CheckpointState`と`RecallResult`を受ける**副作用のないasync planner**を渡し、
pilotはmodelを選ばず、外部toolを実行せず、承認を付与しません。
入力は正確なexpected head、event watermark、memory refs、
callerが永続保持するidempotency keyを含みます。
harnessは`pg-agmemory-langgraph-pilot` version `1`、state schema `1`です。
graphはrecallで返された全item/revisionを宣言済みmemory refsへ保守的に追加し、
完全一致pairを重複排除します。合計がNative上限100参照を超えたらplanner前に拒否します。
modelによる関連性判断を挟まず、recall入力の削除依存を保ちます。
初期stateやその他planner入力の出典は引き続きcallerが宣言します。
保存するのは型付きNative checkpoint stateだけで、
任意のLangGraph channel/message、callback、credential、serializer、
schedulerのpending writeは保存しません。

`restore`はNativeの復元変更前にscope/run/harness互換を確認し、
再認可された完全なenvelopeを明示確認用に返します。
未使用のtarget branchと固定restore keyを使ってください。
graphの実行、既存bindingの変更、`next_actions`の実行、承認の解除は行いません。
`resume_allowed`、`requires_reconciliation`、`untracked_effects`、
現行`tool_effects`を確認し、unknown/dispatched effectは既存台帳で照合してから
新しい作業を開始します。復元先は別途bindingを作成し、返されたcheckpoint IDをheadにします。
`BaseCheckpointSaver`、`Command(resume=...)`、任意graph再開、
外部実行のexactly-once、前nodeのreplayには対応しません。

障害をそのまま伝播し、自動retryや成功風fallbackを行いません。
captureとcheckpointは別transactionで、後段の失敗は先のobserveを取り消しません。
変更結果不明時は正確なpayload/keyを保持しNative SDKで照合します。
graph全体やplanner/toolを無条件に再実行してはいけません。
後続操作で権限/source freshnessを再確認し、
失効後は既に渡されたcontextをhost側で破棄します。
serviceはcallerへ渡したPython objectを回収できません。

[`examples/langgraph_memory.py`](../../examples/langgraph_memory.py)は
合成data・決定的plannerのみでmodelを呼ばない例です。
信頼する`PGAG_API_URL`、`PGAG_API_TOKEN`、`PGAG_SCOPE_ID`、
新規`PGAG_RUN_ID`/`PGAG_BRANCH_ID`、明示同意`PGAG_SYNTHETIC_CONSENT=yes`を渡し、
`uv run --frozen --extra langgraph python examples/langgraph_memory.py`で実行します。
tokenには事前provision済みの使い捨てscopeに対するread/write権限が必要です。
例は返された参照unionを次turnへ引き継ぎ、
型付き`ainvoke(..., version="v2").value`の結果を使います。
外部LangSmith tracingを無効化し、host applicationもframeworkへmemoryを渡す前に
tracing/callback設定を点検する必要があります。
依存関係にはtracing clientが含まれますが、pilotはtelemetry送信を承認しません。
汎用scheduler、自動trigger、外部source connector、postgresem/rerank、
M4全体の認定は別の作業です。

## M3 graph MVP deployment

M3 source releaseの契約は**service 0.2.0 / Native API v1 / schema 20**、
capability stageは`m3-graph-mvp`です。
native Linux amd64/arm64でPython3.12.14、PostgreSQL18.6、pgvector0.8.6を使い、
AGE選択時には別固定したAGE72707aa sourceを要求します。
管理された配置向けのgraph MVPであり、本番、HA/PITR、RPO/RTO、
任意履歴の復元認定ではありません。

認定source tagからserviceをbuildし、hosted service、PyPI package、
registry imageの公開済みを前提にしないでください。
SDK/MCP/hook/workerも同じversionに揃え、API v1/schema20が不変でも混在version互換とは解釈しません。
既定SQLはAGE不要です。AGE選択には修正image、現行の検証済みartifact、
下記の明示publishが必要で、旧rc0 imageや固定hop実験で代用してはいけません。

upgrade前にAPI/workerをdrainし、自動再起動を止め、
正確なsource/component identityに対応する保護backupを取得してください。
管理者DSNで`pg-agmemory migrate`し、MAX(version)だけでなく**ledger 1–20全体**を確認します。
0.1.3/schema20からのmigration追加はなく、M2/schema18からは019/020が必要です。
schema19世代receiptの認証/可読性は維持しますがstaleになり、
schema20 artifactを再構築して、旧fileの改名で代用しません。
管理credentialをruntime containerへ渡さず、source切戻しだけではDBを降格できないことに注意してください。

AGE導入は既存未修正extensionのin-place upgrade保証ではありません。
起動のためにbuild stampや特権診断を捏造せず、対応する新規修正extensionと、
宣言範囲のcanonical-only復元/再構築を使ってください。
復元はAPI/worker/model callを自動起動せず、
旧backupのenabled registryも再開の承認にはなりません。

graph資源recipeは0.2.0用identityに更新しますが、保存した0.1.3 recipeと負荷/閾値は同一です。
元reportのcommit/profile digestは変更せず、version昇格を理由に改名しません。
公開済みM2 tag `v0.1.0`と認定済み昇格前checkpoint `37f9c21`を切戻し参照として残し、
それぞれに対応するDB/artifact状態を要求します。

## Patched AGE enabled profile

現行契約は**service 0.2.0 / API v1 / schema 20**、stageは`m3-graph-mvp`です。
既定は`PGAG_GRAPH_BACKEND=sql`で、`age`は明示的・上限付きprofileです。
黙ったfallbackや一般的な本番/HA認定ではありません。
保存した旧`Dockerfile.age`を有効化用imageに使ってはいけません。

```bash
docker build -f Dockerfile.age-patched -t pg-agmemory-age:72707aa .
docker build --target runtime -t pg-agmemory:0.2.0 .
```

DB imageはPostgreSQL18.6/pgvector0.8.6、公開upstream
`fa109ef1ddb1c7a945a1c340195d650000e49713`と、ローカル
`72707aab7ce982bf13cad3d102bd869dab07d64b`までの完全一致変更を固定します。
archive・patch・適用後Git treeをbuild時に照合し、隣接repoの未追跡fileはコピーしません。
全hashは`patches/age/source.json`に記録します。
既定CMDはAGEをpreloadするため、`shared_preload_libraries=age`を落とすCMDへ置換しないでください。
永続dataの保護とnetwork制限を行い、管理者DSNをAPI/workerへ渡さないでください。

API/worker/自動再起動を停止・drainし、対応backupを保持してcomponentを揃えmigrationします。
020はtenantごとのserving registryだけを追加し、AGEは導入しません。
修正DB上で管理者が別途`CREATE EXTENSION age`を実行します。
通常どおり非owner/NOSUPERUSER/NOBYPASSRLSのruntime loginを準備し、
起動のためにgraph所有権、`pg_read_all_settings`、任意SQL/DDL権限を追加しないでください。

起動/readinessは修正build stampと、任意extensionが持つ
`ag_catalog.pgag_age_preloaded()`を要求します。
引数なし・固定`search_path=pg_catalog`のSECURITY DEFINERで、
固定server設定に対するbooleanだけを返し、memory tableを読まず、設定名を受け取らず、
graph dataへの認可も行いません。runtimeはsignature/extension所属owner/固定設定も確認します。
別のimmutable build stampは信頼する固定image上での識別子であり、
悪意あるDB管理者に対する暗号的attestationではありません。

まず`graph-generation get/begin`、`graph-artifact export/check`、
正確なfile digestによる`graph-generation record`で**現行schema 20のrecorded head**を準備します。
profile/receipt作成だけではgraphをserveしません。
台帳と世代の両CAS revisionで検証済みfileを公開します
（registry未作成なら0、初回recorded headの世代revisionは通常2）。

```bash
pg-agmemory age-projection get --tenant-id "$TENANT_ID"
pg-agmemory age-projection publish --tenant-id "$TENANT_ID" \
  --generation-id "$GENERATION_ID" --expected-generation-revision 2 \
  --expected-revision 0 --file /private/path/graph.json

# 公開済み投影を使うAPI配置だけで設定する。
export PGAG_GRAPH_BACKEND=age
pg-agmemory serve
```

publishは`pgag_age_<hyphenなし世代UUID>`を作成/ANALYZEし、
base/child両labelへcanonical read policyとFORCE RLSを適用、registryを原子的にenabledにします。
runtimeへはSELECT/USAGEだけを付与します。置換時は旧registryに対応するgraphだけを
同じtransactionで削除し、失敗ならDDL/registryともrollbackします。
現行headの再publishも同じgraphを原子的に再構築します。手作業でschemaを作成/改名しないでください。
応答喪失/成否不明時は`get`で照合し、mutationを盲目的に再送しません。

Native `/v1/graph/expand`は`backend:"age"`と世代UUIDの`projection_watermark`を返します。
時計や定数時間の変更counterではありません。実native VLEが1/2-hop探索し、
canonical joinでID/revision/方向、現行認可、evidence、時刻区間、cycle除外、決定論的予算順を守ります。
上限はseed 16件・2 hops・100 pathsのまま、artifact/投影は10,000 nodes・40,000 edge revisionsです。
callerの任意Cypherは受け付けません。

各要求でimage、label保護、取得時ACL/deletion epoch、要求で可視なcanonical topologyの完全性を確認します。
対象の追加/revisionには新世代を要求し、古い投影で新たに可視になったpathを黙って欠落させません。
epochが変わらないACL期限もstatement時点で評価します。
この上限付き照合は高コストになり得るため、定数時間の鮮度や全量S graphのlatencyは主張しません。
statement timeoutは5秒で、E2E応答時間の保証ではありません。

欠落/無効は`graph_projection_unavailable`（409）、stale topology/epochは
`graph_projection_stale`（409）、runtime build不一致は`graph_backend_unqualified`（503）です。
自動SQL fallback・再構築・可視性緩和・model呼出しは行いません。

```bash
pg-agmemory age-projection disable --tenant-id "$TENANT_ID" --expected-revision 1
# 明示切戻し先。APIをこの設定で再起動して反映する。
export PGAG_GRAPH_BACKEND=sql
```

disableはregistry receiptとgraphを保持します。SQL選択はAGEに依存せず、dataを削除しません。
環境変数を変えるだけでは実行中APIに反映されないため、drain・再起動してください。

schema 19の世代receiptは認証・不変性・可読性を維持し、upgrade後はstaleになります。
旧pendingはabandonでき、旧recorded headから新schema 20子世代を作れます。
旧graph fileをschema20へ改名して代用せず、新backupと現行復旧artifactを取得してください。

**復元範囲は意図的に限定しています。** 運用snapshotは24 tableです。
明示optionなしの`recovery-apply`は、従来どおりenabled registryを書込み前に拒否します。
backup前にdisableし後続証跡でもregistryを変更しない従来手順に加え、
v0.1.3ではenabled baselineからの**原子的な復元＋無効化**を選べます。

```bash
# API/worker/自動再起動を停止し、隔離clusterへ復元する。
# 対応する削除suffixを先にreplayし、復元先の正確なCASを取得する。
pg-agmemory processing-recovery export --tenant-id "$TENANT_ID" \
  --file /private/path/restored-expected.json
pg-agmemory recovery-apply apply --tenant-id "$TENANT_ID" \
  --expected /private/path/restored-expected.json --bundle /private/path/latest-bundle.json \
  --isolated --disable-age-projection
```

`latest-bundle.json`は独立して保全した最新正本からexportしたもので、
古い復元先から捏造してはいけません。復旧鍵、canonical本文、job identity、
世代履歴、投影receipt全体は引き続き署名付き参照との一致が必要です。
optionは欠落/新世代を取り込まず、変更されたregistryも許容しません。
隔離は運用上の前提です。復元前からapplicationのnetwork接続と自動再起動を止め、
`--isolated`自体をnetwork遮断機能と見なさないでください。

単一transaction内で最新の運用/canonical fingerprintの**完全一致**を確認した後、
通常のrevision+1 guardで一致するenabled registryを無効化します。
世代、artifact/input digest、物理graphを保持し、投影fingerprintだけが変わったことを照合します。
どこかで失敗すれば全体をrollbackし、AGE query・graph DDL・model呼出し・起動は行いません。

再構築可能なAGE catalog/投影dataを意図的に除外する**canonical-only backup**では、
復元後に同じ信頼済み修正imageからAGEを新規導入し、graph OID/catalog counterを手修復しません。
投影registryとcanonical世代履歴はbackupに残します。物理graphが存在しなくても、
上記操作で復元先のregistryを無効化できます。enabled receiptは、
物理graphの存在やserving適格性を証明するものではありません。

専用drillはPostgreSQL18の`pg_dump --format=custom`に、
`--exclude-extension=age --exclude-schema=ag_catalog --exclude-schema='pgag_age_*'`
を明示指定し、archive manifestにAGE extension/catalog/投影entryがないことを確認します。
この限定memory専用profile以外のAGE graphは黙って除外せず拒否します。
新しい復元先は修正imageの`CREATE EXTENSION age`と、
runtime診断前の`pgag_runtime`への`ag_catalog` schema USAGEを必要とします。
table読取りpolicy/grantはpublisherが再構築し、所有権/BYPASSRLSで代用しません。
明示backup modeであり、全AGE復元失敗後の自動retryではありません。
全AGE catalogの往復復元は引き続き未認定です。

応答は意図的に`processing_state_matches:false`、
`operational_state_restored:true`、`age_projection_disabled:true`、
`projection_rebuild_required:true`と差分`memory_ops.age_projection`一件を返します。
検証済み復元に続く明示的なlocal無効化であり、enabled参照とのbytes一致とは主張しません。
registryなし/既にdisabledなら追加遷移はなく、一致を返します。
応答喪失/成否不明時は新たな状態exportとreceipt照合を行い、
古いCASの盲目的な再送や不一致の迂回はしないでください。

隔離を維持し、現行canonicalの削除/ACL/会計とAGEのdisabled拒否を確認します。
その後だけ現行artifactをbuild/check/recordし、新registry revisionで明示publishします。
source変更時は新しい子世代が必要で、旧artifactの改名では代用しません。
旧物理graphを意図的に除外した場合は`age-projection publish`に`--rebuild-missing`を付けます。
既存の**disabled** registry、物理schemaとAGE graph catalog entryの両方の完全な不在、
現行の両CAS revision、検証済み現行artifactが必要です。
catalog/schemaの片方だけが残る状態は修復せず拒否し、graphが存在するなら通常publishを使います。
成功時は`rebuilt_missing_projection:true`を返し、別graphを代替として削除しません。
別途承認した起動だけがclient受付を再開できます。
欠落/新本文、変更された世代履歴、任意復元履歴、HA/PITR、自動再起動は未対応です。
export済みartifactの保持/削除はoperatorの責務です。

再現可能な隔離認定command:

```bash
bash scripts/test-age-patched-containers.sh container
bash scripts/test-age-enabled-containers.sh container
bash scripts/test-age-recovery-containers.sh container
```

前者は旧probeの期待値をそのまま修正sourceへ適用します。
後者は独立した新test/HTTP cluster、非root製品image、実管理publisher、native HTTP選択を使います。
Docker/native CIでも同じhelperをamd64/arm64で実行し、
log/image/digestを旧rc0失敗や固定hop不採用実験と区別します。
三つ目はsourceを削除してから実canonical-only復元を行い、復旧鍵/canonical identityを照合し、
enabled receiptの無効化とHTTP拒否を確認してから明示再構築します。
現行/履歴のnative-SQL一致とpurge/ACL拒否も確認し、
dump/credential/非公開fixture fileは削除、本文なしreportにsource identityと除外条件を残します。

enabled/recovery helperは`PGAG_AGE_RUNTIME_IMAGE`で、同一sourceから構築した
信頼する非root runtimeを明示指定できます。未指定時は従来どおり自ら構築します。
両helperは選択imageのidentityを記録し、caller提供imageをcleanupで削除しません。
local container builderがrepositoryのallowlist付きbuild contextを転送できない場合も、
別途構築した同一runtimeで確認できます。service versionが同じという理由だけで
古いimageへ置き換えてはいけません。

## Native graph resource profile

`examples/graph-resource-profile.json`は独立したgraph専用profileです。
可視node 12/64件のchain/fanout/multiseedと、同じ投影に残す非公開scope dataを使います。
DB割当は6 vCPU/24 GiB、applicationは2 vCPU/8 GiBで、別記録のVM overhead CPUを区別し、
六つの層を各3 warmup組＋30測定組、SQL先行/AGE先行を交互に実行します。
既存2-hop/100-path上限を維持し、各層の目標は**p95が1,500 ms未満**です。
接続・tenant barrier・commitを含む保守的な全操作時間で判定し、
query単体timerへの置換はしません。

M2 Sの混合負荷、cold cache、同時実行保証、
artifact上限10,000 nodes/40,000 revisions全域の費用認定ではありません。
時間を採用する前に、両backendが独立した期待canonical ID/revision、
順序付きpath/coverage、権限・履歴controlに一致する必要があります。
warmupや失敗/不足sampleを測定成功へ数えず、構築/公開、投影容量、
statement数、可視topology全体の鮮度照合費用は分けて報告します。

benchmarkはRLS無効化、canonical照合省略、費用を隠すplanner設定変更、
model呼出しを行いません。SQLは明示配置選択であり、AGE測定中の黙ったfallbackにはしません。
現行の要求ごとの鮮度確認は上限付き証明で、**定数時間の変更追跡ではありません**。
後続実装変更には別の意味契約/費用証跡が必要です。
閾値や必要sample数に届かないreportは失敗とし、
事後にfixtureを小さくして認定済みへ読み替えません。

cleanなcommit済みcheckoutから実行し、既定runnerは不変の`git archive` snapshotをbuildします。
出力先は新規・非公開・project相対directoryで、通常はignore対象`.review-artifacts`配下です。

```bash
bash scripts/measure-graph-resources.sh .review-artifacts/graph-resources-v1
# Dockerではmodeに空文字を明示する。
bash scripts/measure-graph-resources.sh .review-artifacts/graph-resources-docker '' docker
# 短い診断は意図的に認定対象外とする。
bash scripts/measure-graph-resources.sh .review-artifacts/graph-preflight --preflight container
```

`--development`は作業fileで全recipeを実行しますが非exact・未認定のままです。
`--preflight`はsample数を短縮し、やはり未認定とします。canonical profile digestは
0.2.0の`M3-bounded-native-graph-v2` identityでは
`37b0379d66341047d2def85621feff9f949cc5a42e3826d3746f51c175e0db0d`で、
profile fieldの改変やruntime service/schema不一致を拒否します。
raw sampleには安全なerror code、期待結果digest、計測境界を残し、
生SQL、原文、credentialはreportへ出しません。
warmup除外、順序、両backend件数、全六層をraw記録から再確認し、
cache済みsummary flagを信用しません。診断・不足・中断runは認定しません。
export/publish時間は製品管理CLI起動費用を含み、純粋なDB timerではありません。

現行の上限付き実装は、意図的に要求ごとの完全照合を維持します。
このprofileの成功だけを根拠にmutation counterへ置換したり、
物理投影照合を省いたり、定数時間の鮮度と主張したりしません。
graph拡大、seed増加、同時writer、全量S dataとの同居には、
費用認定範囲を広げる前に別途宣言した測定が必要です。

保存した0.1.3/v1 profileのdigestは
`c89ed11ad1fc31038b2e168a56309c27d01521a627f2fed2e7b4ac6852fb2212`です。
v2はprofile名とservice versionだけを変え、負荷/gateは変更しません。
v1再現には対応する旧source checkoutを使い、v2 reportの改名で代用しません。
releaseではcommit済み0.2.0入力で新profileを再実行します。

## Graph generation metadata (schema 19)

この節はschema 19での導入履歴です。現行schema 20のversion、artifact再生成、
AGE選択は上記enabled profileに従ってください。

開発版componentは**service 0.1.1 / API v1 / schema 19**、
stageは`m3-graph-generation-metadata`です。公開M2 tagはv0.1.0/schema 18のままです。
API/worker/自動再起動をdrain・停止し、対応する保護backupを取得した後、管理者で
`pg-agmemory migrate`を実行しledger 1–19全体を確認します。
client/adapter/workerを揃え、管理者復旧鍵を保持して新backupを取得し、
schema 19の削除/処理/適用artifactを再生成します。schema 18の旧artifactは改名せず拒否します。
sourceだけのrollbackではDBはdowngradeできません。

新しい管理者CLIはstdinからclosed JSON一件（UTF-8で最大32 KiB）を受け取り、
非公開に設定した`PGAG_ADMIN_DATABASE_URL`を使用します。AGEのloadやmodel呼出しはしません。
runtime roleには新table二つのread/write権限を与えず、Native resourceやbackend選択も追加しません。
以下のplaceholderを実UUID/digestへ置き換え、revision/input digestは`get`の値を使ってください。

```bash
printf '%s\n' '{"operation":"get","tenant_id":"TENANT_UUID"}' | pg-agmemory graph-generation

printf '%s\n' '{"operation":"begin","tenant_id":"TENANT_UUID","expected_revision":0,"generation_id":"GENERATION_UUID","expected_input_digest":"INPUT_DIGEST_64_HEX","profile_digest":"PROFILE_DIGEST_64_HEX"}' | pg-agmemory graph-generation

printf '%s\n' '{"operation":"record","tenant_id":"TENANT_UUID","expected_revision":1,"generation_id":"GENERATION_UUID","expected_input_digest":"INPUT_DIGEST_64_HEX","artifact_digest":"ARTIFACT_DIGEST_64_HEX"}' | pg-agmemory graph-generation
```

初回`get`はrow/head/buildなしのrevision 0です。`begin`は指定UUIDを観測済みrevisionと
source digestの両方で予約し、tenantごとの未完buildを一つに限定します。
`record`は同じpending世代/inputと現行revisionを要求し、operator指定のartifact digestを記録、
recorded headを進めてpendingを解消します。旧recordは不変です。
graph/ACL/deletion状態が変われば完了を拒否し、pending buildは明示abandon用に残します。
profile/artifact digestは宣言の対応付けであり、外部bytes・model品質・graph利用可能性の検証ではありません。

pendingを破棄するには`operation:"abandon"`、`tenant_id`、`expected_revision`、
`generation_id`、1–256文字の`reason`だけを渡し、digest引数は付けません。
sourceを再走査しないため増大してcapture上限を超えても実行でき、input/source-matchは
現在値を推測せずnullとします。reasonに原文やcredentialを含めないでください。
receiptは不変の運用metadataで、汎用記憶の保存先ではありません。
自動retry・期限切れ・履歴pruning・producer job・graph起動は追加しません。
応答喪失/成否不明時は`get`で照合し、黙って別世代を作らないでください。

全commandがcommit/outputまでtenant barrierを保持します。入力captureはgraph関連の
固定canonical 7選択、現行access/deletion epoch、管理復旧鍵を使い、
原文でなく件数/hashを記録します。共通上限はtableごと1,000,000 rows、
serialized入力合計256 MiB、30秒と、通常のstatement timeoutです。
管理用の走査であり、hot read用watermark/cacheではありません。
初期契約ではtenantごとの世代履歴を10,000件まで保持できます。
将来のgraph readでも現行認可・statement時点の期限を確認し、
`source_matches`を権限と見なしてはいけません。

**全応答は`artifact_verified:false`、`serving_enabled:false`です。**
`recorded`はreceiptの存在であってgraph構築・検証・client向け公開・起動ではありません。
高コストの固定hopと未認定native VLEは無効を維持し、世代metadataはbackend非依存とすることで、
将来認定したstrategyでもinput/receipt契約を置換せず利用できます。

復旧では新tableを運用snapshot 23 tableと不変content照合へ追加しますが、replacement rowには含めません。
v6隔離drillは変更のない非空世代履歴を復元し、新しいpurgeと最新ACL/会計を反映、
旧headをstale・非servingのまま保持します。
backup後に世代履歴が変わっていれば、現在の適用経路では書込み前に拒否します。
十分新しいbackupを保持し、不変receiptの書換えや未検証graph artifactの取込みで回避しないでください。
実graph dataの再構築/照合、本番HA/PITRは別途必要です。

## Canonical graph artifacts

`pg-agmemory graph-artifact`は**service 0.1.1 / API v1 / schema 19**向けの
管理者専用・backend非依存build入力export/checkとして導入しました。
現行service 0.2.0はschema 20 fileを出力し、同じcommand/上限を使います。
export/check自体はschemaを変更せず、AGE graphも構築/起動しません。
`PGAG_ADMIN_DATABASE_URL`は非公開に設定します。既存pending世代か現行recorded headを要求し、
不明・abandoned・後継headへ置換済みの世代は拒否します。

1. 上記`graph-generation get/begin`で世代と入力を予約します。
2. 現行台帳revisionでartifactをexport/checkします。

```bash
pg-agmemory graph-artifact export --tenant-id TENANT_UUID \
  --generation-id GENERATION_UUID --expected-revision 1 --file /private/path/graph.json
pg-agmemory graph-artifact check --tenant-id TENANT_UUID \
  --generation-id GENERATION_UUID --expected-revision 1 --file /private/path/graph.json
```

3. 出力の`artifact_digest`を`graph-generation record`で記録します。
   別のCAS操作なのでsource/台帳が変われば拒否します。記録失敗後に残るfileをserving用に扱いません。

export/checkは台帳を変更しません。record成功後は新revision（初回世代なら通常2）で
旧fileをcheckするか、**別の存在しないpath**へ再exportできます。
headとcanonical入力が不変ならbytes/SHA256は同一で、世代stateや台帳revisionをfileには含めません。
data/ACL/deletion epochが変わったら旧世代の改名でなく新世代を作成してください。
応答喪失時は`get`と`check`で照合し、fileや世代identityを黙って置換しません。

`pgag-graph-artifact-v1`はtenant/世代/親/profile対応、対応schemaの入力fingerprint、
canonical nodeとedge revision履歴を含みます。nodeはID/scope/作成時刻、
edgeはID/revision/scope/source/target/許可predicateと半開valid/system区間を持ちます。
一意順序と同一scopeのendpointを要求し、label・原文・quote・model応答・秘密は含めません。
保持するtenant topology全体なので、**非公開の管理者artifactであり、principal向け認可済み結果ではありません**。
将来のgraph readerは各探索で現行canonical認可、source可視性、削除、statement時点の期限を確認します。

署名は非公開復旧鍵のHMAC-SHA256で、domain `pgag-graph-artifact-v1:`に、
署名fieldを除くcanonical JSONと末尾改行を連結して計算します。
file digestは署名込みcanonical JSON＋末尾改行のSHA256です。
`check`はこの対応の認証**と**現行canonical内容の再構築・完全一致を要求します。
署名だけ、byte hashだけ、自己申告profileだけで任意graph dataを認定しません。
recorded headならreceiptへ保存したfile digestも一致する必要があります。

export/check成功は`artifact_verified:true`、`serving_enabled:false`です。
検証はcanonical bytesだけを対象とし、汎用世代receiptの`artifact_verified:false`を変えず、
backend起動、他principalのアクセス許可、外部extensionの認定を意味しません。
file importer・公開API endpoint・自動再構築・pruning・推論は追加しません。

上限は**10,000 nodes、40,000 edge revisions、artifactごと16 MiB**です。
canonical構築に30秒、既存input capture上限とstatement timeoutも適用します。
filesystem I/O全体の期限ではありません。両commandはread-only snapshot照合/outputまで
tenant barrierを保持し、exportはread-only transaction commit後にfileを書きます。
fileと後続metadata書込みはstorageをまたぐ原子的transactionではありません。

exportはmode 0600で排他作成し、既存file/symlinkを拒否、file/directory metadataをflushします。
書込み失敗時は自身が新規作成した同じinodeだけを削除し、外部で置換されたpathは削除せず、
cleanup失敗を明示します。checkはgroup/other権限なしの通常fileだけを受け付け、
末尾symlink、上限超過、不正入力を拒否して黙った修復はしません。
信頼できる非公開directoryを使い、artifact/backupを運用metadataとして保護してください。
DBのpurgeでexport済みfileは消えず、operatorの保持/削除管理が必要です。
旧fileの復元を現行source/台帳照合の回避やservice起動許可にしません。

## M3 AGE qualification profile

以下は保存した**旧rc0実験**であり、上記の別固定・修正済み有効profileではありません。
当時の失敗と無効なstrategyはそのまま保持します。

**任意の使い捨て開発profile**であり、service backend、application migration、
本番imageではありません。通常Dockerfileのcore runtimeは不変で、
test stageだけがAGE probe fixtureを含みます。通常API/workerの起動にAGEを追加しません。

`Dockerfile.age`は既存PostgreSQL 18.6/pgvector 0.8.6 imageと、
[公式PG18/v1.8.0-rc0 asset](https://github.com/apache/age/releases/tag/PG18/v1.8.0-rc0)を固定します。
tagのcommitは`e43dc1a12b78fba4acef9835b2b10379b8d243b4`、
release archive SHA256は`555736a31974255223778959ca8bcd9cb710b93a8fab1d846eaf3e84704b9417`です。
PostgreSQL server header packageも同じversionを要求します。
release名/catalogが1.8.0でも、実tagのrc0を安定版と読み替えません。

```bash
# 所有する新しいcontainerだけを使う。application DBを対象にしない。
bash scripts/test-age-containers.sh container
# Docker runnerでは同じhelperのengineをdockerへ変更する。
```

helperは固定extensionをbuildし、offline契約を確認してから、
`shared_preload_libraries=age`付きの新規専用DBを起動します。
このprofileはpreloadを要求し、制限readerへlibrary LOAD・label所有・BYPASSRLS権限を与えません。
NOINHERIT loginで認証後に制限された`pgag_runtime`へ明示SET ROLEします。
probe roleは新clusterだけに作り、applicationの既存runtime roleを変更しません。
base/child labelのすべてへRLSを強制し、値はprepared agtype mapで渡します。
graph名・label・templateはharnessの固定値です。

**Linux/aarch64の実測は認定失敗（exit 1）です。**
元の19確認のうちnative可変長Cypherの6件が失敗し、
`*1..1`、`*1..2`、prepared再利用、edge読取りpolicyなしの探索を含みます。
label直接SQLと明示固定hopは成功しました。追加の固定1-hop/prepared/context/
deny-all/host探索40確認の成功は別に記録し、元のVLE失敗と非0終了を維持します。
合成actor policy contextの変更は、role切替やcanonical membershipの認定ではありません。
次の候補は固定1-hop templateと上限付きhost探索であり、VLEを使用しません。
canonical ACL/time、generation、oracleとの統合は未了で、`adapter_qualified=false`です。

exit 0でもこのprobeの限定確認の成功であって本番承認ではなく、
1は認定失敗、setup/input失敗も非0（Python probeでは2）です。
この非0結果をbackend有効化へ読み替えたり、漏れたpathを黙って除外したりしません。
有効なbackendは引き続きcore SQLのみで、model呼出しはありません。

失敗log/reportはgitignore対象の`.review-artifacts/pgag-age-*`へ保持します。
既存の非公開`PGAG_AGE_EVIDENCE_DIRECTORY`を指定すると、固有名のrun subdirectoryへ保存できます。
`PGAG_AGE_TEST_IMAGE`はPython test imageの再利用用で、AGE imageは常にbuildし、
probeではsource fileを明示mountします。reportは入力4 fileのhash、
archive/image pin、実runtime権限に結び付けます。所有container/imageを削除し、
artifact削除も所有する列挙fileに限定します。失敗reportを保持してから再実行し、
credentialやlocal raw artifactはcommitしないでください。

### Fixed-hop candidate experiment

`scripts/age_graph_candidate.py`は、明示的に**未有効化の実験候補**です。
固定1-hopとnative VLEのtemplate生成を分け、現在の固定artifactではfixedだけを実行できます。
`native_vle`選択は接続前に`graph_backend_unqualified`で拒否します。
将来の修正済みupstreamを別途認定するため、元のVLE probeと失敗記録は変更せず保存します。
このrunnerは脆弱性調査を繰り返しません。

candidateはlabel policyでcanonicalなtenant/principal/scope、source可視性、
有効時刻を参照し、さらにSQL oracleと共通のcanonical neighbor filter・順序・path予算を使います。
projectionはidentity/topologyだけを持ち、canonical labelや原文は複製しません。
runtimeにはlabelの所有/書込み権限を与えず、要求は単一read transactionと既存tenant barrierで扱います。
**generation/freshness/再構築/復元の実装ではありません**。conformance fixtureは
静止したprojectionを要求ごとに作り直し、cost fixtureは比較readの前に一度だけbuildします。

```bash
# 所有する新clusterだけを使用。Native backend選択やlive model呼出しは行わない。
bash scripts/test-age-graph-containers.sh container
```

別helperは同じ固定AGE imageを使い、`PGAG_TEST_AGE_GRAPH=1`でlive conformance 20件を実行し、
通過した場合だけ別の新clusterで`PGAG_TEST_AGE_GRAPH_COST=1`のcost 3件を実行します。
明示opt-inがなければ23件をskipします。通常test imageにはcandidateのoffline契約40件も含み、
skipをAGE成功とは扱いません。application CLI・backend設定・必須extensionは追加しません。

初回の完走cost runはshapeごとに3 warmup組＋30実測組で、SQL先行とAGE先行を交互にします。
両方が同じ独立graph oracleに一致する必要があり、失敗、全sample、SQL数とnearest-rank p95を保存します。
request時間は接続/identity/serializationを含み、明示transaction区間・cursor execute合計は別に出します。
DBの5秒制限は**statement単位**で、request latency目標ではありません。
build/容量費用をread latencyへ混ぜません。

| Shape | 対象node/edge | 全projection node/revision | Build ms | Projection bytes |
|---|---:|---:|---:|---:|
| Chain | 12/11 | 14/12 | 23.20 | 163,840 |
| Fanout | 25/24 | 39/36 | 21.00 | 196,608 |
| Multiseed | 14/40 | 53/76 | 17.28 | 245,760 |

初期のrequest中央値はSQLの9–62倍で、このcandidateは**採用しません**。
別の統計診断はshape/backend/phaseごと3要求・2 EXPLAINを使い、
権限とruntime設定を維持してcanonical/projection tableへ`ANALYZE`を適用しました。
JIT約202–210 msは解消しましたが、chain/fanout中央値はcandidate 101.74/422.10 ms、
SQL 22.74/62.02 ms（4.47/6.81倍）にとどまりました。
少数診断を新しい対測定p95や本番容量とせず、global planner overrideや認可緩和で
安価に見せることもしません。conformance/cost runのexit 0は有効な測定の取得であり、
latency許容やAGE有効化の承認ではありません。
非公開証跡はignore対象`.review-artifacts/pgag-age-graph-*`に保持し、
helperは自身のcontainer/imageだけをcleanupします。credentialやlocal失敗logは公開しないでください。

## M2 core MVP deployment

公開済みM2 tagの契約は**service 0.1.0 / API v1 / schema 18**、stageは`m2-core-mvp`です。
API/worker/SDK/MCP/hookを同じversionに揃え、rolling/mixed-version互換は主張しません。
対応distributionはnative Linux amd64/arm64、Python 3.12.14、PostgreSQL 18.6、
pgvector 0.8.6で、`Dockerfile`、`uv.lock`、container helperの不変pinを使います。
PyPI package・hosted service・registry imageを公開済みとは意味しません。
認定したsource/tagからbuildし、そのidentityを保持してください。

```bash
# 意図したsource checkoutで実行し、未記録のlocal変更を混ぜない。
docker build --target runtime --tag pg-agmemory:0.1.0 .
# 任意の全体認定: 所有する使い捨てclusterのみ破壊し、live modelは呼ばない。
bash scripts/test-containers.sh docker
```

Apple Containerではengine指定を`container`へ置き換えられます。runtimeはUID/GID 10001です。
既存の管理者provisioning commandには非公開の`PGAG_ADMIN_DATABASE_URL`を使います。
API/workerにはowner/superuser/BYPASSRLSでない、`pgag_runtime`を継承した別loginを使い、
管理URLは渡しません。tenant/principal/scope membershipと、信頼するRS256の
issuer/audience/public keyを要求受付前に設定してください。`/healthz`はliveness、
`/readyz`はruntime前提条件を確認し、認証付き`/v1/capabilities`でversion/schema/stageを照合します。
TLS、perimeter policy、credential保管はoperatorの責務です。

upgrade前にAPI/worker/自動再起動を停止・drainし、保護した旧logical backup、
元ACL/削除/call証跡、対応旧codeを保持します。新componentの管理者権限で
`pg-agmemory migrate`を実行し、migration ledger **1–18全体**を確認します。
0.0.35/schema 18からはDDL追加なしです。旧schemaからは通常migrationを適用し、
015で追加する管理者専用復旧鍵を含む新しい保護backupを直後に取得します。
拒否回避のためのledger改変、過去target対応の捏造、失った復旧鍵の再作成は禁止です。
source rollbackだけではDBは戻りません。対応backup/codeを隔離復元し、新しい履歴を照合します。

generation/embedding jobは、管理者が`scope-synthesis`で許可local profile・
送信同意・予算を明示固定するまで無効です。capture権限はmodel呼出し許可ではありません。
対応workerだけを意図した固定principal/profileで開始し、
成否不明callの永続予約を保持してblind retryしません。

復元DBはAPI/model workerを停止し、隔離を維持します。下記の限定`recovery-apply`は
canonical本文/anchor/jobの一致、保全した管理鍵系統、独立した最新正本bundleを要求します。
欠落/新本文の取込み、新しいsuppress replay、receiptごと展開100対象を超えるreplay、
対象重複、service自動起動には対応しません。可変tableは各10,000 rows、
bundleは16 MiBまでです。公開には最新の削除/ACL/policy/会計照合後に別途operator承認が必要で、
CI成功や`--isolated` flagを承認と見なしません。
本番容量、HA/PITR、RPO/RTO、保持期限はM5です。
[証跡](../EVALUATION-jp.md)ではS割当と参考model観測を分離し、意味品質を保証しません。

## Schema 18 tombstone read permissions

**service 0.0.34 / API v1 / schema 18**で導入しました。migration 018前にAPI/workerを
停止/drainし、ledger 1–18を確認します。対応codeで現行schemaの復旧artifactを新規取得し、
過去の証跡を書換え・再署名して認定済みとしないでください。dataと鍵は変更しません。
tombstone metadataのtenant、read/admin membership、statement時点の期限判定は同じです。
削除対象ごとのscalar SQLを集合処理にし、write/delete policyとruntime権限は維持します。

## Schema 17 tombstone visibility

**service 0.0.33 / API v1 / schema 17**で導入しました。migration 017前にAPI/workerを
停止/drainし、対応codeでledger 1–17を確認してください。信頼する最新lineageから現行schemaの
snapshot/bundleを取得し、古いartifactのversionやMACを書き換えないでください。
canonical dataと復旧鍵は変更しません。rollbackには保護した旧backupと対応codeを使います。

object policyのtombstone判定をstatement内・tenant内の除外集合へ変更します。
object IDとtombstone対象IDはNOT NULLのため、同じ強制RLS下で旧`NOT EXISTS`と同等です。
削除後に実測したembedding joinの行数見積り劣化とnested loopを避けます。
要求をまたぐcache、definer bypass、write policy変更、JITの全体無効化は行いません。

## Schema 16 read visibility

**service 0.0.32 / API v1 / schema 16**で導入しました。
migration 016ではAPI/workerを停止・drainし、全migration履歴を確認して対応componentを再開します。
canonical data/復旧鍵は書き換えません。正本から現行schemaのsnapshot/bundleを取得し、
古いartifactのversion/signatureを編集して流用しないでください。

object read policyは、行ごとのmembership呼出しを、同じread/admin/期限条件の
statement-local集合へ置換します。episode/assertion、revision、lexical/vectorのread policyは、
object自体の強制RLSで可視なID集合を参照します。definer bypass、公開cache、
requestをまたぐ認可cache、write policy変更はありません。
prepared実行でもcontext/membershipを再評価します。recallの順位計算とindex欠落判定は
候補materializationを共有し、順位行が0件でもcoverageを返します。
既存の順序、RRF、時点filter、必須参照、削除/失効の応答barrierは維持します。

## Schema 15 operational-state application

**service 0.0.30 / API v1 / schema 15で導入し、現行schemaは18**です。
API/worker/自動再起動を停止・drainして
migration 015を適用し、対応componentとmigration履歴1–15を確認します。
直後に新backupを取得してください。015はtenant別の専用復旧鍵を追加し、RLSを強制し、
runtime policy/権限を与えません。新tenantにもtriggerで生成します。
拡張を追加せずPostgreSQLの強い乱数UUID 2個から鍵を作り、runtimeが読むdedup鍵とは
分離します。bundleにも鍵を含めません。管理者用の保護されたbackupで保存してください。
15より前のbackupには鍵がなく、別途再生成した鍵では既存bundleを認証できません。
拒否回避のためにmigration履歴を消したり鍵を捏造したりせず、rollbackには対応backup/codeを使います。

`recovery-apply`は管理者専用の**限定した状態適用**であり、一般restore orchestratorではありません。
export前に元系統のwriter/model handlerを静止し、復元先はAPI/worker停止・隔離を維持します。
他client sessionを拒否し、明示`--isolated`、tenant barrier、writerを止めるtable lock、
変更前のexpected state一致を要求します。flagはoperatorの確認で、network隔離の検出器ではありません。

```bash
# 独立して保存した正本の最新系統上:
pg-agmemory recovery-apply export --tenant-id "$TENANT_ID" --bundle "$BUNDLE"
# 承認された隔離本文復元/削除replayを終えた復元先上:
pg-agmemory processing-recovery export --tenant-id "$TENANT_ID" --file "$EXPECTED"
pg-agmemory recovery-apply apply --tenant-id "$TENANT_ID" \
  --bundle "$BUNDLE" --expected "$EXPECTED" --isolated
```

bundleはjob payload/state、idempotency応答、policy labelを含む**機微な運用row**です。
`processing-recovery`の内容を含まないfingerprint artifactとは異なります。
exportは排他的なowner専用fileへfsyncし、親directoryも非公開にして、
保護/暗号化されたbackup保管先で扱ってください。上限は可変tableあたり10,000 rows、
bundle 16 MiBです。管理者専用鍵によるMACで全row、参照fingerprint、本文fingerprintを結び付けます。
保存artifactの認証であって最新性の証明ではなく、正本の最新系統はoperatorが選びます。

同じtenant/鍵系統、epoch巻戻しなし、削除適用済みepochの完全一致、
receipt/targetの削除意味の一致、非置換運用状態の不変を要求します。
**25 canonical memory table**も最新keyed fingerprintと一致済みでなければなりません。
欠けたepisodeの取込み、summary/vector再生成、物理消去をこのcommandは行いません。
anchor変更、新しい本文の欠落、旧削除対応不明、job集合の相違を推測せず拒否します。
アクセス/policy履歴は連続した完全なepoch列で、旧prefixを保持する必要があります。
既存予約の消失/identity変更、確定結果の巻戻し、消費quotaの返還、終端jobの巻戻しを拒否します。

固定11運用tableを置換し、既存job rowを更新して元ID・時刻・policy epoch・call結果を保ちます。
015では5つのlive-write triggerを**transaction-localかつ特権限定**の復旧contextで制御します。
runtime roleはGUCを設定しても有効化できません。全trigger無効化やreplication-role変更は使いません。
FK/check/一意/遅延完全性制約は維持し、適用後に21運用fingerprintと25本文fingerprintを照合します。
失敗時は全変更をrollbackします。commit中の切断は結果不明とし、確認・照合後に判断してください。
古いexpected snapshotのままblind retryしません。

宣言した運用状態の保持であり、clusterの完全byte一致ではありません。
一般audit-event履歴/sequenceは置換せず保持し、HA/PITR、任意履歴、欠落本文、
provider側状態は認定外です。`restore_authorized=false`を明示し、自動起動や残る配置gateの免除はしません。

現行**v5 drill**は元cluster削除後に旧dumpを新clusterへ復元し、suppress/purge混在baseline 2件を保持、
その後のpurge 3件を
再適用してから、認証済み最新bundleをCLIで適用します。元receipt ID、idempotency、
ACL/policy、21運用fingerprintと35 canonical fingerprintが一致します。
synthetic予約11件（unknown/失敗/成功を含む）を保持し、意味的job IDの維持、unknown retry拒否、
消費済みquotaでの新call拒否を確認します。probeはrollbackし、
外部model requestや常駐API/worker processは起動しません。

抽出/採用/隔離candidate、原文/assertion vector、working snapshot/tail、SQL graph、
tool-effect履歴の保持対象とpurge対象を含み、生存provenanceとvector/graph検索を確認します。
typed checkpointは正確なstateとunknown effectによる停止を保持し、
epochが古いworking snapshotはread/resumeを明示拒否します。
suppress原文は物理的に保持しても読めません。混在prefixは不変・対象重複なしを要求し、
backup後の新しいsuppress、対象重複、履歴改変、展開100対象超のpurgeは拒否します。
Native suppressの有効化や新しい欠落本文の復元ではなく、適用後もserviceの隔離を維持します。

## Resource measurements

任意のtiming sinkへ、失敗/disconnectも含めNative requestごとに不変の`RequestTiming`を返します。
method、登録route template、status、server生成request IDだけを使い、
本文、query string、actor、token、path parameter値はlabelにしません。
応答bytes、接続/tenant barrier、handler、commit、transaction、server全体の時間を記録し、
未到達phaseは0でなく`None`です。transaction区間はDB接続/barrier前からcommit完了までなので
待機も含み、SQL/packerだけの時間より厳しい測定です。handlerにはrouting/validation/
serializationも含め、E2Eは別測定します。sinkの失敗は明示し、既に応答したcommitを巻き戻しません。
軽量で信頼できるsinkを使い、公開route追加とは扱いません。

`examples/resource-profile-s.json`で本測定前にS条件を固定します。
10 tenant、100k episode/whole-episode chunk、10k assertion、固定768次元projection 110k、
512-byte synthetic ASCII文書、20 recall/s、5 auto-extract observe/s、controlled-provider worker 2本です。
warmup 60秒後に30分間測定し、DBを6 vCPU/24 GiB、API+workerを2 vCPU/8 GiBへ制限します。
clientは別です。選択率100/10/1/0.1%の分母は**request tenant内**で、
tenant横断の認可を許しません。lexical/vector/hybridと全tenant/選択率を組み合わせます。
bulk fixtureにはNative相当のsource/projection/idempotency/audit metadataも作りますが、
fixture準備時間をingest latencyとは扱いません。

```bash
# Apple Container、Linuxのみ。出力先は未作成のprivate directory。
bash scripts/measure-resources.sh /absolute/private/path/development --development
bash scripts/measure-resources.sh /absolute/private/path/S-preflight --preflight
bash scripts/measure-resources.sh /absolute/private/path/S
```

developmentは2 tenant/2,000 episode/200 assertion・steady 30秒、
preflightはS全量データ・steady 30秒です。どちらもS認定ではありません。
development以外はclean commitから`git archive`し、読取り専用code mount、完全一致SHA、
入力hashを記録します。open-loopでdrop、遅延、transport/契約違反を残し、遅いrequestを
速いsampleへ置き換えません。server/client照合、recall層別、worker結果/queue lag、
1 Hz guest CPU/memory、DB/index/WAL/logical-backup bytesを出力します。
hardware `perf` counterは採らず、host kernel設定も変更しません。
guest会計はhost仮想化overheadや専用host容量の測定ではありません。

controlled providerは10 ms後に契約準拠の空抽出を返し、課金/cloud/live model呼出しや品質主張はしません。
timing recordに本文/vectorは含めません。生artifact、backup、request相関IDは非公開で保管します。
helperは自分のcontainerとcredential fileだけを片付け、測定証跡を残します。
**現行reportは`resource_qualified=false`**です。削除/上限とguest-cold probeの結果は
[EVALUATION](../EVALUATION-jp.md)に別記し、物理host cold-cacheや専用本番容量を保証しません。
steady時間gateだけではM2受入れになりません。

### 隔離した資源probe

```bash
bash scripts/measure-resource-probes.sh /private/S/final.dump /private/S-probes
```

Apple Container上の新規使い捨てclusterへ合成S全量dumpを復元します。元DBへは
接続しません。dumpには合成本文だけでなく管理者recovery keyも含まれるため、非公開で保管してください。
旧schema 16のS snapshotまたは現行schemaを受け付け、隔離copyへ通常のmigrationを適用し、
前後のschema番号を記録します。
`examples/resource-probes-plan.json`は小規模Native
purge 100件、並行admission/障害probe、10,000 objectの派生closure 1件を固定します。
小規模purgeは別の30秒間の20 recall/s・5 observe/s・2 worker負荷中に実行します。
この短時間probeを30分S認定の代用にはしません。大規模closureはbulk投入しますが、
preview/purge/replayは実Native APIを使用します。小規模p95 1秒未満、大規模900秒未満
という閾値は変更していません。
並行purgeは実行中model jobの保存済みdeletion epochを無効化し得ます。
`stale_context`拒否は成功jobに数えず別記し、epochが実際に古いこと、result/candidate未公開、
call未予約または既知のfailed会計を要求します。unknown会計や他の失敗はprobe失敗です。
全job成功を要求して失敗した旧harnessの証跡も残します。

論理restore直後と削除probe後に明示的な`ANALYZE`を実行し、前後のtable統計を保存します。
論理dumpだけでは利用可能なplanner統計があることを証明できません。その後DB guestと
postmasterを12回再起動し、mode/selectivityごとに最初の1要求と後続5要求を測定します。
異なるboot ID・postmaster起動時刻・request IDとcommit済みtimingを必須とします。
これは**guest-cold**であり物理host/device-coldではありません。各層の初回1件だけで
cold p95やcold時500ms達成を主張しません。

input/output/context/queue/call上限、結果不明callのretry拒否、DB timeoutと回復を記録します。
制御loopback providerのみを使い、予約会計を実provider課金と混同しません。
失敗時は証跡を保持して非zero終了します。正式実行はclean treeとcommit archiveを要求し、
`--development`は非exactと明記します。raw timing・log・dump・認証情報はcommitしません。
終了時に所有guestと認証情報を除去し、証跡は非公開のまま保持します。

## Schema 14 deletion manifests

**過去の契約はservice 0.0.29 / API v1 / schema 14**です。現行15の手順は上記を使います。
PostgreSQL 18.6、pgvector 0.8.6、
Native/SDK 38 resource、MCP 4 tool、既存model policyは変更しません。
DBとrole/秘密設定をbackupし、全writerと自動再起動を停止・drainしてから、
対応imageと管理DSNで`pg-agmemory migrate`を実行します。
完全なmigration履歴**1–14**を確認して対応API/worker/adapterを起動してください。
schema 13 binary/clientは対応版ではありません。rollbackには移行前backupと対応codeが
必要で、最新の失効・削除・会計を照合するまで隔離します。migration行を削除して戻しません。

migration 014は`memory_ops.deletion_target`、tenant内の削除epoch一意制約、
`deletion_request.target_manifest_version`を追加します。新receiptはversion 1が必須で、
対象数とtarget/tombstoneのscope参照を同じtransactionで検証します。
内部の`xid8`でreceipt作成transactionに限定してtargetを記録し、commit後の追記や
runtimeからの更新/削除を許可しません。件数検証はtargetごとでなくreceiptごとに一度です。
receipt件数内の一意なordinalにより、途中で`SET CONSTRAINTS ... IMMEDIATE`を
実行しても、その後の過剰な追加を防ぎます。
対象の読取にはtenant/scope権限とreceiptのactor一致が必要で、exportは管理者専用です。

旧receiptはversion **0**・対象対応なしのまま保持します。累積tombstoneからreceipt別の
対応を推測しません。旧receipt、未対応tombstone、epoch欠落、件数不一致があれば
`deletion_history_incomplete`でexportを拒否します。旧backup/台帳を保存し、
拒否を迂回するために行を捏造しないでください。新version-1操作を追加しても、
不完全なtenant履歴が遡って完全になるわけではありません。

```bash
# 管理者環境。認証情報をliteral引数へ書かない。
# OUT_FILEは未作成、親directoryはoperatorだけがアクセスできるものを指定する。
pg-agmemory deletion-history export --tenant-id "$TENANT_ID" --out "$OUT_FILE"
```

tenant barrierと一つのrepeatable-read snapshotを使う読取り専用commandです。
ID/scope/actor/mode/state/epoch/全対象だけを出力し、原文、理由、認証情報は含めません。
不明tenantと過大履歴（receipt 10,000、合計target 100,000、file 16 MiB）を拒否し、
切捨て・上書きしません。新fileはowner専用でfsyncし、stdoutへ実file bytesのSHA-256と
別のcanonical history digestを返します。digestは署名や最新性の証明ではありません。
識別子も機微な運用metadataとして保護し、独立した利用許可済みの系統で保存します。

**一般restore toolではありません。** `restore_authorized=false`、
`includes_acl_policy_and_call_accounting=false`は意図した境界です。
削除履歴が完全でも復元DBの公開やworker再開は許可されません。
最新ACL/policy/call予約/unknown/quotaの全体照合、全対応派生物の
復旧は未完です。Native/SDK/MCPの`forget`は`suppress`を引き続き拒否し、
明示記録された履歴の保存/export形式としてだけ扱います。
隔離drillは下記の限定した複数receipt履歴を扱いますが、
model会計の復旧toolではありません。

### Processing-state recovery check

v0.0.29で管理者用の読取り専用照合を追加しました。
**import、自動runtime fence、再開許可ではありません。** 復元API/workerは隔離を維持します。
operatorは独立して保存した正本の最新系統からreferenceを取得してください。
復元した旧DBをexportし、それを「最新」と呼んではいけません。

```bash
# 独立して保存した最新DB上でexport。fileは未作成であること。
pg-agmemory processing-recovery export --tenant-id "$TENANT_ID" --file "$STATE_FILE"
# PGAG_ADMIN_DATABASE_URLを隔離復元先へ切り替えて照合する。
pg-agmemory processing-recovery check --tenant-id "$TENANT_ID" --file "$STATE_FILE"
```

tenant barrier、repeatable-read/read-only transaction、UTC表現、固定21 tableの
fingerprintを使います。principal/scope/membership、capture/synthesis policyと履歴、
object anchor、model call（結果・課金不明・policy epochを含む）、意味的job identity、
job payload/state、input/candidate、source-event/idempotency、tombstone/receipt/target、
derivation、working snapshotを対象にします。access/deletion epochとtenantの
dedup secret系統も結び付けます。全rowをstreamして長さ付きtenant-keyed HMACへ入れ、
fileにはfingerprint、件数、epoch、tenant IDだけを保存します。原文、要約、consent label、
subject、provider payload、secretそのものは出力しません。

exportはowner専用の新fileへ排他的に保存してfsyncします。checkはサイズ制限内の
通常fileだけを読み、symlinkを拒否し、閉じたv1形式・完全schema・順序付き全tableを要求します。
上限はtableあたり1,000,000 rows、表現した入力合計256 MiB、定期確認するcapture予算30秒
（実行中の処理には既存DB statement/lock timeoutも適用）、artifact 32 KiBです。
上限超過・errorで部分成功を返しません。artifactと記録したSHA-256を保護してください。
HMAC一致は、渡されたreferenceの真正性や最新性の証明ではありません。

checkは`processing_state_matches`と、不一致のtable/epoch名を返します。
epoch巻戻しに限らず、どちら向きの差分も終了code **1**です。tenant/dedup系統違いはerror。
完全一致でも`restore_authorized=false`であり、原文の正しさ、完全消去、
provider側artifact、effect、一般DR、配置の隔離を認定しません。
policy、予算、job状態、call結果を変更せず、serviceの起動/自動停止もしません。

復旧drillの証跡は**v3**へ進めました（v1/v2は拒否）。
旧dumpの実復元は元のprocessing snapshotと一致します。
限定replay後は35 canonical fingerprintが最新と一致しても、再生成されたreceipt ID、
idempotency応答、audit/tombstone時刻によって運用fingerprintが不一致になります。
新checkはこれを正しく不一致・再開許可なしとし、想定した差分でも黙って免除しません。
identity、policy、call会計を保持または明示照合する最新状態適用手順が引き続き必要です。

### 限定した複数receipt復旧drill（2026-09-20）

`bash scripts/test-recovery-containers.sh container`で実行します（CIは`docker`）。
自分で使い捨てclusterを作り、API/workerを起動せず、既存DBのDSNも受け取りません。
現行の証跡v3はobject anchor、全receipt/target履歴、processing fingerprintを固定し、
旧v1/v2 artifactを読み替えず拒否します。

fixtureは旧backup前に完了purge 1件、その後に追加purge 2件とACL変更2件
（権限縮小、その後の失効）を持ちます。元clusterを削除し、別の新clusterへ旧`pg_dump`を
復元します。baseline完全一致を確認し、先にACL差分を適用してから、変わらず権限を持つ
operatorで各purgeを再適用します。plannerは不変な履歴prefixの一致から追加receiptだけを
選び、旧tombstoneを再purgeしません。削除対象6件、可視なcontrol、最新epoch、
receipt/targetの意味、35 canonical table fingerprintを独立した最新証跡と照合します。

対応範囲は、対象が重ならないpurge履歴、変更のないprincipal/object anchor、
再適用receiptごとの展開済みtarget 100件以内です。ACLは連続したepochで、
既存の無期限membershipの縮小/失効だけを扱います。grant、期限変更、履歴欠落/並べ替え、
削除operator変更、新anchor、capture/synthesis policy、model call状態は拒否します。
上限回避のためのreceipt分割やrootの捏造はしません。
source内の`purge_replay_suffix`はplannerであって復元許可ではありません。

baselineのreceipt IDは維持します。差分は`forget`経由で新receipt IDが作られるため、
reportへ元IDとの対応を残し、正規化したreceipt/targetの意味をcanonical fingerprintとは
別に照合します。audit/idempotency履歴の完全なbyte一致や、本番restoreの再開可能性を
主張しません。不完全・途中まで再適用したbaselineをblind retryしません。
最新model会計とpolicyの照合はM2-Bの次の残作業です。

## Schema 13 background processing

以下のversion固有手順は過去の記録で、現行は上記schema 14です。
変更していない処理権限・安全条件は維持します。

[2026-09-19改訂計画](../PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)は人手の意味評価や
model比較をgateから外しますが、運用上の安全要件は維持します。
次は対応履歴全体のlogical restoreと最新削除/ACL/policy/model予約/quotaの照合です。
下記の限定drillを超える実装は未完であり、範囲変更を理由に復元workerを再開しないでください。

**現在はservice 0.0.27 / API v1 / schema 13**、
stage `m2-background-processing`です。PostgreSQL 18.6 / pgvector 0.8.6は維持します。
本節と[ADR 0028](../adr/0028-background-processing-jp.md)が、
下に保存したv26固有手順より優先します。lifecycle検査を品質・復旧保証と誤解しないよう、
[EVALUATION](../EVALUATION-jp.md)も確認してください。

既存環境ではDB backupとrole/secret設定を安全に保全し、**すべての**
API/worker/adapter writerと自動再起動を停止・drainしてから、
対応v27 imageで`PGAG_ADMIN_DATABASE_URL`を使って`pg-agmemory migrate`を実行します。
migration 012はsynthesis policy・永続call予約・candidate provenance、
013はworking stream/snapshot・明示candidate採用を導入します。
厳密なledger **1–13**、`public`内の`vector` 0.8.6、readiness、認証付きcapabilitiesを
確認してから、対応v27 componentだけを起動します。runtimeは非ownerの
`NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime`を維持し、policy UPDATEやbypassを与えません。

新規の使い捨て環境は既存Dockerfileのruntime targetをbuildし、同じmigration、
検証済みsubjectのprovision、下記のJWT公開鍵と制限付きruntime DSN設定を行います。
admin DSNをAPI/workerへ渡してはいけません。同一clusterの別databaseはroleを共有するため、
migration test用の独立した使い捨てclusterの代用にはなりません。

**rollbackにはmigration前の対応DB backupと対応codeの両方が必要です。**
schema 11 binaryはschema 13を拒否します。ledger行や新tableを消して版を偽装しないでください。
古いbackupの復元は、最新の削除・ACL失効記録の整合を確認するまで隔離状態を維持します。
自動ledger replay/DRは未実装・未認定です。

復元したsynthesis policy、待機job、call予約、quotaも古い可能性があります。
最新の独立した予約/accounting記録と照合できるまで、**復元先のmodel workerは停止**
してください。削除/ACL replayだけでは外部callの重複やquota巻き戻りを防げず、
古いbackupでpendingに見えるjobも、providerを未呼出しとは限りません。

### Local model workerの明示許可

providerのinstallやcapture許可はsynthesisを有効にしません。operator JSON profileは
`local_http`、固定model revision、明示text output上限（例`max_output_tokens: 512`）を
要求します。operator管理のloopback model serverを使い、container接続のために
providerのendpoint制約を緩めないでください。worker digestは実promptも束縛するため、
prompt/profile変更後は再計算と管理者確認が必要です。

```bash
# 設定の検査だけで、推論・DB接続は行いません。
pg-agmemory worker --provider-config local-worker.json --print-profile-digest

# 管理者shellのみ。DSNは承認済みsecret環境から供給します。
pg-agmemory scope-synthesis get --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID"
pg-agmemory scope-synthesis set --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID" \
  --expected-access-epoch "$EXPECTED_ACCESS_EPOCH" \
  --policy-file approved-synthesis-policy.json

# 別の制限付きruntime shell。ここにadmin DSNを渡してはいけません。
pg-agmemory worker --subject "$WORKER_SUBJECT" --provider-config local-worker.json --once
```

承認policyには`enabled`、算出した`profile_digest`、完全一致`consent_references`、
許可する`kinds`、`max_calls`と入力/出力上限を指定します。
抽出を隔離だけにする場合は`publish_predicates: []`にします。自動公開にはさらに
組込みliteral-preference規則への適合が必要です。省略fieldはdefaultへ戻り、mergeではありません。
停止は`{}`を保存したfileと最新tenant epoch CASで行います。
管理変更の結果不明時はreadbackし、blind retryしないでください。

Native/SDKの`ProcessMemory`または任意の`Observe.auto_extract`/`auto_embed`を使います。
`--provider-config`なしのworkerは構造化jobのみを継続し、model jobをclaimしません。
episode受付とjob完了は別に確認します。結果不明の予約/callは復旧時に
`billing_unknown`で停止し、policy変更や`retry_of`でblind re-callできません。
purge後も本文を含まないcall metadataを保持し、削除によるquota resetを防ぎます。

### Working compactionとhook復元

受付済みepisode参照をserver採番のworking streamへ追加し、typed checkpointと
そのhead/対象prefixを指定して`CompactWorking`を要求します。
typed stateを正確にコピーし、model summaryを**未信頼**として分離し、新しいtailを保持します。
実行や承認はしません。追加7種のNative/SDK APIはADR 0028を参照してください。
MCPへは追加しません。

snapshot参照がなければhookの既存4 field出力を維持します。`after_compaction`だけで
`working_snapshot_id`を指定し、信頼するoperator環境の
`PGAG_HOOK_WORKING_SNAPSHOT_BUDGET_BYTES`を1–65536に設定します（既定0は無効）。
recall-packとは**別の追加予算**です。scope/epochと全tail根拠を検査し、
pagination/不完全tail/予算不足は黙って捨てずに失敗します。
失効後に配信済みcontextを破棄するhost側責任は残ります。

### 再現と残る受入れ

`bash scripts/test-recovery-containers.sh container`は、**synthetic・使い捨て・
purge 1件/失効1件限定**の論理backup復旧実験です。全体container pipelineからも
既存test imageを使って実行し、CIでは`docker`を使用します。
独自のsource/復元先clusterを作り、実際の新旧`pg_dump`、
正式なtombstone/receipt/ACL metadata exportを取得してsource clusterを削除し、
別clusterへ古いbackupを復元します。可視性確認前のreplayには既存service/admin
interfaceを使い、API/workerを起動せず、modelも呼びません。
既存環境のDSNを引数として受け取らず、**本番restore CLIではありません**。

削除baselineが空、完了purgeが1件、その後の失効が1件、principal変更なし、
削除operatorの権限が現在も有効、という履歴だけを扱います。他の履歴や
synthesis policy/call accounting、working snapshot、抽出candidateはfail closedです。
schema 13 tombstoneには展開済みobject IDがありますが、targetごとのreceipt/mode対応は
ないため、suppress/purge混在や複数receiptのreplayを推測してはいけません。
一般DR、model call/quota照合、HA/PITR、保持期限、未実行の派生kindは未認定です。
一時dump/metadataは終了時に削除するので、必要なら内容を含まないJSON reportを別に保存します。

localは`bash scripts/test-containers.sh container`でApple Container内で実行し、
CIはnative Docker amd64/arm64です。production M2 smokeは正確に3回のsynthetic
loopback応答を使い、実model品質の証明ではありません。明示local model/生成ACL opt-in、
固定manifest、失敗callの記録、改訂後の本体受入残作業はEVALUATIONを参照してください。
local実験のための新Azure resourceや有料model callは不要です。

## 保存されたv26運用

以下はupgrade/検証証跡を含む**v0.0.26/schema 11の過去のguide**です。
現行schema 13の版・role条件を変更せず、背景modelへの送信を許可するものでもありません。

## 初期設定とrole分離

下記の固定prebuilt上流pgvector DB profileと、
repositoryの既存`Dockerfile`から構築したapplication imageを使用します。
CLI名は`pg-agmemory`、import package名は`pg_agmemory`です。
ローカルcheckoutは`pg_agmemory`、GitHubは`rioriost/pg_agmemory`です。
現在の上限付きmilestoneは**v0.0.26/schema 11のscope capture policy**です。
**実装`c07630009ff4dcc34542e3ea80064d4f10c4d8b5`のlocalと
native Docker amd64/arm64適格性確認は合格し、M2完了ではありません**。
[実装の証拠](../STATUS-jp.md#v0026--schema-11)と
[source CI 35308638587](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587)は、
後続の文書専用publication commitでのsuite実行を主張しません。
検証済みv0.0.25以前の結果は過去の証拠であり、v0.0.26の適格性確認ではありません。
過去のschema 10 application-only手順ではなく、
[schema 11保守手順](#schema-11-scope-capture-policy-upgrade)を使います。
既存`008_pgvector.sql`は**`public`内の`vector` 0.8.6**を要求し、
別版/別schemaの既存extensionを拒否します。
既存`009_scope_access.sql`は特権audit tableを提供します。
v0.0.15で導入した既存`010_job_cancellation.sql`はjob制約とterminal-state guardを変更し、tableは追加しません。
新`011_capture_policy.sql`はscope policy/private audit tableを追加します。
固定DB profileと依存版は維持します。[job取消](#explicit-job-cancellation)を参照してください。
**過去のv0.0.8:** Apple Containerとnative Docker amd64/arm64で214テストと
production smokeに合格しました。v0.0.9の結果ではありません。
M0〜M3、MVP、本番、性能、品質、DR、完全消去の受入は未完了です。
**過去のv0.0.7だけの証拠:** Apple Containerとnative Docker amd64/arm64の各環境で
**144テスト**（既存warning 2件）、
Ruff、strict mypy（source 12ファイル）、non-root productionの日本語tokenizer、
API HTTP、実worker CLI `--once` idle実行という全3種のsmokeが合格しました。
最終SHA、CI log、所要時間は[検証証拠](../STATUS-jp.md#検証証拠)を参照してください。

過去のv0.0.7最終lockは既存package-feed registryを維持しました。
全36 packageのversion、依存metadata、artifact hashはテスト済みPyPI解決lockと
byte単位で同一です。v6との差分はJanome 0.5.0の追加とprojectのv0.0.7へのversion更新だけで、
無関係なupgradeやregistry移行はありません。native CIはこのretained-registry lockから
buildしました。v0.0.8/v0.0.9のpackage件数や検証についての主張ではありません。
現在のlock済みbuildを使用してください。任意のMCP extraは`mcp==2.2.0`と
`httpx==0.28.1`を固定し、Docker test/runtime両stageに含めます。
v0.0.26は両stageに`hook`と`sdk`も維持します。`pg-agmemory[hook]`は
`httpx==0.28.1`を固定し、**MCP SDKは含めません**。
container検査scriptは使い捨てsmoke設定を含め、**Apple ContainerとDockerの両方**で
runner側の`jq`を必須とします。

| 設定 | 利用者 | 用途 |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | 管理CLIのみ | migration、offline lexical rebuild、provision、scope-access/scope-capture管理 |
| `PGAG_DATABASE_URL` | API/worker runtime | `pgag_runtime`に所属する専用の制限付きlogin |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | 2048 bit以上の静的PEM RSA検証公開鍵。署名用秘密鍵は渡さない |
| `PGAG_JWT_ISSUER` | API runtime | 信頼するissuerの完全一致値 |
| `PGAG_JWT_AUDIENCE` | API runtime | 本サービスのaudienceの完全一致値 |
| `PGAG_MCP_API_URL` | Local MCP adapterのみ | 信頼する固定Native API HTTPS originまたはloopback HTTP origin。URL credential/application path/query/fragmentは禁止 |
| `PGAG_MCP_API_TOKEN` | Local MCP adapterのみ | Native API audience用の固定bearer token。callごとでなく起動時に安全に渡す |

1. admin URLが、意図した空の使い捨てMemory DBを指すことを確認します。
   対応extensionを提供する固定上流DB imageを使います。
   operator管理の代替環境にも同じextension版/schemaが必要ですが、host APT/source build手順は提供しません。
   対応application imageから`pg-agmemory migrate`を実行します。
   migrationはtransactionalで、
   `public.pgag_schema_migration`に版を記録します。migration loopは対応する
   連続した履歴のみを受け付け、適用済み版は再実行時にskipします。
   lock取得timeoutは5秒です。既存DBの更新には下記の保守手順が必要です。
2. 変更しない`src/pg_agmemory/storage/001_initial.sql`、
   `src/pg_agmemory/storage/002_assertion_revisions.sql`、
   `src/pg_agmemory/storage/003_checkpoints.sql`、
   `src/pg_agmemory/storage/004_tool_effects.sql`、
   `src/pg_agmemory/storage/005_relational_graph.sql`、
   `src/pg_agmemory/storage/006_durable_jobs.sql`に続き、追加的な
   `src/pg_agmemory/storage/007_japanese_fts.sql`、既存`008_pgvector.sql`、
   既存`009_scope_access.sql`、既存`010_job_cancellation.sql`、新`011_capture_policy.sql`をpackage resourceとして
   同梱します。計画の例示DDLで代用したり、生成済みfileを想定したりしないでください。
   管理者はsuperuser、または必要な所有権/DDL・role/schema作成・`btree_gist`/対応pgvector
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
   owner role経由の所属も対象です。またschema ledgerの厳密な`[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]`と
   schema `public`内のextension `vector` 0.8.6を要求し、不一致を拒否します。
   workerもこのrole/schema/extension検査を使いますが、APIのJWT設定は不要です。

admin URL、署名用秘密鍵、token、tenant HMAC secretをsource管理、issue、
logへ残さず、不要なものをruntime環境へ渡さないでください。
runtime DB資格情報をagentへ渡して任意SQL入口にしてはいけません。
固定queryと信頼されたidentity contextも認可境界の一部です。

## Scope-capture administration

**service 0.0.26 / API v1 / schema 11。実装はlocal・native CI検証済みです。**
`PGAG_ADMIN_DATABASE_URL`を持つ信頼する管理者だけが
`pg-agmemory scope-capture get|set`を実行します。
このURLをAPI/worker/SDK/MCP/hook process、client、agentへ渡してはいけません。
HTTP管理endpoint、SDK resource、MCP toolの追加はありません。
以下のplaceholder IDを意図したtenant/scopeへ置換し、承認済みsecret管理経路でDSNを渡します。
DSN/password/tokenのliteralをshell履歴、JSON、version controlへ書きません。

roleはsuperuserまたは適切な`BYPASSRLS`と必要なtable/schema権限を要求します。
scope-accessと共有するadmin接続は厳密なschema ledgerと`public`内の`vector` 0.8.6を
検査し、接続/statement/lockを5秒で制限します。tenant session barrierはtransaction
commitからflushしたCLI出力まで維持します。online policy変更はこのbarrierを使いますが、
schema更新には下記の保守停止/drainが必要です。

### Get, replace, and restore

```bash
# PGAG_ADMIN_DATABASE_URL is already supplied securely to this admin shell.
TENANT_ID='00000000-0000-0000-0000-000000000001'
SCOPE_ID='00000000-0000-0000-0000-000000000002'
pg-agmemory scope-capture get --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID"
```

結果は`operation`、`tenant_id`、`scope_id`、現在のtenant `access_epoch`、
nullableな最終変更`policy_access_epoch`、`configured`、`changed`、有効`policy`です。
rowなしはlegacy受付（enabled、両allowlist `null`、最大262144 byte）です。
後で同じ設定へ戻す場合は、有効policyと現在epochを安全に保存してください。

作業directory内に承認済みの非secret labelで完全なclosed policy fileを作成します。
全4 field必須、file上限は**64 KiB**です。

```bash
umask 077
cat > capture-policy.json <<'JSON'
{
  "enabled": true,
  "source_namespaces": ["approved-source"],
  "consent_references": ["approved-consent-label"],
  "max_content_bytes": 16384
}
JSON
# Replace with access_epoch from the immediately preceding get; 1 is an example.
EXPECTED_ACCESS_EPOCH=1
pg-agmemory scope-capture set \
  --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID" \
  --expected-access-epoch "$EXPECTED_ACCESS_EPOCH" \
  --policy-file capture-policy.json
pg-agmemory scope-capture get --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID"
```

`enabled`はstrict boolean、`max_content_bytes`はstrict整数1〜262144です。
各allowlistは`null`（制限なし）、または空白除去/sort後の相異なる最大64文字列で、
各1〜256文字、C0 controlと不正UTF-8を含めません。`[]`はすべて拒否します。
比較は大文字小文字を区別する完全一致で、wildcardや検証済み同意記録ではありません。
byte上限はraw JSONでなく正規化した`Observe.content`のUTF-8に適用し、
65,536文字の本文上限とNative request body上限も維持します。
無効化には`enabled: false`を含む別の完全なdocumentが必要で、
patchやfield省略は使えません。

**legacy受付**へ戻すには、完全なlegacy policyを明示設定します。

```bash
cat > capture-policy-legacy.json <<'JSON'
{
  "enabled": true,
  "source_namespaces": null,
  "consent_references": null,
  "max_content_bytes": 262144
}
JSON
pg-agmemory scope-capture get --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID"
# Set this environment variable to the access_epoch just read, not the earlier value.
pg-agmemory scope-capture set \
  --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID" \
  --expected-access-epoch "${CURRENT_ACCESS_EPOCH:?set the freshly read tenant epoch}" \
  --policy-file capture-policy-legacy.json
pg-agmemory scope-capture get --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID"
rm capture-policy.json capture-policy-legacy.json
```

legacy受付への復元は以前のcustom policyへの復元とは異なります。
custom policyへ戻す場合は保存した完全documentと新しく読んだtenant epochを使います。
delete/reset操作はなく、legacy policyへ戻しても既存rowは残ります。
同値set（rowなしscopeへのdefault設定を含む）はno-opでaudit/epoch変更はありませんが、
正しいtenant CASが必要です。他scope/membership変更でもepochは古くなります。

### Denial, audit, and uncertain outcomes

現在のscope read/write ACLをpolicyより先に検査し、
不存在/未認可scopeは**404 `not_found`**を維持します。
observe/capture/batchはidempotency/source dedup**より前に**policyを検査するため、
無効化・制限後は以前の完全一致replayでも**403 `capture_policy_denied`**となり、書込みません。
不正な保存policyは**503 `capture_policy_invalid`**、
本文の不正UTF-8は既存request検証で**422 `invalid_request`**です。
保存設定の失敗は管理者が調査し、runtime書込み権限の追加や検証回避はしません。
SDKはpolicy codeを保持し、mutationの403は`retryable: false` /
`outcome_unknown: false`です。503はfail-closedなpolicyでも保守的に
`retryable: true` / `outcome_unknown: true`です。自動retryはなく、
元key/bodyを保持して整合確認し、rollbackを推測しません。

実変更は設定、tenant epochの一度だけの増加、
`memory_ops.capture_policy_event`の変更前後snapshotとDB roleを原子的にcommitします。
configはFORCE RLSと読取り可能scopeへのruntime SELECTのみ、
auditはFORCE RLSかつruntime accessなしです。
episode本文を記録しなくてもlabelは機微情報になり得るため、audit/config backupを保護します。
epoch **9223372036854775807**でも正しいepochの同値setは可能ですが、
実変更は`access_epoch_exhausted`です。回避のためにepochをresetしてはいけません。

安全なadmin errorは`{"error":{"code":"…","outcome_unknown":false}}`
（commitの可能性があれば`true`）であり、生DB診断ではありません。
`access_epoch_conflict`では新しい`get`と意図的な整合確認が必要です。
出力喪失や`outcome_unknown: true`では**blind retryをしません**。
get/readbackでpolicyと現在epochを確認し、必要なら特権auditを調べ、
現在CASで再度完全setすべきか判断します。出力喪失はrollbackの証明ではありません。

既存内容は引き続き読取り可能で、既存episodeの明示remember/jobも許可します。
purge、job cancel、secret/PII検出、同意の証明、provider egress認可ではありません。
epoch変更は古いcontextとclaim済みworkをfenceしますが、新epochでworkerは復旧できます。
実際の削除/cancelには別の認可済みworkflowを使ってください。
[完全な契約](../STATUS-jp.md#scope-capture-policy)と
[ADR 0026](../adr/0026-scope-capture-policy-jp.md)を参照してください。

## Schema 11 scope-capture-policy upgrade

**v25/schema 10 → v26/schema 11はDB migrationであり、application-only更新ではありません。**
実装は[local・native適格性確認済み](../STATUS-jp.md#v0026--schema-11)です。
以下は保守手順であり、DR/productionの受入証拠ではありません。
作業branchは`feat/scope-capture-policy`です。継承rollback checkpoint `9740f96`は
**未適格のv26作業**であり、安定したmigration前codeではありません。
**適格性確認済みschema 11 code rollback checkpointは
`c07630009ff4dcc34542e3ea80064d4f10c4d8b5`です。**
復旧計画の**安定したv25/schema 10 code参照は`869b854`**とし、
対応するmigration前DB backupも用意します。いずれのSHAもDB rollbackではありません。

1. 復元可能な更新前DB backup、対応する旧image/lock識別子、ACL/削除記録、
   tenant epoch、operator設定を保全します。特権admin auditも含め、
   別の使い捨てDBでrestore手順を確認してください。backup資格情報は文書外で管理します。
2. **schema 10とその他旧版・新版の全API、writer、worker、replica、自動再起動、
   SDK caller、MCP adapter、hook/inference起動、管理commandを停止/drain**します。
   旧writerは新policyを強制しないため、混在させてはいけません。
3. 対応**v0.0.26** imageと環境経由の`PGAG_ADMIN_DATABASE_URL`で
   `pg-agmemory migrate`を実行します。既存migrationと`011_capture_policy.sql`を順に適用します。
   既存schema 10 DBにはpolicy/auditの2 tableを追加し、rowなしはlegacy受付を維持します。
   古いschemaには既存backfillを含む001〜010も必要です。
4. 厳密なledger **`[1,2,3,4,5,6,7,8,9,10,11]`**、PostgreSQL **18.6**、
   **`public`内の`vector` 0.8.6**、policy FORCE RLS/runtime SELECT-only権限、
   private audit分離を確認します。runtime loginは`NOSUPERUSER NOBYPASSRLS`で
   table owner roleに所属させず、migration管理者のDDL権限を分離します。
5. 対応する**service 0.0.26 / API v1 / schema 11**のAPI/worker/SDK/MCP/hookだけを起動します。
   readinessと認証付き`m2-scope-capture-policy` capabilitiesを確認し、
   traffic再開前に承認済み使い捨てdataでget/set/no-op/deny/replay/restoreを確認します。
   実導入の検査結果は実装適格性確認と分けて記録してください。readinessだけでは適格性確認になりません。

**Rollback：** schema 10 binaryはschema 11を拒否します。
code/imageのrevertだけではDBは戻らず、**downgrade commandはありません**。
migration失敗時は復旧判断前にledgerを確認し、guard回避のために手動で減算したり、
tableをdropしたりしないでください。schema 11がcommit済みならtraffic停止を維持し、
forward fix、または完全な更新前backupを隔離DBへ対応schema 10 applicationとともに復元します。
backup以降の書込みを考慮し、traffic再開前に現在のACL、削除、policy判断を再適用/整合確認します。
古いbackupで失効済みアクセスやpurge済みdataを復活させてはいけません。
schema 10はcapture policyを強制できないため、必要な制限を承認済み外部制御で維持できなければ受付停止を続けます。
整合確認用にschema 11 storage/auditも保全します。このbackup手順は
DR/full-erasureの適格性を主張しません。

## Selectable inference providers

**過去のv25はlocal・native CI検証済みです。保持する正確なprofileでのlive証拠も、
全providerやAzure上のMemoryDB全体のhosting認定ではありません。**
対応checkoutから`python -m pip install '.[providers]'`で導入します。
既存`httpx==0.28.1`を追加し、独立した軽量packageや新しい依存versionではありません。
operatorがprofile、text開示、provider cost、model identity、retentionを承認します。
recall text/tool出力からendpoint/secret/DSNを選ばないでください。
Native SDKやworkerはこのlibrary/CLIを自動呼出ししません。
正確なAzure profileは**live 5 case / 20.12秒**とCLI検査に合格しましたが、
**英語入力の要約が一度スペイン語になりました**。
契約合格は言語/grounding/品質gateの合格ではなく、要約はuntrustedのままです。
[限定されたlive証拠と確認済みcleanup](../INFERENCE_PROFILES-jp.md#recorded-azure-live-evidence)を参照してください。
下記の汎用profileは説明用です。記録したAzure profileは観測した`azure_ai` **2.0.1**を固定し、
過去の2.0.0 example/fixtureとは区別します。

### Profiles and explicit commands

信頼したlocal profileを`local-inference.json`として保存します。
name/revisionはsynthetic placeholderであり、対応modelを自分で配置して実identityを固定してください。
Native SDK originと異なり、推論base URLは`/v1`等のpathを含められます。

```json
{
  "backend": "local_http",
  "endpoint": "http://127.0.0.1:8001/v1",
  "text_model": {"name": "operator-selected-text", "revision": "operator-pin-v1"},
  "embedding_model": {
    "name": "operator-selected-embedding",
    "revision": "operator-pin-v1",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1"
  },
  "embedding_target": "local-embedding-alias",
  "timeout_seconds": 30,
  "max_output_tokens": 1024
}
```

既存の互換HTTPS APIには、別の`api-inference.json`を使います。

```json
{
  "backend": "openai_compatible",
  "endpoint": "https://model-api.example/v1",
  "api_key_env": "PGAG_MODEL_API_KEY",
  "auth_header": "bearer",
  "text_model": {"name": "operator-selected-text", "revision": "operator-pin-v1"}
}
```

参照先の環境secretを安全に注入し、fileにkeyを含めないでください。
`auth_header: "api-key"`も明示選択できます。
query/fragment/userinfo、percent-encoded path、dot segment、proxy環境、
redirect、fallback、retryはありません。localはloopback HTTP/HTTPSのみ、APIはHTTPS必須です。
この上限付きchat/embedding shapeを全provider/modelが実装するとは保証しません。

Azure SQLには、別の`azure-inference.json`でFlexible Serverを選択できます。

```json
{
  "backend": "azure_ai",
  "database_url_env": "PGAG_INFERENCE_DATABASE_URL",
  "azure_product": "flexible_server",
  "azure_extension_version": "2.0.0",
  "azure_summary_mode": "generate",
  "text_model": {"name": "operator-text-deployment", "revision": "operator-pin-v1"},
  "embedding_model": {
    "name": "operator-selected-embedding",
    "revision": "operator-pin-v1",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1"
  },
  "embedding_target": "operator-embedding-deployment",
  "timeout_seconds": 30
}
```

SQL環境変数にはcanonical管理/runtime接続ではなく専用推論DSNを指定します。
extension/model資格情報とgrantはapplication外で準備し、対応環境ではmanaged identityを推奨します。
HorizonDBでは`azure_product: "horizondb"`と実際の導入版
（文書上のPG17例は`"2.2.1"`）を使い、`text_model.name`/`embedding_target`は
deployment名でなく登録model aliasにします。
Flexible例は文書上のPG18版であり、配置readinessの証拠ではありません。
SQL profileにHTTP設定や`max_output_tokens`を含めないでください。
embedding専用なら`text_model`と`azure_summary_mode`を両方省略します。
HTTPでも`max_output_tokens`指定には`text_model`が必須で、embedding専用profileでは省略します。
Flexible Language modeは`azure_summary_mode: "language"`、
`text_model.name: "azure_cognitive.summarize_abstractive"`、任意`language`、
`sentence_count` 1〜20（既定3）で明示します。HorizonDBでは非対応です。
非defaultの`sentence_count`はFlexible Language mode以外では拒否します。
新規構成には`generate`を推奨します。Language Summarizationは**2029-03-31**に終了予定です。
[公式version/preview/lifecycle参照](../adr/0024-selectable-inference-jp.md#azure-reference-boundary)を参照してください。

承認済みsynthetic入力を`inference-input.json`に保存します。

```json
{"text": "Synthetic example: the decision is tentative, not approved."}
```

以下は**operatorが明示する推論呼出し**で、実dataに対する検証手順ではありません。
callごとにprofileを選び、要約にはlocal、embedにはAzureを指定できます。
成功出力にはmodel dataが含まれるためfileを保護してください。

```bash
umask 077
pg-agmemory infer inspect --config local-inference.json
pg-agmemory infer inspect --config azure-inference.json
pg-agmemory infer summarize --config local-inference.json < inference-input.json > summary-result.json
pg-agmemory infer embed --config azure-inference.json < inference-input.json > embedding-result.json
```

`inspect`は入力不要です。HTTPはnetwork呼出しなしにclientを構築・closeして
設定と資格情報headerを検証し、SQLは接続してread-onlyな
catalog/role/extension/overload/SQL権限を検査し、model推論はしません。
provider権限、quota、接続、出力品質を保証するものではありません。
`summarize`/`embed`はstdinからraw textでなくclosed JSON objectを一つ読みます。
CLI推論結果は`{status, result, error}` envelope一つです。不正入力/設定はexit 2、
他provider失敗はexit 1で、errorは`code`、`retryable`、`billing_unknown`だけを返します。
raw検証エラーやsecretをlogへ出さないでください。
configは32 KiB以下、input textは空白だけでない1〜65,536文字で元bytesを維持し、
UTF-8/JSON入力とHTTP requestは256 KiB以下、HTTP/SQL結果guardは2 MiBです。
serialize overheadも含みます。strictな`timeout_seconds`は1〜120（既定30）、
HTTP専用`max_output_tokens`は1〜4096（既定1024）です。

### Library, output review, and explicit upload

typed libraryも同じprofile契約を使い、暗黙のmemory clientはありません。

```python
import asyncio
from pathlib import Path

from pg_agmemory.providers import (
    InferenceInput, ProviderFailure, make_provider, parse_settings,
)


async def main() -> None:
    try:
        provider = make_provider(parse_settings(Path("local-inference.json").read_bytes()))
        result = await provider.summarize(
            InferenceInput(text="Synthetic example: no approval has been given.")
        )
        print(result.status)
    except ProviderFailure as exc:
        print(exc.error.code)
        if exc.error.billing_unknown:
            print("Billing may have occurred; no automatic retry was attempted.")


asyncio.run(main())
```

`SummaryResult`は`{model, input_digest, summary, status: "untrusted"}`です。
privateにreviewし、根拠付きassertion、承認、compaction snapshotと見なさないでください。
`GeneratedEmbedding`は既存`VectorQuery` + `input_digest`で、
有限・非zeroなnormを持つ正確に768個の有限値です。padding/truncateやmodel空間の推定はしません。
model identity/revisionはoperatorが固定し、aliasやAIMM upgradeの証明ではありません。

memoryへのuploadは明示的に`source = await memory.embedding_input(Explain(...))`を取得し、
`generated = await provider.embed(InferenceInput(text=source.text))`と呼びます。
`source.memory_id`、`source.revision`、`generated.model`、`generated.values`、
`generated.input_digest`で`PutEmbedding`を構築し、保持したcaller idempotency keyで送信します。
返された正確なtext/digestを維持し、Nativeは現在ACL、model/digest、purge状態を再検査します。
providerはuploadせず、cursor/summary/digestから権限を取得しません。
upload結果不明なら**同じ生成payload/key**だけを再送し、modelを再実行しないでください。
providerの`billing_unknown`はNativeの`outcome_unknown`と別です。
cancelは伝播し、remote実行/課金を止めるとは限りません。

SQLは`verify-full` TLS/system CAまたはoperator指定CA fileを使う専用autocommit接続で、
canonical transaction/session lockではありません。各function statementにはDB transactionがあります。
preflightは特権role/所有権を拒否し、各callで固定版・extension所有・互換overloadと
SQL `USAGE`/`EXECUTE`を要求し、model registry/key設定を読みません。
`MATERIALIZED`による一度の結果評価とserver側size guardはadapter結果query内の
重複推論を防ぐもので、provider側retryを制御するものではありません。
文書化されたfunctionが対応する場合は`max_attempts => 1`を明示し、
これはSQL embedding/Languageに適用します。applicationはretryしません。
`azure_ai.generate`には検証済みretry/output-token knobがなく、
extension内部の動作や課金は保証しません。一度の`MATERIALIZED` SQL呼出しも
**課金されるupstream呼出しが一度だけとの証明ではありません**。
Language modeは全summary partを維持し、`disable_service_logs => true`を要求しますが、
**完全消去ではありません**。server query log、provider log、retention、budgetはoperator責任です。
[全設定/SQL契約](../STATUS-jp.md#selectable-inference-providers)と
[v25適格性確認の状態](../STATUS-jp.md#v0025--schema-10)を参照してください。

### TLS trust-store selection

Azure live実行はDSNに`sslrootcert=/etc/ssl/certs/ca-certificates.crt`を明示し、
**verify-full/TLS 1.3**を維持しました。DSNはoperatorのprocess環境変数で渡し、
共通profile JSONやrepositoryには入れません。
Native testは別local使い捨てDBを使い、cloud推論DBにMemoryDB schemaはありませんでした。

bundled OpenSSL buildの既定CA file pathが存在せず、
offline検査で両方の`SSL_CERT_FILE`対応を確認しました。
このOS CA bundleが存在して読取り可能なLinux環境では以下を設定します。

```sh
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
```

v25 Docker baseはこの変数を設定し、non-root production smokeは環境変数値と
読込み済みroot CA storeが空でないことを検査します。
local実libpq TLS 9 caseと対象Ruffが合格し、別のnon-root v25 runtime検査で
環境変数値とtrusted CA 150件を確認しました。
local full suiteとsystem CAを含む全production smokeも合格しました。
完全一致SHAのnative CIは合格しました。
**成功したAzure live実行は、この環境変数修正を使っていません**。
証明書/hostname検証を弱めたりfallbackを加えたり、
local regression検証のためcloud resourceを再作成したりしないでください。
Azure trial resource、soft-deleted account記録、local test DB、secret file 6件は削除し、
cleanupは**12:07:30 JST**に確認済みです。
[TLS既知issue](../INFERENCE_PROFILES-jp.md#tls-trust-store-known-issue)を参照してください。

## Episode query and pagination

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
Native JWT認証と現在read権限のある信頼したscope UUIDを使います。
read-only `POST /v1/episodes/query`はwrite権限も`Idempotency-Key`も不要です。
以下はsyntheticな最初のpage request例であり、live dataに対して実行しないでください。

```json
{
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "occurred_from": "2026-09-17T00:00:00Z",
  "occurred_to": "2026-09-18T00:00:00Z",
  "max_items": 20,
  "before": null
}
```

closedな`QueryEpisodes`は重複しないscope UUID 1〜32件が必須です。
timezone付き`occurred_from`/`occurred_to`はnullable、既定nullで、
半開区間**`occurred_from <= occurred_at < occurred_to`**です。
省略/null側は無制限ですが、同一/逆転境界は不正です。
strict整数`max_items`は1〜100、既定20でbool/floatを拒否します。
`before`は既定nullで、closedな`EpisodeCursor`はtimezone付き`recorded_at`とUUID `memory_id`が必須です。
scope重複、naive timestamp、不正UUID/range、追加fieldは**422 `invalid_request`**です。
scope/time filterはLIMIT前に適用します。
source identityはHMAC anchorで平文query fieldではないため、`source_namespace` filterはありません。
time filterは`as_of`、`known_at`、過去ACL、二時点再構成ではありません。

信頼した`PGAG_SDK_API_URL`/`PGAG_SDK_API_TOKEN`で設定したopen済み`AsyncMemoryClient`内で、
`pg_agmemory.models`から`QueryEpisodes`と`Explain`をimportし、
`request = QueryEpisodes.model_validate(data)`、
`page = await memory.query_episodes(request)`と呼びます。
明示継続では意図したscope/time境界を保ち、新しい`QueryEpisodes`の`before`に
`page.next_cursor`を渡し、nullで停止します。再度nullを送ると終端継続ではなく最新から再開します。
返されたepisodeを**明示選択した後**、
`await memory.explain(Explain(memory_id=chosen_id, revision=1))`を呼びます。
その後に意図する場合だけ、既存のliteral根拠とidempotency-key契約で`Remember`を明示します。
新GET/get-episode method、自動選択、ingestion、synthesis、抽出、compactionは追加しません。
`MemoryClientError as exc`をcatchしてsanitizedな`exc.error.code`を確認し、
raw検証エラー、token、source content、ID、応答をlogに残さないでください。
read応答消失は`outcome_unknown: false`でmutation結果不確実ではありません。
明示再照会は変化したdataを返し得ます。transport errorは空pageではありません。

**所有権filterはありません**。所有job照会と異なり、現在tenant/scope RLSの下で
読取り可能な共有scopeのepisodeも含みます。
未知/非公開/別tenant scopeは行を寄与しません。一致なしは次のshapeで、
epochは例示値であり、defaultや非公開scopeの説明ではありません。

```json
{
  "episodes": [],
  "next_cursor": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

正確な**200 `EpisodePage`**は最大100件の`EpisodeSummary`を含み、
各itemは`memory_id`、`revision: 1`、`scope_id`、`occurred_at`、`recorded_at`です。
pageには`next_cursor`と`consistency`（`access_epoch`、`deletion_epoch`）もあります。
body/content、consent reference、source URI、`source_namespace`、event ID、
job payload、total countは返しません。ID/timestampもアクセス制御されたmetadataで、
Explain contentは根拠であり信頼する指示や現在の真実ではありません。
SQLは`episode` + `object`のmetadataだけを読み、auditや他application書込みを作りません。
各pageで現在ACL/purge検査とtenant response-delivery/drain barrierを維持し、
後続Explainはアクセス/purge変更で失敗し得ます。

objectの`created_at`を使う**`recorded_at DESC, memory_id DESC`**順で、
`occurred_at`順ではなく、遅れて受付した過去eventが先頭に現れ得ます。
exclusive境界は`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`です。
`max_items + 1`件を取得し、overflowの場合だけ最後に返すitemからcursorを作ります。
署名なしcursorは位置であり、権限、snapshot、receipt、event sequence、
compaction watermark、cache、retention objectではありません。
既存objectは不要で、削除済み/偽造位置も現在認可された行を限定するだけです。
古い境界より新しいrecorded行には`before`なしの明示再開が必要です。
SDKは`mutation=False`、call時再検証、通常の**request 256 KiB / response 2 MiB**上限を使い、
自動paging/retryやprovider呼出しはありません。
Native/SDKは31 resource methodで、MCPの4 toolとclosed hookは不変です。
[契約](../STATUS-jp.md#episode-query-and-pagination)、
[ADR 0023](../adr/0023-episode-query-jp.md)、
[適格性確認の証拠](../STATUS-jp.md#v0023--schema-10)を参照してください。

## Explicit batch capture

以下の受付/replay例にも、現在scope認可後の
[scope capture policy](#scope-capture-administration)を適用します。
policy変更後は完全一致replayも拒否され得ます。

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
`POST /v1/captures/batch`はNative JWTとcaller所有の`Idempotency-Key`を要求します。
許可済みsynthetic dataと現在認可されたscopeを使います。
下記を`batch-capture.json`へ保存し、例のscope UUIDを認可されたものへ置換してください。
送信前にこの正確なbody、安定したsource-event identity、caller所有keyを安全に保持します。
token、request body、返されたprivate IDをlogに出さないでください。

```json
{
  "episode": {
    "scope_id": "11111111-1111-4111-8111-111111111111",
    "source_namespace": "batch-capture-demo",
    "source_event_id": "batch-capture-demo-001",
    "occurred_at": "2026-09-17T00:00:00Z",
    "content": "Demo project is Gold. Demo owner is Example.",
    "consent_reference": "operator-approved-demo-consent"
  },
  "memories": [
    {
      "subject": "Demo project",
      "predicate": "tier",
      "value": "Gold",
      "evidence_quote": "Demo project is Gold.",
      "explicit_intent": true
    },
    {
      "subject": "Demo project",
      "predicate": "owner",
      "value": "Example",
      "evidence_quote": "Demo owner is Example.",
      "explicit_intent": true
    }
  ]
}
```

closedな`CaptureBatch(episode: Observe, memories: list[CapturedMemory])`は
proposal 1〜16件を受け付けます。既存field/time/quote上限を適用し、
各proposalはこのepisode内の一つのliteral quoteを使い、scope/evidence/identityをoverrideしません。
正規化model JSONで重複するcandidateはtrimで同一になるものを含め
**422 `invalid_request`**です。上例の同じsubjectで異なるpredicateは別の明示intentであり、
自動抽出した事実ではありません。

対応SDKを導入し、信頼する`PGAG_SDK_API_URL`、`PGAG_SDK_API_TOKEN`、
保持済みの`PGAG_BATCH_CAPTURE_KEY`を指定します。

```python
import asyncio
import os
from pathlib import Path

from pg_agmemory.models import CaptureBatch, CaptureBatchResult
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


async def capture() -> CaptureBatchResult:
    request = CaptureBatch.model_validate_json(
        Path("batch-capture.json").read_text(encoding="utf-8")
    )
    key = os.environ["PGAG_BATCH_CAPTURE_KEY"]
    async with AsyncMemoryClient(
        os.environ["PGAG_SDK_API_URL"], os.environ["PGAG_SDK_API_TOKEN"]
    ) as memory:
        try:
            return await memory.capture_batch(request, idempotency_key=key)
        except MemoryClientError as exc:
            if exc.error.outcome_unknown:
                print("Outcome unknown: reconcile with the original key and body.")
            raise


receipt = asyncio.run(capture())
```

`receipt`を安全に保存してください。正確な**201**の`CaptureBatchResult`は
episode UUIDの`memory_id`、1の`revision`、request順のjob UUID 1〜16件の
`synthesis_job_ids`を返します。assertion IDや集約job statusは返しません。
各IDの`get_job`か現在caller所有の`query_jobs`を使い、取消/retryはjobごとに明示します。
worker公開は独立し、実行順や原子的batch完了は保証しません。
group取消や自動pollはありません。

受付は既存の一つのtenant transaction/barrierで全新規
episode/lexical/job/identity/receipt/audit書込みをcommitします。
後方candidateの不正quote、途中でのscope当たりactive job 100件の残quota枯渇、
audit失敗は新規受付全体をrollbackし、既存行は変更しません。
再利用jobは新規job quotaを消費せず、16件はrequest上限で新しいglobal quotaではありません。
同じsource/intentのfresh keyはcancelled/failed/succeededでも元IDを再利用し、復活させません。
fresh keyでの並べ替えは並べ替えた既存IDを返し、同じkeyでの並べ替えは**409**です。
replayは現在write権限と全job livenessを確認し、sourceまたは一つのjobのpurge後は
receipt全体が**404**で、部分replayや復活はありません。
source purgeは依存job/assertionへの既存closureを維持します。

SDKは`mutation=True`、call時strict検証、通常の**request 256 KiB / response 2 MiB**上限を使い、
自動retryや分割は行いません。個別に有効な大きいentry 16件でもNative body上限（**413**）を超え得ます。
応答消失後は元key/bodyを保持し、実行中mutationの取消はrollbackを証明しません。
context exitは接続を閉じるだけで、保存済みdataを消しません。
LLM/provider、抽出、自動capture、公開品質の主張はありません。
別の単一job `/v1/captures`と`atomic_capture`は不変です。
Native/SDKは31 resourceで、MCPの4 toolとclosed hookにbatch actionは追加しません。
[全契約](../STATUS-jp.md#explicit-batch-capture)、
[ADR 0022](../adr/0022-batch-capture-jp.md)、
[検証状況と証拠](../STATUS-jp.md#v0022--schema-10)を参照してください。

## Exact entity query and pagination

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
Native JWT認証と現在のread権限を持つ信頼済みscope UUIDを使います。
read-only `POST /v1/entities/query`にwrite権限や`Idempotency-Key`は不要です。
次のsyntheticな最初のpage requestは両方の完全一致filterを使います。
文書例をlive dataへ実行しないでください。

```json
{
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "entity_type": "project",
  "canonical_label": "synthetic-entity-example",
  "max_items": 20,
  "before": null
}
```

closedな`QueryEntities`は重複しない`scope_ids`を1〜32件要求し、
任意の`entity_type`は既存8種の`EntityType` literal、
`canonical_label`はnullableな`ShortText`（通常の空白除去後1〜256文字）です。
両filterの既定はnullで、**LIMIT前にAND**で組み合わせ、
labelは`C` collationで大文字小文字を区別する完全一致です。
filter省略/nullは要求scope内の現在読取り可能な全entityを対象にします。
空/空白だけのlabelは無filter queryではなく`422 invalid_request`です。
alias、fuzzy/substring/wildcard/Unicode正規化match、embedding照会、
merge、自動identity選択はありません。
`max_items`はstrict整数1〜100、既定20で、`before`の既定はnullです。
closedな`EntityCursor`はtimezone付き`recorded_at`とUUID `memory_id`が必須です。
不正UUID/timestamp/範囲、scope重複、`max_items`へのboolean/float、
未知fieldは`422 invalid_request`です。

既に開いた信頼済み`AsyncMemoryClient`内で、`pg_agmemory.models`から`QueryEntities`をimportし、
`request = QueryEntities.model_validate(data)`を作り、
`page = await memory.query_entities(request)`を呼びます。
callerが継続を選ぶ場合は意図したscope/filterを維持し、新しい`QueryEntities`の`before`へ
`page.next_cursor`を渡し、nullで停止します。再びnullを渡すと最新から再開し、終端の続きではありません。
同じlabelの結果も別IDです。最初のitemを解決済みidentityとして選ばないでください。
candidateを明示選択後、`await memory.get_entity(chosen_id)`で既存`EntityDetail`の根拠を確認し、
graph seedを明示選択します。query自体はidentity解決もgraph展開もしません。
`MemoryClientError as exc`を捕捉し、sanitizedな`exc.error.code`を確認します。
raw validation error、label、source data、token、responseをlogへ出さないでください。

所有job照会と異なり、**所有権filterはありません**。
現在tenant/scope/source RLSで読取り可能な共有scopeのentityも含みます。
未知/非公開scopeは行を返しません。一致なしは次の形式で、
epochは例示値であり、既定値や非公開scopeの説明ではありません。

```json
{
  "entities": [],
  "next_cursor": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

正確な`200 EntityPage`は既存`EntitySummary`を最大100件含みます。
fieldは`memory_id`、`revision: 1`、`scope_id`、`entity_type`、`canonical_label`、
`recorded_at`で、pageに`next_cursor`と`consistency`
（`access_epoch`、`deletion_epoch`）を持ちます。evidence quote、source ID、total countはありません。
SQLはquoteを取得せずmetadata/件数を選びますが、labelは人間のtextであり、
内容を含まないdataや信頼済み指示/現在の真実ではありません。
選択して返すitemの`entity_evidence JOIN episode`による可視根拠件数は
保存済み`reference_count`と一致する必要があります。
不一致は`409 entity_invalidated`で**全page拒否**とし、黙ったskipや部分成功にしません。
各pageに最新のアクセス/source/削除guardと現在のtenant response-delivery/drain barrierを適用します。

順序は`recorded_at DESC, memory_id DESC`で、`recorded_at`はobjectの`created_at`です。
source event timeではありません。exclusiveな境界は
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`です。
`max_items + 1`件を取得し、overflowの場合だけlookaheadでなく最後に返すitemからcursorを作ります。
cursorは署名なしの透明な位置であり、権限、snapshot、receipt、cache、retention objectではなく、
既存objectも不要です。古い/削除済み/偽造位置も現在認可された行を限定するだけです。
page構成はアクセス/source/削除で変わり得ます。
古い境界より新しいentityには`before`なしの明示再開が必要です。
SDKは`mutation=False`、通常の**request 256 KiB / response 2 MiB**上限、
read-only `outcome_unknown: false`を使い、自動pagination/retry、
mutation、provider呼出し、自動identity選択はありません。
Native/SDKは31 resource methodで、MCPの4 toolとclosed hookは不変です。
[契約](../STATUS-jp.md#exact-entity-query-and-pagination)、
[ADR 0021](../adr/0021-entity-query-jp.md)、[過去のv21証拠](../STATUS-jp.md#v0021--schema-10)を参照してください。

## Assertion metadata history

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
Native JWT認証とassertionへの現在のread権限を使います。
read-only `POST /v1/assertions/history`にはwrite権限も`Idempotency-Key`も不要です。
取得した指示でなく信頼済みassertion UUIDを使ってください。
以下は使い捨てdata向けの例示requestで、live data用commandではありません。

```json
{
  "memory_id": "22222222-2222-4222-8222-222222222222",
  "max_items": 20,
  "before_revision": null
}
```

closedな`AssertionHistory`は必須UUID `memory_id`、strict整数`max_items`
（1〜100、既定20）、strict整数`before_revision`（1〜1001）またはnull（既定）だけです。
boolean/float、不正UUID/範囲、追加fieldは`422 invalid_request`です。
`as_of`、`known_at`、identity/scope override、過去ACL、full-value selector、watchは受け付けません。
通常assertionとcanonical relation assertionが対象で、
不在/非公開/kind違いは空応答でなく`404 not_found`です。

既に開いた信頼済み`AsyncMemoryClient`内で、`pg_agmemory.models`から
`AssertionHistory`と`Explain`をimportし、
`request = AssertionHistory.model_validate(data)`を作り、
`page = await memory.get_assertion_history(request)`を呼びます。
callerが継続を選ぶ場合は`memory_id`を維持し、
`next_before_revision`を次requestの`before_revision`へ渡します。
nullで停止し、再びnullを渡すと最新から再開します。
全内容には返されたordinalを明示選択し、
`await memory.explain(Explain(memory_id=page.memory_id, revision=chosen_revision))`を呼びます。
Explainのrevision省略は引き続き**latestでなく1**です。
両readともアクセス権を再検査し、historyは後続Explain結果を予約しません。
`MemoryClientError as exc`を捕捉し、sanitizedな`exc.error.code`を確認します。
raw validation error、private metadata、value、evidence、tokenをlogへ出さないでください。

正確な`200 AssertionHistoryPage`は`memory_id`、`scope_id`、`subject`、
`predicate`、`current_revision`、最大100件の`revisions`、nullableな
`next_before_revision`、`consistency`（`access_epoch`、`deletion_epoch`）を返します。
各revisionは`revision`、nullableな`valid_from`/`valid_to`、
`recorded_at`（system-time下限）、nullableな`known_until`（上限）、
nullableな`correction_reason`、`epistemic_status: "reported"`、
revision 1の正確なepisode `evidence_refs`（最大32件）、
`source_entity`/`target_entity`を持つ`relation`またはnullを含みます。
読取り可能なrevision-1 assertionより前の空page例は次の通りです。

```json
{
  "memory_id": "22222222-2222-4222-8222-222222222222",
  "scope_id": "11111111-1111-4111-8111-111111111111",
  "subject": "synthetic-history-example",
  "predicate": "status",
  "current_revision": 1,
  "revisions": [],
  "next_before_revision": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

この空page例には最初のrequestのnullでなく`before_revision: 1`が必要です。
epochは例示値で、既定値ではありません。読取り不可assertionがこの成功形式を返すことはありません。
SQLは上限付きmetadataだけを取得し、完全なvalue/evidence quoteは取得しませんが、
**subject、predicate、correction reasonは人間のtextであり、内容を含まないdataではありません。**
metadataをevidenceとして扱い、信頼済み指示や現在の事実として扱わないでください。

revisionは降順で、exclusiveな`revision < before_revision`を使います。
`max_items + 1`件を取得し、overflowの場合だけlookaheadでなく最後に返すordinalから位置を作ります。
`before_revision: 1`は空、`1001`は既存最大revision 1000までのheadを含みます。
ordinalは位置であり、権限、snapshot、receipt、cache、retention objectではありません。
page間の新revisionには位置なしの再開が必要で、以前のheadの`known_until`は閉じることがあります。
`current_revision`はCAS予約でも結果不明writeの証明でもありません。
元のmutationを元のidempotency key/bodyで照合し、
expected revisionの自動更新や置換keyの生成はしないでください。

各pageで現在ACL/source/削除検査と既存tenant response-delivery/drain barrierを適用します。
選択metadataの欠落/番号gap、または読取り可能な根拠がない場合は
`409 assertion_invalidated`で**全page失敗**となり、
relation endpoint不在は`409 relation_invalidated`です。部分成功、skip、fallbackはありません。
権限取消/非公開/不在/kind違いは引き続き汎用404です。
SDKは`mutation=False`、通常の**request 256 KiB / response 2 MiB**上限、
sanitizedなread-only `outcome_unknown: false`を使い、自動paging/retryはありません。
mutation、cache、provider呼出し、watch、保持cursorはありません。
Native/SDKは31 resource methodで、MCPの4 toolとclosed hookは不変です。
[契約](../STATUS-jp.md#assertion-metadata-history)、
[ADR 0020](../adr/0020-assertion-history-jp.md)、[過去の証拠](../STATUS-jp.md#v0020--schema-10)を参照してください。

## Owned-job query and pagination

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
Native JWT認証と現在read権限を使い、`POST /v1/jobs/query`にwrite権限や`Idempotency-Key`は不要です。
取得した指示でなく、信頼するscope UUIDを使ってください。
説明用の最初のpage requestは全状態を対象とし、文書例をlive dataへ実行してはいけません。

```json
{
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "states": [],
  "max_items": 20,
  "before": null
}
```

`QueryJobs`はclosedです。`scope_ids`は重複しないUUID 1〜32件、
`states`は重複しない`JobState`（`pending`、`running`、`succeeded`、`failed`、`cancelled`）の最大5件です。
空/省略statesは全5状態です。`max_items`はstrict整数1〜100で既定20です。
`before`はnull/省略で最新から、指定する場合はtimezone付き`created_at`とUUID `job_id`が
必須のclosedな`JobCursor`です。owner/principal/tenant/kind/payload filter、
offset、watch、`after`入力は受け付けません。不正field/値は`422 invalid_request`です。

開いた信頼する`AsyncMemoryClient`内で`pg_agmemory.models`から`QueryJobs`をimportし、
`request = QueryJobs.model_validate(data)`を構築して
`page = await memory.query_jobs(request)`を呼びます。結果は型付き`JobPage`です。
callerが次pageを選ぶ場合、意図したscope/stateを維持し、新しい`QueryJobs`の`before`へ
返された`next_cursor`を渡します。cursorがnullなら終了してください。
再びnullを送ると終了pageの続きでなく最新から再開します。
SDKの自動pagination/retryはありません。`MemoryClientError as exc`としてcatchし、
sanitizedな`exc.error.code`を確認します。model構築error、private job data、入力、token、応答をrawでlogに出してはいけません。

queryが発見するのはscope-admin権限がある場合も**現在callerの所有jobだけ**です。
既知ID指定GETは他principalの同一scopeの現在読取り可能jobを許可しますが、
その広い可視性をquery権限にしてはいけません。
tenant、要求scope、現在のRLS/source/削除可視性、任意state filterをすべて適用し、
未知/private scopeは結果へ寄与しません。次の空結果のepochは説明用の現在値で、非公開scope理由ではありません。

```json
{
  "jobs": [],
  "next_cursor": null,
  "consistency": {"access_epoch": 7, "deletion_epoch": 3}
}
```

完全な応答の`ListedJob` itemは既存の完全な`JobDetail`と`scope_id`で、
参照、state/時刻、安全なerror、`retry_of`、元のresult revision 1を含みます。
GET形式は不変で、保存payload、evidence quote、`lease_token`、intent digest、total countは返しません。
response modelは`jobs`を100件に制限し、SDKもこの上限を検証します。
選択された各page itemは`Jobs.get`の参照件数/result生存検査を通します。
実際に不正な選択jobがあれば既存`404 not_found` / `409 job_invalidated`でpage全体を失敗させ、
黙ってskipしたり部分dataを受け入れたりしてはいけません。

行順序は`created_at DESC, id DESC`で、継続はexclusiveな
`(created_at, id) < (before.created_at, before.job_id)`です。
`max_items + 1`の取得でoverflowを検出し、その場合だけlookahead行でなく
最後に返すitemから`next_cursor`を作ります。返すjobは最大`max_items`件です。
cursorは署名なしの位置情報で、権限、receipt、cache、snapshot、retention objectではありません。
そのjobは存在しなくてもよく、偽造/古い/削除済み位置も現在認可された所有jobを限定するだけです。
state変更でも元の`created_at`は維持しますが、state/アクセス/削除変更でpage間の構成は変わり得ます。
cursorより新しいjobには`before`なしで明示再開します。
`Consistency`は現在epochであり、構成固定、snapshot watermark、global LSNではありません。
pageごとに現在の権限/削除/tenant response-drain barrierを適用します。

SDKは`mutation=False`、通常の**request 256 KiB / response 2 MiB**上限、
read-only `outcome_unknown: false`を使います。state変更、claim、取消、
worker/provider呼出し、永続cursor、新safe error codeは追加しません。
**過去のv0.0.19 smokeだけの証拠です。** 実production SDK smokeはlocalと両native architectureで合格しました。
source + job三つ → pendingの最初の1件page → middle jobを明示取消 →
現在の次pageは最古jobだけ → cancelled jobを照会 →
source purge `object_count: 4` → pending照会が空、の順です。
取消は別の明示mutationであり、queryの副作用ではありません。
**Native/SDK resource methodは31**で、MCPの4 toolは不変、job toolやhook fieldはありません。
[契約](../STATUS-jp.md#owned-job-query-and-pagination)、
[ADR 0019](../adr/0019-job-query-jp.md)、
[過去の証拠](../STATUS-jp.md#v0019--schema-10)を参照してください。

## Checkpoint-head lookup

**既存checkpoint head契約を維持します。v0.0.26実装はlocal・native CI検証済みです。**
正確なscopeの現在のread権限を持つ既存Native JWT identityを使い、write権限は不要です。
次の`CheckpointBranch` bodyを`POST /v1/checkpoints/head`へ送り、`Idempotency-Key`は不要です。
説明用UUIDを既知のscope内run/branchへ置き換えてください。
文書レビュー中にlive DBへ例を実行してはいけません。

```json
{
  "scope_id": "11111111-1111-4111-8111-111111111111",
  "run_id": "22222222-2222-4222-8222-222222222222",
  "branch_id": "33333333-3333-4333-8333-333333333333"
}
```

全3 UUID fieldが必須で、modelは追加fieldを拒否します。
identity override、`expected_head`、harness selector、`as_of`、
branch横断のlatest selector、履歴一覧fieldを送ってはいけません。
開いた`AsyncMemoryClient`内で`pg_agmemory.models`から`CheckpointBranch`をimportし、
`branch = CheckpointBranch.model_validate(data)`を構築して
`head = await memory.get_checkpoint_head(branch)`を呼びます。
型付き結果は`CheckpointEnvelope`です。`MemoryClientError as exc`としてcatchし、`exc.error.code`でsanitized処理を行い、
private state、入力、token、応答をrawでlogに出してはいけません。
初回model構築errorにもprivate入力が含まれ得るためrawでlogに出してはいけません。

| 結果 | 扱い |
|---|---|
| `200` | 正確なbranchの照会時点headの完全な検査済みenvelope。実行指示ではない |
| `422 invalid_request` | UUID欠落/不正または追加request field。caller入力を修正 |
| `404 not_found` | 不在/非公開/異なるscope/別tenant/失効していない空branch、または非公開head。要求ID/内容や空成功はない |
| `409 checkpoint_invalidated` | 読取り可能な失効branch、既存envelope規則のrun/完全性拒否、または読んだscope/run/branch/sequenceの不一致。fallbackしない |

sourceの`forget`は対象branchを`invalidated=true`とし、canonical checkpoint/参照payloadを削除しますが、
opaqueな`head_id`と`sequence`、`memory.object` anchor、tombstoneは保持します。
照会は失効を先に確認し、まだ読取り可能な失効branchには409を返して保持head IDは返しません。
アクセス取消後は404でbranchを隠します。
失効を回避するためearlier ancestor、sibling、default branchを探してはいけません。
既知checkpoint ID指定GETは既存検査下で生存ancestorを読める場合がありますがlatestを特定しません。
restore先forkのtarget headは独立し、head照会はrestoreもeffect遷移も行いません。

同じenvelope helperでHMAC/state/参照と現在の認可を確認し、
runの`effects_invalidated`拒否を維持して、保存/現在epochと現在のrun全体の
`tool_effects`、`requires_reconciliation`、`untracked_effects`、`resume_allowed`、
`automatic_reexecution: false`を返します。
保存epochや`resume_allowed: true`は権限、承認、provider receiptではありません。
hostが継続を検討する前にeffectを確認・照合してください。
harness adapter、compaction機構、汎用復旧の適格性確認ではありません。

checkpoint IDを失った後のhead照会用に、安定したscope/run/branch IDを保持します。
readはwatch、予約、後続保証ではありません。意図したwriteでは
`CreateCheckpoint.expected_head`（正確な現checkpoint ID、空branchだけnull）を明示選択します。
照会後に別writerがheadを進められるため、`409 checkpoint_head_conflict`には
自動retryでなく意図したwriteの再検討で対応します。
**head照会を結果不明mutationのcommit証明にしてはいけません。**
現在のreplay guard下で元mutationの元idempotency key/bodyを再送して
元receiptを取得し、その後headを確認します。
新key、ancestor fallback、自動restore/実行、暗黙の承認には進みません。

既存APIのtenant session-lock/drain barrier下で、`FOR UPDATE`なしのSQL `SELECT`を使います。
branch/run作成、state変更、audit event、idempotency receiptはありません。
SDK `_post`は`mutation=False`で、通常の**request 256 KiB / response 2 MiB**上限、
read-only `outcome_unknown: false`、自動retryなしを維持します。
**Native/SDK resource methodは31**となりますが、**MCP toolは4**のままで、
checkpoint toolやhook fieldは追加しません。
`checkpoint_invalidated`は既存SDK safe codeであり、新error codeではありません。
過去のv0.0.18 production SDK smokeはlocalと両native architectureで合格しました。
source + checkpoint二つ → head sequence 2 / 過去GET sequence 1 →
source purge `object_count: 3` → head `409 checkpoint_invalidated`をassertします。
[契約](../STATUS-jp.md#checkpoint-head-lookup)、
[ADR 0018](../adr/0018-checkpoint-head-jp.md)、
[検証済み証拠](../STATUS-jp.md#v0018--schema-10)を参照してください。

## Exact structured recall filters

**既存recall filter契約を維持します。v0.0.26実装はlocal・native CI検証済みです。**
既存の認証付き`POST /v1/recall`を使います。次の合成requestは、
認可済みscopeで保存subject/predicateが完全一致するassertionをbrowseします。
説明用UUIDをprovision済みscopeへ置き換え、
文書の例をlive DBへ実行してはいけません。

```json
{
  "query": "",
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "mode": "explicit",
  "retrieval_mode": "lexical",
  "filters": {
    "kind": "assertion",
    "subject": "Synthetic Project",
    "predicate": "uses"
  },
  "max_items": 4,
  "token_budget": 2000
}
```

開いた`AsyncMemoryClient`内で、`pg_agmemory.models`から`Recall`と`RecallFilters`をimportし、
`Recall.model_validate(data)`でparseして`await memory.recall(request)`を呼びます。
nested値は`RecallFilters(kind="assertion", subject="Synthetic Project", predicate="uses")`
としても構築できます。idempotency keyは不要です。
`MemoryClientError`はsanitized `exc.error.code`で処理し、
read-only errorの`outcome_unknown: false`、自動retryなし、raw応答/入力logなしを維持します。
初回model構築errorにはprivate入力が含まれ得るためrawでlogに出してはいけません。
MCPは同じobjectを`memory_recall`の`{request: <Recall>}` wrapperで受け付けます。
hookは`filters`を拒否し、内部既定は`None`です。

`Recall.filters`は既定`None`です。closedな`RecallFilters`のfieldはnullableな
`kind`（`"episode"`/`"assertion"`）、`subject`（`ShortText`、既存の前後空白除去後1〜256文字）、
`predicate`（`^[a-z][a-z0-9_]{0,63}$`）だけで、各既定はnullです。
省略/null/`{}`/全field nullなら全3 retrieval modeの既存結果を維持します。
non-null fieldをANDで結合します。subject/predicateはrelationを含むassertion候補を選び、
`"episode"`は全assertionを除外して、これらのfieldとは組み合わせられません。
通常のstring trim後、`C` collationで大文字小文字を区別して完全一致比較します。
`"Synthetic Project"`と`"synthetic project"`は異なり、
部分文字列、FTS、Unicode正規化、fuzzy一致、alias、entity解決はありません。
range、array形式filter、推論selector、任意SQLを使ってはいけません。

空lexical queryはfilter後の候補をbrowseし、通常の非空lexical queryには一致が必要です。
filterはqueryを迂回しません。top-K選択後でなく、共有materialized候補の内部で
lexical/vector/hybridのrankingとcoverageより前に適用します。
vector query/model規則は不変です。現在RLS、要求scope、固定`as_of`/`known_at`、
根拠/削除gateを維持します。lexical/vector欠落はfilter後の適格候補集合を対象とし、
`coverage.jobs_pending`は構造化job一致でなくscope単位を維持します。
required参照は別契約のlexical専用keyword迂回、**最新でなく1**の既定revision、
request順を維持しますが、filterにも一致が必要です。

| 結果 | 意味 |
|---|---|
| `422 invalid_request` | 不正/未知のfilter field、値、形式。episode kindとnon-null subject/predicateの組合せも対象 |
| `200`, `empty_reason: "not_found"` | 適格なfilter一致なし。既存coverage/予算規則を適用 |
| `404 not_found` | 正確なrequired参照がfilter/適格性を満たさず、IDや部分packのないrequest全体の失敗 |
| `422 budget_exhausted` | 既存の全required prefix予算失敗。filterで弱めない |

compact `ContextPack`全体のUTF-8 byte予算、implicit 2,000-byte上限、
`budget_too_small`、optional-onlyの成功時の空理由`budget_exhausted`は不変です。
safe error code、route、SDK method、write、provider、index、永続priority、cacheは追加しません。
memoryは根拠であり、検証済み真実ではありません。
[契約](../STATUS-jp.md#exact-structured-recall-filters)、
[ADR 0017](../adr/0017-recall-filters-jp.md)、
[検証済み証拠](../STATUS-jp.md#v0017--schema-10)を参照してください。

## Required-context recall

**既存required-context契約を維持し、v0.0.26実装はlocal・native CI検証済みです。**
現在読取り可能なepisode/assertionから正確な参照を選び、
policy権限や承認を主張する信頼できないtextからは選ばないでください。
説明用のopaque IDを要求scope内の既存IDに置き換えます。
文書レビュー中にlive DBへ例を実行してはいけません。
認証付き`POST /v1/recall`は次の例を使えます。

```json
{
  "query": "handoff",
  "scope_ids": ["11111111-1111-4111-8111-111111111111"],
  "mode": "explicit",
  "retrieval_mode": "lexical",
  "search_profile": "simple-v1",
  "required_memory_refs": [
    {"memory_id": "22222222-2222-4222-8222-222222222222", "revision": 1}
  ],
  "max_items": 4,
  "token_budget": 2000
}
```

recallはread-onlyのため`Idempotency-Key`は不要です。
既存の開いた`AsyncMemoryClient` context内で、
`pg_agmemory.models`の`Recall.model_validate(data)`を使ってobjectをparseし、
`await memory.recall(request)`を呼びます。
`MemoryClientError`はsanitized `exc.error.code`で処理し、raw応答/入力をlogへ出しません。
`budget_exhausted`はrequired context全体が収まらないことを示し、
`outcome_unknown`はfalse、自動retryはありません。
要件を黙って除外せず、予算/参照を明示的に再検討します。SDK methodの追加はありません。
MCPでは同じobjectを`memory_recall`の`{request: <Recall>}` wrapperで使います。

参照は最大**16**件、revision違いでもmemory IDは一意で、件数は`max_items`以下です。
revisionは**1〜1000**、既定は**最新でなく1**です。
空でない参照はlexical専用で、explicit/implicit両方に対応しますが、
既存implicit **2,000-byte**上限を維持します。
`[]`または省略なら選択した構造化filter内でlexical/vector/hybridの既存動作を維持します。
required IDが迂回するのはquery一致とranking cutoffだけです。
同じ要求scope、現在ACL、固定`as_of`/`known_at`、revision適格性、
[構造化filter](#exact-structured-recall-filters)を適用します。
正確な参照が一つでも利用不可ならrequest全体を汎用**404 `not_found`**とし、
欠落参照名や部分contextは返しません。

required item全体をrequest順で先頭に置き、required IDを除いた通常のlexical順位の
optional itemを続けます。両方がitem上限と、quote/引用/warning markerを含む
**compact `ContextPack`全体のUTF-8 byte予算**を使います。
optional overflow/除外は引き続き`coverage.truncated`を設定します。
`simple-v1`と`ja-janome-0.5.0-v1`の両方で正確なcanonical参照を扱い、
日本語projection欠落時は`lexical_incomplete`を維持して自動修復しません。

| 結果 | 意味 |
|---|---|
| `422 invalid_request` | 不正な組合せ/件数/重複ID/参照形式。vector/hybridで空でない参照も対象 |
| `404 not_found` | 正確な参照の一つ以上が、適格で現在読取り可能な要求scope内候補でない |
| `422 budget_exhausted` | required item全体が収まらず、空/部分contextの成功にはしない |
| `422 budget_too_small` | required参照なしでの既存empty-envelope失敗 |
| `200`, `empty_reason: "budget_exhausted"` | 既存optional-onlyの空結果成功。新errorではない |

required recallは予算64で空envelopeも収まらない場合でも`budget_exhausted`になり得ます。
`token_budget`を正確なmodel token数と見なしてはいけません。
SDKとMCPは新safe codeを伝播します。
hookは`required_memory_refs`を追加入力fieldとして拒否し、内部`Recall`は`[]`のため、
通常のoptional-only予算/coverage動作と3 eventは不変です。
host pinning、永続priority、write、推論、provider呼出し、cacheは追加しません。
選択memoryは根拠であり、信頼する指示、承認、現在事実の保証ではありません。
[契約](../STATUS-jp.md#required-context-recall)と
[ADR 0016](../adr/0016-required-context-jp.md)を参照してください。

## Schema 10 application-only upgrade

**過去v24→v25/schema 10だけの手順と証拠です。**
現在v26には[schema 11 migration](#schema-11-scope-capture-policy-upgrade)を使います。
以下の過去記録はv26の適格性や異なるschema writerの混在を認めるものではありません。

**v0.0.25実装はlocal・native CI検証済みです。** v24→v25はschema 10を維持し、**migrationは追加しません**。
application版へ合わせるだけの目的で新schema版を適用しないでください。

1. replica/restartを含む旧API、worker、SDK caller、MCP adapter、hook/推論起動、
   管理commandを停止/drainします。backupと現在ACL/削除記録を保全し、
   rolling/混在版互換性は主張しません。
2. 固定PostgreSQL **18.6** / **`public`内の`vector` 0.8.6** imageと、
   厳密なmigration履歴`[1,2,3,4,5,6,7,8,9,10]`を維持します。
   既存schema 10 DBにこのmilestone用DDL/backfillは不要です。
   古いschemaには停止中に[010までの既存migration sequence](#schema-10-job-cancellation-upgrade)を適用します。
3. 対応v0.0.25 API/worker/SDK/MCP/hook componentだけを起動します。
   全adapter handshakeは**service 0.0.25 / API v1 / schema 10**を要求し、
   startup/readinessは厳密な履歴とrole/extension検査を維持します。
4. 認証付きstage `m2-selectable-inference`、feature `optional_provider_adapters`、
   [正確な`model_inference` metadata](../STATUS-jp.md#deployment-and-qualification-boundary)を確認します。
   synthetic契約の適格性確認と記録した正確なprofileのlive試行後も、
   `live_provider_qualified`は`false`のままです。
   Native episode/capture/batchと他resourceの契約は維持します。
   必要な場所だけに任意provider extraを導入し、各profileを別途承認します。
   synthetic HTTP/SQL guardと公開しない明示推論を予行し、SQL `inspect`を
   live modelやMemoryDB hostingの適格性確認と見なさないでください。
   readinessだけでは不十分で、下記実装の証拠は本番配置の適格性確認ではありません。

Native/SDK resource method 31、MCP tool 4を維持し、hookはfilterもrequired参照も受け付けません。
依存versionやMemoryDB imageのupgradeはなく、provider実行は別のoperator library/CLIに限定します。
**v25 local適格性確認全体とnative CIは合格です。**
Apple Container `./scripts/test-containers.sh`は
**999合格、live skip 5件、既知warning 1件、494.49秒**でした。
Ruff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入smoke、
**新system CA smokeを含む全production smoke**も合格しました。Azure推論は再実行していません。
実装
[`adbead0ab42dfa4a5465f9464d3855c5f64d85c1`](https://github.com/rioriost/pg_agmemory/commit/adbead0ab42dfa4a5465f9464d3855c5f64d85c1)
の完全一致SHAで[CI 35303758871](https://github.com/rioriost/pg_agmemory/actions/runs/35303758871)が合格しました。
**amd64 999合格、live skip 5件、warning 1件 / 600.77秒、
arm64 999合格、live skip 5件、warning 1件 / 797.75秒**です。
両native jobでRuff、mypy **22+1**、全4導入profile、
system CAを含む全production smokeも合格しました。
実装CIの証拠であり、後続の最終文書commitの適格性確認を主張するものではありません。
stage `m2-selectable-inference`、`auto_synthesis: false`、
globalな`live_provider_qualified: false`は不変です。
[ADR 0025](../adr/0025-live-provider-qualification-jp.md)を参照してください。

**過去のv24実装とsynthetic provider契約は適格性確認済みです。**
実装
[`88975a862ff97873c60e5ce53e066e1aa7b52686`](https://github.com/rioriost/pg_agmemory/commit/88975a862ff97873c60e5ce53e066e1aa7b52686)は
Apple Containerのfull `./scripts/test-containers.sh`で**987合格、warning 1件、493.68秒**でした。
完全一致SHAの[CI 35292285229](https://github.com/rioriost/pg_agmemory/actions/runs/35292285229)は
**amd64 987合格 / 762.27秒、arm64 987合格 / 809.06秒**でした。
全3環境でRuff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入profile、全production smokeも合格しました。
新smokeはsynthetic HTTPに実operator CLIを接続し、その後にNative vector upload/replay/purgeを明示します。
**987 = 既存821 + 新規166 case**です。
SQL testはsynthetic function/extension所属を使い、Azure extension binaryではありません。
live Azure/実model呼出し、課金resource、private dataの外部送信は使っていません。
live互換性、品質、M2完了の証明ではありません。
[適格性確認の証拠](../STATUS-jp.md#v0024--schema-10)を参照してください。
この更新の最終docs CIはまだ実行していません。

**過去のv0.0.23実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`bf53a30625ffcfb0f23f986abcec5e2d608dcb68`](https://github.com/rioriost/pg_agmemory/commit/bf53a30625ffcfb0f23f986abcec5e2d608dcb68)は
Apple Containerのfull `./scripts/test-containers.sh`で**821合格、warning 1件、499.94秒**でした。
完全一致SHAの[CI 35280254044](https://github.com/rioriost/pg_agmemory/actions/runs/35280254044)は、
amd64 **821合格、warning 1件、821.33秒**、arm64 **821合格、warning 1件、823.87秒**でした。
全3環境でRuff、mypy **source 19 + strict SDK consumer 1ファイル**、
全optional導入検査、episode照会を含む全production smokeも合格しました。
**821 = 既存780 + 新規41 case**です。
[適格性確認の証拠](../STATUS-jp.md#v0023--schema-10)を参照してください。
別の最終v23 docs
[`9bc5092e5919997abb945554ec8363e4bf0e6dae`](https://github.com/rioriost/pg_agmemory/commit/9bc5092e5919997abb945554ec8363e4bf0e6dae)の
[CI 35282317544](https://github.com/rioriost/pg_agmemory/actions/runs/35282317544)はattempt 2で成功し、
**各821 case、amd64 851.59秒 / arm64 802.42秒**でした。
attempt 1のamd64 Docker Hub認証接続resetは**テスト前**で、
product code変更なしに失敗jobだけを再試行しました。
この最終docs証拠は実装CIと別で、v24の適格性確認ではありません。

**過去のv0.0.22のgraph修正と全方向の回帰は検証済みです。**
方向別追加検証の
[`3f56c51434333428fe742bb6a464d1d3117e8e26`](https://github.com/rioriost/pg_agmemory/commit/3f56c51434333428fe742bb6a464d1d3117e8e26)は
Apple Containerのfull `./scripts/test-containers.sh`で
**780合格、warning 1件、507.09秒**でした。
[CI 35271311062](https://github.com/rioriost/pg_agmemory/actions/runs/35271311062)は完全一致SHAで、
amd64 **780合格、warning 1件、819.23秒**、arm64 **780合格、warning 1件、745.25秒**でした。
全3環境でRuff、mypy **source 19 + strict SDK consumer 1ファイル**、
全optional導入検査、全production smokeも合格しました。
`auto`/`generic`/`nested_loop` × `outgoing`/`incoming`/`both`の9組が検証済みで、
**780 = batch基準773 + nested-loop case 1 + direction case 6**です。
この拡張は回帰coverageの変更であり、product SQL、version、schemaは不変です。
別の最終v22 docs
[`36dc19d8897db6768badaf39019445e2a22d23da`](https://github.com/rioriost/pg_agmemory/commit/36dc19d8897db6768badaf39019445e2a22d23da)は
[CI 35273848787](https://github.com/rioriost/pg_agmemory/actions/runs/35273848787)に合格しました。
**各780 case、amd64 862.46秒 / arm64 730.09秒**で、全検査/smokeも合格しました。
この最終docs runは方向別実装runと別で、v23の適格性確認ではありません。

**以前の774件のgraph修正適格性確認:**
修正[`bf7429327955071239fdc2f7b60d1a5d47dfff7e`](https://github.com/rioriost/pg_agmemory/commit/bf7429327955071239fdc2f7b60d1a5d47dfff7e)は
Apple Containerのfull suiteで**774合格、445.86秒**、全smokeも合格しました。
[CI 35268438022](https://github.com/rioriost/pg_agmemory/actions/runs/35268438022)は完全一致SHAで、
amd64 **774合格、779.45秒**、arm64 **774合格、682.78秒**でした。
両方でRuff、mypy **source 19 + strict SDK consumer 1ファイル**、
全導入検査、全production smokeも合格しました。
以前のtest image内の未変更の修正前sourceに対するnegative controlは期待通り失敗しました。
保持したsynthetic planは失敗runnerのplanではありません。

最終docs revision
[`af91974d091feb276db79baf838c7fa2bab8904f`](https://github.com/rioriost/pg_agmemory/commit/af91974d091feb276db79baf838c7fa2bab8904f)の
[CI 35265233011](https://github.com/rioriost/pg_agmemory/actions/runs/35265233011)は**失敗**しました。
amd64は既存100-path auto-plan graph caseの**503 `QueryCanceled`**で
**772合格、1失敗、631.34秒**、arm64は**773合格、701.79秒**で全smokeも合格しました。
実際の失敗CI planは**未取得**です。保持済みの使い捨てDB診断は
`adjacent` materialization後に残るcanonical metadataとendpointの反復scanを示します。
追加修正はこのCTEを維持し、scope/predicateで絞ったassertion、timeで絞ったrevision、
重複しない認可済み・根拠有効なendpointを別途materializeします。
事前計算ID arrayでsemijoin反転と保護されたcanonical dataの反復scanを防ぎ、
外側joinはmaterialize済みの認可metadataを使います。
RLS、scope/time/根拠検証、順序、上限、**5000 ms** timeoutは維持し、
timeout引上げや変更なしのcode再実行を修正とはしません。
方向別回帰拡張は全9組とscan loop検査を含めて検証済みです。
この診断は性能benchmarkや本番適格性確認ではありません。
version/API/schemaと30 resource methodは不変で、v0.0.22 handshake一致だけでは修正buildを識別できません。
初期batch build、canonical SQL修正、検証済み方向別revisionを区別するため、commit/image provenanceを追跡してください。

**初期v22実装の証拠であり、追加修正の適格性確認ではありません。**
Apple Containerのfull `./scripts/test-containers.sh`は
**773合格、既存warning 1件、447.40秒**でした。実装
[`75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe`](https://github.com/rioriost/pg_agmemory/commit/75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe)は
完全一致SHAの[CI 35262682028](https://github.com/rioriost/pg_agmemory/actions/runs/35262682028)に合格しました。
native amd64は**773合格、621.56秒**、arm64は**773合格、702.01秒**でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
従来の全production smokeとbatch captureのworker/replay/purgeが全3環境で合格しました。
初期結果は、その後のdocs CI失敗を覆すものでも追加修正の適格性確認でもありません。
[検証状況と証拠](../STATUS-jp.md#v0022--schema-10)を参照してください。

**過去のv0.0.21追加修正はlocalと両native architectureで検証済みです。**
修正[`956b232f38caeeb7d0421a2d6fd3d8340206bcbc`](https://github.com/rioriost/pg_agmemory/commit/956b232f38caeeb7d0421a2d6fd3d8340206bcbc)は
上限付きgraph隣接集合をmaterializeし、強制generic prepared planで再現した
relation revisionの反復scanを除去します。RLS、時刻/scope/根拠検証、順序、上限を維持し、
timeout引上げやJIT無効化は行いません。失敗したCIのplanは未取得で、
使い捨てfixtureによる診断は本番性能benchmarkではありません。
Apple Containerのfull `./scripts/test-containers.sh`は
**730合格、既存warning 1件、430.12秒**（**既存729 + generic-plan回帰1件**）でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
全production smokeも合格しました。
[CI 35257534254](https://github.com/rioriost/pg_agmemory/actions/runs/35257534254)は
この修正の完全一致SHAで、native amd64 **730合格、warning 1件、748.37秒**、
arm64 **730合格、warning 1件、654.52秒**でした。
両native runで同じ検査、optional導入、全production smokeも合格しました。
失敗したdocs revisionの再実行成功ではなく、新しいcode修正の適格性確認です。
別の最終v21 docs
[`8f60790e3309368a340f16a771ad44d285aaafde`](https://github.com/rioriost/pg_agmemory/commit/8f60790e3309368a340f16a771ad44d285aaafde)は
[CI 35259655219](https://github.com/rioriost/pg_agmemory/actions/runs/35259655219)に合格しました。
native amd64は**730 case、727.92秒**、arm64は**730 case、628.61秒**で、
全検査、optional導入、production smokeも合格しました。
この最終docs結果はcode修正CIと別で、v22の適格性確認ではありません。
version/schema/APIと29 resource methodは不変で、SQL migrationはありません。
version handshakeの一致だけでは修正済みbuildと以前のv21 buildを区別できないため、
applicationのcommit/image provenanceを追跡してください。

**以前のv21証拠であり、修正の適格性確認ではありません。** 初期実装
[`a34477d7511f22202f0bd981772f51408634a9af`](https://github.com/rioriost/pg_agmemory/commit/a34477d7511f22202f0bd981772f51408634a9af)は
完全一致SHAの[CI 35252290223](https://github.com/rioriost/pg_agmemory/actions/runs/35252290223)に合格しました。
localは**729合格、既存warning 1件、390.27秒**、
native amd64は**729合格、764.95秒**、arm64は**729合格、614.98秒**で、
全検査/導入/smokeも合格しました。その後のdocs revision
[`4d97e92ec08d845ebd2c969819a999e9f0fd6f83`](https://github.com/rioriost/pg_agmemory/commit/4d97e92ec08d845ebd2c969819a999e9f0fd6f83)の
[最終docs CI 35254318489](https://github.com/rioriost/pg_agmemory/actions/runs/35254318489)は**失敗**しました。
amd64は既存100-path graph上限テストの`QueryCanceled` / statement timeoutで
**728合格、1失敗、549.51秒**、arm64は**729合格、626.14秒**でした。
[検証状況と証拠](../STATUS-jp.md#v0021--schema-10)を参照してください。

**過去のv0.0.20実装はlocalと両native architectureで検証済みです。**
Apple Containerのfull `./scripts/test-containers.sh`は
**695合格、既存warning 1件、359.74秒**でした。実装
[`6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97`](https://github.com/rioriost/pg_agmemory/commit/6f03b69bd3317bbb4ed1c40982d3a3aa565bbe97)は
完全一致SHAの[CI 35247519977](https://github.com/rioriost/pg_agmemory/actions/runs/35247519977)に合格しました。
native amd64は**695合格、527.99秒**、arm64は**695合格、615.07秒**でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
従来の全production smokeとassertion historyが全3環境で合格しました。
別の最終v0.0.20 docs
[`c8977088d92f060c1b9f2594db6300f79cea3963`](https://github.com/rioriost/pg_agmemory/commit/c8977088d92f060c1b9f2594db6300f79cea3963)は
[CI 35249560753](https://github.com/rioriost/pg_agmemory/actions/runs/35249560753)に合格しました。
native各**695 case**、**amd64 660.52秒 / arm64 642.26秒**で、
従来のRuff/mypy **19 + 1**、全optional導入、全production smokeも合格しました。
docs所要時間は実装CI 35247519977とは別で、両runともv0.0.21の適格性確認ではありません。
[過去の証拠](../STATUS-jp.md#v0020--schema-10)を参照してください。

**過去のv0.0.19実装はlocalと両native architectureで検証済みです。**
Apple Containerの`./scripts/test-containers.sh`は**exit 0**で、
**663合格、既存warning 1件、366.14秒（6:06）**でした。実装
[`e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf`](https://github.com/rioriost/pg_agmemory/commit/e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf)は
完全一致SHAの[CI 35242118110](https://github.com/rioriost/pg_agmemory/actions/runs/35242118110)に合格しました。
native実logで各**663合格、warning 1件**、**amd64 638.06秒 / arm64 586.58秒**を確認しました。
Ruff、strict mypy **source 19 + SDK consumer 1ファイル**、真のcore/hook/sdk-only導入、
所有job照会を含む全non-root production smokeが全3環境で合格しました。
別の最終v0.0.19 docs
[`12b7628af6ad19b1073600b27a713e8c52789bec`](https://github.com/rioriost/pg_agmemory/commit/12b7628af6ad19b1073600b27a713e8c52789bec)は
[CI 35244626331](https://github.com/rioriost/pg_agmemory/actions/runs/35244626331)に合格しました。
native各**663テスト**、**amd64 554.26秒 / arm64 601.49秒**で、
従来のRuff/mypy **19 + 1**、全optional導入、全production smokeも合格しました。
docs所要時間は実装CI 35242118110とは別で、両runともv0.0.20の適格性確認ではありません。
[過去の証拠](../STATUS-jp.md#v0019--schema-10)を参照してください。

**過去のv0.0.18実装はlocalと両native architectureで検証済みです。** Apple Containerの
`./scripts/test-containers.sh`は**exit 0**で、**630合格、既存warning 1件、
369.39秒（6:09）**でした。Ruff、strict mypy **source 19 + SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、上記checkpoint headの新sequenceを含む全non-root production smokeが全3環境で合格しました。
実装
[`4accd38408a8384b4376f6250d953bb2fa480ec8`](https://github.com/rioriost/pg_agmemory/commit/4accd38408a8384b4376f6250d953bb2fa480ec8)
は[CI 35235315016](https://github.com/rioriost/pg_agmemory/actions/runs/35235315016)で
両native Docker architectureとも合格しました。実logで各**630合格、warning 1件**、
**amd64 663.91秒 / arm64 544.14秒**を確認しました。
別の最終v0.0.18 docs
[`3f01faf56563202c40a00d73cee72830022220b1`](https://github.com/rioriost/pg_agmemory/commit/3f01faf56563202c40a00d73cee72830022220b1)は
[CI 35237907862](https://github.com/rioriost/pg_agmemory/actions/runs/35237907862)に合格しました。
各native architecture **630テスト、warning 1件**、**amd64 644.25秒 / arm64 570.20秒**で、
Ruff、mypy **19 + 1**、optional導入、全production smokeが合格しました。
docs所要時間は実装CI 35235315016とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](../STATUS-jp.md#v0018--schema-10)を参照してください。

**過去のv0.0.17実装証拠だけを記録します。**
Apple Containerと両native Docker実行は各**604テスト、既存warning 1件**で合格しました。
localは**321.56秒（5:21）**、amd64は**638.66秒**、arm64は**544.33秒**でした。
[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)は完全一致の実装
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894)で合格しました。
全3環境でRuff、strict mypy **source 19ファイル + SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、全non-root production smokeが合格しました。
新SDK smokeはobserve + remember、`max_items: 1`でtruncationなしの3 field完全一致、
required source/assertion filter不一致の`404 not_found`とread-only `outcome_unknown: false`、
`object_count: 2`を返すsource purge、filter付き空readで合格しました。
別の最終v0.0.17 docs
[`5226c81fae7a50a5668109478d5d9f23fc4d7761`](https://github.com/rioriost/pg_agmemory/commit/5226c81fae7a50a5668109478d5d9f23fc4d7761)は
[CI 35232139680](https://github.com/rioriost/pg_agmemory/actions/runs/35232139680)に合格しました。
native amd64は**465.50秒**、arm64は**557.08秒**で、各**604テスト、warning 1件**、
Ruff、strict mypy **source 19 + consumer 1ファイル**、optional導入、全production smokeが合格しました。
docs所要時間は実装CI 35230044140とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](../STATUS-jp.md#v0017--schema-10)を参照してください。

**過去のv0.0.16実装証拠だけを記録します。**
local Apple Containerと両native Docker実行は各**566テスト、既存warning 1件**で合格しました。
localは**298.15秒（4:58）**、amd64は**601.73秒**、arm64は**565.34秒**でした。
[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)は実装
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57)で合格しました。
全3環境でRuff、strict mypy **source 19ファイル + SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、全non-root production smokeが合格しました。
required-context smokeはSDK後・job取消前に、専用sourceとoptionalな`Gold`一致、
`max_items: 1`とtruncationでのrequired query迂回、explicit予算64による
`budget_exhausted`とread-only `outcome_unknown: false`、
続くsource purgeの`object_count: 2`で合格しました。
既存smokeもすべて合格しました。別の最終v0.0.16 docs
[`520990d95718b17ae93a7d5259d600e379991df2`](https://github.com/rioriost/pg_agmemory/commit/520990d95718b17ae93a7d5259d600e379991df2)は
[CI 35226313891](https://github.com/rioriost/pg_agmemory/actions/runs/35226313891)に合格しました。
native amd64は**476.15秒**、arm64は**478.08秒**で、各**566テスト、warning 1件**、
全検査、optional導入、production smokeが合格しました。
docs所要時間は実装CI 35224189967とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](../STATUS-jp.md#v0016--schema-10)を参照してください。

## Explicit job cancellation

**既存job取消契約を維持し、v0.0.26実装はlocal・native CI検証済みです。**
Native JWT認証と、現在scope **read/write**権限を持つjob ownerのidentityを使います。
同一scopeの別readerは`admin` permissionがあっても他ownerのjobを取消できません。
source可視性/完全性とruntime RLSを維持します。
不在/private/cross-tenant/non-job/purge済みIDは404、参照失効は`409 job_invalidated`になり得ます。
取得memory本文からidentityを選ばないでください。

最初に`/v1/jobs/{job_id}`をGETしてから、**stateとattempt**を意識的に選びます。
pending、attempt 0と観測した承認済みjobの正確なrequest bodyは次のとおりです。

```json
{"expected_state":"pending","expected_attempt":0}
```

callerが安全に保持する`Idempotency-Key`とともに`/v1/jobs/{job_id}/cancel`へPOSTします。
runningは観測したattempt **1〜5**、pendingは**0〜5**を使います。
attemptはstrict整数でありboolean/文字列/暗黙変換値ではありません。
reason、provider、lease token、追加field、強制target stateは受け付けません。
このCASはtenant `access_epoch`や外部tool版ではありません。
**説明用requestであり、文書レビュー中にlive DBへ実行してはいけません。**

既にasync context内で開いている`AsyncMemoryClient`から、`pg_agmemory.models`の`CancelJob`をimportし、
`await memory.cancel_job(job_id, CancelJob(expected_state="pending", expected_attempt=0), idempotency_key=cancel_key)`
を使います。例の値は明示レビューしたsnapshotと一致させ、request/keyを自動生成し直しません。
call時のmodel/key/UUID検証とsanitized SDK errorを適用します。
正確な**HTTP 200**（202ではない）で型付き`JobReceipt`を返します。
receiptは`job_id`、`kind: "structured_remember"`、`recipe_version: "structured-remember-v1"`を含み、
GETで`state: "cancelled"`を確認します。

### 競合と不明な応答

state/attempt不一致やterminal succeeded/failed/cancelledは`409 job_cancel_conflict`です。
cancelledへ新keyで再要求しても競合します。成功した同key/bodyは現在の
owner/write/access/liveness検査の下だけで元receiptを返します。
同じkeyでbody**またはjob ID**を変えると`409 idempotency_conflict`です。
SDKは`job_cancel_conflict`を認識します。
HTTP応答喪失/結果不明時は**同じkey/body**を保持して再送するか現在jobをGETします。
expected値やkeyを盲目的に更新しません。
Python taskのcancelは`cancel_job`を暗黙に呼びません。
進行中の変更はcommitされる可能性があり、rollbackの証明にもなりません。

既存tenantの**session advisory lockをHTTP配信完了まで保持**し、
worker claim/publicationも同じbarrierを使います。
runningのleaseは有効/期限切れのどちらも取消対象です。
取消が先ならそのjobからresultは公開されません。
古い準備処理が続いてもpublish/heartbeat/failはnot-runningを
`job_lease_conflict`として拒否し、workerは`lease_lost`を報告します。
publicationのcommitが先なら取消は競合し、succeeded resultを維持します。
worker kill、provider abort、compensation、公開撤回は保証しません。
data除去が目的なら明示`forget`を使います。

### 保持するもの

terminal cancelledは保存job payload、lease token/deadline、errorを消去し、resultはありません。
job ID、attempt、input/source/intent参照、retry parent、作成時刻は保持し、
DB `updated_at`で完了を記録します。private取消reasonは保存しません。
job遷移、`job_cancelled` audit、HMAC保護request/keyのidempotency receiptは
原子的にcommitするか全rollbackします。保存replay結果は`{job_id}`だけです。
access/deletion epochは進めません。DB制約/triggerはterminal復活/書換えを拒否し、
新jobは引き続きpending、attempt 0が必須です。

cancelledは100 pending/running上限と`jobs_pending`から除外します。
retryはfailed専用で、cancelledは`409 job_retry_conflict`です。
同intentのenqueue/captureはcancelled jobへdedupし、capture replayは元episode/job pairを維持します。
新HTTP keyや架空recipe/intent keyは復活経路ではありません。
取消は**purgeではなく**、canonical episode/evidence、intent/dedup anchor、
worker準備memory、WAL、backupは消去しません。
source forgetは依存jobをpurgeして取消replayを拒否し、後のgrantでもpurge済みpayloadは復活しません。
[契約](../STATUS-jp.md#explicit-job-cancellation)と
[ADR 0015](../adr/0015-job-cancellation-jp.md)を参照してください。

## Schema 10 job-cancellation upgrade

**v25までを対象とする過去schema 10時代の手順です。**
schema 10からを含む現在の全更新には
[schema 11 migration](#schema-11-scope-capture-policy-upgrade)を使います。
以下のcommand/version検査は過去の手順であり、schema 11でv25を起動する指示ではありません。

**schema 10未満への既存migrationであり、v0.0.25はlocal・native CI検証済みです。**
既存schema 10 DBには[application-only更新](#schema-10-application-only-upgrade)を使います。
schema 9→10はv0.0.15で導入した**`010_job_cancellation.sql`**を要求します。
既存job state/payload制約とguard triggerを変更します。
新tableは追加しません。provider、依存、PostgreSQL 18.6/pgvector 0.8.6、固定imageは変更しません。

1. replica/restartを含む旧版・新版のAPI、worker、SDK caller、MCP adapter、
   hook起動、管理commandを停止/drainします。現在ACL/削除記録とbackupを保全し、
   予行は使い捨てdataだけで行います。
2. 対応v0.0.25 migration toolingと`PGAG_ADMIN_DATABASE_URL`で`pg-agmemory migrate`を実行し、
   厳密な履歴001〜009の後に010を適用します。古いschemaには既存migrationもすべて必要です。
   runtime資格情報でmigrationしたりschema guardを迂回したりしないでください。
3. 厳密な履歴`[1,2,3,4,5,6,7,8,9,10]`と`public`内の`vector` 0.8.6を確認します。
   **service 0.0.25 / API v1 / schema 10**を要求する対応v0.0.25 API/worker/SDK/MCP/hookだけを起動します。
   混在版rolloutやdowngradeはありません。
4. traffic再開前に認証付きstage `m2-selectable-inference`、既存`job_cancellation` metadata
   （`endpoint: "/v1/jobs/{job_id}/cancel"`、`compare_and_swap: ["state", "attempt"]`、
   `terminal_state: "cancelled"`、`provider_interruption: false`）、
   上限付きreadiness、承認済みresource/adapter検査を確認します。

過去v0.0.15の実装
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)は
完全一致SHAの[CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)に合格しました。
native実logで各architecture **535テスト、既存warning 1件**、
**amd64 406.88秒 / arm64 490.57秒**を確認しました。
local Apple Containerも**535テスト、既存warning 1件、298.29秒（4:58）**で合格しました。
Ruff、strict mypy **source 19ファイル + SDK consumer 1ファイル**、
真のoptional導入、全production smokeは全3環境で合格しました。
DDL後のschema 9→10 ledger失敗は以前のguard function、制約、schema 9履歴を復元してからretryし、
既存v6 jobのstate/attempt/payloadは不変でした。
production smokeは実SDKでenqueue → cancel → 同key replay → GET cancelled →
worker `--once` idle → source purgeを実行して合格し、そのfixtureは`object_count: 2`を返しました。
別の最終v0.0.15 docs
[`9d34d5329c9db580e7de0459b743511235ad6fb8`](https://github.com/rioriost/pg_agmemory/commit/9d34d5329c9db580e7de0459b743511235ad6fb8)は
[CI 35218254940](https://github.com/rioriost/pg_agmemory/actions/runs/35218254940)に合格しました。
native実logで各**535テスト、warning 1件**、**amd64 605.83秒 / arm64 473.57秒**、
Ruff、strict mypy **source 19 + consumer 1ファイル**、optional導入、全smokeを確認しています。
docs所要時間は実装CI 35216770999とは別で、両runともv0.0.24の検証ではありません。
所要時間は性能benchmarkではありません。[過去の証拠](../STATUS-jp.md#v0015--schema-10)を参照してください。
Native resource/SDKは31 methodとなり、MCPの4 toolとread-only hookは変更しません。
MVP/本番/品質/DR適格性確認は主張しません。

## Runtime readiness

**既存readiness契約を維持し、v0.0.26/schema 11実装はlocal・native CI検証済みです。**
livenessと依存readinessを分離してください。
`GET /healthz`は起動成功後に正確な`{"status":"ok"}`を返し、DBへ接続しません。
public・認証不要の`GET /readyz`は**200**と正確な`{"status":"ready"}`、
または想定内の失敗時に**503**と`{"status":"not_ready"}`を返します。
両readiness応答に`Cache-Control: no-store`と生成UUIDの`X-Request-ID`を含めます。
認証headerは無視するため、tokenを送らずtenant/principalも選びません。
readiness bodyにprivate reasonやschema一覧は含めません。
OpenAPIの両応答はNative `ErrorBody`でなく`ReadinessStatus`です。
不正なHTTP methodはreadiness検査なしで405を返します。

### Dataを変更せず照会する

**説明用commandであり、文書レビュー中にlive DBへ実行してはいけません。**
placeholderを信頼する承認済みAPI originへ置き換えてください。
資格情報や自動retryは不要です。例のcurl 10秒制限はcaller予算であり、
serverのwall-clock保証ではありません。HTTP 503でcurlはexit 22になります。

```bash
PROBE_API_URL='https://memory.example.invalid'
curl --include --silent --show-error --fail-with-body --max-time 10 \
  "$PROBE_API_URL/healthz"
curl --include --silent --show-error --fail-with-body --max-time 10 \
  "$PROBE_API_URL/readyz"
```

受け付けたreadiness検査ごとに`validate_runtime`経由で`PGAG_DATABASE_URL`の新接続を開きます。
`PGAG_ADMIN_DATABASE_URL`やfallbackを与えないでください。
明示SQLは`SET`一つとrole/schema/extension catalog、schema ledger用`SELECT`三つの最大4文です。
`default_transaction_read_only = on`は起動/worker検証を含む専用validation接続に限定し、
後続Native mutationの書込み可能性を維持します。
superuser、`BYPASSRLS`、`memory`/`memory_ops`のtable所有権/owner-role membershipを拒否し、
`NOINHERIT`も対象とします。
厳密な履歴`[1,2,3,4,5,6,7,8,9,10,11]`と`public`内の`vector` 0.8.6を要求します。
memory本文を読まず、tenant lockを取らず、audit、epoch、job、receipt、
source、tombstoneを書き込みません。
migration、cache、background polling、provider呼出し、retryは行いません。

### Restart stormを避けた失敗の解釈

app/processごとにactive検査は一つです。並行要求は別接続や待機なしで即503、
log reason `probe_busy`となります。
固定の**active検査予算5.0秒**と既存DB connect/statement/lock各**5秒**は
**厳密なwall-clock SLAではなく**、cancel/connection cleanupで長くなり得ます。
cancelは伝播しgate/connectionを解放します。

生成`X-Request-ID`と`readiness_unavailable` logの`request_id`を照合してください。
固定`reason`は`runtime_role_invalid`、`schema_unavailable`、`schema_version_mismatch`、
`extension_version_mismatch`、`probe_busy`、または例外class名です。
想定内の`RuntimeValidationError`、`psycopg.Error`、`TimeoutError`は503となり、
raw error本文、traceback、DSN、資格情報、payloadをlogへ出しません。
想定外のprogramming exceptionは503に変換しないため、
通常の`RuntimeError`を想定内driftと扱ってはいけません。
信頼する設定/DB状態を別途調査し、probeを通すためにruntime roleを昇格したり
自動migrationを起動したりしないでください。

readyはある時点の接続/runtime role/schema/vector検査だけです。
SELECT-only DBも合格し得て、書込み/primary状態、全principal権限、
table grant/RLS policy完全性、JWT検証、tokenizer/provider正常性、
backlog/load、HA/DR、本番/品質/性能を証明しません。
resource routeはreadinessを呼ばず、drift後の永続gateも取得しません。
既存Native認可は維持します。traffic停止signalであり、認可firewallではありません。
依存readinessをlivenessへ接続してrestart stormを起こさないでください。
busy 503を考慮した失敗/復旧thresholdと、deployment perimeterでのpublic probe制限/rate limitを設定します。
process単位のadmissionはglobal rate limiterやrequest flood適格性確認ではありません。
Kubernetes、Compose、Docker `HEALTHCHECK`設定は提供しません。

### Schema 9 application-only upgrade

**過去v0.0.13→v0.0.14だけの手順であり、v0.0.26更新ではありません。**
現在のtoolingには[schema 11保守](#schema-11-scope-capture-policy-upgrade)を使ってください。

v0.0.13→v0.0.14は**migrationを追加せず**、依存/imageもupgradeしません。
PostgreSQL 18.6、`public`内の`vector` 0.8.6、厳密なschema履歴1〜9を維持します。
replica/restartを含む旧API、worker、adapter、hook起動、SDK caller、管理commandを停止/drainし、
現在ACL/削除記録とbackupを保全してください。
対応v0.0.14 componentだけを導入し、混在版rolloutを行いません。
既存の起動時fail-closedを維持し、SDK/MCP/hookは厳密な
**service 0.0.14 / API v1 / schema 9**を要求します。
認証付きcapabilitiesのstage `m2-runtime-readiness`と`health_probes` metadata
（`liveness: "/healthz"`、`readiness: "/readyz"`、`readiness_timeout_seconds: 5.0`、
`readiness_max_in_flight_per_process: 1`）を確認します。
traffic再開前にliveness、上限付きreadiness、承認済みの認証付きresource/adapter動作を検査してください。
readyだけでは不十分です。この過去手順は009までのmigrationを要求しました。
現在の古いschemaからの更新は[011までのsequence](#schema-11-scope-capture-policy-upgrade)を必要とします。

過去v0.0.14の使い捨てDB production smokeは通常HTTP smokeの後に同じAPI processで
ready 200 → schema ledger rename →
health 200のままready 503 → ledger復元 → ready 200と既存の認証付きsmokeを対象とし、
source/tombstoneを書き込みません。live DBでdriftを再現しないでください。
**このlifecycleはlocalと両native architectureで合格しました。**
Apple Containerとnative Docker amd64/arm64で各**495テスト、既存warning 1件**、
内訳は**既存464 + readiness unit 16 + integration 15テスト（新規31）**です。
Ruff、strict mypy（**source 19ファイル + SDK consumer 1ファイル**）、
真のcore/hook/sdk-only導入、non-root production全smokeも全3環境で合格しました。
実装
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
は[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)に合格し、
native実logで検査/smokeを確認しました。
所要時間は**local 295.67秒 / amd64 385.41秒 / arm64 470.16秒**であり、性能benchmarkではありません。
別の最終v0.0.14 docs
[d4b24f6](https://github.com/rioriost/pg_agmemory/commit/d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68)は
[CI 35202931424](https://github.com/rioriost/pg_agmemory/actions/runs/35202931424)に合格しました。
各native architecture 495テスト/warning 1件と全検査/smoke、
**amd64 319.51秒 / arm64 503.56秒**でした。両runともv0.0.15を検証しません。
[検証証拠](../STATUS-jp.md#v0014--schema-9)を参照してください。
readinessはSDK/MCP/hook probe methodを追加せず、episode照会を含むresource/SDK surfaceは31のままです。
health probeはSDK route coverage対象外です。
[ADR 0014](../adr/0014-runtime-readiness-jp.md)を参照してください。

## Scope-access administration

**既存scope-access契約を維持し、v0.0.26実装はlocal・native CI検証済みです。**
手書きmembership SQLより`pg-agmemory scope-access`を優先してください。
同じtenantに属する承認済み既存tenant/scope/principal UUIDだけを使います。
このcommandはprovisionせず、HTTP/MCP/Python SDKでは公開しません。
取得memory本文ではなく信頼する管理情報からIDを選びます。

`PGAG_ADMIN_DATABASE_URL`を対象DBへ安全に設定します。
runtime URL fallback、JWT、`--subject`、`--once`はありません。
DB roleはsuperuserまたは`BYPASSRLS`**と必要なSQL table権限**を要求し、
`get`もruntime loginを拒否します。DSN、資格情報、private membership識別情報を
logやissueへ出さないでください。scope管理はprovider accessを変更しません。
照会用の非owner `BYPASSRLS` roleは`memory`の`USAGE`と
schema履歴/tenant/scope/principal/`scope_member`の`SELECT`を必要とし、
`get`は`FOR UPDATE`を使いません。変更には追加でtenantの`UPDATE`、
該当する`scope_member`のSELECT/UPDATE/INSERT/DELETE、`memory_ops`のUSAGE、
`scope_access_event`のINSERTが必要です。すべての操作でRLS bypassは必須です。

`get`は3 ID optionだけを受け付けます。`set`はpermissionとexpiryの全置換です。
重複しない`read`/`write`/`delete` flagまたは`admin`単独と、
`--expires-at <未来のtimezone-aware ISO timestamp>`または`--no-expiry`を明示します。
write-only/delete-onlyも許可しますが、Nativeのread/action要件を上書きしません。
重複や`admin`混在は不正です。expiryはlock取得後のDB clockと比較し、省略を無期限と扱いません。
`revoke`はpermission/expiry optionを受け付けずmembershipを削除します。

両変更で同じtenantの観測値である**1〜9223372036854775807の`--expected-access-epoch`**を
必須とします。counterはtenant全体であり、無関係scopeの変更とも競合します。
状態が同じでも古いCASは失敗します。現在epochで既に不在の`revoke`や
同等なpermission順序変更はno-opですが、expiry変更は実変更です。

### Get、read-only全置換、revoke

**説明用commandであり、文書レビュー中にDBへ実行してはいけません。**
権限を持つoperatorがすべてのplaceholderを承認済み既存ID、
新しく観測したepoch、timezone offset付きの未来expiryへ置き換えます。
予行は使い捨てtest記録だけで行います。epochを自分で1増やしたり、
古い値を使い回したり、別管理者の変更後に盲目的に全sequenceを実行したりしないでください。

```bash
TENANT_ID='<approved-existing-tenant-uuid>'
SCOPE_ID='<approved-existing-scope-uuid>'
PRINCIPAL_ID='<approved-existing-principal-uuid>'

pg-agmemory scope-access get --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID"

pg-agmemory scope-access set --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID" \
  --expected-access-epoch '<access_epoch-observed-in-first-get>' \
  --permissions read --expires-at '<approved-future-ISO-timestamp-with-offset>'

pg-agmemory scope-access get --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID"

pg-agmemory scope-access revoke --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID" \
  --expected-access-epoch '<access_epoch-observed-in-second-get>'

pg-agmemory scope-access get --tenant-id "$TENANT_ID" \
  --scope-id "$SCOPE_ID" --principal-id "$PRINCIPAL_ID"
```

成功はwrapperなしのJSON 1行で、`operation`、`tenant_id`、`scope_id`、`principal_id`、
`access_epoch`、`changed`、`membership_exists`、`permissions`、`expires_at`、
`effective_permissions`、`evaluated_at`を返します。
membership不在はfalse/空/nullであり、legacyの空permission行は存在扱いです。
設定flagはread/write/delete/admin順です。
有効flagはDB `evaluated_at`で期限切れなら空、有効な`admin`なら全4個、それ以外は設定flagです。
Native action認可や将来の有効性を保証しません。
自然期限切れはepochを進めずpayload/auditを消さず処理中HTTPもdrainしません。
強いdrainには明示revoke/barrierを使います。

### Drain、audit、復旧

専用同期autocommit connectionは**connect/statement/lock各5秒timeout**を使い、
role/schema/extensionを確認してAPI/workerと**同じtenant session advisory lock**を取得します。
`get`とno-opも対象です。transaction commit**とJSON stdout flushまで**保持し、
成功/失敗ともconnection closeで解放します。poolやtransactionだけのlockへの置換は禁止です。
先行する遅い応答はcommandを遅らせ、lock待ちtimeoutは変更なしを意味します。
協調する同じ版のAPI clientはonlineを維持できます。

実変更は`memory.scope_member`更新、tenant epoch増加、
`memory_ops.scope_access_event`追記を原子的に行います。
no-op/get/conflictはepoch/eventを作らず、変更途中の失敗は3者をまとめてrollbackします。
auditは対象opaque ID、epoch、`set`/`revoke`、変更前後flag/expiry、
`current_user`由来の`database_role`、DB clockの`recorded_at`を保存し、本文/subject/DSNは保存しません。
`evaluated_at`はresponse専用でaudit fieldではありません。
forced RLSで特権専用、runtime policy/grantはありません。
記録するDB roleは実行時の`current_user`であり、end-user actorではありません。
特権DB管理者への改ざん耐性の証明や独立revocation復旧台帳ではありません。

syntax/model/config errorは固定sanitized stderr、exit **2**、JSONなしです。
不正なCLI構文は`invalid_scope_access_arguments`を報告し、`PGAG_ADMIN_DATABASE_URL`
未設定も固定stderr、exit 2、stdoutなしです。
DB/domain errorはstdout `{"error":{"code":"...","outcome_unknown":false}}`、
exit **1**であり、raw DB error/資格情報は出力しません。
[全error catalog](../STATUS-jp.md#scope-access-administration)を参照してください。
commit通信失敗は`outcome_unknown: true`になり得て、commit試行前の失敗はfalseです。
cancel、process kill、stdout喪失も変更結果不明と扱います。
新しい`get`と特権auditを確認してから新CAS操作を明示承認してください。
**自動retry、Idempotency-Key、変更receiptはありません**。古いepochを盲目的に再送しません。
stdout barrierは配信済みcontextを撤回できません。
最新ACL/削除記録のrestoreは手動のままで、grantはpurge済みdataを復活させません。

## Schema 9 scope-access upgrade

**以下は過去migration段階とv25時代のtoolingであり、v0.0.26更新ではありません。**
現在toolingは[schema 11更新](#schema-11-scope-capture-policy-upgrade)を使います。
既存schema 9 DBも含め、過去v25 toolingは[schema 10更新](#schema-10-job-cancellation-upgrade)まで
進める必要があります。v0.0.25実装はlocal・native CI検証済みです。
下記の固定PostgreSQL **18.6** / **`public`内の`vector` 0.8.6** imageを維持します。
migration 009はv0.0.13でdurable特権auditを導入したもので、v0.0.25の追加ではありません。

1. replica/restartを含め、旧版・新版の全API、worker、adapter、hook起動、
   SDK caller、管理commandを停止/drainします。
   backupと現在ACL/削除記録を保全し、予行は使い捨てDBだけで行います。
2. matching v0.0.25 toolingとmigration管理者で`pg-agmemory migrate`を実行します。
   記録順に001〜008の後へ`009_scope_access.sql`、さらに010を適用します。
   schema 8→9 ledger書込み失敗後のrollback/retryと以前のmigration検査は
   過去v0.0.14で合格し、v0.0.15全local/native suiteも合格しました。既存ACL行を維持し、過去の手動変更のaudit backfillは行いません。
   embedding backfillや暗黙ownership/purge変更は追加しません。
3. 厳密な履歴`[1,2,3,4,5,6,7,8,9,10]`、extension版/schema、特権専用audit tableを確認し、
   対応v0.0.25 API/workerだけを起動します。
   SDK/MCP/hookはservice **0.0.25**、API **v1**、schema **10**を要求します。
4. 認証付きcapabilitiesのstage `m2-selectable-inference`と`scope_access_administration`
   metadata（`transport: "admin-cli"`、`command: "scope-access"`、
   `compare_and_swap: "tenant_access_epoch"`、`audit: "database_role"`）を確認し、
   traffic再開前に承認済み使い捨てdataでCLI/ACL/drainと既存resourceを検査します。
   版混在やdowngrade互換性は保証しません。失敗時は旧processの停止を維持します。

**過去のv0.0.13証拠:** Apple Containerとnative Docker amd64/arm64で各**464テスト、既存warning 1件**、
Ruff、strict mypy（**source 19ファイル + SDK consumer 1ファイル**）、
真のcore/hook/sdk-only導入、実scope-access CLI/SDK lifecycleを含むnon-root production全smokeが合格しました。
内訳は**既存426 + scope-admin unit 22 + integration 16テスト（新規38）**です。実装
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413)
は[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)に合格し、
native実logで検査/smokeを確認しました。
所要時間は**local 297.52秒 / amd64 539.86秒 / arm64 460.73秒**であり、性能benchmarkではありません。
別の最終v0.0.13 docs
[185f433](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)は
[CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967)に合格しました。
各native architecture 464テスト/warning 1件と全検査/smoke、
**amd64 499.25秒 / arm64 454.37秒**でした。両runともv0.0.14を検証しません。
[検証証拠](../STATUS-jp.md#v0013--schema-9)と
[ADR 0013](../adr/0013-scope-access-jp.md)を参照してください。本番/DR認定ではありません。

## Python SDK operations

**SDKはmemoryの31 methodを維持し、providerは別library/CLIです。v0.0.26実装はlocal・native CI検証済みです。**
対応checkoutから`python -m pip install '.[sdk]'`で導入します。
`pg-agmemory[sdk]` extraはMCP SDKでなく`httpx==0.28.1`だけを固定しますが、
同じcore packageには引き続きFastAPI、psycopg、Janomeが含まれます。
独立した軽量distributionやPyPI公開済みという主張ではありません。
HTTPXがなければ固定SDK import `ImportError`となり、
`hook`/`mcp`由来のHTTPXでも依存を満たします。packageは`py.typed`を提供します。

1. 信頼する管理手順でscope/tokenを用意し、**SDKへDB資格情報や署名鍵を渡さないでください**。
   信頼する設定からconstructorへ明示引数を渡し、memory本文、tool引数、redirectから選びません。
   [非同期例](../../README-jp.md#python-sdk)の`PGAG_SDK_API_URL`、
   `PGAG_SDK_API_TOKEN`、`PGAG_SDK_SCOPE_ID`は環境変数名の例であり、
   SDKが自動読込みする設定ではありません。
2. application path、userinfo、query、fragmentを含まない固定HTTPS originまたは
   loopback HTTP originを選びます。constructorはURL/tokenの形を検査し、
   `MemoryClientError`でなくsanitized `ValueError`を返します。
   serverがbearer token認証と現在ACLを強制します。request scopeは権限を狭めるだけです。
3. `async with AsyncMemoryClient(api_url, api_token) as memory:`へentryします。
   所有HTTP clientを作り、認証付きcapabilitiesで
   **service 0.0.26 / API v1 / schema 11**を要求します。
   context前/後の利用と同一instanceへの再entryは
   `client_not_open` / `client_already_used`で拒否します。
   exitで閉じるのは接続であり、**保存memoryは消去しません**。
   exit前に未完了taskをawaitするかcancel後にawaitしてください。
   client closeはrequestのschedule/cancelやDB rollbackをしません。
4. `pg_agmemory.models`のNative request modelを渡し、mutable instanceもcall時に再検証します。
   Pydantic model構築はSDK callの外で別に`ValidationError`を返し得ます。
   そこに含まれるprivate入力詳細をlogに出さないでください。
   最初のoutbound network await前に再検証済みrequestのsnapshotを作ります。
   resource IDにはUUID objectを使います。各変更のkeyword-only `idempotency_key`は
   **1〜256文字の可視ASCIIで、trimしません**。
   `forget`のpreview/purgeも必須で、両方Native HTTP 202とmode固有の型付き応答です。
5. 送信前に各key/bodyを正確に安全に保持します。`MemoryClientError`は
   `AdapterFailure`のaliasで、`.error.code`、`.retryable`、`.outcome_unknown`、
   `.native_status`、`.request_id`を参照します。
   raw memory/入力/token/応答をlogへ出さないでください。
   SDK domain codeはcatalog化し、未知server codeは`native_api_error`です。
   MCP/hookのsafe codeは不変です。
6. network/5xx/不正応答/想定外成功応答で変更結果が不明なら**同じkey/body**で照合します。
   自動retry、新key交換、未commitの推測はありません。
   cancelは伝播し、処理中の変更cancelもrollbackの推測でなく照合が必要です。
   local不正request/key/UUIDは送信前にsanitized `invalid_request`、
   `outcome_unknown: false`で失敗します。

transportは**exchange全体20秒、I/O 10秒 / connect 5秒、4接続、
TLS検証あり、proxy環境不使用、redirectなし**を維持します。
応答は**2 MiB**、requestは**256 KiB**で、`create_checkpoint`だけ**1 MiB**です。
MCP/hook上限は引き上げません。
cache、provider呼出し、host登録/delegation、自動job、token refresh、
同期/TypeScript client、任意HTTP methodはありません。
対象はmemory resourceで、admin/worker CLI実行ではありません。
capabilitiesは内部probeであり、SDKのpublic health/OpenAPI download methodではありません。
返されたmemoryは根拠であり、信頼する指示/現在の事実ではありません。
Native byte予算、coverage flag、現在ACL、purge、host/backup/WALの制限を維持し、
context exitでは既に返したcopyを消せません。
[全31型付きmethod](../STATUS-jp.md#python-sdk)と
[ADR 0012](../adr/0012-python-sdk-jp.md)を参照してください。

<a id="v0012-application-update"></a>

## v0.0.12 application更新（schema変更なし）

**過去のschema 8専用手順であり、v0.0.26更新ではありません。**
現在は以前のmigrationも含む[schema 11更新](#schema-11-scope-capture-policy-upgrade)を使います。
**保守手順であり、本番upgradeや災害復旧の適格性認定ではありません。**
既存schema 8 DBは下記の固定PostgreSQL 18.6 / `public`内の`vector` 0.8.6
DB imageを維持します。schema 9 migration、embedding backfill、provider呼出しは追加しません。

1. replica/自動再起動を含む旧API、worker、adapter、hook起動、SDK callerを停止/drainします。
   現在の削除/ACL記録とbackupを保全し、予行は使い捨てDBだけで行います。
2. lock済みsourceから対応v0.0.12 API/worker/adapter/hook/SDKをbuild/導入します。
   既存schema 8は厳密な履歴`[1,2,3,4,5,6,7,8]`を維持し、
   `migrate`、API、workerはextension版/schemaを引き続き検査します。
   古いschemaには先に下記の既存migrationが必要です。
3. 対応v0.0.12 componentだけを起動し、認証付きcapabilities、stage `m2-python-sdk`、
   `python_sdk` metadata（`installation: "sdk-extra"`、`async: true`、
   `automatic_retry: false`）を確認します。metadataはendpoint追加ではありません。
4. traffic再開前に合成dataで既存adapterとSDK lifecycle/recovery/purgeを検査します。
   版混在/rolling互換やdowngradeは保証しません。失敗時は停止を維持して調査します。

最終local Apple Containerとnative Docker amd64/arm64のtest、strict source/型付きconsumer検査、
実HTTP SDK integration全5テスト、真のcore/hook/sdk wheel導入検査、
non-root production全smokeが合格しました。正確な件数、所要時間、範囲は
[検証済み証拠](../STATUS-jp.md#v0012--schema-8)を参照してください。
**各環境426テスト、warning 1件**、**local 292.76秒 / amd64 484.79秒 /
arm64 472.49秒**です。実装
[88e1206](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)は
[CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945)に合格し、
実logで完全一致SHAと検査を確認しました。所要時間は性能benchmarkではありません。
SDK smokeはworkerを呼ばずpending jobを確認し、従来の実capture-worker smokeは別に維持します。
docs CI 35190495385を含む別々のv0.0.11実装/最終docs runは
[過去の証拠](../STATUS-jp.md#v0011--schema-8)として維持します。

## Schema 8 pgvector upgrade

**以下は過去migration段階とv25時代のtoolingであり、現在v26の手順ではありません。**
現在toolingには[schema 11 migration](#schema-11-scope-capture-policy-upgrade)を使います。

**v0.0.11のapplication/migration検査は合格しました。本番適格性の認定は未完了です。**
migration 008はv0.0.11で導入・検証済みです。過去のv0.0.25 toolingは古いschemaへ既存009と010も適用し、
v0.0.25実装はlocal・native CI検証済みです。過去v0.0.12のapplication-only手順でなく、
[schema 10境界](#schema-10-job-cancellation-upgrade)に従ってください。

採用prebuilt image:

```text
docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a
```

[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6)は検証済みの
**2026-07-29** stable releaseで、公式tag commitは
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`です
（[固定changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)）。
**PostgreSQL License**であり、再配布時は上流license fileを保持します。
両native最終imageは`/usr/share/doc/pgvector/LICENSE`を保持し、
固定上流licenseとbyte単位一致を検証済みです。SHA-256は
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`です。
両最終amd64/arm64 imageを検査し、PostgreSQL **18.6-1.pgdg12+2**、
native ELF、`vector.control` **0.8.6**を確認しました。
**PostgreSQL版は同じでも、DB image/base digestは旧library PostgreSQL profileと異なります**。
新しい固定上流vector DB profileであり、不変base上のsource buildではありません。
実装profileに新DB Dockerfile、mutable host APT導入、source-build workflowは含めません。

1. 任意のlatestでなく上記の完全なimage tag**とdigest**を使います。
   operator管理PostgreSQLの代替環境にはmigration前に対応extensionが必要ですが、
   この文書はhost導入workflowを提供しません。
2. replicaや自動再起動も含め、**旧版・新版の全API、worker、adapter、hook起動を停止/drain**します。
   migration lockは旧schema 7 processの稼働継続を防ぎません。rolling共存は非対応です。
3. backup、application/schema/extension版、現在の削除/ACL記録を保全します。
   予行は使い捨てDBだけで行い、restore隔離とDR/完全消去の未完了を維持します。
4. migration管理者と対応v0.0.25 applicationで`pg-agmemory migrate`を実行します。
   変更しない001〜007に続いて`008_pgvector.sql`、`009_scope_access.sql`、
   `010_job_cancellation.sql`を適用し、
   古いDBにはmigration 007のlexical backfillも必要です。
   migrationは**`public`内の`vector` 0.8.6**を要求し、版/schemaが異なる既存extensionを拒否します。
   `migrate`はschema 8記録済みでもextensionを検査し、適用済みを理由に省略しません。
   新episode/assertion revision vector projectionにはforced RLS、
   canonical `ON DELETE CASCADE`、runtime **SELECT/INSERTのみ**を適用します。
   **既存dataのembedding backfillはありません**。
5. 厳密な履歴`[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]`とschema `public`内のextension `vector` 0.8.6を確認してから、
   対応v0.0.25 API/workerだけを起動します。
   traffic再開前に認証付きcapabilities、lexical互換、合成vector/hybrid ranking、
   coverage、RLS/時間filter、replay/purge、既存adapterを検査します。
   API/worker起動はschema/extension不一致を拒否します。
6. 失敗時はprocess停止を維持します。変更済みschemaで旧imageを起動したり、
   downgrade対応を想定したりしないでください。自動embedding再構築/providerはなく、
   lexical reindexもvectorを投入しません。

migration 008は**過去のschema 8 migration**であり、上記v25時代toolingには009を含むschema 10更新も必要でした。
現在v26には[schema 11更新](#schema-11-scope-capture-policy-upgrade)を使います。
下記の過去v0.0.10 application-only更新とは異なります。
artifact/版/license検査とv0.0.11 application検査はそれぞれ確認済みです。
[現在の契約](../STATUS-jp.md#pgvector-exact-and-hybrid-retrieval)と
[ADR 0011](../adr/0011-pgvector-retrieval-jp.md)を参照してください。

## 明示vectorの運用

現在認可されたepisode/assertion revisionだけを使います。
読取り専用`POST /v1/embedding-inputs`はExplain `{memory_id, revision}`
（既定は**latestでなく1**）を受け取り、idempotency keyは不要です。
private canonical textとそのUTF-8 SHA-256 digestを`memory-content-v1`形式で返します。
logや明示承認のない第三者への送信は禁止です。
digestはmodel由来、意味的な支持、品質を証明しません。

Native `POST /v1/embeddings`へ、保持したcaller管理`Idempotency-Key`、
正確なdigest、caller宣言model名/revision（各1〜256文字）、
固定768/cosine/`l2-f32-v1` metadata、768個の有限JSON数値を渡します。
serverはfloat64で正規化してpgvector float32で保存します。
zero/非有限vector、boolean、数値文字列、切詰め、次元変換は受け付けません。
scope/identityはcanonical parentから導出し、read/write権限を要求します。
送信前に正確なkey/requestを保持し、logへ出さないでください。

parent revision/model namespaceごとに不変です。
同じ正規化float32 vector/digestは別keyでも重複抑止し、
変更は`409 embedding_conflict`、digest不一致は`409 embedding_input_mismatch`です。
置換には新model revisionを使います。
**canonical revision当たりmodel version 8件**を超える9件目は
`422 embedding_limit_exceeded`ですが、既存duplicateは許可します。
upload応答は`{memory_id, revision, model, input_digest}`で、独立embedding IDではありません。
**保存idempotency resultは`{memory_id, revision}`だけ**であり、
receiptに平文digest/model名/vectorは含めません。現在読取り可能なcanonical inputと
対応projectionから応答の完全なmodel/digestを再構成し、request HMAC/opaque anchorは保持します。
replayは生存parentとprojectionを再確認します。parentが生存中に管理者がprojectionだけを
削除した場合は再作成せず**409 `embedding_unavailable`**を返します。
projection/idempotency/audit writeは原子的です。

lexicalは引き続き既定でvector queryを拒否し、vector-onlyは空textとvector、
hybridは非空textとvectorを要求します。
exact cosineは現在のACL/時間条件を満たす候補のmaterialize後に計算し、
hybridは決定的な**RRF k=60**であって近似neighbor indexではありません。
省略`as_of`/`known_at`はselection/coverage前に一度だけ確定し、
途中の未来境界にかかわらず両pathで同じ確定時刻を使います。明示時刻は変更しません。
実際のdistance/scoreの同順位はUUIDで解決しますが、
任意の浮動小数点結果/rankingの全CPU間bit単位一致を保証しません。
model名/revision空間を混ぜないでください。
`vector_incomplete`、`lexical_incomplete`、`retrieval_complete`とNativeの空/予算結果を明示し、
可視vector欠落を黙って完全indexの成功と扱わないでください。
ranking metadataはconfidenceではありません。
JSON全体のbyte予算とDB statement timeout 5秒を維持しますが、意味品質やlatency SLOではありません。
responseの既定に`MemoryItem.retrieval: null`、`retrieval_mode: "lexical"`、
`embedding_model: null`、`coverage.vector_incomplete: false`を追加します。
non-nullの`MemoryItem.retrieval`は`method`（`exact_cosine`/`rrf-60`）、
`lexical_rank`、`vector_rank`、`vector_distance`、`fusion_score`を持ち、
該当しない値はnullableです。**lexical動作は維持しますが、HTTP response/schema形式のbyte単位互換ではありません**。
厳格なconsumerは追加fieldへ対応してください。
v0.0.11 vector機能は新Python依存を追加せず、serviceはraw parameter-bound vector castを使います。
capabilitiesに`retrieval_modes: ["lexical", "vector", "hybrid"]`と
`default_retrieval_mode: "lexical"`を追加します。

canonical purgeはlexicalとともにvector/digest/宣言model名へcascadeします。
このmetadataを保持する独立model registryはありません。
vectorは別provenance vertexや削除件数ではありません。
projection-only delete endpointや自動生成/再構築はありません。
保持anchorと現在のACL/削除確認は復活を防ぎ、host/backup/WAL消去は証明しません。
MCPは4 toolを維持してRecall引数だけを拡張します。
embedding input/uploadはSDKも対象とするNative routeであり、MCP toolではありません。
hook、Observe、capture、job、workerはembeddingを生成せず、hookはlexical専用です。
Native応答の非lexical `retrieval_mode`、non-nullの`embedding_model`/item `retrieval`、
trueの`coverage.vector_incomplete`を拒否します。予期しないvector出力はerrorであり、黙って降格しません。

### Synthetic vector example

**Draft request例であり、本番modelや検索品質benchmarkではありません。**
合成dataだけを含む専用の使い捨てscopeで、変更しない`POST /v1/observe`を使い、
contentが正確に`synthetic vector fixture`のepisodeを作成してください。
信頼するoperator環境から`PGAG_DEMO_API_URL`（Native origin）、
`PGAG_DEMO_API_TOKEN`（Native audience token）、`PGAG_DEMO_SCOPE_ID`、
`PGAG_DEMO_MEMORY_ID`（そのepisode UUID）、`PGAG_DEMO_EMBEDDING_KEY`
（保持したcaller管理key）を渡します。これらの環境変数名はこの例専用です。
promptからstartup設定を導出したり、shell tracing、本文/tokenのlog出力を行ったりしないでください。
結果不明後に例を再実行する場合も同じkey/bodyを維持します。

```bash
python - <<'PY'
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("redirect disabled")

try:
    base = os.environ["PGAG_DEMO_API_URL"].rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
    ):
        raise ValueError("invalid origin")
    token = os.environ["PGAG_DEMO_API_TOKEN"]
    memory_id = str(uuid.UUID(os.environ["PGAG_DEMO_MEMORY_ID"]))
    scope_id = str(uuid.UUID(os.environ["PGAG_DEMO_SCOPE_ID"]))
    key = os.environ["PGAG_DEMO_EMBEDDING_KEY"]
    if not token or not 1 <= len(key) <= 256 or not all(33 <= ord(c) <= 126 for c in key):
        raise ValueError("invalid operator configuration")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def post(path, body, idempotency_key=None):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with opener.open(request, timeout=10) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("response too large")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid response")
        return result

    source = post("/v1/embedding-inputs", {"memory_id": memory_id, "revision": 1})
    expected = {
        "memory_id": memory_id, "revision": 1, "type": "episode",
        "text": "synthetic vector fixture", "input_format": "memory-content-v1",
    }
    if any(source.get(name) != value for name, value in expected.items()):
        raise ValueError("not the synthetic fixture")
    digest = hashlib.sha256(expected["text"].encode("utf-8")).hexdigest()
    if source.get("input_digest") != digest:
        raise ValueError("digest mismatch")
    model = {
        "name": "synthetic-basis-demo", "revision": "basis-v1", "dimensions": 768,
        "distance_metric": "cosine", "normalization": "l2-f32-v1",
    }
    values = [1.0] + [0.0] * 767
    receipt = post("/v1/embeddings", {
        "memory_id": memory_id, "revision": 1, "input_digest": digest,
        "model": model, "values": values,
    }, key)
    if any(receipt.get(name) != value for name, value in {
        "memory_id": memory_id, "revision": 1, "model": model, "input_digest": digest,
    }.items()):
        raise ValueError("invalid receipt")
    for mode, query in (("vector", ""), ("hybrid", "synthetic")):
        result = post("/v1/recall", {
            "scope_ids": [scope_id], "query": query, "mode": "explicit", "purpose": "synthetic_fixture",
            "token_budget": 2000, "max_items": 20, "search_profile": "simple-v1",
            "retrieval_mode": mode, "vector_query": {"model": model, "values": values},
        })
        coverage = result.get("coverage")
        names = ("retrieval_complete", "vector_incomplete", "lexical_incomplete")
        if result.get("retrieval_mode") != mode or not isinstance(coverage, dict):
            raise ValueError("invalid recall response")
        if any(type(coverage.get(name)) is not bool for name in names):
            raise ValueError("invalid coverage")
        print(json.dumps({"retrieval_mode": mode, "synthetic_only": True,
                          "coverage": {name: coverage[name] for name in names}}))
except (KeyError, ValueError, TypeError, OSError, urllib.error.URLError):
    print("Synthetic vector example failed; inspect sanitized Native diagnostics.", file=sys.stderr)
    raise SystemExit(1) from None
PY
```

Python標準libraryと信頼するNative APIだけを使い、**外部model、registry、provider、
pgvector Python packageは呼びません**。
数学的basis vectorを意図的に使い、合成canonical input/digestを確認し、
private text、vector、receipt、errorではなく固定coverage fieldだけを出力します。
proxy環境とredirectを無効化し、HTTPSは既定TLS検証を使います。
例のI/O timeout 10秒はtotal deadlineや本番SLOではありません。
不完全coverageを明示確認し、一つのprojection追加をcorpus全体の完全性の証明にしないでください。
意味的embedding modelと呼ばないでください。

## Atomic structured captureの運用

現在の[scope capture policy](#scope-capture-administration)をidempotency/source dedup前に検査します。
以下の例とreplay動作はepisodeが引き続き許可される前提です。
replay拒否は以前の書込みがrollbackした証明ではありません。

1〜16件のproposalには別の[batch route](#explicit-batch-capture)を使い、
以下の単一job契約は変更しません。

**既存capture契約はv0.0.11でも検証済みです。**
上記に従いNative APIとruntime roleを準備します。
対応するservice `0.0.26`、API `v1`、schema `11`を使います。
過去v0.0.10 stage `m2-atomic-capture`はM2全体の完了ではありません。
captureはNative routeであり、**MCP toolや自動recall-hook操作ではありません**。
既存schema 8/9 migrationはcapture semanticsとは別です。
schema 10はterminal取消を追加しますが、同intentのcaptureはjobを復活させずdedupします。
captureはembeddingを生成しません。

### 明示request例

許可済み・除去処理済みの使い捨てdataを使います。
operatorが`MEMORY_URL`（信頼するAPI origin）、`TOKEN`（Native audience token）、
`SCOPE_ID`（認可済みUUID）、`SOURCE_EVENT_ID`（安定したsource identity）、
`CAPTURE_KEY`（caller管理idempotency key）を渡します。placeholderであり埋込み資格情報ではありません。
送信前に正確なrequest/keyを安全に保持し、shell tracingを有効にしたり、
token/本文をlogやissueへコピーしたりしないでください。

```bash
curl --fail-with-body "${MEMORY_URL%/}/v1/captures" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Idempotency-Key: ${CAPTURE_KEY}" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<JSON
{
  "episode": {
    "scope_id": "${SCOPE_ID}",
    "source_namespace": "atomic-capture-demo",
    "source_event_id": "${SOURCE_EVENT_ID}",
    "occurred_at": "2026-09-17T00:00:00Z",
    "content": "ACME contract is Gold",
    "consent_reference": "operator-approved-demo-consent"
  },
  "memory": {
    "subject": "ACME",
    "predicate": "contract_tier",
    "value": "Gold",
    "evidence_quote": "ACME contract is Gold",
    "explicit_intent": true,
    "valid_from": null,
    "valid_to": null
  }
}
JSON
```

`episode`は変更しないObserveです。`memory`は構造化intent一つだけで、
scope、根拠ID、identityの上書きはなく、serverがtransaction内でscopeと一つのepisode根拠IDを導出します。
一つのquoteは正規化episodeの1〜4,096文字の原文substringである必要があります。
Rememberの上限を維持し、subject 1〜256文字、predicate `^[a-z][a-z0-9_]{0,63}$`、
value 1〜65,536文字、explicit intent true、valid boundはtimezone付き/null、
両端指定時は開始が終了より前です。
原文quoteは意味的な真実の証明ではなく、公開assertionはreported・未校正を維持します。

### Assertionを推測せずjobを追跡

HTTP **201**は`{memory_id: <episode UUID>, revision: 1,
synthesis_job_id: <job UUID>}`を返します。
原子的な**episodeとstructured_remember / structured-remember-v1 jobのcommit**を示し、
assertion publicationではありません。terminalを含む既存jobも再利用でき、
**201は新規/pendingを意味しません**。両IDを過去参照として保存し、現在のjob statusはGETを正とします。
返却job UUIDを`CAPTURE_JOB_ID`に設定して照会します。

```bash
curl --fail-with-body "${MEMORY_URL%/}/v1/jobs/${CAPTURE_JOB_ID}" \
  -H "Authorization: Bearer ${TOKEN}"
```

制限付きruntime DB資格情報と**同じowner principalの信頼する固定subject**で既存workerを実行し、
GETを再度行ってfresh stateを取得します。`--once`は実行時刻に達したowned jobを最大一つ処理し、
このjobとは限らずqueue drainでもありません。
成功したjob resultだけが公開assertion IDを与えます。
既存quota（scope当たりpending/running 100件）、5試行、lease、epoch、publication fencingを維持します。
[worker運用](#durable-jobとworkerの運用)を参照してください。

純粋な`POST /v1/observe`は`synthesis_job_id: null`を返し、自動queue化しません。
直接`/v1/jobs`と同期`/v1/remember`も変更せず、Observe/Rememberのserialization/HMAC互換を維持します。
captureはLLM/provider、抽出、自然言語synthesis、自動embedding、意味品質の適格性認定を追加しません。

### Replay、失敗、削除

- API再起動を含む結果不明時は**同じcapture keyと正規化body**を再利用します。
  HTTP応答喪失はrollbackの証明ではありません。同keyのbody変更は`409`です。
  IDは過去参照であり、fresh job stateはGETで取得します。
- 同じepisode/intent/principalの新keyは**両ID**を重複抑止します。
  以前observeした同一eventも再利用可能です。
  同じsource identityでepisode bodyが異なる場合は`409`で、新規partial writeはありません。
- 新しい別の明示intentは生存episode上に別jobを作れます。
  別の認可済みprincipalは独立したjob identity/worker所有権を持ち、source重複抑止は権限を付与しません。
- episode/projection、job data/identity、outer idempotency receipt、auditの後のtransaction失敗は、
  新規変更をまとめてrollbackします。以前から独立して存在したepisodeは残り、
  新しいjobだけが部分的に残ることはありません。capture操作当たりjobは最大一つです。
- **両返却ID**について現在のACL/削除が優先します。
  episode purgeはjob/assertion子孫を閉じます。job単体purgeはepisodeと独立保存の公開済みoutputを残しますが、
  旧組replayや同intentの新keyは`404`となり、purge済みjob identityを再作成しません。
  result assertion purgeは依存jobを削除しsourceを残すため、組は無効になります。
- source全体の永久sealではなく、生存source上の新しい**別の**明示intentは既存job semanticsに従います。
- failed jobのretryは既存`POST /v1/jobs/{job_id}/retry`に元の完全な`EnqueueJob` intentと
  caller管理keyを渡します。返却episode IDと保持quote/intentから既存Remember evidenceを再構成します。
  child作成後もcapture replayは**retry childでなく元のfailed job参照**を返します。

保持するopaque source/job/idempotency anchorにはcaller keyからserver HMACで導出した
内部composition keyも含みます。その内部keyを指定/生成する必要はなく、
MCP caller keyの自動生成や新API入力ではありません。
Native tenant HTTP response-drain境界は維持します。
保持anchor、host context、backup、WAL、配信済みdataについて新しい完全消去保証はありません。
capability feature `atomic_structured_capture`の`atomic_capture` metadataは
`endpoint: "/v1/captures"`、`max_jobs: 1`、
`recipe_version: "structured-remember-v1"`、`automatic_capture: false`です。
[差分契約](../STATUS-jp.md#atomic-structured-capture)と
[ADR 0010](../adr/0010-atomic-capture-jp.md)を参照してください。

## Local stdio MCPの運用

所有job照会はNative/SDK専用であり、MCPの4 bindingへjob toolは追加しません。

checkpoint head照会はNative/SDK専用で、MCPの4 bindingへcheckpoint toolは追加しません。

`memory_recall`はNative request wrapperで同じ型付き`filters`を受け付けます。
[構造化完全一致選択](#exact-structured-recall-filters)を参照してください。toolやsafe error codeは追加しません。

### 一つの信頼identityで導入・起動

1. 上記role分離に従ってNative APIを準備し、意図したsubject/scopeをprovisionします。
   adapterには**DB URL、admin資格情報、署名key、workerの`--subject`は不要**です。
   全callでNative API認証と現在のACL/削除検査を正とします。
2. `pg-agmemory[mcp]`を導入するか、extra導入済みrepository imageを使います。
   source checkoutでは`uv sync --frozen --extra mcp`でlock済み環境を準備します。
   固定版は公式`mcp==2.2.0`と`httpx==0.28.1`で、類似名の第三者MCP packageではありません。
3. 信頼するlocal hostのprocess環境に`PGAG_MCP_API_URL`と`PGAG_MCP_API_TOKEN`を
   安全に渡します。tokenをhost設定にcommitしたり、command-line引数、例、log、
   issue報告に残したりしないでください。tokenはMCP host用でなく**Native API audience用**
   で、Native APIが検証します。固定された信頼Native clientであり、
   MCP caller identityの転送ではありません。
4. `https://memory.example.com`のようなHTTPS origin、または`http://127.0.0.1:8000`の
   ようなloopback HTTP originを指定します。credential、`/v1`等のapplication path、
   query、fragmentは禁止です。root `/`は許可します。非loopbackの平文HTTPは拒否します。
   loopbackはadapterのprocess/container基準であり、自動的にMac hostや別containerを
   指すものではありません。container内adapterではAPIが同じloopback境界を共有する場合を
   除き、到達可能な信頼HTTPS originが必要です。TLS検証は有効を維持し、
   redirectとproxy環境設定は使いません。
5. hostからinstall済み実行fileを引数`mcp`で起動するよう設定します。

   ```bash
   pg-agmemory mcp
   ```

   checkout環境でvirtualenv実行fileがPATHにない場合は、
   `uv run --frozen --extra mcp pg-agmemory mcp`を使えます。
   `--subject`と`--once`は両方拒否するため付けません。
   stdin/stdoutは人間用promptや通常logでなくMCP message用に接続を維持します。
   診断にはsanitized stderrを使います。
6. 起動時に`GET /v1/capabilities`へ認証し、API `v1`、service `0.0.26`、schema `11`の
   一致を確認してからtoolを提供します。不正設定/token、API到達不能、version不一致は
   secretをlogに出さず非zero終了します。`/healthz`合格だけでは不十分です。
   信頼する設定を修正し再起動してください。検査を回避したり、
   tool引数でidentity/URLを上書きしたりしてはいけません。

remote MCP HTTP/SSE transport、OAuth、identity委譲、callごとのheader/URL/token上書きは
ありません。**信頼identityごとにadapterを一つ**起動し、trust domainをまたいで接続を
共有したり、network wrapperから公開したりしないでください。
token更新時は安全に渡した置換tokenで再起動します。自動refreshはありません。
前の操作を復旧する際は同じ認可済みsubjectを維持してください。

### 誤った重複書込みを避ける呼出しと復旧

toolは`memory_recall`、`memory_remember`、`memory_explain`、`memory_forget`だけです。
各toolは**Native Pydantic request body**を`{request: ...}`で包みます。
remember/forgetはpreviewも含め、**1〜256文字のvisible ASCII
（`0x21`〜`0x7e`、空白不可）**の`idempotency_key`を追加で要求します。
keyはtrim/書換えせず、256文字は許可、257文字は拒否します。
Native forgetは従来どおりpreview/purgeとも**HTTP 202**です。
statusだけをpurgeの証拠にせず、Native resultのvariantを確認してください。
host/callerは**dispatch前**にkeyと正確なbodyを安全に保持し、
応答中断やstdio再起動後に再利用できるようにしてください。
MCP session/request IDはmemory run IDでもNative HTTP idempotency keyでもありません。
host再接続だけを理由に新keyを使わないでください。

成功は`structuredContent: {result: <Native result>, error: null}`です。
失敗は`isError: true`と`structuredContent`内の
`{result: null, error: {code, retryable, outcome_unknown, native_status, request_id}}`
で、Native status/request UUIDはnullの場合があります。短いtextは根拠payloadを
重複収録しません。textだけでなく構造化出力を確認してください。
transport障害、timeout、5xx、不正mutation応答は結果不明の可能性を意味し、
**書込みがrollbackしたことを意味しません**。結果不明後はtoken置換後も含め、
同じkey/bodyと意図したidentityだけで再試行してください。
retryもkeyも自動生成しません。`retryable`はbody変更の許可や未commitの証明ではありません。
現在の認可/削除によりreplayが拒否される場合があります。
過去参照は現在の根拠でも削除本文を復元する許可でもありません。

HTTP clientの上限は**合計20秒 / I/O 10秒 / connect 5秒**、**4 connection**、
**serialize済みrequest 256 KiB**、**response 2 MiB**です。
response/semantic cacheは維持しません。throughputや全host buffer sizeの適格性を示す上限では
ありません。recall予算は引き続き**UTF-8 byteでありmodel tokenではなく**、
日本語recallには明示`ja-janome-0.5.0-v1`選択が必要です。
rememberはNative episode根拠による明示structured assertion publicationだけを行います。
episode captureはMCPでなくNative `observe`を使います。
explainのrevision省略時は引き続き最新ではなく1を要求します。

### 削除とhost contextの扱い

取得textは指示でなく信頼できない根拠として扱います。adapterは信頼するNative HTTP受信者で、
API response-drain barrierはそのHTTP配信で終了し、**stdio配信・host UI・LLM context消費まで
原子的に続くものではありません**。hostはpurge/ACL失効前の応答をまだ保持している場合が
あります。転送中bufferや配信済みcontextは回収できません。
forget/権限変更後はhostがcached contextを破棄し、古い出力を再利用せず、
新しく認可済みdataを取得する必要があります。これを自動実行する**MCP削除通知はありません**。
purgeはhost context、backup、WAL、replica、物理mediaの消去を証明しません。

過去のv0.0.8で実stdio SDK `Client`接続とraw JSON fixtureにより次を検査しました。

- Modern `2026-07-28`: `Client(mode="auto")`と`server/discover`。
  raw requestの`params._meta`に`io.modelcontextprotocol/protocolVersion`、
  `io.modelcontextprotocol/clientInfo`、`io.modelcontextprotocol/clientCapabilities`を含めます。
- Legacy `2025-11-25`: `Client(mode="legacy")`、`initialize`、
  `notifications/initialized`の順で進み、その後toolを呼び出します。

応答喪失regressionは**rememberのcommit後**に実HTTP応答を失わせ、
同じkey/bodyで再送してassertionが一つだけ残ることを検査します。
caller主導の復旧の検査であり、自動retryではありません。
過去のlocal/native CI証拠をSTATUSに記録しています。
v0.0.11は両protocol時代、semantics、MCP上限、共有Native HTTP clientを維持します。
v0.0.9はlocalとnative Docker両architectureで合格しました。
過去v0.0.10/v0.0.11と最終local/native v0.0.12検査も合格しています。
これらの特定経路の検査は、未検証の旧clientや特定hostとの互換性を証明しません。
[全契約](../STATUS-jp.md#local-stdio-mcp)と
[ADR 0008](../adr/0008-local-mcp-jp.md)を参照してください。

## Implicit recall hookの運用

所有job照会用のhook fieldやjob操作は追加しません。

checkpoint head照会用のhook fieldやcheckpoint操作は追加しません。

hook eventへ`filters`を追加しないでください。未知fieldは拒否し、
内部で構築する`Recall.filters`は`None`のまま、信頼する起動時境界を維持します。

hook入力は`required_memory_refs`を拒否し、内部`Recall`は`[]`を維持します。
成功時の空理由`empty_reason: "budget_exhausted"`は`200` / hook終了値0であり、
required-context Native/SDK/MCPの`422 budget_exhausted` errorとは別です。
hook packetにhost pinning fieldを追加しないでください。

**既存の読取り専用・lexical専用hookはv0.0.11でも検証済みです。**
vendor-neutralな一回実行のharness側commandであり、MCP、model caller、
自動登録host pluginではありません。Copilot/Claude/Codex連携を主張しません。

### 信頼するoperator環境からの導入と設定

1. 上記role分離に従ってNative APIのsubject/scopeをprovisionします。
   hookには**DB資格情報**、admin URL、JWT署名key、外部model keyは不要です。
   設定されたNative audience tokenだけを使います。
2. `pg-agmemory[hook]`を導入するか、`mcp`・`hook`・`sdk`を含むv0.0.26 repository imageを
   使用します。checkoutでは`uv sync --frozen --extra hook`を使います。
   hook-only導入は`httpx==0.28.1`を固定し、**MCP SDKは含めません**。
3. 信頼するharnessを起動する前にoperatorが以下の環境を安全に渡します。
   prompt、query、event field、tool、取得textから生成してはいけません。
   tokenをcommand-line引数、commitする例、log、issue報告へ残さないでください。

   | 変数 | Operator設定 / 既定値 |
   |---|---|
   | `PGAG_HOOK_API_URL` | 必須、defaultなし。信頼するHTTPS originまたはloopback HTTP origin。userinfo/application path/query/fragment禁止。root `/`は許可。URL未設定は`invalid_hook_configuration` |
   | `PGAG_HOOK_API_TOKEN` | 必須の固定Native API audience bearer token |
   | `PGAG_HOOK_SCOPE_IDS` | 必須の重複しないprovision済みscope UUID 1〜32件のJSON配列 |
   | `PGAG_HOOK_PURPOSE` | 既定`implicit_context`。1〜256文字 |
   | `PGAG_HOOK_TOKEN_BUDGET` | 既定`2000`。整数64〜2,000 **UTF-8 byte、model tokenではない** |
   | `PGAG_HOOK_MAX_ITEMS` | 既定`20`。整数1〜20 |
   | `PGAG_HOOK_SEARCH_PROFILE` | 既定`simple-v1`。`ja-janome-0.5.0-v1`には明示opt-in |
   | `PGAG_HOOK_TIMEOUT_SECONDS` | 既定`2.0`。有限の0.1〜20秒 |

   URL、token、scope IDは**すべて必須**です。共有`NativeSettings`は`httpx.URL`でも
   originをparseし、transport前に制御文字や不正IDNAを拒否します。
   これらの検査は過去のv0.0.9全3環境で合格しています。

   loopbackはhook process/container基準です。別containerやMac hostへ到達すると
   仮定してはいけません。非loopback HTTPは拒否します。
   redirect/proxy環境設定を無効にし、TLS検証を有効にします。
4. `pg-agmemory recall-hook`を起動し、stdinへJSON document一つとEOFを送ります。
   `--subject`/`--once`は拒否します。許可するのは`event`と`query`だけです。
   `event`は`session_start`、`task_switch`、`after_compaction`のいずれか、
   `query`は必須の最大4,096 Unicode文字のstringです。
   空queryは設定scope内のcanonical browsingであり、field省略ではありません。
   取得意図はJSONの`query`だけから渡し、`event`はlifecycle名です。
   identity、`scope_ids`、purpose、mode、budget、URL、header、tool、時刻、その他fieldは
   許可しません。event textはアクセス権を付与しません。
5. 呼出しごとに新しく認証付き`GET /v1/capabilities`で厳密なservice `0.0.26`、
   API `v1`、schema `11`を検査し、`POST /v1/recall`へ`mode: "implicit"`、
   信頼するrecall設定、Nativeの現在時刻defaultを送ります。
   両requestに同じ固定tokenを使い、認可やresponseをcacheしません。
   資格情報の置換も信頼する起動設定だけを使います。

recallは未認可scopeや失効membershipを黙ってfilterします。
許可済みsubsetまたは成功したitemなし/`not_found`となり、**scope存在を示す404にはしません**。
一方、token認証失敗は明示的なNative **401**、hookの**終了値1とerror envelope**になります。
Native動作の維持であり、hookのfallbackや権限付与ではありません。

stdin上限は**32,768 byte**です。不正UTF-8/JSON、上限超過、validation失敗は明示errorです。
network deadlineは**capabilitiesとrecallの合計**で共有し、別々の時間枠ではありません。
既定2秒（有限の0.1〜20秒）はprocess起動、stdin入力/EOF待機、出力を含まず、
process全体やLLM latencyのSLOでは**ありません**。
host側に別のsubprocess timeoutを設定し、stdinを閉じてください。
request/response上限は**256 KiB/2 MiB**です。
**context pack全体をcompact JSON serializeした結果**のUTF-8 byte数が
`context_pack.byte_count`と一致し、設定byte予算以下である必要があります。
`ensure_ascii=False`、`separators=(",", ":")`を使い、
**本文だけでなくmetadata/citationも含みます**。
返却item数は設定`max_items`以下、返却profileは設定と一致しなければなりません。
不一致は失敗させ、広いfallbackは行いません。
全host buffer共通の上限ではありません。共有client抽出でも上記MCP上限を維持する必要があります。
hookは書込み、capture、enqueue、LLM/provider呼出し、cache、retry、
idempotency key送信を行いません。

予算64は有効な設定ですが、空packでも約192 byteを要します。
metadataが収まらなければNativeは**422 `budget_too_small`**、
hookは**終了値1**のerror envelopeを返し、空の成功にはしません。
空packの概算sizeを保証された定数として扱ってはいけません。
packは収まるが候補が収まらない`budget_exhausted`はNative **200 / hook終了値0**です。
index欠落の`index_incomplete`も**200 / 終了値0**で、
projectionが欠けて候補がない場合は`coverage.lexical_incomplete: true`、
`coverage.retrieval_complete: false`になります。
hookが成功してもhostは不完全coverageを表示しなければなりません。

### Vendor-neutral Python harness例

実行fileとoperator環境を準備してから以下のshell blockを実行します。
Python標準libraryだけを使用します。この例のhost policyは**失敗または不完全な取得で停止**
することであり、黙って継続しません。別のhostがmemoryなしで継続する場合も、
明示的に選択し表示する必要があります。
30秒のsubprocess timeoutは例示的な**別のhost policy**であり、
測定済み起動保証やservice SLOではありません。配置に合わせて設定してください。
信頼する実行file/PATHと起動環境はoperatorが制御する必要があります。
空のsample queryに外部modelは不要です。hostは信頼できないtask textを
`query`だけに渡せますが、設定には使えません。queryをlogに残してはいけません。

```bash
python - <<'PY'
import json
import os
import shutil
import subprocess
import sys

def pause(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)

setting_names = (
    "PGAG_HOOK_API_URL",
    "PGAG_HOOK_API_TOKEN",
    "PGAG_HOOK_SCOPE_IDS",
    "PGAG_HOOK_PURPOSE",
    "PGAG_HOOK_TOKEN_BUDGET",
    "PGAG_HOOK_MAX_ITEMS",
    "PGAG_HOOK_SEARCH_PROFILE",
    "PGAG_HOOK_TIMEOUT_SECONDS",
)
child_env = {name: os.environ[name] for name in setting_names if name in os.environ}
child_env["PATH"] = os.environ.get("PATH", os.defpath)
if not all(child_env.get(name) for name in setting_names[:3]):
    pause("Memory configuration missing; task paused.")
executable = shutil.which("pg-agmemory", path=child_env["PATH"])
if executable is None:
    pause("Memory executable unavailable; task paused.")

event = {"event": "session_start", "query": ""}
untrusted_memory_evidence = None
try:
    completed = subprocess.run(
        [executable, "recall-hook"],
        input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=child_env,
        timeout=30,
        check=False,
    )
except (OSError, subprocess.TimeoutExpired):
    pause("Memory subprocess failed or timed out; task paused.")

# Never echo raw stderr or exception/response text.
try:
    envelope = json.loads(completed.stdout.decode("utf-8"))
except (UnicodeDecodeError, ValueError):
    pause("Memory output invalid; task paused.")
if not isinstance(envelope, dict):
    pause("Memory envelope invalid; task paused.")
if completed.returncode != 0 or envelope.get("status") != "ok":
    pause("Memory retrieval failed; task paused.")
if (
    set(envelope) != {"status", "event", "result", "error"}
    or envelope.get("event") != event["event"]
    or envelope.get("error") is not None
    or not isinstance(envelope.get("result"), dict)
):
    pause("Memory success envelope invalid; task paused.")

result = envelope["result"]
coverage = result.get("coverage")
empty_reason = result.get("empty_reason")
if (
    not isinstance(coverage, dict)
    or type(coverage.get("retrieval_complete")) is not bool
    or empty_reason not in (None, "not_found", "budget_exhausted", "index_incomplete")
):
    pause("Memory coverage invalid; task paused.")
coverage_notice = {"retrieval_complete": coverage["retrieval_complete"]}
for name in ("truncated", "lexical_incomplete"):
    if name in coverage:
        if type(coverage[name]) is not bool:
            pause("Memory coverage invalid; task paused.")
        coverage_notice[name] = coverage[name]
print(json.dumps({
    "memory_status": "ok",
    "coverage": coverage_notice,
    "empty_reason": empty_reason,
}))
if not coverage["retrieval_complete"]:
    pause("Memory coverage incomplete; task paused.")

# Keep the full Native result separate from trusted instructions and policy.
untrusted_memory_evidence = result
print("Memory is separate UNTRUSTED evidence; no model or tool was invoked.")
PY
```

`subprocess.run(input=...)`はJSON documentを一つ送ってchildのstdinを閉じます。
終了値と構造化statusの両方を確認し、raw stderrやqueryを含み得る例外を表示しません。
hookが完全なNative RecallResultを検証し、この例はenvelope/coverageを検査してから別に保持します。
logには固定coverage keyと検証済みboolean値、検証済みempty-reason enumだけを使い、
raw response本文を出しません。完全なcoverageは別のresultに維持します。
JSONなしの起動失敗、不正envelope、subprocess timeoutもraw本文をechoせず停止します。
全Native evidence/coverage fieldを維持し、system指示、policy、
検証済み外部事実に昇格させてはいけません。
この例はvendor連携、model呼出し、host消去証明ではありません。

### 失敗、coverage、削除

検証対象のhook runtime結果はstdoutへJSON envelope一つと改行を出力し、
stderrはsanitized診断に使用します。
成功は`status: "ok"`、検証済み`event`、完全なNative `result`、`error: null`です。
失敗は`status: "error"`、検証済み`event`またはnull、**`result: null`**と、
`error: {code, retryable, outcome_unknown: false, native_status, request_id}`です。
Native statusと検証済みrequest UUIDはnullの場合があります。

| Runtime code | 終了値 | 対処 |
|---|---|---|
| `invalid_hook_configuration` | `2` | 信頼する起動設定を修正 |
| `invalid_hook_input` | `2` | 入力をlogに残さずUTF-8/JSONやevent/query検査errorを修正 |
| `hook_input_too_large` | `2` | stdinを32,768 byte以内にする |
| `hook_input_unavailable` | `2` | 読取り可能なstdinを渡す |
| `hook_deadline_exceeded` | `1` | 合算network deadline超過。`retryable: true` |
| `native_api_unavailable` | `1` | Native API transport利用不能。`retryable: true` |
| `native_version_mismatch` | `1` | 対応するservice/API/schema版を使う |
| `invalid_native_response` | `1` | 不正protocol/responseを拒否し、fallbackしない |
| `budget_too_small` | `1` | Native `422`。pack metadataが収まらないため信頼するbyte予算を調整 |
| Mapping済みsanitized Native code | `1` | raw詳細を転送せずNative失敗を表示 |

**起動方法のerrorは例外です。** CLI flag拒否（`--subject`/`--once`を含む）と
`hook` extra未導入は、argparseのstderr診断と**終了値2で、JSON envelopeを返しません**。
上記設定/入力codeを含む検証対象のhook runtime errorはすべてerror envelopeを返します。
検査/parse前にstdoutをJSONと仮定せず、raw stderrや不正出力をechoしてはいけません。

終了値**0**はNativeの正当な`not_found`、`budget_exhausted`、`index_incomplete`空理由も
含みます。取得成功でもcoverageを確認してください。
**2**は不正設定/入力、**1**はNative/network/version/protocol障害です。
hostがkillしたprocessはenvelopeを返さない場合があります。
**取得失敗を空の成功へ変換したり、古いcontextで隠したりしてはいけません。**
error/coverageを表示し、停止かmemoryなし継続かを明示判断します。
`retryable`はhintにすぎません。hookは自動retryやidempotency keyを持たず、
`outcome_unknown: false`は読取り専用操作を反映し、MCP mutation動作を変更しません。

Native tenant session advisory barrierは**信頼するlocal hook**へのHTTP配信で終了します。
hook/stdout/pipe bufferやhost contextは原子的な対象ではありません。
回収や削除通知はありません。
forget/ACL変更後は以前のcontextの利用を停止/破棄し、現在の認可で新しくhookを呼び出します。
変更前の転送中結果をfreshと仮定してはいけません。
hookは権限を拡大せず、host/WAL/replica/backup/物理media消去を証明しません。
技術検査は特定vendor連携、意味品質、性能の適格性を確認するものではありません。
[全契約](../STATUS-jp.md#implicit-recall-hook)と
[ADR 0009](../adr/0009-implicit-recall-hook-jp.md)を参照してください。

<a id="v008のapplication更新schema変更なし"></a>

<a id="v009のapplication更新schema変更なし"></a>

<a id="v0010のapplication更新schema変更なし"></a>

## 過去のv0.0.10のapplication更新（schema変更なし）

**過去のschema 7専用workflowであり、v0.0.11 upgradeではありません。**
現在の版は[schema 11 migration](#schema-11-scope-capture-policy-upgrade)を使います。

既存v0.0.7/v0.0.8/v0.0.9のschema 7 DBには**migration 008/009/010も新backfillもありません**。
application/schema版を記録し、backupと現在の削除/ACL記録を保全します。
自動再起動を含む旧API/worker/MCP adapterを停止/drainしてhook起動も止め、
対応するv0.0.10 imageへ置換します。
厳密なschema履歴`[1, 2, 3, 4, 5, 6, 7]`を確認後、制限付きNative API/workerを起動し、
認証付きcapabilitiesを検査して各固定identity adapter/hookを起動します。
schemaが同じでもrolling混在互換性や対応済みdowngradeの根拠にはなりません。
v0.0.10のpackage化runtimeと既存schema契約はlocalとnative Docker両architectureで合格しました。
過去の結果は[検証証拠](../STATUS-jp.md#検証証拠)に分けて記録しています。
この過去workflowで旧schemaを扱う場合は対応v0.0.10 imageを使います。
reindexは別のoffline保守であり、MCP/hook commandではありません。

<a id="v003の保守migration"></a>
<a id="v004の保守migration"></a>
<a id="v005の保守migration"></a>
<a id="v006の保守migration"></a>

## v0.0.7の保守migration

**v0.0.7〜v0.0.10だけを対象とする過去のschema 7手順です。**
古いschemaからv0.0.26へ更新するには[migration 008](#schema-8-pgvector-upgrade)、
[009](#schema-9-scope-access-upgrade)、[010](#schema-10-job-cancellation-upgrade)と
[011](#schema-11-scope-capture-policy-upgrade)も必要であり、
ここで説明するschema 7 processを再起動してはいけません。

旧DB用に残しているv0.0.7導入時のschema 7 migration手順であり、
**v0.0.8/v0.0.9/v0.0.10の新migrationではありません**。

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
   意図した固定worker subjectで**対応するv0.0.10 API/workerだけを起動**します。
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
中国語recallを適格としません。このlexical profileはembedding modelやfileベースのmemory indexではなく、
vector/hybrid modeは別です。context予算は別の`utf8-bytes-v1`契約を維持します。

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
lexical modeの空queryはflagがあってもcanonical itemをbrowseします。
正確なrequired参照もprojectionを修復せずcanonical itemを選択できます。
query/required候補なしでprojection欠落があれば`empty_reason: "index_incomplete"`です。
optional-only候補が一つも収まらなければ成功時の空理由`"budget_exhausted"`を維持し、
required itemが収まらなければ`422 budget_exhausted`です。
非空結果の`empty_reason`はnullです。
simple profileへの黙ったfallbackや修復workerはありません。
simple検索の選択は日本語projectionを修復しません。
破損辞書logは入力textを含まない`japanese_dictionary_error`へ除去処理します。
Janomeの`SystemExit`はtokenizer-unavailableへ変換し、`index_incomplete`ではなく
APIの`503 dependency_unavailable`となります。
workerは入力をechoせず既存の上限付き依存障害retryを使います。

reindexはworkerのprincipal/scope単位でなく、**選択DBの全tenant**を再構築します。
`--subject`は範囲を狭めるoptionではなく明示拒否し、`--once`もworker専用として拒否します。
migration済みschema 8 DBのlexical projectionをcanonical dataから再構築する手順
（v0.0.11管理toolで検証済み。embeddingは再構築しません）:

1. 自動再起動を含む**全API/workerを停止/drain**し、migration同様にbackupします。
   offline保守であり、稼働中の管理APIではありません。
2. 対応するv0.0.26 imageと**`PGAG_ADMIN_DATABASE_URL`**を使用し、forced RLS bypassと
   必要なtable権限を持つ管理者で次を実行します。

   ```bash
   pg-agmemory reindex-lexical
   ```

3. 厳密な履歴`[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]`と対応extensionを使い、commandは5秒のlock timeoutで
   migration advisory lockを取得して、両projection tableを一つのtransactionで置換します。
   tombstoneを除く全保持episode/assertion revisionを分割し、headだけに限定しません。
   canonical ID、system time、根拠、receipt、同期request hashは変えません。
   JSON出力は`profile: "ja-janome-0.5.0-v1"`と整数の
   `episodes`/`assertion_revisions`件数だけで、source本文/tokenは含みません。
4. 一部置換後の失敗でも既存lexical projectionを維持します。
   traffic停止を維持し、schema、権限、lock競合を調査します。
   index修復のためruntime bypassを付与したり、canonical本文、timestamp、receiptを
   編集したりしてはいけません。
5. 対応するv0.0.26 API/workerだけを再起動します。traffic再開前に許可済みtest dataで
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
同一scope readerはGETできますが、他principalのjobを実行/retry/cancelできません。
[所有job照会](#owned-job-query-and-pagination)で発見できるのはscope-admin権限があってもcaller自身のjobだけで、
workerは呼び出しません。

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
   terminal成功/失敗/取消ではrequestを消去し、成功resultは後の訂正後もrevision 1です。
   explainには結果の正確なassertion revisionを使います。
   assertionのrecorded/system timeはjob enqueueでなくworker publication時に始まります。
   jobの`created_at`をassertionの採用時刻として使ってはいけません。
4. 自動retry可能な失敗は`2^attempt + [0,1)`秒のbackoff/jitterを使います。
   `invalid_input`は即時失敗で、期限切れの5回目claimは`attempt_limit`となり6回目はありません。
   payloadをlogへ出さず、安全な`dependency_unavailable`/`stale_context` codeで診断します。
   `lease_lost`は古い準備bodyからの再publication許可ではありません。
5. 所有するterminal failed jobには、元の`EnqueueJob` body全体とkeyを
   `/v1/jobs/{job_id}/retry`へPOSTします。現在の根拠/権限とHMAC intentを再検査し、
   intent変更は`409 job_intent_conflict`、cancelledを含むfailed以外のparentは`409 job_retry_conflict`、
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
[明示取消](#explicit-job-cancellation)ではstate/attempt CASと共有tenant barrierが競合を決定します。
取消は古いpublicationを拒否しますが、commit済みresultの公開撤回や外部作業の中断はしません。

`observe`は自動enqueueせず、同期`remember`とlegacy JSON/HMACは変更しません。
recallの`jobs_pending`は要求scopeの読取り可能なpending/running jobを対象としcancelledを除外して、
`synthesis_pending`と`graph_used`はfalseのままです。
jobはrecall/explain itemやcheckpoint/effect参照kindではありません。
[契約](../STATUS-jp.md#durable-job)と[ADR 0006](../adr/0006-durable-jobs-jp.md)を参照してください。
M0/M1/M2/M3、MVP/本番、性能、品質、DRの受入は未完了です。

## Entityとgraphの運用

[完全一致entity照会](#exact-entity-query-and-pagination)で読取り可能scopeの別candidate IDを発見します。
所有権filterなしで共有scopeのentityも含むため、GET根拠を確認してgraph seedを明示選択してください。

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
3. recall/explainでなく既知checkpoint ID指定GET、または正確なscope/run/branchの
   [head照会](#checkpoint-head-lookup)で読み込みます。GETは最新headとは限らず、
   head照会は失効branchからfallbackしません。
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
5. 結果が不明なwriteは同じkey/payloadで再送してからheadを確認します。
   head照会だけではそのwriteのcommitを証明しません。
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

これは台帳であり、worker、tool実行用harness adapter、provider照会client、承認サービス、
外部exactly-once機構ではありません。[契約](../STATUS-jp.md#tool-effect-ledger)と
[ADR 0004](../adr/0004-tool-effects-jp.md)を参照してください。

## Revisionの運用

上限付きread-only発見には[assertion metadata履歴](#assertion-metadata-history)を使います。
過去の認可でもCAS予約でもありません。

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
認可の正しさ、本番readinessを保証しません。
[runtime readiness](#runtime-readiness)の`GET /readyz`は上限付きread-only契約を検査し、
認可全体や書込み可能性を証明しません。両probeとも認証付きresource検査の代用ではありません。
memory resource要求のDB/lock障害は`503`になり得ます。
mutationの結果が不明なら、新しいkeyを作らず同じkey・同じpayloadで再送してください。
commit済みでもHTTP応答だけ失われる場合があります。

## Membership変更とrequest drain

**現在の既定はCAS-safeな[scope-access CLI](#scope-access-administration)です。**
session lockをcommitとstdout flushまで保持し、原子的epoch/audit更新を行います。
下記手書きsequenceは**過去のexpert fallback**でありschema 9/10用の完成手順ではありません。
expected-epoch CAS、条件付きepoch増加、`scope_access_event` auditが欠けるため、
schema 9または10でそのまま実行してはいけません。expert overrideは現在の全不変条件を
実装する必要があり、それができなければ対応CLIを使います。直接SQL bypassはCLIの保証対象外です。

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
過去のv0.0.8 runnerはnon-root production image内で実`pg-agmemory mcp` childも起動します。
固定tokenとprovision済みscopeでloopback Native APIへ接続し、
modern `2026-07-28`・legacy `2025-11-25`の**両mode**で4 tool列挙とrecallを検査します。
既存の日本語/API/worker smokeも維持し、v0.0.8の3環境すべてで合格しています。
v0.0.8のCI step名は`Test containers and smoke-test production API, worker, and MCP`です。
idle-worker検査はpublicationテストや本番/DR適格性確認ではありません。
過去のv0.0.7実装
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)は、
local Apple Containerと完全一致SHAのnative Docker
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)で合格し、
各環境で全3種のproduction smokeも合格しました。
詳細は[STATUS](../STATUS-jp.md#検証証拠)に記録していますが、本番/DR受入の主張ではありません。
過去の最終docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)も、
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899)で
両native jobが合格しました。**過去のv0.0.8**の実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)は、
ローカルと両native architectureの
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)で、
214テスト、Ruff、strict mypy（13ファイル）、全production smokeに合格しました。
二つのv0.0.7 runはMCP adapterを検証していません。
その後のv0.0.8 bilingual docs commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)は、
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509)で
**各native architectureで214テスト**に合格しました。
これらの過去runはv0.0.9 hookや共有client抽出を検証していません。
**過去のv0.0.9の最終結果を2026-09-17 JSTに確認しました。**
Apple Containerとnative Docker amd64/arm64の各環境で**274テスト、既存warning 1件**、
Ruff、strict mypy（**source 15ファイル**）、真のcore-only/hook-only導入検査、
non-root productionの日本語/API/worker、
MCP **`2026-07-28`・`2025-11-25`**、
hook **`session_start`・`task_switch`・`after_compaction`**の全smokeに合格しました。
テスト所要時間はlocal **248.29秒**、native amd64 **482.21秒**、native arm64 **374.33秒**です。
検査した最終local sourceは公開済み実装
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)と一致します。
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)の
両jobの実logで、job statusだけでなく完全一致SHAと全検査を確認しました。
所要時間は性能benchmarkではありません。
[v0.0.9証拠](../STATUS-jp.md#v009--schema-7)を参照してください。
本番/DR受入の主張ではありません。
実装済みのDocker **`adapter-extras-check`** targetは真のcore-only導入/extra未導入、
続いて**MCPなし**のhook-only導入を検査し、HTTP失敗時の明示JSONも対象とします。
container scriptはlocal Apple Containerとnative Docker両architectureでこのtargetをbuildします。
**全3環境で合格**しました。
元のmilestoneや受入gateの完了ではありません。
最終v0.0.9 docs
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)も、
[CI 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689)で
両native architecture各**274テスト**に合格しました。
過去の結果であり、v0.0.10証拠ではありません。

**過去のv0.0.10の最終localとnative結果を2026-09-17 JSTに確認しました。**
Apple Containerとnative Docker amd64/arm64は各**304テスト、既存warning 1件**に合格しました。
**Ruff、strict mypy（source 16ファイル）、真のcore-only/hook-only導入検査、
non-root productionの全smoke**も全3環境で合格しました。
日本語/API/worker、MCP両時代、hook全3 event、atomic captureが対象です。
テスト所要時間はApple Container **275.53秒**、native amd64 **467.75秒**、
native arm64 **434.40秒**です。
最終local sourceは公開済み実装
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f)と一致します。
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)は
両native jobとも合格し、実logでjob statusだけでなく完全一致SHA、件数、所要時間、全検査を確認しました。
検査はrollback fault、source/key重複抑止の競合、quota/RLS/削除、
API process再起動と実workerを対象にします。
全3環境で合格した新規fixtureのproduction smokeはMCP/hook検査後に、Native capture → pending job →
実worker CLI `--once` → episode/assertion recall → 同capture replay →
source purge → job GET `404`とcapture replay `404`を確認します。
全3環境の最終runには、commit済みHTTP 201応答喪失後のsame-key復旧で正確な同一組と一つのpublicationを
確認する検査と、明示retry child作成後も元のfailed capture jobをreplayする検査も含めます。
所要時間は性能benchmarkではありません。全受入gateは未完了です。
[v0.0.10証拠](../STATUS-jp.md#v0010--schema-7)を参照してください。
最終v0.0.10 docs
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)も、
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760)で
**各native architecture 304テスト**に合格しました。
このdocs runは上記実装runの所要時間とは別で、両runともv0.0.11/schema 8を検証していません。

**v0.0.11 application検査は全3環境で合格しました**。新しい固定上流DB profile、
migration/schema/extension版/schema guard、正規化/digest/不変model空間上限、exact/RRF数学、
ACL/時間の事前filter、coverage、purge/replay、既存lexical/MCP/hook/capture検査が対象です。
実装fixtureにはDB norm/次元/composite FK/8 model guard、直接RLS可視性/UPDATE拒否、
ACL失効、実際のschema 7→8でledger失敗時のDDL/extension rollback後のretryも含み、
backfillは行いません。production vector smokeは**episode/assertion両projection**をuploadし、
basis distance **[0, 1]**とRRF、source purge後のupload replay `404`を検査します。
各環境で**345テスト、既存warning 1件**、Ruff、strict mypy（**source 17ファイル**）、
core-only/hook-only導入検査、productionの全smokeに合格しました。
テスト所要時間は**local 283.44秒、amd64 404.40秒、arm64 433.46秒**であり、性能benchmarkではありません。
公開済み実装[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)は
[CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403)に合格しました。
[検証証拠](../STATUS-jp.md#v0011--schema-8)を参照してください。
artifact検査とextension pin/licenseは別途確認済みです。
合成basis vector fixtureは意味品質、性能、untrusted vectorへの頑健性、本番、DR、完全消去を認定しません。
