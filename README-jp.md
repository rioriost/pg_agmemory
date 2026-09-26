# pg_agmemory

[English](README.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**MITライセンスのPostgreSQLベースAgent Memory Serviceです。**
公開リポジトリは[`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory)、
ローカルcheckoutディレクトリ・Pythonパッケージ・サービス名は`pg_agmemory`です。
以下のコマンドはこのローカルcheckoutから実行してください。

**M5開発: 0.4.0.dev1 / API v1 / schema 22。**
新しい[運用foundation](docs/operations/README-jp.md#m5-operational-foundations)に、
明示的なread-only embedding移行確認とone-shot監視exportを追加します。
[実装と配置先受入れは別](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md#m5実装と配置先受入れの区別)であり、
本番保持期限、capacity、RPO/RTOには対象環境とpolicyの指定が必要です。同じfoundationは
read-only運用/複製観測、pauseを維持するPITR、所有primaryのfencingを確認するHA rehearsalを含めます。
reportはservice起動許可、本番HA、RPO/RTO、backup保持期限の認定ではありません。
v2 HA rehearsalではreplay停止中のCOMMIT期限超過を別途観測し、
不明な書込みをretryせず、fencing前に物理replicaと照合します。
v3では昇格先から新しい非同期standbyも構築しますが、
旧primaryのrejoinやservice再開を承認するものではありません。
v4 labは続いて同期policyを明示的に再構成し、制限付きwriterが置換先replayを待つことを観測しますが、
servingは承認しません。
v5 labでは所有複製接続の一時拒否も行い、接続復元後に不明COMMITを照合しますが、
retryやservice再開は行いません。
書込み境界では[COMMIT結果不明](docs/operations/README-jp.md#unconfirmed-commit-outcomes)を拒否します。
同期待機cancel後のlocal commitをrollbackや複製完了とは扱わず、
workerは再試行せず照合のため停止します。
guard対象COMMITの応答待ちは別の[5秒local budget](docs/operations/README-jp.md#commit-acknowledgement-deadline)で制限し、
期限超過もrollbackではなく結果不明と扱います。
Native `/v1/` requestには、エラー応答用1秒を含む累積
[30秒のapplication budget](docs/operations/README-jp.md#native-request-deadline)も設け、
COMMIT中の期限超過は引き続き結果不明として扱います。
migration022は遅延revision制約でのhistory/evidence再走査を減らし、期限とRLSは緩めません。
[schema22移行手順](docs/operations/README-jp.md#schema-22-revision-validation)に従ってください。
公開済みM4 releaseと証跡は変更しません。

**M4 integration pilot: v0.3.0 / API v1 / schema 21。**
管理者専用[source-access coordinator](docs/operations/README-jp.md#m4-durable-source-access-coordinator)で、
通知順序を永続管理し、read leaseを原子的に更新します。
再送で権限を更新せず、通知欠落時はfail closedにします。
migration021と同一versionのcomponentが必要で、AGE再開前にgraph artifactを再構築します。
宣言した明示保持pilotの範囲でM4を完了しました。
[release証跡と制限](docs/STATUS-jp.md#m4-integration-pilot-v030)を参照してください。

[署名付き通知receiver](docs/operations/README-jp.md#signed-source-notice-receiver)は、
管理者が固定した公開鍵とsource/reader対応でRS256通知を検証してからcoordinatorへ渡します。
local管理commandであり、公開webhookや上流ACLの独立照会ではありません。

[dataset管理command](docs/operations/README-jp.md#registered-dataset-readers)で
最大100件の登録済みreaderを発見し、対象集合digestとtenant access epochを照合して
membershipを原子的に失効できます。緊急操作であり、
datasetの恒久block、上流削除、物理purgeではありません。

任意のLangGraph safe-boundary pilotでNative SDKを使い、
明示capture/recallと型付きcheckpoint/restoreを接続します。
汎用LangGraph checkpointerではなく、副作用は実行しません。
[pilot契約](docs/operations/README-jp.md#m4-langgraph-safe-boundary-pilot)を参照してください。
続く[外部source snapshot pilot](docs/operations/README-jp.md#m4-external-source-snapshot-pilot)では、
履歴provenance envelopeと保存の部分結果を追加し、
client側checkだけでなく専用scope leaseとNative削除を使います。
[計画付きsource purge](docs/operations/README-jp.md#planned-source-snapshot-purge)は、
terminal通知とcapture停止後に物理snapshot rootを発見し、
toolを再実行せずNative provenance closureを削除します。
[M4受入れinventory](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md#m4明示保持pilotの受入れ)で
明示保持pilotを定義します。source固有の本番connector、自動長期保持、
M5のHA/PITRはこのreleaseの範囲外です。
以下の公開済みM3 releaseは変更しません。

## M3 graph MVP: v0.2.0 / API v1 / schema 20

**エージェントとLLMの記憶基盤であり、判断システムではありません。**
限定graph MVPの契約として、canonical PostgreSQL dataを正本に保ち、
SQLを既定、別固定の修正済みAGEをopt-inとします。
capability stageは`m3-graph-mvp`で、service/SDK/MCP/hook/workerのversionを揃えてください。
0.1.3からの昇格によるmigrationやquery動作の変更はありません。
固定versionのnative配布、隔離復元、別固定graph資源runが成功した後だけsource tagを公開します。

[M3配置・upgrade契約](docs/operations/README-jp.md#m3-graph-mvp-deployment)と
[release証跡](docs/STATUS-jp.md)を入口にしてください。
source releaseはhosted service、PyPI/registry image公開、本番/HA/PITR認定、
modelの判断保証を意味しません。以下の旧milestone節は履歴であり、この契約を上書きしません。

隔離復元では、変更のないenabled AGE registryを
`recovery-apply apply --isolated --disable-age-projection`で明示的に扱えるようになりました。
署名付き最新状態を照合し、投影を原子的に無効化して、その意図的な差分を報告します。
再構築/publishは別の明示操作で、自動再起動や新世代の取込みは行いません。
schema 20以降のmigration追加はありません。
旧物理投影を意図的に除外するcanonical-only復元には、条件付きの
`age-projection publish --rebuild-missing`を使います。

**修正済みAGEを明示選択できるgraph backendとして有効化しました。**
`Dockerfile.age-patched`はローカルAGE commit
`72707aab7ce982bf13cad3d102bd869dab07d64b`の完全一致source treeをbuildし、
旧rc0 buildの改名で代用しません。
`pg-agmemory age-projection publish`で検証済み世代を公開した後、
`PGAG_GRAPH_BACKEND=age`で`/v1/graph/expand`がnativeの上限付きVLEを使用します。
RLS・現行canonical認可・順序・予算を維持し、投影の欠落/無効/staleは明示エラーにします。
SQLへの黙ったfallbackはせず、SQLは既定および明示的な切戻し先として残します。

upgradeは停止/drain、migration 020、componentの整合が必要です。
現行artifactはschema 20で、schema 19の旧receiptは読めますがstaleとして扱います。
[修正済みAGEの設定と復元制限](docs/operations/README-jp.md#patched-age-enabled-profile)を参照してください。
warm graph recipeの認定対象はscope当たり可視node 12/64件で、
artifact上限全域、同時writer、cold cache、全量S corpus同居ではありません。
全AGE catalogの往復復元は範囲外とし、canonical-only復元後の明示的投影再構築を対応経路とします。

## 以前の開発checkpoint: v0.1.1 / schema 19

M3で**管理者専用のgraph世代metadata**を追加しました。graph backendの有効化ではありません。
`pg-agmemory graph-generation`は入力fingerprint、世代関係、artifact receiptを
revision/input CASで記録します。receiptからartifact検証やserving許可を推測しません。
有効なgraph backendはSQLのみで、高コストな固定hop候補と未認定native VLEは無効です。
migration 019前に停止/drainし、componentを揃えてschema 19復旧artifactを再生成してください。
[世代管理の運用](docs/operations/README-jp.md#graph-generation-metadata-schema-19)を参照してください。
M3は未完了で、公開済みM2のrollback点はtag `v0.1.0`です。

`pg-agmemory graph-artifact export/check`で、既存世代の非公開・決定論的なcanonical topology
artifactを作成/照合できます。IDと時刻付きedge revisionを含み、labelや原文は含めません。
不変のrecorded世代は同じbytesへ再構築し、鍵付き署名と現在のcanonical dataの両方を照合します。
graph backendの有効化や世代metadataの変更は行いません。
[artifact運用](docs/operations/README-jp.md#canonical-graph-artifacts)を参照してください。

## 公開済みM2契約: v0.1.0 / schema 18

**エージェントとLLMの記憶基盤であり、判断システムではありません。**
scope付き保存/検索/更新、非破壊圧縮、限定隔離復元、model呼出し会計、資源上限、
単一参考benchmarkについて、M2で宣言したengineering gateを達成しました。
0.1.0ではrelease metadataとcapability stageを`m2-core-mvp`へ更新し、migrationは追加しません。
公開前に固定したrelease buildの両native認定を要求します。

**管理された配置向けのcore MVP**であり、本番/HA/PITR認定やmodelの判断保証ではありません。
Native API v1/SDKは38 memory resource、MCPは4 tool、hookはread-onlyです。
generation/embedding処理は既定拒否で、管理者が固定したlocal profileを必要とします。
SQL graphは対応し、AGE/SQL-PGQ連携はM3です。
[現行の配置/upgrade契約](docs/operations/README-jp.md#m2-core-mvp-deployment)と
[適格性の証跡・制限](docs/STATUS-jp.md)を先に確認してください。
以下の過去の記述・実測値は保存した履歴であり、この現行契約を上書きしません。

限定復元drillは、抽出・採用・vector・working snapshot/tail・SQL graph・tool-effectの
保持対象とpurge対象を含みます。backup内の変更されていないsuppress/purge混在prefixは
保持しますが、新しいsuppressのreplayは未対応です。migrationや公開APIは追加しません。
[復旧運用](docs/operations/README-jp.md#schema-15-operational-state-application)を参照してください。

[単一の参考記憶benchmark](docs/EVALUATION-jp.md#単一の参考記憶benchmark)に固定profileと結果JSONを
まとめました。typed state・coverage/tail・呼出し会計の保持、1 caseの時間/量と失敗履歴を示します。
既存の実測証跡であり、新しいmodel比較・意味的合格判定・schema 18上のlive model再実行ではありません。

migration 018ではtombstone metadataのread権限も集合処理にします。
大規模な削除履歴でもtenant/scope/期限の認可条件は変更しません。

migration 017は削除可視性をtenant内・statement内のtombstone集合として評価します。
RLSやPostgreSQL planner設定を緩めず、削除後に実測した実行計画の劣化へ対応します。
migration前に停止/drainし、現行schemaの復旧artifactを再生成してください。

migration 016で、tenant/scope/期限/tombstone条件と強制RLSを維持したまま、
read可視性を集合処理にしました。recallは順位計算とcoverageで候補のmaterializationを共有します。
RLS無効化、順位規則変更、barrier緩和、ANN有効化はしません。
対応componentと現行schemaの復旧artifactを使い、schema 15のdata/復旧鍵は保持します。

任意指定の`create_app(..., timing_sink=...)`で、本文・query・actor labelを記録せずに
commit済みのserver timingを測定できます。既定は無効で、HTTP endpoint/headerは増やしません。
固定したS資源recipeと、実HTTP・worker 2本の負荷harnessを追加しました。
[資源測定](docs/operations/README-jp.md#resource-measurements)を参照してください。
development/preflight測定をS認定やM2完了とは扱いません。

`recovery-apply export/apply`で、隔離され、保持する本文が一致する復元先へ、
元のreceipt/idempotency、現行ACL/policy、job状態、呼出し会計を適用できます。
bundle認証にはruntimeから読めない専用の復旧鍵を使います。
CAS、構造制約、適用後fingerprintが一つのtransaction内ですべて一致しなければ、
変更全体をrollbackします。runtime roleは履歴書込みcontextを有効化できません。
限定した管理操作であり、自動消去、任意時点復旧、service起動許可ではありません。
[schema 15復旧運用](docs/operations/README-jp.md#schema-15-operational-state-application)を参照し、
upgrade後に復旧鍵を含む新しいschema 15 backupを取得してください。

管理者専用の`processing-recovery export/check`を追加しました。
現行policy、ACL、呼出し予約、意味的job identity、job状態と関連運用metadataを
一貫したsnapshotで照合し、不一致なら非0で終了します。
この照合command自体はDBを変更せず、**一致しても再開を許可しません**。
上記の限定した適用操作には別途前提条件があります。
[処理状態の復旧照合](docs/operations/README-jp.md#processing-state-recovery-check)を参照してください。
現行開発版のcomponentはv0.1.1 / API v1 / schema 19に揃えます。照合はmodelを呼びません。

schema 14で**transactionに結び付いた削除対象manifest**と管理者専用の
`pg-agmemory deletion-history export`を追加しました。新しいreceiptは展開済みの
全対象を記録し、不完全な記録やcommit後の追記をDBが拒否します。
schema 13以前のreceiptは対応不明と明示し、推測でbackfillしません。

exportは削除metadataのみで、**復元commandでもAPI/model worker再開許可でもありません**。
最新ACL/policy/model call/quotaの全体照合は必須であり、このexportだけでは満たせません。
Native `forget`は引き続き`preview`/`purge`のみで、保存形式の`suppress`を
公開APIとして有効化しません。migration 014でmanifest、015で復旧対応を追加しました。
[manifest運用](docs/operations/README-jp.md#schema-14-deletion-manifests)も参照してください。
model呼出しやNative/MCP resourceの追加はありません。

## 保存したv0.0.27 / schema 13 processing契約

以下の処理動作は維持します。version固有のupgrade・測定記述は過去のreleaseのもので、
schema 14の認定を意味しません。

**記憶基盤であり、判断システムではありません。** ユーザーが予算に応じて対応modelを
指定し、pg_agmemoryはmodelの推論力でなく記憶の契約を保証します。
[改訂M2–M5計画](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)は本体受入れと
一つの参考benchmarkを分離し、複数model比較や人手の意味採点を必須にしません。
[Wikipedia評価packet](docs/HUMAN_REVIEW-jp.md)は保存した任意の診断であり、
次に行う必須作業ではありません。

Stage **`m2-background-processing`** は、管理者が明示許可する既定拒否の
local extraction/embedding job、隔離candidateのcallerによる採用、
同一scopeのworking compactionを追加します。Native API/SDKは**38 memory resource**、
MCPは4 toolです。read-only hookは明示snapshot IDと別途設定したbyte予算により
working snapshotを復元できます。通常のobserve/hookの既定動作は維持します。

抽出modelはsubject/predicate/value/完全一致quoteを提案し、信頼するcodeが一意な
Unicode spanを導出します。自動公開はpolicyで許可したliteral preferenceに限定し、
検証済みの意図・意味的真実とは扱いません。他candidateは未信頼のままです。
model待機中はmemory transactionを保持せず、永続予約、lease/epoch検査、
purge依存関係でblind retryと古い結果の公開を防ぎます。

**過去のv0.0.27時点ではM2は未完了でした。** 50 group・600 held-out synthetic問で
hybrid Recall@20は99.818%（vector-onlyと同値）、実Native APIの不正認可10,000件で
予期しない結果0件、実workerのSIGKILL復旧、local modelを3回呼び出す
抽出・embedding・圧縮・復元を確認しました。本体の残作業は、対応履歴全体のlogical
restore、最新ACL/model呼出し会計の照合、資源認定などであり、人手labelや実agent
task 20件ではありません。
修正後の公開oracle QAは144試行で不正出力0件ですが、abstention 136件、
機械的完全一致22件にとどまり、回答品質は未認定です。過去の不正出力も証跡に保持します。
完全一致の検証SHA、失敗履歴、公開baselineの状態は
[EVALUATION](docs/EVALUATION-jp.md)、現行APIと管理commandは
[ADR 0028](docs/adr/0028-background-processing-jp.md)と
[schema 13運用](docs/operations/README-jp.md#schema-13-background-processing)を参照してください。

API/worker/SDK/MCP/hookは**service 0.0.27 / API v1 / schema 13**で揃えます。
更新にはmigration 012–013が必要で、sourceのrollbackだけではDBを戻せません。
管理者が`scope-synthesis`でlocal worker profileを固定するまでmodel処理は無効です。
capture権限だけではmodel呼出しを許可しません。

## 保存されたv26 guideと過去の証跡

以下のversion固有手順は**v0.0.26/schema 11**の記録であり、現在のbinaryの
capability条件や追加processing APIではありません。v27には上記の現行契約と
schema 13運用を使ってください。変更しない既定off経路のAPI例は参考にできますが、
過去のversion検査・migration対象をv27の現行手順として実行しないでください。

**過去の上限付きmilestoneはv0.0.26/schema 11のscope capture policyです。
実装`c07630009ff4dcc34542e3ea80064d4f10c4d8b5`のlocal適格性確認と
完全一致SHAのnative Docker amd64/arm64 CIは合格しました。
以下の検証済みv0.0.25以前の結果は過去の証拠であり、v0.0.26の適格性確認ではありません。
M0/M1/M2/M3全体の完了、MVP完成版、本番リリースではありません。**
[完全一致実装の証拠](docs/STATUS-jp.md#v0026--schema-11)と
[source CI 35308638587](https://github.com/rioriost/pg_agmemory/actions/runs/35308638587)を参照してください。
後続の文書専用publication commitは検査済み実装SHAではありません。
認証付き観測保存、同一scopeのepisodeを根拠とする明示的な構造化記憶、
PostgreSQL全文検索、根拠表示、トランザクション内の冪等性、
稼働DBからの同期purgeを実装しています。tenant/scope権限をサービスと
PostgreSQL RLSの両方で強制し、変更はcommit後に応答します。
assertion revisionはサーバー管理のsystem-time履歴とrevision固有の根拠を維持します。
typed checkpointは新branchへのrestore envelopeとdurableなtool-effect台帳を提供します。
明示entityとrevision付きrelation assertionは上限付きの読取り専用SQL graph探索を提供します。
固定principal workerによる明示queue型の構造化publicationと、
opt-inのversion付き日本語lexical search、Native API上のlocal・固定identity MCP adapterも提供します。
任意のvendor-neutral implicit recall hookは読取り専用を維持します。
既存v0.0.10 captureはepisode一つと明示要求された構造化publication job一つを原子的にcommitします。
assertion公開済み、自動capture、自然言語synthesis、検索品質の測定済み改善ではありません。

別assertion間のsupersession/fact調停、provider receipt検証、vendor固有harness adapter、
自動enqueue/抽出、汎用multi-tenant scheduling、自動embedding生成、ANN/HNSW、
AGE/SQL/PGQ、remote MCP HTTP/SSE/OAuth/delegation、同期/TypeScript SDK、
postgresem連携は今後の実装対象です。
性能・記憶品質・災害復旧・完全消去の受入は未測定または未認定です。
利用前に[現在の契約と制限](docs/STATUS-jp.md)を確認してください。

## Scope capture policy

**v0.0.26 / API v1 / schema 11。実装はlocal・native CI検証済みです。**
stage `m2-scope-capture-policy`は`PGAG_ADMIN_DATABASE_URL`を使う管理者専用の
`pg-agmemory scope-capture get|set`を追加します。
memory REST route、SDK resource、MCP toolの追加はありません。
Native/SDKは**31 memory resource**、MCPは**4 tool**、hookはread-onlyのままです。

closed policyは次の全4 fieldを必須とします。

```json
{
  "enabled": true,
  "source_namespaces": null,
  "consent_references": null,
  "max_content_bytes": 262144
}
```

rowがない場合もこのlegacy policyです。`enabled`はstrict booleanです。
各listは`null`（制限なし）、または空白除去・sort後の相異なる最大64文字列で、
各文字列は1〜256文字、C0 controlと不正UTF-8を含めません。
`[]`はすべて拒否し、大文字小文字を区別して完全一致で比較します。
`max_content_bytes`はstrict整数1〜262144で、raw JSONではなく正規化した
`Observe.content`のUTF-8 byte数を制限します。既存の65,536文字上限も維持します。

現在のscope read/write認可をpolicyより先に確認します。
observe、capture、batch captureは**idempotency/source-event dedupより先に**
policyを検査するため、変更後は過去の完全一致replayも
**403 `capture_policy_denied`**となり、書込みません。
不正な保存policyは**503 `capture_policy_invalid`**でfail-closed、
capture本文の不正UTF-8は既存request検証で**422 `invalid_request`**となり、
capture専用の新errorではありません。
不存在または権限のないscopeは**404 `not_found`**を維持します。

`set`は現在の**tenant access-epoch CAS**でpolicy全体を置換します。
実変更はepochを一度だけ増加し、private auditの変更前後snapshotとDB roleを原子的に記録します。
同値policyはno-opですが正しいepochが必要です。未設定scopeへのdefault設定はrowを作りません。
完全なlegacy policyへ戻しても既存rowは削除しません。
管理者のtenant barrierはcommitから**出力のdeliveryまで**維持します。
commitの可能性がある失敗ではget/readbackで整合を確認し、新しいCASを使ってください。
blind retryはしません。

これは受付制御であり、**同意の検証、secret/PII検出、provider egress認可、
自動抽出、compactionではありません**。
既存内容の読取り、既存episodeを使う明示remember/jobは引き続き可能です。
遡及purge/cancelはしません。epoch変更は古いcontextとclaim済みjobをfenceしますが、
workerは新epochで復旧できます。
migration 011前にbackupとschema 10 writer/workerの停止・drainを行い、
対応schema 11 componentだけを起動します。codeのrevertだけではDBを戻せません。
[get/set/restore・upgrade運用](docs/operations/README-jp.md#scope-capture-administration)、
[ADR 0026](docs/adr/0026-scope-capture-policy-jp.md)、
[適格性確認と次の限定M2作業](docs/STATUS-jp.md#v0026--schema-11)を参照してください。
次の再開点はM2の自動synthesis、embedding統合、compactionと残る品質/task-replay gateを
限定して進める作業です。本milestoneでは有効化せず、capture受付をprovider egress認可とは扱いません。

## Selectable inference providers

**既存provider基盤を維持します。v26実装はlocal・native CI検証済みです。**
以下のCA/live結果は過去v25/v24の証拠であり、v26の適格性確認ではありません。
Docker baseは`SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`でOS CA bundleを選択します。
non-root production smokeはこの設定とroot CA storeにCAがあることを検査します。
対象のlocal実libpq TLS、runtime CA検査、local full suiteは合格し、native CIは合格しました。
成功したv24 Azure実行は明示DSN CA fileを使い、このimage既定値の検証ではありません。
任意の`pg-agmemory[providers]`は**httpx==0.28.1**を追加し、新しい依存versionや
provider専用schema migrationはありません。schema 11のmigrationはcapture policy用です。
同じcore distributionであり、独立SDKやPyPI公開の主張ではありません。
typedな`pg_agmemory.providers` library、またはoperator command
`pg-agmemory infer inspect|summarize|embed --config FILE`を使います。
callごとに信頼するJSON profileを一つ選択します。
`local_http`（loopback HTTP/HTTPS）、`openai_compatible`（HTTPS）、
`azure_ai`（Flexible Server/HorizonDBのSQL）です。
local要約と別途選択したAzure profileでのembeddingを明示的に組み合わせられます。
自動backend fallback、retry、ingestion、公開、enqueue、compactionはありません。

HTTPは上限付きOpenAI互換`chat/completions`と`embeddings`を使い、
全vendor/modelとの互換性は保証しません。
SQLは専用のTLS検証付き・権限制限されたautocommit接続を使い、
canonical memory transactionやsession lockには参加しません。
extension版、extension所有の互換function signature、SQL権限を検査します。
Azure資格情報/model登録はoperatorがapplication外で準備し、対応環境ではmanaged identityを推奨します。
adapterはinstall/configureしません。
HTTP `inspect`はnetwork呼出しなしにclientを構築・closeして設定と資格情報headerを検証し、
SQL `inspect`はread-only catalogを検査します。
推論やmodelアクセス/quota/endpoint接続の保証ではありません。
`max_output_tokens`はHTTP summary modelが必須で、
非defaultの`sentence_count`はFlexible Language modeだけで使えます。
applicationはretryしません。SQL embedding/Languageは`max_attempts => 1`を明示しますが、
`azure_ai.generate`には検証済みretry/output-token knobがありません。
一度の`MATERIALIZED` SQL呼出しも、課金されるupstream呼出しが一度だけ、または課金上限を**保証しません**。

入力はclosedな`{"text": "..."}`でtext bytesを維持し、65,536文字/256 KiB上限、
HTTP request/responseは256 KiB/2 MiB上限です。
要約は`status: "untrusted"`で、根拠付きassertion、承認、compaction snapshotではありません。
embeddingは宣言したmodel空間の有限・非zeroな正確に768値を要求し、padding/truncateはしません。
operatorのmodel revision固定はremote aliasやAIMM upgradeの検証ではありません。
明示的な`embedding_input` → provider `embed` → `PutEmbedding`だけが、
既存の現在ACL/digest/purge検査でvectorをuploadします。
upload結果不明時は生成payloadとwrite keyを保持し、
**retry再構成のためmodelを再実行しないでください**。
providerの`billing_unknown`はNative mutationの`outcome_unknown`と別で、cancel後も課金が続き得ます。

Native/SDKは**31 memory resource**、MCPは**4 tool**、hookも不変です。
stage `m2-scope-capture-policy`が維持する`model_inference`は
`automatic: false`、`publishes_memory: false`、`live_provider_qualified: false`です。
正確なOllama/Azure Flexible Server profileには
[上限付きlive証拠](docs/INFERENCE_PROFILES-jp.md#recorded-azure-live-evidence)がありますが、
provider全体やMemoryDB全体のAzure hosting認定ではありません。
**Azureで英語入力の要約が一度スペイン語になり**、
契約合格は言語/grounding/品質gateの合格ではありません。
MemoryDBのPostgreSQL 18.6 / `vector` 0.8.6固定を維持し、推論SQLは別DBにできます。
preview/version/lifecycle制約、operatorのprivacy/budget責任を明示し、
人のreview、品質、MVP、M2のgateは未完了です。
[契約](docs/STATUS-jp.md#selectable-inference-providers)、
[profileとcommand](docs/operations/README-jp.md#selectable-inference-providers)、
[ADR 0025](docs/adr/0025-live-provider-qualification-jp.md)、
[v25適格性確認の状態](docs/STATUS-jp.md#v0025--schema-10)を参照してください。

具体的なOllama/OpenAI/Azure SQL設定file、secretの扱い、opt-in live検査は
[推論profile guide](docs/INFERENCE_PROFILES-jp.md)を参照してください。

## Episode query and pagination

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
認証付きread-only `POST /v1/episodes/query`は`Idempotency-Key`不要です。
closedな`QueryEpisodes`は重複しない`scope_ids`（UUID 1〜32件）、
nullableなtimezone付き`occurred_from`/`occurred_to`、strictな`max_items`
（1〜100、既定20）、nullableな`before: EpisodeCursor`を受け付けます。
closed cursorはtimezone付き`recorded_at`とUUID `memory_id`が必須です。
同一/逆転time境界や不正fieldは**422 `invalid_request`**です。
scopeとoccurred-time filterは**LIMIT前**に適用し、
半開区間`occurred_from <= occurred_at < occurred_to`で省略/null側は無制限です。
`as_of`、`known_at`、二時点再構成ではありません。

**200 `EpisodePage`**は最大100件の`EpisodeSummary`を返し、
各itemは`memory_id`、`revision: 1`、`scope_id`、`occurred_at`、`recorded_at`だけです。
pageには`next_cursor`と`consistency`（`access_epoch`、`deletion_epoch`）もあります。
content/body、consent reference、source URI、`source_namespace`、event ID、job payloadは返しません。
source identityはHMAC anchorで、平文`source_namespace` filterはありません。
episodeを明示選択し、`Explain(memory_id, revision=1)`でcontentを取得してから、
必要ならliteral根拠付き`Remember`を明示します。自動ingestion、synthesis、抽出、compactionではなく、
contentは根拠であって信頼する指示や現在の真実ではありません。

現在tenant/scope RLSを適用し、**所有権filterはありません**。
読取り可能な共有scopeを含み、未知/非公開/別tenant scopeは行を寄与しません。
一致なしは`episodes: []`、`next_cursor: null`と現在epochです。
各pageでresponse-drain barrierの下で現在ACL/purge可視性を再確認します。
SQLは`episode` + `object`のmetadataを読み、auditや他application書込みを作りません。
順序は`object.created_at`を使う**`recorded_at DESC, memory_id DESC`**で、
occurred time順ではありません。遅い過去eventが一致行の先頭に現れ得ます。
exclusive cursorは位置であり、権限、snapshot、receipt、event sequence、
compaction watermarkではありません。削除済み/偽造位置も現在認可された行を限定するだけで、
境界より新しいrecorded行には明示再開が必要です。
`max_items + 1`件を取得し、overflowの場合だけ**最後に返すitem**からcursorを作ります。

async `query_episodes(QueryEpisodes) -> EpisodePage`は`mutation=False`、
**request 256 KiB / response 2 MiB**上限を使い、自動paging/retryはありません。
read応答消失は`outcome_unknown: false`で、mutation結果不確実とはしません。
Native/SDKは**31 resource method**で、MCPの4 toolとclosed hookは不変です。
stage `m2-scope-capture-policy`は`episode_query`を維持し、schema 11はmigration 011を追加します。
[契約](docs/STATUS-jp.md#episode-query-and-pagination)、
[paging例](docs/operations/README-jp.md#episode-query-and-pagination)、
[ADR 0023](docs/adr/0023-episode-query-jp.md)、
[適格性確認の証拠](docs/STATUS-jp.md#v0023--schema-10)を参照してください。

## Explicit batch capture

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
以下の受付/replay結果は、先に[現在のscope capture policy](#scope-capture-policy)が
episodeを許可する場合だけです。
認証付き`POST /v1/captures/batch`にcaller所有の`Idempotency-Key`を要求します。
closedな`CaptureBatch`は既存`episode: Observe`と
**1〜16件**の`memories: list[CapturedMemory]`を持ちます。
全proposalは明示指定、同一scope、同じepisode内の一つのliteral quoteを使い、
抽出、provider、自動captureはありません。正規化model JSONで重複するcandidateは、
trimで同一になるものを含め**422 `invalid_request`**です。
既存の単一job `Capture` / `CaptureResult`と`/v1/captures`は不変です。

**201 `CaptureBatchResult`**はepisodeの`memory_id`、`revision: 1`、
**request順の`synthesis_job_ids` 1〜16件**を返し、公開済みassertionではありません。
新規episode/lexical/job/identity/receipt/auditの全書込みは一つの原子的受付です。
後方candidateの不正quote、途中のquota枯渇、audit失敗では全新規書込みをrollbackし、
既存行を変更しません。既存の**scope当たりactive job 100件**quotaは新規jobだけに適用し、
16件はrequest単位の上限です。受付後のworker公開は独立し、原子的完了や実行順は保証しません。
個別jobをpoll/照会/取消/retryし、batch job/status/cancel APIはありません。

同じsource/intentのfresh keyはterminal jobを含む元IDを再利用し、復活させません。
fresh keyでcandidateを並べ替えると既存IDを並べ替えて返しますが、
同じkeyでの並べ替えは**409**です。
exact-key replayは現在write権限と**全jobのliveness**を再検証し、
sourceまたは一つのjobのpurge後はreceipt全体が**404**で、部分replayや復活はありません。
source purgeは既存の依存job/assertion closureを維持します。

async `capture_batch(CaptureBatch, *, idempotency_key) -> CaptureBatchResult`は
`mutation=True`で201を要求します。不確実な結果では同じkey/bodyを維持し、
retry、分割、key置換は自動化しません。request/response上限は**256 KiB / 2 MiB**を維持し、
個別に有効な大きいcandidate 16件でもNative body上限を超えて**413**になり得ます。
Native/SDKは**31 resource method**で、MCPの4 toolとclosed hookは不変です。
stage `m2-scope-capture-policy`は`atomic_batch_structured_capture`と`atomic_batch_capture`
metadataを維持し、単一jobの`atomic_capture`は変更しません。
schema 11はmigration 011を追加し、依存versionは不変、provider実行は別のoperator経路です。
[契約](docs/STATUS-jp.md#explicit-batch-capture)、
[async例](docs/operations/README-jp.md#explicit-batch-capture)、
[ADR 0022](docs/adr/0022-batch-capture-jp.md)、
[検証状況と証拠](docs/STATUS-jp.md#v0022--schema-10)を参照してください。

## Exact entity query and pagination

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
認証付きread-only `POST /v1/entities/query`は`Idempotency-Key`不要です。
closedな`QueryEntities`は重複しない`scope_ids`（UUID 1〜32件）、
nullableな`entity_type`（既存8種の`EntityType`）、nullableな`canonical_label`
（既存の空白除去後1〜256文字の`ShortText`）、strict整数`max_items`（1〜100、既定20）、
nullableな`before`（`EntityCursor`）を受け付けます。filterと`before`の既定はnullです。
closed cursorはtimezone付き`recorded_at`とUUID `memory_id`が必須です。
不正field、重複、空label、範囲違反は**422 `invalid_request`**です。

typeとlabelのfilterは**LIMIT前にAND**で組み合わせます。
labelは通常の空白除去後に**`C` collationで大文字小文字を区別する完全一致**です。
filter省略/nullは要求scope内の現在読取り可能な全候補を対象にします。
alias、fuzzy/substring/wildcard/Unicode正規化match、embedding検索、
merge、自動identity選択はありません。
同じlabelでもpageを通じて別IDを維持します。既存
`GET /v1/entities/{memory_id}` / SDK `get_entity`で根拠を確認し、
graph seedを明示選択してください。label一致はidentityの証明ではありません。

所有job照会と異なり、**entity照会に所有権filterはありません**。
現在tenant/scope/source RLSで読取り可能な共有scopeのentityも含みます。
未知/非公開scopeは結果へ寄与せず、一致なしは**200**と
`entities: []`、`next_cursor: null`で、非公開理由やtotal countは返しません。
選択itemの`entity_evidence JOIN episode`による可視根拠件数が
保存済み`reference_count`と一致しなければ、**409 `entity_invalidated`**で**全page失敗**です。
黙ったskipや部分成功はなく、SQLはquoteを取得しません。
各pageに現在のtenant response-delivery/drain barrierを適用します。

順序は`recorded_at DESC, memory_id DESC`で、`recorded_at`はentity objectの
`created_at`です。exclusiveな
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`を使います。
`max_items + 1`件を取得し、overflowの場合だけlookaheadでなく**最後に返すitem**から
`next_cursor`を作ります。署名なしcursorは位置であり、権限、snapshot、receipt、保持objectではありません。
既存objectを指す必要はなく、古い/削除済み/偽造位置も現在認可された行を限定するだけです。
境界より新しいentityには`before`なしの明示再開が必要です。

正確な**200 `EntityPage`**は既存`EntitySummary`を最大100件
（`memory_id`、revision 1、`scope_id`、`entity_type`、`canonical_label`、`recorded_at`）、
`next_cursor`、`consistency`（`access_epoch`、`deletion_epoch`）として返します。
evidence quote、source ID、total countはなく、既知IDの`EntityDetail`は不変です。
labelは人間のtextであり、信頼できる指示や現在の真実ではありません。
async SDK `query_entities(QueryEntities) -> EntityPage`は`mutation=False`、
通常の**request 256 KiB / response 2 MiB**上限、read-only `outcome_unknown: false`を使い、
自動paging/retryやprovider呼出しはありません。
Native/SDKは**31 resource method**で、MCPの4 toolとclosed hookは不変です。
schema 11はcapture policy migration 011を追加し、依存versionやAGEは変更しません。
[契約](docs/STATUS-jp.md#exact-entity-query-and-pagination)、
[paging例](docs/operations/README-jp.md#exact-entity-query-and-pagination)、
[ADR 0021](docs/adr/0021-entity-query-jp.md)を参照してください。

## Assertion metadata history

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
認証付きread-only `POST /v1/assertions/history`は`Idempotency-Key`不要です。
closedな`AssertionHistory`は必須UUID `memory_id`、strict整数`max_items`
（1〜100、既定20）、nullableなstrict整数`before_revision`（1〜1001、既定null）だけを受け付けます。
不正field/値は**422 `invalid_request`**です。
現在読取り可能な通常assertionとcanonical relation assertionが対象で、他のobject kindは対象外です。
不在、非公開、kind違いは汎用**404 `not_found`**です。
`as_of`、`known_at`、過去ACL selectorはありません。

revisionはordinal降順で、exclusiveな`revision < before_revision`を使います。
省略/nullは最新から開始し、`before_revision: 1`は空page、
`1001`は既存revision上限1000までの現在headを含みます。
`max_items + 1`件を取得し、最大`max_items`件を返します。
overflowがある場合だけ、lookahead行でなく**最後に返すordinal**から`next_before_revision`を作ります。
位置は権限、snapshot、receipt、保持cursorではありません。
各pageで現在ACL/source/削除可視性を再検査し、tenant response-drain barrierを使います。
page間の新revisionには明示再開が必要で、以前のheadの`known_until`は閉じることがあります。
`current_revision`はCAS予約でも、結果不明のwriteがcommitした証拠でもありません。

正確な**200 `AssertionHistoryPage`**は`memory_id`、`scope_id`、`subject`、
`predicate`、`current_revision`、最大100件の`revisions`、`next_before_revision`、
`consistency`（`access_epoch`、`deletion_epoch`）を返します。
各revisionはordinal、nullableな`valid_from`/`valid_to`、
`recorded_at`（system-time下限）、nullableな`known_until`（上限）、
nullableな`correction_reason`、`epistemic_status: "reported"`、
revision 1の正確なepisode `evidence_refs`最大32件、
`source_entity`/`target_entity`を持つ`relation`またはnullを含みます。
上限付きSQLは完全なvalueやevidence quoteを取得しません。
**Metadataは内容を含まないという意味ではありません。** subject、predicate、correction reasonは人間のtextです。
応答はevidenceとして扱い、指示や現在の真実として信用しないでください。
全内容には既存`Explain`へ正確な`memory_id`とrevisionを渡します。
revision省略は引き続き**latestでなく1**です。

選択revision metadataの欠落/番号gap、または読取り可能な根拠がない場合は
**409 `assertion_invalidated`**で**全pageを失敗**させ、
relation endpoint不在は**409 `relation_invalidated`**です。部分成功、skip、fallbackはありません。
async SDK `get_assertion_history(AssertionHistory) -> AssertionHistoryPage`は
`mutation=False`、通常の**request 256 KiB / response 2 MiB**上限、
read-only `outcome_unknown: false`を使います。
自動paging/retry、mutation、cache、provider呼出し、watch、retention objectは追加しません。
Native/SDKは**31 resource method**となり、MCPの4 toolとclosed hookは不変で、
history tool/fieldはありません。schema 11はcapture policy用です。
[契約](docs/STATUS-jp.md#assertion-metadata-history)、
[paging例](docs/operations/README-jp.md#assertion-metadata-history)、
[ADR 0020](docs/adr/0020-assertion-history-jp.md)を参照してください。

## Owned-job query and pagination

**v0.0.26/schema 11で既存契約を維持します。実装はlocal・native CI検証済みです。**
認証付きread-only `POST /v1/jobs/query`は`Idempotency-Key`不要です。
closedな`QueryJobs`は重複しない`scope_ids`（UUID 1〜32件）、
重複しない`states`（最大5件、`[]`/省略で全状態）、strict整数の`max_items`
（1〜100、既定20）、`before: JobCursor | None`（既定null）を受け付けます。
`JobState`は`pending`、`running`、`succeeded`、`failed`、`cancelled`の5状態を維持します。
closedな`JobCursor`はtimezone付き`created_at` timestampとUUID `job_id`が必須です。
不正field/値は**422 `invalid_request`**です。owner/principal/tenant、kind、
payload query、offset、watch、`after` selectorは受け付けません。

選択は**現在callerが所有するjobだけ**で、tenant、要求scope、
現在のRLS/source/削除可視性、任意state filterを適用します。
既知ID指定GETより意図的に狭く、他principal所有の同一scopeの読取り可能jobは
引き続きGET可能ですが、scope-admin権限があってもこのqueryでは発見できません。
未知/読取り不可scopeは結果へ寄与しません。
一致なしは**200**と`jobs: []`、`next_cursor: null`で、非公開理由やtotalを返しません。

順序は`created_at DESC, id DESC`で、`before`はexclusiveな
`(created_at, id) < (before.created_at, before.job_id)`境界です。
`max_items + 1`件を取得し、overflowがある場合だけ**最後に返すjob**から
`next_cursor`を作ります。lookahead行からは作らず、返すjobは最大`max_items`件です。
署名なしcursorは透明な位置情報であり、権限、receipt、snapshot、cache、retention objectではありません。
そのjobが存在する必要もなく、偽造/古い/削除済みcursorでも現在認可された所有行だけが対象です。

成功は型付き**200 `JobPage`**で、`ListedJob`の`jobs`、`next_cursor`、
現在の`Consistency`（`access_epoch`、`deletion_epoch`）を返します。
SDKもresponse modelの100-job上限を検証します。
`ListedJob`は既存の完全な`JobDetail`へ`scope_id`を加え、GET形式は変えません。
参照、state/時刻、安全なerror、retry parent、元のresult revision 1を返し、
保存payload、evidence quote、lease token、intent digestは返しません。
選択された各page itemは`Jobs.get`の参照件数/result生存検査を通し、
実際に不正な選択jobがあれば既存404/409で**page全体を失敗**させ、黙ったskipや部分成功にしません。

pageごとに現在権限とtenant response-drain barrierを使い、page間snapshotではありません。
state遷移でも元の`created_at`は維持しますが、state/アクセス/削除変更でpage間の構成は変わり得ます。
古いcursorより新しいjobには、`before`なしで明示的に再開します。
SDK `query_jobs(QueryJobs) -> JobPage`はasyncで`mutation=False`を使い、
通常の**request 256 KiB / response 2 MiB**上限とread-only `outcome_unknown: false`を維持します。
自動pagination/retry、claim、取消、worker/provider呼出し、state変更はありません。
Native/SDKは**31 resource method**となり、MCPの4 toolとhookは不変でjob toolはありません。
[契約](docs/STATUS-jp.md#owned-job-query-and-pagination)、
[paging例](docs/operations/README-jp.md#owned-job-query-and-pagination)、
[ADR 0019](docs/adr/0019-job-query-jp.md)を参照してください。

## Checkpoint-head lookup

**既存checkpoint head契約を維持します。v0.0.26実装はlocal・native CI検証済みです。**
認証付き`POST /v1/checkpoints/head`はread-onlyで、`Idempotency-Key`は不要です。
closedな`CheckpointBranch` bodyは必須UUIDの`scope_id`、`run_id`、`branch_id`だけです。
現在読取り可能な正確なscope/run/branchだけを選び、identity override、
`expected_head`、harness selector、`as_of`、branch横断のlatest選択、履歴一覧は受け付けません。

既存APIのtenant session-lock/drain barrierを使い、SQLは`FOR UPDATE`なしの`SELECT`です。
branch/run作成、state変更、audit event、idempotency receiptはありません。
不在、非公開、異なるscope、別tenant、失効していない空branchは、
ID/内容や空成功でなく汎用**404 `not_found`**です。
sourceの`forget`は対象branchを`invalidated=true`とし、canonical checkpoint/参照payloadを削除しますが、
opaqueな`head_id`と`sequence`、`memory.object` anchor、tombstoneは保持します。
照会は失効を先に確認し、読取り可能な失効branchには**409 `checkpoint_invalidated`**を返し、
保持head IDは返しません。アクセス権の取消後はbranchを404で隠します。
**ancestor、sibling、default branchへのfallbackはありません**。

成功は正確な**200 `CheckpointEnvelope`**で、既存load/envelope検査を再利用します。
保存state/HMAC/参照/epochと現在epoch、`tool_effects`、`requires_reconciliation`、
`untracked_effects`、`resume_allowed`、`automatic_reexecution: false`を返します。
runの`effects_invalidated`、HMAC/state/参照不正、非公開headは既存の拒否動作を維持します。
読んだscope/run/branch/sequenceが選択pointerと一致しなければ409です。

checkpoint IDを失っても安定したbranch IDから現在headを取得できますが、
latestは**照会時点のpointer**であり、watch、予約、後続headの保証ではありません。
`CreateCheckpoint.expected_head`は必須のcaller CASを維持し、読取り後に別writerが進められます。
head照会は**結果不明writeのcommit証明ではありません**。
その変更の元key/bodyを再送して元receiptを取得し、その後headを確認します。
新head/keyによる自動retry、restore、effect遷移、実行、承認をしてはいけません。
checkpoint ID指定GETは既存検査下で生存ancestorも読めますがlatestではなく、
restore先forkのbranch headは独立しています。

SDK `get_checkpoint_head(CheckpointBranch) -> CheckpointEnvelope`はasync/read-only
（`mutation=False`）で、通常の**request 256 KiB / response 2 MiB**上限、
sanitized error、`outcome_unknown: false`、自動retryなしを維持します。
episode照会による現在のNative/SDK resourceは**31 method**で、MCPの**4 tool**とhookは不変でcheckpoint toolはありません。
schema 11はcapture policy migration 011を要求し、依存version/artifact固定値は不変です。
[契約](docs/STATUS-jp.md#checkpoint-head-lookup)、
[照会例と更新](docs/operations/README-jp.md#checkpoint-head-lookup)、
[ADR 0018](docs/adr/0018-checkpoint-head-jp.md)を参照してください。

## Lexical recallのquery計画

Lexical recallは**全lexeme一致**であり、自然文の意味検索ではありません。
英語stemmingもないため、疑問文の文法語や同義語を足すと一致しなくなります。
Native `Recall.query` schema、MCP tool、hook schema、認証済みcapabilitiesで
`pgag-lexical-query-v1`の説明を共有します。SQL ranking、認可、時間/削除filter、
request上限、既定の検索modeは変更しません。

選択したLLMへ`pg_agmemory.query_planning.lexical_query_prompt(question, search_profile)`を渡し、
`{"terms":[...]}`応答を`parse_lexical_query_plan`で検証します。
`plan.query`を通常の`Recall`へ渡します。計画は短いliteral term 1～3個に限定し、
正答の推測や検索operator、空queryへの無断置換を行いません。
serverへのLLM呼出し、query書換え、OR検索、browse fallbackは追加しません。
[query契約](docs/operations/README-jp.md#lexical-query-planning)と
[別versionの比較](docs/EVALUATION-jp.md#lexical-v2-query-planning-comparison)を参照してください。

明示的な追加探索には`pg_agmemory.bounded_recall`が計画2 round・scoped検索4回までと、
その後の最新required-reference検査を提供します。
`pg_agmemory.retention_review`はmodelのforget提案を削除承認にせず保留します。
caller所有のopt-in workflowであり、server全体の認可を変えたり、
pending rowを全clientから読めなくしたりするものではありません。
[上限とreview契約](docs/operations/README-jp.md#上限付き追加検索と保持review)を参照してください。

## Exact structured recall filters

**既存recall filter契約を維持します。v0.0.26実装はlocal・native CI検証済みです。**
既存Native `POST /v1/recall`、型付きSDK `recall`、MCP `memory_recall`は
`Recall.filters: RecallFilters | None = None`を受け付けます。
未知fieldを拒否するnested modelのfieldはnullableな`kind`（`"episode"`または`"assertion"`）、
`subject`（`ShortText`、既存の前後空白除去後1〜256文字）、
`predicate`（`^[a-z][a-z0-9_]{0,63}$`）だけで、既定はnullです。
未知field、不正値、`kind: "episode"`とnon-null subject/predicateの組合せは
**422 `invalid_request`**です。省略、null、`{}`、全field nullなら
全3 retrieval modeの既存結果を維持します。

non-null fieldを**AND**で結合します。subject/predicateはassertionを意味し、
`kind: "assertion"`はrelation assertionを含み、`"episode"`は全assertionを除外します。
subject/predicateは通常のcontract trim後、**大文字小文字を区別する`C` collationの完全一致**です。
部分文字列/FTS一致、Unicode正規化、fuzzy一致、alias、entity解決はありません。
空queryのlexical recallはfilter後の候補をbrowseします。
filterは非空queryの通常lexical一致を迂回しません。

filterは**共有materialized候補の内部で、lexical/vector/hybridのranking、
coverage、required参照の適格性より前**に適用します。
rankingとlexical/vector欠落coverageはfilter後の適格候補集合を使います。
`coverage.jobs_pending`は意図的に要求scope単位のsignalであり、構造化job一致ではありません。
固定`as_of`/`known_at`、要求scope、現在RLS、根拠可視性、削除gateを維持します。
required参照は別契約のlexical専用keyword迂回とrequest順を維持しますが、
filterにも一致が必要です。一つでも不一致ならIDや部分contextを返さず
request全体を**404 `not_found`**にします。既存byte予算/error動作は不変です。

**Native/SDK resource method 31、MCP tool 4**を維持し、safe error codeの追加はありません。
hook入力は`filters`を拒否し、内部既定`None`で信頼する起動時境界を維持します。
recall filter自体は依存version/index変更、永続priority、cacheを追加しません。
filterはcallerの選択条件であり、信頼する指示や検証済み真実ではありません。
[契約](docs/STATUS-jp.md#exact-structured-recall-filters)、
[例と更新](docs/operations/README-jp.md#exact-structured-recall-filters)、
[ADR 0017](docs/adr/0017-recall-filters-jp.md)を参照してください。

## Required-context recall

**既存required-context契約を維持し、v0.0.26実装はlocal・native CI検証済みです。**
既存Native `POST /v1/recall`、SDK `recall`、MCP `memory_recall`は
`Recall.required_memory_refs`を受け付けます。既定は省略または`[]`で、
最大**16**件の`MemoryReference`です。UUIDと正確なrevision **1〜1000**を選び、
revision省略時は**最新でなく1**です。revision違いでも同一IDは重複不可で、
件数は`max_items`以内です。空でない参照には`retrieval_mode: "lexical"`が必須です。
explicit/implicit両recallに対応し、implicitの**2,000-byte**上限など既存制限を維持します。

required itemは通常recallと同じ現在認可済みepisode/assertion候補、
要求scope、固定した`as_of`/`known_at`を使います。
keyword一致とranking cutoffは迂回しますが、**ACL/scope/時間/構造化recall filterは迂回しません**。
不在、読取り不可、purge済み、異なるkind、時間条件外などの正確なrevisionは、
一つでもあればrequest全体を汎用**404 `not_found`**で拒否します。
latest/revision fallbackや欠落参照の開示はありません。

完全なrequired prefixを**request順**で先頭に置き、ID重複を除いた通常のlexical順位の
optional itemを続けます。全itemが`max_items`とcompact `ContextPack`全体のUTF-8 byte予算に含まれます。
required itemが一つでも丸ごと収まらなければ**422 `budget_exhausted`**で部分contextを返しません。
optional itemは従来のgreedyなitem単位除外と`coverage.truncated`を維持します。
参照省略/空配列は選択した構造化filter内で既存の順序、pack、全3 retrieval modeのsemanticsを維持します。

`simple-v1`と`ja-janome-0.5.0-v1`の両方で正確な参照を使え、
日本語projection欠落時もcanonical itemを対象にできますが、
`lexical_incomplete`は欠落coverageを引き続き示します。
index修復や必須制約の自動検出ではありません。caller指定参照は**policy権限や検証済み承認ではなく**、
memoryは根拠であって信頼する指示ではありません。
**Native/SDK resource method 31、MCP tool 4**を維持します。
hookはこの入力fieldを拒否し、参照なしのrecallを構築するためhost pinningは追加しません。
write、idempotency、永続priority、cache、推論、provider呼出し、schema migrationは追加しません。
[契約](docs/STATUS-jp.md#required-context-recall)、
[例と更新](docs/operations/README-jp.md#required-context-recall)、
[ADR 0016](docs/adr/0016-required-context-jp.md)を参照してください。

## Explicit job cancellation

**既存job取消契約を維持し、v0.0.26実装はlocal・native CI検証済みです。**
`POST /v1/jobs/{job_id}/cancel`はNative認証、callerが保持する`Idempotency-Key`、
`expected_state`（`pending`または`running`）とstrict整数`expected_attempt`
（0〜5、runningは1以上）だけを要求します。
最初にjobを読み、tenant access epochや外部tool版ではなく**state/attempt CAS**を明示選択します。
認証中のownerで、現在のscope read/write権限と有効・可視の入力を持つ場合だけ取消できます。
同一scopeの別readerは`admin` permissionがあっても取消できません。

HTTP **200**でcommit済み`JobReceipt`を返し、GETでterminal `cancelled`を確認します。
期限切れleaseを含むpending/running jobを取消できます。
同じjobのidentity、attempt、source/intent参照、retry parent、作成時刻を保持し、
保存job payload、lease、errorを消去し、resultはありません。
既存tenant session barrier下でstate変更、`job_cancelled` audit、idempotency receiptを
原子的にcommitし、access/deletion epochは進めません。
非同期の取消job、worker kill、provider中断、`forget`ではありません。
source episode、dedup anchor、worker準備memory、WAL、backupは取消で消去されません。

取消が先なら古いworker publicationを拒否し、publicationが先なら
`409 job_cancel_conflict`となり公開済みresultは撤回しません。
応答喪失時は**同じkey/body**か新しいGETで照合し、CAS値を盲目的に更新しません。
failed専用retryはcancelled jobを拒否し、enqueue/captureのdedupは復活させずcancelled jobを返します。
source purgeは引き続きjobを失効させ、取消replayを拒否します。
SDKは`cancel_job`とepisode照会を維持し、surfaceは不変の**31 method**です。
MCPの4 toolとread-only hookは変更しません。
[完全な契約](docs/STATUS-jp.md#explicit-job-cancellation)、
[運用とschema 10 migration](docs/operations/README-jp.md#explicit-job-cancellation)、
[ADR 0015](docs/adr/0015-job-cancellation-jp.md)を参照してください。

## Runtime readiness

**既存readiness契約を維持し、v0.0.26/schema 11実装はlocal・native CI検証済みです。**
`GET /healthz`は起動成功後のprocess livenessを維持し、DBを呼ばず
`{"status":"ok"}`を返します。public・認証不要の`GET /readyz`は
HTTP **200**と正確な`{"status":"ready"}`、または想定内の失敗時に
**503**と正確な`{"status":"not_ready"}`を返します。
両readiness応答は`Cache-Control: no-store`と生成UUIDの`X-Request-ID`を持ち、
private詳細を含めません。渡された認証headerは無視し、tenant/principalは選びません。

受け付けた検査ごとに新しい**runtime** DB接続を開き、role/schema/extension契約だけを読みます。
特権runtime roleやアプリtable所有権がないこと、厳密なmigration履歴1〜11、
`public`内の`vector` 0.8.6を確認します。validation sessionは明示read-onlyです。
memory本文、tenant lock、audit/epoch/job/receipt書込み、migration、
retry、cache、background検査はありません。
API app/processごとに同時検査は一つで、並行probeは別接続を開かず即503です。
**active検査予算5.0秒は厳密なwall-clock SLAではなく**、cancel/connection cleanupで遅延が増え得ます。

readinessはある時点のsignalであり、**書込み可能性、認可全体、JWT/tokenizer/provider正常性、
処理能力、本番readinessの証明ではありません**。SELECT-only DBも合格し得ます。
resource routeはprobeを呼ばず、新たな永続readiness gateも取得しません。既存Native認可は維持します。
readinessはtraffic制御に使い、依存障害でrestart stormを起こすlivenessとして使わないでください。
失敗/復旧thresholdとperimeter制限/rate limitを設定します。
Kubernetes、Compose、Docker `HEALTHCHECK`設定は追加しません。
readinessはSDK/MCP/hook probe methodを追加せず、episode照会を含むNative/SDK memory surfaceは31のままです。
[完全な契約](docs/STATUS-jp.md#runtime-readiness)、
[probe運用](docs/operations/README-jp.md#runtime-readiness)、
[ADR 0014](docs/adr/0014-runtime-readiness-jp.md)を参照してください。

## Scope-access administration

**既存scope-access契約を維持し、v0.0.26実装はlocal・native CI検証済みです。**
特権`pg-agmemory scope-access get|set|revoke` CLIで、既存の同一tenantに属する
scope/principal UUIDのmembershipを管理します。
`PGAG_ADMIN_DATABASE_URL`、RLS bypassと適切なSQL権限を持つ管理者、
明示的に選んだIDが必要です。JWT、runtime資格情報、取得本文由来のidentityは使いません。
**HTTP/MCP/SDK管理methodはありません**。

`get`は現在membershipとtenant全体の`access_epoch`を返します。
`set`はpermission全置換と明示的な未来のaware expiryまたは`--no-expiry`を要求し、
`revoke`はmembershipを削除します。両方`--expected-access-epoch`が必須です。
古いCASは要求状態と既に一致しても失敗します。実変更はmembership更新、
epoch増加、特権専用audit eventを原子的に行い、read/no-opはどちらも行いません。
自然な期限切れはepoch変更でも処理中応答のdrainでもありません。

CLIは共有**tenant session advisory lock**をcommitとJSON stdout flushまで保持します。
同じ版のAPI clientとonlineで協調できますが、migrationは引き続き停止/drainが必要です。
変更結果を失った場合、`get`と特権auditで確認してから新CAS操作を明示承認します。
盲目的retryやidempotency receiptはありません。配信済みcontextは撤回できず、
purge済みdataも復活しません。auditは改ざん耐性の証明やDR solutionではありません。

`009_scope_access.sql`はschema 9でscope-access auditを導入し、schema 11も維持します。
PostgreSQL 18.6/pgvector 0.8.6固定imageと依存版は維持します。
現在stageは`m2-scope-capture-policy`です。[完全な契約](docs/STATUS-jp.md#scope-access-administration)、
[get → set → revoke例とmigration](docs/operations/README-jp.md#scope-access-administration)、
[ADR 0013](docs/adr/0013-scope-access-jp.md)を参照してください。

## Python SDK

**SDKはmemoryの31 methodを維持し、providerは別library/CLIです。v0.0.26実装はlocal・native CI検証済みです。** 対応checkoutから導入します。

```bash
python -m pip install '.[sdk]'
```

任意の`pg-agmemory[sdk]` extraが追加するのは**httpx==0.28.1だけ**です。
FastAPI、psycopg、Janomeを含む既存core distributionのままであり、
独立した軽量SDK packageでも、PyPI公開済みという主張でもありません。
PEP 561の`py.typed` markerを追加します。`mcp`/`hook`が提供するHTTPXでも
import依存を満たし、HTTPXがなければ固定の導入案内`ImportError`となります。

memory/tool入力でなく、信頼する設定から明示引数を渡してください。
以下の環境変数名は例であり、**SDKは自動で読みません**。
scopeは事前にprovision・認可済みである必要があります。
読取り専用の例で、private memory、token、入力、raw error応答を出力しません。

```python
import asyncio
import os
from uuid import UUID

from pg_agmemory.models import Recall
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


async def main() -> None:
    request = Recall(
        scope_ids=[UUID(os.environ["PGAG_SDK_SCOPE_ID"])],
        query="synthetic fixture",
        purpose="read-only SDK example",
    )
    try:
        async with AsyncMemoryClient(
            os.environ["PGAG_SDK_API_URL"], os.environ["PGAG_SDK_API_TOKEN"]
        ) as memory:
            result = await memory.recall(request)
            if not result.coverage.retrieval_complete:
                print("Recall coverage is incomplete; do not infer absence.")
    except MemoryClientError as exc:
        if exc.error.outcome_unknown:
            print("Outcome unknown: retain the existing key and body; reconcile.")
        else:
            print("Memory request failed; no automatic retry was attempted.")


asyncio.run(main())
```

`NativeSettings`の設定errorはsanitized `ValueError`であり、`MemoryClientError`ではありません。
request model構築では別にPydantic `ValidationError`が発生し得ます。
そこに含まれるprivate入力詳細をlogに出さないでください。
SDK call時の検証はsanitized SDK errorを返します。

`AsyncMemoryClient`は固定HTTPS originまたはloopback HTTP originとtokenの形を検査し、
実際のtoken認証はserverが行います。context entryで所有HTTP clientを作成し、
認証付きcapabilitiesの**service 0.0.26 / API v1 / schema 11**完全一致を要求します。
一つのcontext内だけで使い、再entryや自動retryはありません。
未完了taskはawaitするかcancel後にawaitして、**contextをexitする前に完了を確認**してください。
client closeはrequestのschedule/cancel管理でもDB rollbackでもありません。
exitで閉じるのは接続であり、**保存memoryは消去しません**。
scopeはserver ACLを狭めるだけです。返されたmemoryは根拠であって、
信頼する指示や現在の事実の保証ではありません。

batch capture、entity照会、明示job取消、embedding、job、graph、checkpoint、tool effectを含む全31 public memory resource
methodを対象とし、CLI管理やworker実行は対象外です。
すべての変更にはcallerが保持するkeyword-onlyの`idempotency_key`が必須です。
処理中のcancelを含む変更結果不明時は**同じkeyとbody**で照合し、
新keyへの交換やrollbackの推測をしてはいけません。
[型付きmethod/error契約](docs/STATUS-jp.md#python-sdk)、
[運用と更新](docs/operations/README-jp.md#python-sdk-operations)、
[ADR 0012](docs/adr/0012-python-sdk-jp.md)を参照してください。SDKは管理操作でなくHTTP専用です。

## コンテナ検証

ローカル開発ではDocker Desktopではなく**Apple Container**を使います。
[Apple Container](https://github.com/apple/container)をインストールし、実行します。

```bash
container system start
./scripts/test-containers.sh
```

runnerには`jq`も導入してください。使い捨てsmoke設定を含め、
scriptは**Apple ContainerとDockerの両方**で`jq`を必須とします。

このスクリプトは固定したPython依存関係をビルドし、Ruff、mypy、unitテスト、
PostgreSQL integrationテスト後にproduction APIのHTTP healthを確認し、
**non-root production image**内で実際の`pg-agmemory worker --subject ... --once`を実行します。
worker smokeは使い捨てのprovision済みprincipalとruntime専用資格情報を使い、
`{"outcome":"idle"}`を検査して`Production worker smoke passed`をlogに出します。
non-root runtime imageのtokenizer smokeは`東京都` → `東京` / `都`も検査し、
成功時に`Production Japanese tokenizer smoke passed`を出力します。
end-to-end recallや分割品質の評価ではありません。
v0.0.8 runnerはnon-root production image内で実`pg-agmemory mcp` childも実行します。
固定tokenとprovision済みscopeでloopback Native APIへ接続し、4 toolの列挙とrecallを
modern `2026-07-28`・legacy `2025-11-25`の**両mode**で検査します。
日本語/API/worker smokeも維持しています。
専用の使い捨てPostgreSQLコンテナを利用し、
自分が作ったコンテナ・ネットワークだけを片付けます。
既存のDBやコンテナは変更しません。Python/PostgreSQL/uvのimage版とdigestは固定しています。

GitHub Actionsではnative **linux/amd64**・**linux/arm64** runner上のDockerで
同じスクリプトを実行します。
過去のv0.0.8 step名は`Test containers and smoke-test production API, worker, and MCP`です。

```bash
./scripts/test-containers.sh docker
```

synthetic container検査に商用model API keyや外部memory DBは不要です。
初回はコンテナimageとPython依存packageを取得できる必要があります。
**過去のv25 local適格性確認全体とnative CIは合格です。**
v26を適格とする記録ではありません。[別記録のv26証拠](docs/STATUS-jp.md#v0026--schema-11)を参照してください。
Apple Container `./scripts/test-containers.sh`は
**999合格、live skip 5件、既知warning 1件、494.49秒**でした。
Ruff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入smoke、
**新system CA smokeを含む全production smoke**も合格しました。
実装
[`adbead0ab42dfa4a5465f9464d3855c5f64d85c1`](https://github.com/rioriost/pg_agmemory/commit/adbead0ab42dfa4a5465f9464d3855c5f64d85c1)
の完全一致SHAで[CI 35303758871](https://github.com/rioriost/pg_agmemory/actions/runs/35303758871)が合格しました。
**amd64 999合格、live skip 5件、warning 1件 / 600.77秒、
arm64 999合格、live skip 5件、warning 1件 / 797.75秒**です。
両native jobでRuff、mypy **22+1**、全4導入profile、
system CAを含む全production smokeも合格しました。
実装CIの証拠であり、後続の最終文書commitの適格性確認を主張するものではありません。
Azure推論の再実行や、AzureでのCA ENV variantの適格性確認ではありません。
[ADR 0025](docs/adr/0025-live-provider-qualification-jp.md)を参照してください。

**過去のv24実装とsynthetic provider契約は適格性確認済みです。**
実装
[`88975a862ff97873c60e5ce53e066e1aa7b52686`](https://github.com/rioriost/pg_agmemory/commit/88975a862ff97873c60e5ce53e066e1aa7b52686)は
Apple Containerのfull `./scripts/test-containers.sh`で**987合格、warning 1件、493.68秒**でした。
完全一致SHAの[CI 35292285229](https://github.com/rioriost/pg_agmemory/actions/runs/35292285229)は
**amd64 987合格 / 762.27秒、arm64 987合格 / 809.06秒**でした。
全3環境でRuff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入profile、全production smokeも合格しました。
新smokeはsynthetic HTTPに実operator CLIを接続し、
その後にNative vector upload/replay/purgeを明示します。**987 = 既存821 + 新規166 case**です。
SQL fixtureは実際の使い捨てPostgreSQLで実行しますが、**Azure extension binaryではありません**。
live Azure/実model呼出し、課金resource、private dataの外部送信は使っていません。
live互換性、品質、M2完了の認定ではありません。
[適格性確認の証拠](docs/STATUS-jp.md#v0024--schema-10)を参照してください。
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
[適格性確認の証拠](docs/STATUS-jp.md#v0023--schema-10)を参照してください。
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
未変更の修正前sourceに対するnegative controlは期待通り失敗しましたが、
そのsynthetic診断は失敗CI planでも性能benchmarkでもありません。

最終docs revision
[`af91974d091feb276db79baf838c7fa2bab8904f`](https://github.com/rioriost/pg_agmemory/commit/af91974d091feb276db79baf838c7fa2bab8904f)の
[CI 35265233011](https://github.com/rioriost/pg_agmemory/actions/runs/35265233011)は**失敗**しました。
amd64は既存100-path auto-plan graph caseの**503 `QueryCanceled`**で
**772合格、1失敗、631.34秒**、arm64は**773合格、701.79秒**で全smokeも合格しました。
保持済みの使い捨てDB診断は`adjacent` materialization後に残るcanonical metadataとendpointの
反復scanを示しますが、実際の失敗CI planは**未取得**です。
追加修正は`adjacent`のmaterializeを維持し、scope/predicateで絞ったassertion、
timeで絞ったrevision、重複しない認可済み・根拠有効なendpointを別途materializeします。
事前計算ID arrayでsemijoin反転と保護されたcanonical dataの反復scanを防ぐ設計です。
RLS、scope/time/根拠検証、順序、上限、**5000 ms** timeoutは維持します。
方向別回帰拡張は全9組とscan loop検査を含めて検証済みです。
性能benchmarkや本番適格性確認ではありません。

**初期v22実装の証拠であり、追加修正の適格性確認ではありません。**
Apple Containerのfull `./scripts/test-containers.sh`は
**773合格、既存warning 1件、447.40秒**でした。実装
[`75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe`](https://github.com/rioriost/pg_agmemory/commit/75f1ff3d807fd5b17e9cd8b748ffe2a7ed03bafe)は
完全一致SHAの[CI 35262682028](https://github.com/rioriost/pg_agmemory/actions/runs/35262682028)に合格しました。
native amd64は**773合格、621.56秒**、arm64は**773合格、702.01秒**でした。
Ruff、mypy **source 19 + strict SDK consumer 1ファイル**、core/hook/sdk-only導入、
従来の全production smokeとbatch captureのworker/replay/purgeが全3環境で合格しました。
初期結果は、その後のdocs CI失敗を覆すものでも追加修正の適格性確認でもありません。
[検証状況と証拠](docs/STATUS-jp.md#v0022--schema-10)を参照してください。

**過去のv0.0.21追加修正はlocalと両native architectureで検証済みです。**
修正[`956b232f38caeeb7d0421a2d6fd3d8340206bcbc`](https://github.com/rioriost/pg_agmemory/commit/956b232f38caeeb7d0421a2d6fd3d8340206bcbc)は
上限付きgraph隣接集合をmaterializeし、強制generic prepared planで再現した
relation revisionの反復scanを除去します。RLS、時刻/scope/根拠検証、順序、上限は維持します。
失敗したCIのplanは未取得で、この診断は本番性能benchmarkではありません。
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
version/schema/APIと29 resource methodは不変です。
SQL migration、timeout引上げ、JIT無効化、RLS緩和はありません。

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
[検証状況と証拠](docs/STATUS-jp.md#v0021--schema-10)を参照してください。

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
[過去の証拠](docs/STATUS-jp.md#v0020--schema-10)を参照してください。

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
[過去の証拠](docs/STATUS-jp.md#v0019--schema-10)を参照してください。

**過去のv0.0.18実装はlocalと両native architectureで検証済みです。** Apple Containerの
`./scripts/test-containers.sh`は**exit 0**で、**630合格、既存warning 1件、
369.39秒（6:09）**でした。Ruff、strict mypy **source 19 + SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、checkpoint head照会を含む全non-root production smokeが全3環境で合格しました。
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
両runともv0.0.19の適格性確認ではありません。[過去の証拠](docs/STATUS-jp.md#v0018--schema-10)を参照してください。

**過去のv0.0.17実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894)は
完全一致SHAの[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)に合格しました。
各環境**604テスト、既存warning 1件**で、local Apple Containerは**321.56秒（5:21）**、
native Docker amd64は**638.66秒**、arm64は**544.33秒**でした。
Ruff、strict mypy **source 19ファイル + strict SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、構造化recall filterを含む全non-root production smokeが全3環境で合格しました。
別の最終v0.0.17 docs
[`5226c81fae7a50a5668109478d5d9f23fc4d7761`](https://github.com/rioriost/pg_agmemory/commit/5226c81fae7a50a5668109478d5d9f23fc4d7761)は
[CI 35232139680](https://github.com/rioriost/pg_agmemory/actions/runs/35232139680)に合格しました。
native amd64は**465.50秒**、arm64は**557.08秒**で、各**604テスト、warning 1件**、
Ruff、strict mypy **source 19 + consumer 1ファイル**、optional導入、全production smokeが合格しました。
docs所要時間は実装CI 35230044140とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](docs/STATUS-jp.md#v0017--schema-10)を参照してください。

**過去のv0.0.16実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57)は
完全一致SHAの[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)に合格しました。
各環境**566テスト、既存warning 1件**で、local Apple Containerは**298.15秒（4:58）**、
native Docker amd64は**601.73秒**、arm64は**565.34秒**でした。
Ruff、strict mypy **source 19ファイル + strict SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、required-context recallを含む全non-root production smokeが全3環境で合格しました。
別の最終v0.0.16 docs
[`520990d95718b17ae93a7d5259d600e379991df2`](https://github.com/rioriost/pg_agmemory/commit/520990d95718b17ae93a7d5259d600e379991df2)は
[CI 35226313891](https://github.com/rioriost/pg_agmemory/actions/runs/35226313891)に合格しました。
native amd64は**476.15秒**、arm64は**478.08秒**で、各**566テスト、warning 1件**、
全検査、optional導入、production smokeが合格しました。
docs所要時間は実装CI 35224189967とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](docs/STATUS-jp.md#v0016--schema-10)を参照してください。

**過去のv0.0.15実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)は
完全一致SHAの[CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)に合格しました。
各環境**535テスト、既存warning 1件**で、local Apple Containerは**298.29秒（4:58）**、
native Docker amd64は**406.88秒**、arm64は**490.57秒**でした。
native実logでRuff、strict mypy **source 19ファイル + SDK consumer 1ファイル**、
真のoptional導入、明示job取消を含む全production smokeを確認し、localも同じ検査に合格しました。
所要時間は性能benchmarkではありません。別の最終v0.0.15 docs
[`9d34d5329c9db580e7de0459b743511235ad6fb8`](https://github.com/rioriost/pg_agmemory/commit/9d34d5329c9db580e7de0459b743511235ad6fb8)は
[CI 35218254940](https://github.com/rioriost/pg_agmemory/actions/runs/35218254940)に合格しました。
native実logで各**535テスト、warning 1件**、**amd64 605.83秒 / arm64 473.57秒**、
Ruff、strict mypy **source 19 + consumer 1ファイル**、optional導入、全smokeを確認しています。
docs所要時間は実装CI 35216770999とは別です。両v0.0.15 runともv0.0.19の検証ではありません。
[過去の証拠](docs/STATUS-jp.md#v0015--schema-10)を参照してください。

**過去のv0.0.14最終localとnative結果を2026-09-17 JSTに検証しました。**
Apple Containerとnative Docker amd64/arm64で各**495テスト、既存warning 1件**、
Ruff、strict mypy（**source 19ファイル + SDK consumer 1ファイル**）、
真のcore/hook/sdk-only導入、readiness障害/liveness/復旧を含むnon-root production全smokeが合格しました。
内訳は**既存464 + readiness unit 16 + integration 15テスト（新規31）**で、schema 9は不変です。
実装
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
は[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)に合格しました。
native実logで完全一致SHA、件数、全検査/smokeを確認しています。
所要時間は**local 295.67秒 / amd64 385.41秒 / arm64 470.16秒**であり、性能benchmarkではありません。
[検証証拠](docs/STATUS-jp.md#v0014--schema-9)を参照してください。
別の最終v0.0.14 docs
[d4b24f6](https://github.com/rioriost/pg_agmemory/commit/d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68)は
[CI 35202931424](https://github.com/rioriost/pg_agmemory/actions/runs/35202931424)に合格しました。
実logで各native architecture 495テスト/warning 1件と全検査/smokeを確認し、
**amd64 319.51秒 / arm64 503.56秒**でした。両v0.0.14 runともv0.0.15の検証ではありません。

**過去のv0.0.13最終localとnative結果を2026-09-17 JSTに検証しました。**
Apple Containerとnative Docker amd64/arm64で各**464テスト、既存warning 1件**、
Ruff、strict mypy（**source 19ファイル + SDK consumer 1ファイル**）、
真のcore/hook/sdk-only導入検査、scope-accessを含むnon-root production全smokeが合格しました。
内訳は**既存426 + scope-admin unit 22 + integration 16テスト（新規38）**です。
schema 8→9 rollback/retryと以前のmigrationも合格しました。
実装
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413)
は[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)に合格しました。
native実logで完全一致SHA、件数、全検査/smokeを確認しています。
所要時間は**local 297.52秒 / amd64 539.86秒 / arm64 460.73秒**であり、性能benchmarkではありません。
[検証証拠](docs/STATUS-jp.md#v0013--schema-9)を参照してください。
別の最終v0.0.13 docs
[185f433](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)は
[CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967)に合格しました。
実logで各native architecture 464テスト/warning 1件と全検査/smokeを確認し、
**amd64 499.25秒 / arm64 454.37秒**でした。両v0.0.13 runともv0.0.14の検証ではありません。

**過去のv0.0.12最終localとnative結果を2026-09-17 JSTに検証しました。**
Apple Containerとnative Docker amd64/arm64で各**426テスト、既存warning 1件**、
Ruff、strict mypy（**source 18ファイル**）、別のstrict型付きconsumer（**1ファイル**）、
真のcore/hook/sdk wheel導入検査、同梱`py.typed`、
新SDK lifecycleを含むnon-root production全smokeが合格しました。
既存345テストに**SDK unit 76 + integration 5テスト（新規81）**を加えた結果です。
実装
[88e1206](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)は
[CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945)に合格しました。
native実logで完全一致SHA、件数、全検査を確認しています。
所要時間は**local 292.76秒 / amd64 484.79秒 / arm64 472.49秒**であり、性能benchmarkではありません。
[検証証拠](docs/STATUS-jp.md#v0012--schema-8)を参照してください。
最終v0.0.12 docs [0e00abd](https://github.com/rioriost/pg_agmemory/commit/0e00abdae930dcc1e2d2015fbf5931a74701fe7d)も
[CI 35194141510](https://github.com/rioriost/pg_agmemory/actions/runs/35194141510)に合格しました。
実logで各native architecture 426テストと全検査/smokeを確認し、
**amd64 488.20秒 / arm64 454.88秒**でした。上記実装所要時間やv0.0.13検証とは別のdocs runです。

**過去のv0.0.11/schema 8を2026-09-17 JSTに検証しました。** Apple Containerとnative Docker
amd64/arm64は各**345テスト、既存warning 1件**、Ruff、strict mypy（**source 17ファイル**）、
core-only/hook-only導入検査、non-root productionの全smokeに合格しました。
episode/assertion vectorのexact/hybrid検索とpurgeも対象です。
テスト所要時間は**local 283.44秒、amd64 404.40秒、arm64 433.46秒**であり、性能benchmarkではありません。
実装[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)は
[CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403)に合格しました。
[検証証拠](docs/STATUS-jp.md#v0011--schema-8)を参照してください。
最終v0.0.11 docs
[dccd5cb](https://github.com/rioriost/pg_agmemory/commit/dccd5cb5571873515aace8621ce4adb3de250d3a)も
[CI 35190495385](https://github.com/rioriost/pg_agmemory/actions/runs/35190495385)に合格し、
両native実logで**345テスト、warning 1件**、Ruff、mypy **source 17ファイル**、
導入検査、全smokeを確認しました。**amd64 506.38秒 / arm64 460.18秒**です。
これは上記実装runとは別のdocs run観測値であり、どちらもv0.0.12の検証ではありません。

採用DB profileはprebuilt
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`です。
artifact検査で**amd64/arm64両image**のPostgreSQL **18.6-1.pgdg12+2**、
native ELF、`vector.control` **0.8.6**を確認しました。
PostgreSQL版は18.6のままですが、**base digestが異なる新しい上流DB image/profile**であり、
旧library PostgreSQL imageを変更していないという意味ではありません。
このprofileに新DB Dockerfile、source build、host APT workflowはありません。
artifact検証は上記のapplication/migration/CI合格証拠とは別です。
過去のv0.0.11ではPython依存はproject版metadata以外変更せず、
raw parameter-bound vector castにpgvector Python packageは不要でした。

**過去のv0.0.10/schema 7の最終localとnative結果を2026-09-17 JSTに確認しました。**
Apple Containerとnative Docker amd64/arm64は各**304テスト、既存warning 1件**に合格しました。
**Ruff、strict mypy（source 16ファイル）、真のcore-only/hook-only導入検査、
non-root productionの全smoke**も全3環境で合格しました。
日本語/API/worker、MCP両時代、hook全3 eventに加え、
新しい原子的な**capture → 実worker → recall → replay → purge** workflowが対象です。

| 環境 | テスト所要時間 |
|---|---|
| ローカルApple Container | **275.53秒** |
| Docker、native `linux/amd64` | **467.75秒** |
| Docker、native `linux/arm64` | **434.40秒** |

最終local sourceは公開済み実装
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f)と一致します。
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)は
両native jobとも合格し、実logでjob statusだけでなく完全一致SHA、件数、所要時間、検査を確認しました。
所要時間は性能benchmarkではありません。
[v0.0.10証拠](docs/STATUS-jp.md#v0010--schema-7)を参照してください。
最終v0.0.10 docs
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)も、
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760)で
**各native architecture 304テスト**に合格しました。
このdocs runは上記実装runの所要時間とは別であり、両runともv0.0.11/schema 8を検証していません。

**過去のv0.0.9/schema 7の最終結果を2026-09-17 JSTに確認しました。**
全3環境で**274テスト、既存warning 1件**、Ruff、strict mypy（**source 15ファイル**）、
真のcore-only/hook-only導入検査、non-root productionの日本語/API/worker、
MCP **`2026-07-28`・`2025-11-25`**、
hook **`session_start`・`task_switch`・`after_compaction`**の全smokeに合格しました。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **248.29秒** |
| Docker、native `linux/amd64` | **482.21秒** |
| Docker、native `linux/arm64` | **374.33秒** |

最終local sourceは公開済み実装
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)と一致します。
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)の
両native jobの実logで、job statusだけでなく完全一致SHAと上記全検査を確認しました。
所要時間はテスト観測値であり、性能benchmarkではありません。
範囲は[v0.0.9検証証拠](docs/STATUS-jp.md#v009--schema-7)を参照してください。
これらの検査で元のmilestoneや受入gateが完了したとは扱いません。
最終v0.0.9 docs commit
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)も、
[CI run 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689)で
両native architecture各**274テスト**に合格しました。
どちらのv0.0.9 runもv0.0.10 atomic captureの検証ではありません。

**過去のv0.0.8/schema 7:** 実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)は、
Apple Containerとnative Docker amd64/arm64で各**214テスト**（既存warning 1件）、
Ruff、strict mypy（source 13ファイル）、全production smokeに合格しました。
上記MCPの両protocol modeも合格しています。
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)と
[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。
その後のbilingual docs commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)は、
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509)で
**両native architecture各214テスト**に合格しました。
どちらのv0.0.8 runもv0.0.9 hookや共有client抽出の検証ではありません。

**過去のv0.0.7/schema 7限定の証拠です。** 実装commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)は、
Apple Containerとnative Dockerの**linux/amd64**・**linux/arm64**で、
それぞれ**144テスト**（既存warning 2件）、Ruff、strict mypy（source 12ファイル）、
non-root productionの日本語tokenizer、API HTTP、実CLI worker `--once` idle実行という
全3種のsmokeが合格しました。最終結果は**2026-09-17 JST**に確認しました。
両CI jobは同じ完全一致SHAで実行し、実logで全検査を確認しています。
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)と、
[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。
最終bilingual docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)も
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899)で
両native jobが合格しました。これらの過去runはv0.0.8/v0.0.9の結果ではありません。

**過去のv0.0.7**最終lockは既存package-feed registryを維持し、全**36 package**のversion、依存metadata、
artifact hashはテスト済みPyPI解決lockとbyte単位で同一です。v6との差分はJanome 0.5.0の
追加とprojectのv0.0.7へのversion更新だけで、無関係なupgradeやregistry移行はありません。
native CIはこのretained-registry lockからbuildしました。v0.0.8 MCP extraには追加依存があり、
旧package件数とlock比較は新lockを説明するものではありません。

## APIの起動

上記の固定上流pgvector DB profile（PostgreSQL 18.6、pgvector 0.8.6）を使用します。
以下のアプリケーションコマンドは
`Dockerfile`で構築したimage内で実行します。最終stageがruntime imageです。

1. 対象の空Memory DBを`PGAG_ADMIN_DATABASE_URL`に指定し、
   `pg-agmemory migrate`を実行します。migrationはtransactionalで再実行可能です。
   管理者はforced RLSをbypassできる必要があります
   （superuserまたは適切な権限を持つ`BYPASSRLS`）。
   role/schema/tableのDDLと`btree_gist`導入に必要な権限も必要です。
   runtimeに付与する権限ではありません。
2. `NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime`の専用loginを作成し、
   passwordを安全に設定します。**migrationのtable owner roleへの所属を与えないでください。**
   このloginの接続先を`PGAG_DATABASE_URL`に設定します。
3. admin URLで`pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT`を実行し、
   private tenant、principal、scopeを作成します。返された`scope_id`を保存します。
   subjectは設定したissuer内で一意です。
4. `PGAG_JWT_PUBLIC_KEY`に2048 bit以上のPEM RSA公開鍵、
   `PGAG_JWT_ISSUER`に正確なissuer、`PGAG_JWT_AUDIENCE`に本サービスのaudienceを設定します。
   tokenはRS256署名と`sub`、`iss`、`aud`、`iat`、`exp`を必須とします。
   request bodyのtenant/principal指定は拒否します。
5. `pg-agmemory serve`を実行します。TLSは信頼できるreverse proxyで終端してください。
   port 8000自体はHTTPです。信頼できないnetworkへ直接公開しないでください。

runtime環境にadmin URLや署名用秘密鍵を渡さないでください。
組込みcredential、既定token、認証回避設定はありません。
migration/provision/rebuildは管理操作であり、public endpointとして公開してはいけません。
起動時にsuperuser、RLS bypass、table ownerのruntime接続を拒否します。

**v0.0.26はschema 11とmigration `011_capture_policy.sql`を要求します。** 既存schema 10 DBには
[schema 11保守更新](docs/operations/README-jp.md#schema-11-scope-capture-policy-upgrade)を使います。
古いschemaにはv0.0.15で導入した`010_job_cancellation.sql`が引き続き必要です。
[011までのmigration sequence](docs/operations/README-jp.md#schema-11-scope-capture-policy-upgrade)に従ってください。
古いschemaにはdurable admin audit用の`009_scope_access.sql`を含む既存migration sequenceも必要です。
古いDBには引き続きv0.0.11の`008_pgvector.sql` migrationが必要です。
PostgreSQLは**`public`内の`vector` 0.8.6**を必要とし、
migrationは版/schemaが異なる既存extensionを拒否します。prebuilt profileは対応extensionを提供します。
API、worker、`migrate`はschema 11記録済みでもこれを検査します。
必要なmigrationの前に**旧版・新版の全API、worker、adapter、hook起動、
SDK caller、管理commandを停止/drain**し、backupと現在の削除/ACL記録を保全してoffline migrationを行います。
古いDBにはmigration 007のlexical backfillを含む既存migrationも適用します。
**embedding backfillや自動embedding再構築はありません**。
対応するv0.0.26 processだけを再起動し、API/workerは厳密な履歴
`[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]`とschema `public`内のextension `vector` 0.8.6を要求します。
古いschemaのprocessとschema 11のrolling混在互換性はありません。
旧imageは停止を維持してください。v0.0.1にはschema互換性guardがありません。
rolling共存やdowngradeは非対応です。
[現在のschema 11手順](docs/operations/README-jp.md#schema-11-scope-capture-policy-upgrade)に従ってください。

shellに`MEMORY_URL`、`TOKEN`、作成済みの`SCOPE_ID`を設定して実行します。

```bash
curl --fail-with-body "$MEMORY_URL/v1/observe" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-observation-1' \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"source_namespace\":\"demo\",\
\"source_event_id\":\"contract-1\",\"occurred_at\":\"2026-09-01T00:00:00Z\",\
\"content\":\"ACME contract is Gold\",\"consent_reference\":\"demo-consent\"}"

curl --fail-with-body "$MEMORY_URL/v1/recall" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"scope_ids\":[\"$SCOPE_ID\"],\"query\":\"Gold\",\
\"purpose\":\"demo\",\"token_budget\":2000}"
```

`consent_reference`はcallerによる同意の申告を記録します。現段階では外部の同意台帳の
検証やsecret/PIIの自動除去は行いません。保存が許可され、除去処理済みのdataだけを送ってください。
対話的schema表示は`/docs`、OpenAPIは`/openapi.json`です。
`/healthz`は起動検証後のprocess livenessであり、継続的なDB readinessではありません。
上限付きruntime検査は[`/readyz`とその制限](docs/STATUS-jp.md#runtime-readiness)を参照してください。

## Pgvector retrieval foundation

**v0.0.11/schema 8で実装・検証済みです。**
実験的なprovider非依存の数学的基盤であり、意味検索の品質認定ではありません。
従来のlexical既定は変えず、vectorは明示的に渡します。

- Native読取り専用`POST /v1/embedding-inputs`は既存Explain
  `{memory_id, revision}`を受け取り（revision既定はlatestでなく**1**）、idempotency keyは不要です。
  認可済みcanonical text、UTF-8 SHA-256の`input_digest`、
  `input_format: "memory-content-v1"`を返します。
  episode textは正規化content、assertion textはrelation display valueも含む正確な
  `subject / predicate: value`で、ID/時刻を含めません。
- Native `POST /v1/embeddings`は`Idempotency-Key`、正確なdigest、
  caller宣言model名/revision、**768個の有限JSON数値**を要求します。
  空間はcosine / `l2-f32-v1`固定で、serverはfloat64で正規化した後pgvector float32へ変換します。
  boolean、数値文字列、zero vector、切詰め、次元変換は認めません。read/write scopeはcanonical parentから導出します。
- canonical revision/model namespace当たり不変vector一つです。
  同じ正規化float32値/digestは別HTTP keyでも重複抑止し、異なるvectorはconflictします。
  置換には新model revisionが必要です。**canonical revision当たりmodel versionは最大8件**で、
  上限時も既存duplicateは許可します。
  保存idempotency resultは`{memory_id, revision}`だけで、平文digest/model名/vectorは含めません。
  現在読取り可能なcanonical inputとprojectionから応答metadataを再構成し、
  HMAC/opaque anchorは保持します。parentが生存していても管理者がprojectionだけを削除した場合、
  replayは再構築せず**409 `embedding_unavailable`**を返します。
- recallに`retrieval_mode: "lexical" | "vector" | "hybrid"`とinline `vector_query`を追加します。
  lexicalはvectorを拒否し、vectorは空text query、hybridは非空textを要求します。
  queryの黙った無視や広いfallbackはありません。vectorは現在の認可/時間条件を満たす
  materialized候補のexact cosine、hybridは決定的lexical/vector rankを**RRF k=60**で統合します。
  省略`as_of`/`known_at`はselection/coverage前に一度だけ確定し、明示時刻は変更しません。
  実際のdistance/scoreの同順位はUUIDで解決しますが、
  任意float結果/rankingの全CPU間bit単位一致を保証しません。
  ANN/HNSWやneighborによるscope拡大はありません。
- 可視・適格projectionの欠落は`vector_incomplete` coverageで明示し、非認可itemをcoverageへ含めません。
  ranking metadataはconfidence/真実ではありません。JSON全体のUTF-8 context-pack予算は維持します。
  parent purgeはvector/digest/model metadataへcascadeし、別memory identityやprovenance vertexを作りません。
  独立model registryもありません。

既定response fieldを追加し、`MemoryItem.retrieval: null`、
`RecallResult.retrieval_mode: "lexical"`、`embedding_model: null`、
`coverage.vector_incomplete: false`となります。
non-nullの`retrieval`は`exact_cosine`/`rrf-60`のranking evidenceとnullableなrank/distance/fusion fieldを持ちます。
既定lexical semanticsは維持しますが、**HTTP JSON形式のbyte単位互換を意味しません**。
capabilitiesに`retrieval_modes: ["lexical", "vector", "hybrid"]`と
`default_retrieval_mode: "lexical"`を追加します。

MCPは**4 tool**を維持し、生成Recall引数へvector/hybrid fieldを追加しますが、
embedding input/upload toolは追加しません。hookは**読取り専用・lexical専用**です。
Native応答の非lexical `retrieval_mode`、non-nullの`embedding_model`/item `retrieval`、
trueの`coverage.vector_incomplete`を拒否します。
capture、Observe、job、workerはembeddingを生成しません。
canonical inputをlogへ出したり、明示承認なく第三者へ送ったりしないでください。
[合成basis vector例](docs/operations/README-jp.md#synthetic-vector-example)は外部modelを呼ばず、
本番embedding modelでもありません。[予定契約](docs/STATUS-jp.md#pgvector-exact-and-hybrid-retrieval)と
[ADR 0011](docs/adr/0011-pgvector-retrieval-jp.md)を参照してください。

## Atomic structured capture

schema 11は完全一致replayを含む両dedup経路の前で
[scope capture policy](#scope-capture-policy)を検査します。
以下の既存形式/job動作は現在の認可とpolicy受付を通過した場合に適用します。

1〜16件のproposalには別の[batch route](#explicit-batch-capture)を使い、
以下の単一job契約は変更しません。

**既存capture契約はv0.0.11でも検証済みです。v0.0.10結果は過去の証拠です。**
Native `POST /v1/captures`は`Idempotency-Key`と
`{episode: <変更しないObserve>, memory: <一つの構造化intent>}`を要求します。
memory intentは`subject`、`predicate`、`value`、一つの`evidence_quote`、
`explicit_intent: true`、任意のtimezone付き/nullの`valid_from`/`valid_to`を持ちます。
**scope、根拠ID、identity fieldは受け付けず**、scopeと一つのepisode根拠IDをtransaction内で導出します。
1〜4,096文字のquoteは正規化済みepisodeに原文として含まれる必要があります。
既存Rememberのfield上限/valid-time規則とreported・未校正semanticsを維持します。

**HTTP 201**は`{memory_id: <episode UUID>, revision: 1,
synthesis_job_id: <job UUID>}`を返します。
episodeと一つの`structured_remember` / `structured-remember-v1` jobのcommitを確認するだけで、
**assertion publicationの完了ではありません**。
既存jobやterminal jobを再利用する場合があり、**201は新規/pending jobを保証しません**。
返却値は過去参照であり、現在のstatusはGETを正とします。
`GET /v1/jobs/{synthesis_job_id}`と既存の固定subject workerで後続publicationを扱います。
scope当たりactive job 100件、job当たり5試行、lease、epoch検査、publication fencingを維持します。
`POST /v1/observe`は変更せず`synthesis_job_id: null`で、自動jobはありません。
明示`/v1/jobs`と同期`/v1/remember`も変更しません。

同じkey/正規化bodyは同じepisode/job組を返し、同じkeyでbodyを変えるとconflictします。
同じepisode/intent/principalなら新HTTP keyでも組を重複抑止します。
別の明示intentは生存episode上に別jobを作れ、別の認可済みprincipalは独立したjob identity/所有権を持ちます。
episode/projection、job data/identity、idempotency、auditを一つのtransactionで扱います。
transaction失敗は新規変更をrollbackし、以前から独立して存在したepisodeは削除しません。

replayは**両IDの現在のACL/削除**を確認します。episode/job/resultの依存purgeで組は
`404`になり得て、新keyでもpurge済みjob identityを再作成できません。
failed jobは既存job-retry操作で明示retryし、capture replayはretry childでなく元jobを返します。
明示retry childを作成した後も変わりません。
生存source上の新しい別intentを永久禁止するものではありません。
IDは過去参照であり、fresh stateはGETで取得します。

captureは**MCP toolではなく**、recall hookも自動captureしません。
capture自体はembedding生成、LLM/provider呼出し、intent抽出、自動synthesis、意味的真実の認定を行いません。
Native HTTP response-drainとhost消去の境界は変更しません。
[全差分契約](docs/STATUS-jp.md#atomic-structured-capture)、
[operator向けcurl例](docs/operations/README-jp.md#atomic-structured-captureの運用)、
[ADR 0010](docs/adr/0010-atomic-capture-jp.md)を参照してください。

## Local stdio MCP

所有job照会はNative/SDK専用であり、MCP job toolは追加しません。

checkpoint head照会はNative/SDK専用で、この4 bindingへcheckpoint toolは追加しません。

`memory_recall`は型付き`filters`も受け付けます。ranking前の完全一致選択と
required参照との共通条件は[filter契約](#exact-structured-recall-filters)に従います。
toolやsafe error codeは追加しません。

`memory_recall`はNative request wrapperで追加の`required_memory_refs`を受け付けます。
required prefixの予算失敗はsafe `budget_exhausted`のtool errorであり、
空の成功結果ではありません。4-tool surfaceは不変です。

通常の`pg-agmemory` package導入では任意の`mcp`/`hook`/`sdk`依存を**導入しません**。
MCPには`pg-agmemory[mcp]`、recall-hookには`pg-agmemory[hook]`、
Python SDKには`pg-agmemory[sdk]`を選択してください。
repositoryのDocker test/runtime imageは意図的に3 extraすべてを含みますが、
**base packageの既定ではありません**。

任意の`pg-agmemory[mcp]` package extraを導入するか、v0.0.26のtest/runtime両stageに
`mcp`・`hook`・`sdk`を含むrepository imageを使用します。
MCP extraは公式**mcp 2.2.0** SDKと**httpx 0.28.1**を固定しています。
checkoutでは`uv sync --frozen --extra mcp`でlock済み環境を準備できます。
信頼するlocal MCP hostから次を起動します。

```bash
pg-agmemory mcp
```

**`PGAG_MCP_API_URL`**と**`PGAG_MCP_API_TOKEN`**は信頼する起動設定から渡し、
tool引数やcommitするhost設定には含めないでください。URLはHTTPS originまたはloopback HTTP
originに限定し、credential/path/query/fragmentは禁止です。tokenは**Native API audience用**で、
Native APIが検査します。MCP caller identityを転送するものではありません。
起動時に認証付きcapabilitiesを照会し、API `v1`、service `0.0.26`、schema `11`の一致を要求します。
設定/認証/versionの失敗はsecretを出さず非zero終了します。
固定tokenの更新には再起動が必要です。`--subject`と`--once`は拒否します。

Native Pydantic model由来のschemaを持つ次の4 toolだけを公開します。

| Tool | 引数 | Native操作 |
|---|---|---|
| `memory_recall` | `{request: <Recall body>}` | `POST /v1/recall` |
| `memory_remember` | `{request: <Remember body>, idempotency_key: "..."}` | `POST /v1/remember` |
| `memory_explain` | `{request: <Explain body>}` | `POST /v1/explain` |
| `memory_forget` | `{request: <Forget body>, idempotency_key: "..."}` | `POST /v1/forget` |

両mutation toolはforget previewも含め、caller管理の**1〜256文字のvisible ASCII key**
（空白不可）を必須とします。keyのtrim/書換えはせず、正確に256文字は許可、257文字は拒否します。
Native forgetは従来どおり**preview/purgeともHTTP 202**です。
結果不明時は**stdio再起動をまたいでも同じkeyとbodyを再利用**
してください。自動retryやkey生成はありません。mutationのtransport障害、5xx、不正応答は
`outcome_unknown`であり、**rollbackではありません**。成功は
`structuredContent: {result: <Native result>, error: null}`、失敗は`isError: true`と
`{result: null, error: {code, retryable, outcome_unknown, native_status, request_id}}`
を返します。短いtextには根拠を重複収録しません。

rememberは明示structured publication専用です。episode単体保存にはNative `/v1/observe`、
明示episode/jobの原子性にはNative `/v1/captures`を使い、どちらもMCP toolではありません。
UTF-8 byte予算（model tokenではない）、日本語recallのopt-in、現在のNative認証/ACL、
削除検査、過去のidempotency参照は維持します。MCP session/request IDはmemory run IDでも
HTTP idempotency keyでもありません。

**信頼identityごとにadapterを一つ使い、共有やnetwork公開はしないでください。**
callごとのheader/identity/URL上書き、remote MCP HTTP/SSE、OAuth、delegationはありません。
adapterは信頼するlocal Native API clientです。Native response-drain barrierはadapterへのHTTP
配信で終了し、**stdio・host UI・LLM contextまでの原子的barrierではありません**。
buffer済み/配信済みcontextは回収できません。forget/ACL変更後はhostがcached contextを破棄する
必要があり、MCP削除通知やadapterのresponse/semantic cacheはありません。
protocol検証の制限を含む[全契約](docs/STATUS-jp.md#local-stdio-mcp)、
[起動と復旧](docs/operations/README-jp.md#local-stdio-mcpの運用)、
[ADR 0008](docs/adr/0008-local-mcp-jp.md)を参照してください。
v0.0.11はmodern `2026-07-28`とlegacy `2025-11-25`の両protocol契約と全MCP semanticsを維持します。
過去のv0.0.9 regressionと両protocol smokeはlocalとnative Docker両architectureで合格しました。
過去v0.0.10/v0.0.11と最終local/native v0.0.12検査も合格しています。

## Implicit recall hook

所有job照会用のhook fieldやjob操作は追加しません。

checkpoint head照会用のhook fieldやcheckpoint操作は追加しません。

hook入力は`filters`を未知fieldとして拒否し、内部`Recall.filters`は既定`None`です。
信頼する起動時設定をeventごとに上書きする機能は追加しません。

hook入力は`required_memory_refs`を拒否し、内部`Recall`は`[]`のためhost pinningはありません。
optional-onlyの成功時の空理由`empty_reason: "budget_exhausted"`は、
required-context Native/SDK/MCPの`422 budget_exhausted` errorとは別です。

**既存の読取り専用・lexical専用hookはv0.0.11でも検証済みです。**
過去のv0.0.9 local/native検査は合格しています。
任意の`pg-agmemory[hook]`を導入します（checkoutでは`uv sync --frozen --extra hook`）。
固定依存は**httpx 0.28.1だけで、MCP SDKではありません**。
Docker test/runtimeには`mcp`・`hook`・`sdk`を含めます。
`pg-agmemory recall-hook`は一回実行のvendor-neutralなlocal Native HTTP clientです。
hostへの自動登録はなく、Copilot・Claude・Codex連携を**主張しません**。
外部model呼出しやDB資格情報は不要です。

stdinへUTF-8 JSON documentを一つ送り、**stdinを閉じます**。

```json
{"event":"session_start","query":""}
```

`event`は`session_start`、`task_switch`、`after_compaction`のいずれかです。
`query`は必須、最大4,096 Unicode文字で、空なら設定scope内をcanonical browseします。
取得意図はJSONの`query`だけから渡し、`event`はquery本文ではなくlifecycle名です。
stdin上限は32,768 byteです。identity、scope、purpose、mode、予算、URL、header、tool、
時刻を含む追加fieldは禁止です。event textはアクセス権を付与しません。
`--subject`/`--once`は拒否します。

prompt/event dataでなく**信頼する起動環境だけ**から必須の`PGAG_HOOK_API_URL`、
必須の固定Native audience用`PGAG_HOOK_API_TOKEN`、
必須の`PGAG_HOOK_SCOPE_IDS`（重複しないUUID 1〜32件のJSON配列）とrecall設定を渡します。
既定はpurpose `implicit_context`、予算**2,000 UTF-8 byte（model tokenではない）**
（64〜2,000）、max items 20（1〜20）、search profile `simple-v1`
（日本語`ja-janome-0.5.0-v1`はopt-in）、timeout 2.0秒（有限の0.1〜20秒）です。
purposeは1〜256文字です。[全環境設定表](docs/STATUS-jp.md#implicit-recall-hook)を参照してください。
HTTPS originまたはloopback HTTPだけを許可し、URL credential/application path/query/fragmentを
禁止しますが、root `/`は許可します。URL/token/scope-ID設定はすべて必須で、
URL未設定は既定宛先でなく`invalid_hook_configuration`になります。
共有`NativeSettings`は`httpx.URL`も使い、transport前に制御文字や不正IDNAを拒否します。
redirect/proxy環境を無効化し、TLSを検証します。

呼出しごとに新しく認証付きcapabilitiesで厳密な
**service `0.0.26` / API `v1` / schema `11`**を検査し、
`mode: "implicit"`とNativeの現在時刻defaultでrecallをPOSTします。
deadlineは**両HTTP処理の合計**に適用し、process起動・stdin入力/待機・出力は含みません。
LLM latency SLOではありません。harness側には別のsubprocess timeoutが必要です。
共有の上限付きNative HTTP transportでMCPの不変条件を維持します。
request/response上限は256 KiB/2 MiBです。`context_pack.byte_count`は**本文だけでなく、
metadata/citationを含むcontext pack全体のcompact JSON serialize結果**を数えます。
UTF-8、`ensure_ascii=False`、`separators=(",", ":")`を使い、
hookがその件数と設定byte予算を検査します。返却item数は`max_items`以下、
返却search profileは設定と一致しなければなりません。不一致は失敗させ、広いfallbackは行いません。
書込み、capture、queue、LLM provider、cache、retry、idempotency keyは追加しません。

検証対象のhook runtime結果はstdoutへJSON結果一つと改行を出し、stderr診断はsanitizedです。
成功は`{status: "ok", event, result: <完全なNative RecallResult>, error: null}`、
失敗は`{status: "error", event: <検証済みeventまたはnull>, result: null,
error: {code, retryable, outcome_unknown: false, native_status, request_id}}`です。
Native status/request UUIDはnullableです。
[確定error code表](docs/STATUS-jp.md#上限結果失敗処理)を参照してください。
終了値**0**はNativeの正当な`not_found`/`budget_exhausted`/`index_incomplete`空結果も含みます。
**2**は不正設定/入力、**1**はNative/network/version/protocol障害です。
**取得失敗を空の成功に変換してはいけません。**
空pack自体にも約192 byteが必要で、有効な指定予算64でも
明示的なNative **422 `budget_too_small` / hook終了値1**になり得ます。
一方、packは収まるが候補が収まらない`budget_exhausted`は**200 / 終了値0**です。
index欠落による`index_incomplete`も不完全coverage付きの200/終了値0になり得ます。
recallは未認可scopeや失効membershipを黙ってfilterし、許可済みsubsetまたは
itemなし/`not_found`を返します。**scopeの存在を示す404ではありません**。
token認証失敗は引き続き明示的な**401 / hook終了値1**です。Native semanticsの変更ではありません。
**起動方法のerrorは例外です。** CLI flag拒否や`hook` extra未導入は、
argparseのstderr診断と**終了値2で、JSON envelopeを返しません**。
hostのtimeout/killでもenvelopeを返せない場合があるため、harnessは非JSON/不正出力も扱います。

hostはerror/coverageを表示し、停止かmemoryなしの継続かを明示判断してください。
取得memoryは**信頼できない根拠であり、指示やpolicyではありません**。
queryをlogへ記録したりerrorへコピーしたりしてはいけません。
Native session advisory barrierは信頼するhookへのHTTP配信で終了し、
buffer/stdout/host contextまで原子的には続きません。
回収、削除通知、host消去証明はありません。
forget/ACL変更後は前のcontextを破棄し、新しくhookを呼び出します。
hookは権限を拡大しません。実行可能な
[vendor-neutral Python harness例](docs/operations/README-jp.md#vendor-neutral-python-harness例)と
[ADR 0009](docs/adr/0009-implicit-recall-hook-jp.md)を参照してください。

## Opt-inの日本語lexical recall

`POST /v1/recall`の既定は`search_profile: "simple-v1"`で、
PostgreSQL `simple`/`plainto_tsquery`/`ts_rank_cd`を維持します。
日本語scriptのsurface/wakati分割には`"ja-janome-0.5.0-v1"`を明示選択し、
応答はそのprofileを返します。Janome **0.5.0**と、Janome追加語を含む同梱
**mecab-ipadic-2.7.0-20070801**を使います。対象の日本語script連続部分だけを分割し、
ASCII識別子/英語はsegmenterをそのまま通過します。Unicode/全半角正規化、原形化/stemming、
同義語処理、分割品質の保証はありません。漢字scriptの処理は中国語文字にも及びますが、
中国語recallの適格性は未確認です。

Janomeは日本語script連続部分がある場合だけlazy importします。
入力prefix cacheは無効（`max_cached_word_len=0`）で、保持するcacheは同梱辞書resourceだけです。
test/runtime container buildは**静的Janome package bytecodeだけ**を逐次事前compileし、
user textやmemory index/cacheは作りません。この事前compileのないcold host installationでは
初期化時のpeakが大幅に増える可能性があり、配置時のresource sizingは適格性未確認です。

`tokenizer_id: "utf8-bytes-v1"`は引き続きcontext byte予算用で、日本語token数ではありません。
scope、時間、根拠、ACL、削除、byte上限を維持します。
認可済み・時間条件内の日本語projectionが欠けると、黙ってfallbackせず
`coverage.lexical_incomplete: true`と`coverage.retrieval_complete: false`を返します。
projection欠落があり候補もなければ`empty_reason: "index_incomplete"`となり、
既存の予算不足とは区別します。lexical modeの空queryはindex不完全flagがあってもcanonical itemをbrowseします。
flagはprojection coverageであり、query関連性やqueue状態ではありません。

書込みはepisodeと全assertion revisionを原子的にindex化し、typed relation/job publicationも
含みます。lexical runtime権限は`SELECT`/`INSERT`だけで、`UPDATE`や直接`DELETE`は
付与しません。canonical parent purgeが同じbarrierでFK cascadeにより派生行を消し、
子tableのDELETE権限は不要です。別memoryとしては扱いません。
offline `pg-agmemory reindex-lexical`は`PGAG_ADMIN_DATABASE_URL`で
**選択DBの全tenant**を再構築します。`--subject`はscope filterではなく拒否し、
`--once`もworker専用です。API/workerを停止/drainし、backup、再構築後に
lexical projectionを再構築して、対応するv0.0.26 processだけを再起動します。
embeddingの投入/再構築は行いません。
自動修復workerやfileベースのmemory indexはなく、lexical再構築はproviderを呼びません。
別のoperator推論libraryはこの保守経路を変更しません。
[契約](docs/STATUS-jp.md#日本語lexical-profile)、
[保守](docs/operations/README-jp.md#lexical-profileとreindexの運用)、
[ADR 0007](docs/adr/0007-japanese-fts-jp.md)を参照してください。

## Durableな構造化publication job

`POST /v1/jobs`は`Idempotency-Key`と
`{kind: "structured_remember", memory: <変更しないRemember request>}`を要求します。
現在のread/write権限の下で明示intentと同一scopeのepisode原文根拠を指定します。
`202`は`{job_id, kind, recipe_version: "structured-remember-v1"}`という
job参照であり、**publication完了ではありません**。
同一principal/scope内でcanonical intent/recipeが同じならHTTP keyをまたいで重複抑止します。
job dedupでは根拠順を正規化しますが、同じHTTP keyには同じ正規化requestが必要です。

`GET /v1/jobs/{job_id}`は安全なstate、試行回数、時刻、入力参照、元のrevision 1結果参照を
返し、request、lease token、owner principalは返しません。
scope当たりpending/runningは100件、job当たり最大5試行です。
terminal jobはrequest JSONを消去します。ownerは`POST /v1/jobs/{job_id}/retry`へ
元のbody全体とkeyを送り、failed jobを明示再試行できます。
ownerはstate/attempt CASで[pending/running作業を取消](#explicit-job-cancellation)できます。
cancelled jobはterminalでretryできず、active-job上限と`jobs_pending`の対象外ですが、
同intentのenqueue/captureは引き続きそのjobへdedupします。
同じparentの再試行は一つのchildを再利用し、そのchildが失敗したらchildを再試行します。

subjectのprovision後、制限付き`PGAG_DATABASE_URL`資格情報で実行します。

```bash
pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT --once
```

`--once`省略時は継続実行します。workerはそのprincipalのjobだけをclaimします。
`--subject`は信頼する配置設定でありHTTP偽装機能ではなく、workerにJWT key/admin URLは不要です。
commit済みclaim、期限付きtoken lease、現在の認可/epoch再検査、
assertion/jobの原子的publicationで古い試行を拒否します。
at-least-once処理とjob当たり最大一つのcommit済み結果であり、外部exactly-once実行ではありません。
assertionのrecorded/system timeはenqueueでなくworker publication時に始まります。
worker stdout/logのoutcome参照は過去の記録であり、現在のread許可ではありません。
job GET/explainが現在のアクセス権と削除状態を再検査します。

`observe`は引き続きenqueueせず（`synthesis_job_id: null`）、同期`remember`も変更しません。
recallは読取り可能な待機作業を`coverage.jobs_pending`で示し、
`synthesis_pending: false`と`graph_used: false`を維持します。
jobはrecall/explain itemでもcheckpoint/effectの参照kindでもありません。
source/result purgeは依存jobとretry子孫を削除し、実行中publisherを拒否します。
**jobやretry chainだけの削除では、公開済みassertionやsource episodeは消えません。**
factを消すにはresult/sourceを明示purgeしてください。
[契約](docs/STATUS-jp.md#durable-job)、
[worker運用](docs/operations/README-jp.md#durable-jobとworkerの運用)、
[ADR 0006](docs/adr/0006-durable-jobs-jp.md)を参照してください。

## Assertionの訂正

[assertion metadata履歴](#assertion-metadata-history)はvalue/quoteを取得せず
正確なrevision ordinalを発見するもので、訂正CASは変えません。

`POST /v1/assertions/{memory_id}/revisions`は`Idempotency-Key`と、
`expected_revision`、`value`、同一scopeのepisode `evidence`、
`explicit_intent: true`、valid bound、`reason`を含む全置換bodyを要求します。
subject、predicate、scopeは変更できません。成功時は次のrevisionを含む`201`、
head不一致時は`409 revision_conflict`を返します。

これは**valid interval全体の置換**であり、省略したboundは無限端になります。
期間を分割したり、未来日付の開始前に旧値を維持したりしません。
過去の`known_at`では旧revisionを照会できます。
`explain`のrevision省略時は**最新ではなく**`1`です。
どのrevisionでも使われたsourceを削除すれば、assertionの全履歴をpurgeします。
[完全な契約](docs/STATUS-jp.md#assertion-revisionの契約)と
[ADR 0002](docs/adr/0002-assertion-revisions-jp.md)を参照してください。

## EntityとSQL graph oracle

[完全一致entity照会](#exact-entity-query-and-pagination)で読取り可能scopeの別IDを発見し、
各candidateのGET根拠を確認してgraph seedを明示選択してください。label一致はidentity解決ではありません。

`POST /v1/entities`はallowlist内のtype、canonical label、同一scopeのepisode引用
1〜32件、`explicit_intent: true`で不変のrevision 1 identityを作成します。
metadata/根拠は`GET /v1/entities/{memory_id}`で取得し、entityはrecall/explainから除外します。
caller申告のidentityであり、検証済みfactや信頼できる指示ではありません。
alias、merge、名前解決、意味的重複抑止、label訂正endpointはありません。
同じHTTP key/bodyはanchorを再利用しますが、別keyなら同名の別entityを作成し得ます。

`POST /v1/relations`は同一scopeのentity UUID間に同一scopeのepisode根拠を付け、
独立したrelation objectでなく**一つのcanonical assertion**を作成します。
predicateは`depends_on`、`part_of`、`affects`、`works_for`、`decides`で、
すべて複数値を許すreportedな申告であり、fact調停はありません。
`POST /v1/relations/{memory_id}/revisions`はrevision-CASでtarget/根拠を変更し、
valid interval全体を置換します。source/predicateは固定です。
汎用assertion訂正endpointはtyped relationを`409 relation_revision_required`で拒否します。
free-text `remember`はlabel/predicateが一致してもrelationになりません。
recallはFTSのまま`graph_used: false`で、relation item/説明に正確なentity IDを含め、
contextの既存byte予算に計上します。

認証必須の`POST /v1/graph/expand`は読取り専用で`Idempotency-Key`は不要です。
固定parameterized SQL joinを使い、AGE、SQL/PGQ、Cypher、動的label/queryは使いません。
明示scope/seed/predicate filterは現在のアクセス範囲を狭めるだけです。
決定的な幅優先simple pathを1〜2 hop・1〜100 pathに制限し、全prefixを数えます。
結果は`backend: "sql"`、`projection_watermark: null`、時間条件、整合性epoch、
上限内のcoverageを示すだけで、知識の完全性ではありません。
非公開seedは返さず、可視の孤立seedはpathなしでも現れ得ます。
incoming探索は逆向きfactを推論しません。

entity revision 1と正確なrelation assertion revisionをcheckpoint/effectの
`memory_refs`へ宣言できます。source purgeはentity根拠と**全過去relation target**から、
既存のcheckpoint/effect依存へ伝播します。
relationが消えただけで他の生存entityを削除しません。
[契約](docs/STATUS-jp.md#entityとsql-graph-oracle)と
[ADR 0005](docs/adr/0005-relational-graph-jp.md)を参照してください。
将来backendの正しさを比較する基準であり、graph有用性の測定やM1/M3全体の受入ではありません。

## Typed checkpoint

`POST /v1/checkpoints`はscope内のrun/branchにschema 1のtyped stateを保存します。
`expected_head`は必須（最初は`null`）で、非減少のevent watermark、
正確なmemory参照、HMAC checksumを使います。
`GET /v1/checkpoints/{checkpoint_id}`は現在の認可と検査を通した指定IDのenvelopeを返し、現headとは限りません。
[head照会](#checkpoint-head-lookup)は作成やrestoreをせず、
正確なscope/run/branchから同じ検査済みenvelopeを取得します。
checkpointは`recall`や`explain`には出しません。

`POST /v1/checkpoints/restore`はharness/versionの完全一致と新しいbranchを要求し、
元branchを巻き戻しません。run台帳のdispatched effectはfork作成と原子的にunknownになります。
GET/head/restoreはsnapshot後に追加されたものも含め、runの生存effect全件を統合します。
未追跡hintはplannedでも再開を阻止します。旧動作を意図的に厳格化しています。
`automatic_reexecution`は常にfalseです。
保存済みassertion参照は正確な過去revisionを維持し、
restoreで最新revisionを選び直したり、現在の外部事実を更新したりはしません。
callerはコピーした全entityまたはassertion revision依存を`memory_refs`へ宣言し、
stateから機密情報を除去してください。
未宣言のコピー本文は自動発見しません。

source削除はassertion履歴、checkpoint参照、全子孫/fork lineageへ伝播します。
影響するbranch headは再開できません。
[checkpoint契約](docs/STATUS-jp.md#checkpointの契約)と
[ADR 0003](docs/adr/0003-checkpoints-jp.md)を参照してください。
M1全体や災害復旧の完成を意味しません。

## Tool-effect ledger

先にbootstrap checkpointを作成してください。`POST /v1/tool-effects`は
同一scopeの既存runを要求します。caller生成のoperation UUID、tool名、
正規化actionの小文字64桁hex `action_hash`、すべての正確なmemory依存を記録します。
生の引数/hashは永続化せず、GETはtenant-HMAC fingerprintと安定した外部冪等性keyを返します。
runの存続期間中のeffect上限は100件です。

`POST /v1/tool-effects/{memory_id}/transitions`はCAS付きの状態遷移を追記します。
harnessはtool呼出し**前**にdispatchを永続記録し、providerが対応する場合は
安定した外部keyを使ってください。plan/遷移の応答は過去revisionの参照であり、
現在状態のsnapshotや実行許可ではありません。
現在の認可の下で、生存effectはrun封鎖後も旧参照を再送できますが、
新たなdispatchは引き続き拒否します。
confirmed/failedにはcaller申告のreceipt参照が必要ですが、
serverは参照を検証せずproviderにも照会しません。
外部exactly-once保証、承認サービス、自動実行はありません。

effectを直接または宣言済みsource経由でpurgeすると、そのrunの全checkpoint payloadを削除し、
新effect、dispatch、checkpoint、再開を永続的に禁止します。
独立した生存effectの読取り/照合は可能です。新run/operation IDは意味的な重複抑止ではありません。
[台帳の契約](docs/STATUS-jp.md#tool-effect-ledger)、
[運用](docs/operations/README-jp.md#tool-effectの運用)、
[ADR 0004](docs/adr/0004-tool-effects-jp.md)を参照してください。

## ドキュメント

| 日本語 | English |
|---|---|
| [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) |
| [現在の契約と制限](docs/STATUS-jp.md) | [Current contract and limitations](docs/STATUS.md) |
| [初期アーキテクチャ決定](docs/adr/0001-initial-slice-jp.md) | [Initial architecture decisions](docs/adr/0001-initial-slice.md) |
| [Assertion revisionの決定](docs/adr/0002-assertion-revisions-jp.md) | [Assertion revision decisions](docs/adr/0002-assertion-revisions.md) |
| [Checkpointの決定](docs/adr/0003-checkpoints-jp.md) | [Checkpoint decisions](docs/adr/0003-checkpoints.md) |
| [Tool-effect ledgerの決定](docs/adr/0004-tool-effects-jp.md) | [Tool-effect ledger decisions](docs/adr/0004-tool-effects.md) |
| [SQL graph oracleの決定](docs/adr/0005-relational-graph-jp.md) | [SQL graph oracle decisions](docs/adr/0005-relational-graph.md) |
| [Durable jobの決定](docs/adr/0006-durable-jobs-jp.md) | [Durable-job decisions](docs/adr/0006-durable-jobs.md) |
| [日本語lexical FTSの決定](docs/adr/0007-japanese-fts-jp.md) | [Japanese lexical FTS decisions](docs/adr/0007-japanese-fts.md) |
| [Local MCPの決定](docs/adr/0008-local-mcp-jp.md) | [Local MCP decisions](docs/adr/0008-local-mcp.md) |
| [Implicit recall hookの決定](docs/adr/0009-implicit-recall-hook-jp.md) | [Implicit recall hook decisions](docs/adr/0009-implicit-recall-hook.md) |
| [Atomic structured captureの決定](docs/adr/0010-atomic-capture-jp.md) | [Atomic structured capture decisions](docs/adr/0010-atomic-capture.md) |
| [Pgvector retrievalの決定](docs/adr/0011-pgvector-retrieval-jp.md) | [Pgvector retrieval decisions](docs/adr/0011-pgvector-retrieval.md) |
| [Python SDKの決定](docs/adr/0012-python-sdk-jp.md) | [Python SDK decisions](docs/adr/0012-python-sdk.md) |
| [Scope-access管理](docs/adr/0013-scope-access-jp.md) | [Scope-access administration](docs/adr/0013-scope-access.md) |
| [Scope capture policy](docs/adr/0026-scope-capture-policy-jp.md) | [Scope capture policy](docs/adr/0026-scope-capture-policy.md) |
| [Runtime readiness](docs/adr/0014-runtime-readiness-jp.md) | [Runtime readiness](docs/adr/0014-runtime-readiness.md) |
| [Job取消](docs/adr/0015-job-cancellation-jp.md) | [Job cancellation](docs/adr/0015-job-cancellation.md) |
| [Required-context recall](docs/adr/0016-required-context-jp.md) | [Required-context recall](docs/adr/0016-required-context.md) |
| [構造化recallの完全一致filter](docs/adr/0017-recall-filters-jp.md) | [Exact structured recall filters](docs/adr/0017-recall-filters.md) |
| [Checkpoint headの照会](docs/adr/0018-checkpoint-head-jp.md) | [Checkpoint-head lookup](docs/adr/0018-checkpoint-head.md) |
| [所有jobの照会とpagination](docs/adr/0019-job-query-jp.md) | [Owned-job query and pagination](docs/adr/0019-job-query.md) |
| [Assertion metadata履歴](docs/adr/0020-assertion-history-jp.md) | [Assertion metadata history](docs/adr/0020-assertion-history.md) |
| [完全一致entity照会とpagination](docs/adr/0021-entity-query-jp.md) | [Exact entity query and pagination](docs/adr/0021-entity-query.md) |
| [明示batch capture](docs/adr/0022-batch-capture-jp.md) | [Explicit batch capture](docs/adr/0022-batch-capture.md) |
| [Episode照会とpagination](docs/adr/0023-episode-query-jp.md) | [Episode query and pagination](docs/adr/0023-episode-query.md) |
| [選択式推論基盤](docs/adr/0024-selectable-inference-jp.md) | [Selectable inference foundation](docs/adr/0024-selectable-inference.md) |
| [Live provider証拠とCA image修正](docs/adr/0025-live-provider-qualification-jp.md) | [Live provider evidence and CA-image fix](docs/adr/0025-live-provider-qualification.md) |
| [運用](docs/operations/README-jp.md) | [Operations](docs/operations/README.md) |
| [貢献方法](CONTRIBUTING-jp.md) | [Contributing](CONTRIBUTING.md) |

## 依存ライセンス

project codeは[MIT](LICENSE)ですが、**依存関係すべてがMITではありません**。
[Janome 0.5.0はApache-2.0](https://github.com/mocobeta/janome/blob/0.5.0/LICENSE.txt)です。
同梱mecab-ipadic辞書/統計dataには別の
[IPADIC copyright/license notice（NAIST/ICOT）](https://github.com/mocobeta/janome/blob/0.5.0/NOTICE.txt)
が適用され、[Janomeの辞書追加語](https://github.com/mocobeta/janome/blob/0.5.0/ipadic/Noun.proper.csv.patch)
もこの固定releaseに含まれます。package/image再配布時は上流のlicense/notice fileを保持してください。
同梱辞書はcode依存でありuser memoryではなく、memory projectionの保存先はPostgreSQLだけです。
LLM weightsやbenchmark用会話履歴は同梱していません。
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6)はprojectのMITでなく
**PostgreSQL License**です。DB image再配布時は上流licenseを保持してください。
stable release日は**2026-07-29**で、検証済み公式tag commitは
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`です
（[固定changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)）。
採用artifactは固定prebuilt上流imageであり、local source buildではありません。
両native最終imageは`/usr/share/doc/pgvector/LICENSE`を保持し、
固定上流licenseとbyte単位一致を検証済みです。SHA-256は
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`です。
