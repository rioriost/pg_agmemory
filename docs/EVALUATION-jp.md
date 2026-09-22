# 評価契約と証跡の境界

[English](EVALUATION.md)

## 現在の開発版: v0.1.1 / schema 19世代metadata

M3 coordinatorのlifecycle/復元対象44件と、schema 19のmigration/互換性を確認しました。
development v6の実backup drillは非空の世代receipt一つを保持し、
削除/ACL replay後の入力をstaleとし、artifact検証/servingを無効に保ちます。
運用照合は23 tableで、世代履歴は完全一致を要求し、replacement rowとして取り込みません。
metadata契約の確認であり、AGE認定やgraph data再構築ではありません。
固定buildの両native証跡は別途必要です。
[現行制限](STATUS-jp.md)と[運用](operations/README-jp.md#graph-generation-metadata-schema-19)を参照してください。
下記の過去の資源/model観測と公開済みM2認定は元source versionとの対応を維持します。

## 公開済みM2: core MVP v0.1.0 / schema 18完了

固定release実装**`af878fc51fa50cefecca69de2df22edfef2a321b`**の
[native CI 35565944016](https://github.com/rioriost/pg_agmemory/actions/runs/35565944016)は、
amd64/arm64とも**1,945 passed / optional live 8 skips**、全production smoke、
完全一致v5 backup/適用reportが成功しました。各reportは35 canonical/21運用fingerprint、
元receipt 5件、予約11件を保持し、tombstone対象20件を拒否します。
service 0.1.0/`m2-core-mvp`自身のrunで、pytestはamd64 1397.44秒、arm64 1263.10秒です。
派生物13 tableすべてに保持/purge双方のcaseがあり、unknown retry拒否と消費quotaを保持します。
`v0.1.0`公開checkpointは認定文書だけを追加し、全build入力は検査済みcommitと同一です。
先行`6d967c4`は別の証跡として保持し、最終release runの代用にはしません。
[現行の配置制限](operations/README-jp.md#m2-core-mvp-deployment)を参照してください。

**2026-09-19の範囲改訂:** 受入れは[実装計画1.3節・17–18章](PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)
に従う。pg_agmemoryは判断システムでなく記憶基盤である。
人手の意味的スコアや実agent task 20件の成功をrelease gateにせず、
model比較や新しい人手label収集も計画しない。一つの固定した参考記憶benchmarkは
利用例として残すが、model品質の合格点は設けない。末尾に受入結果と後続milestoneを示す。

後述の過去の証跡節の実験結果・スコア閾値・harness recipeは**2026-09-18までの診断**であり、
改訂後のrelease方針ではない。旧gateのための全baseline再実行や評価票記入は不要。
既存の`NOT_MEASURED`、`human_review_verified=false`、`human_quality_qualified=false`、
`m2_qualified=false`は事実のまま保持し、version付きの本体受入一覧の代わりにしない。
意味的な測定結果を捏造しない。

### 単一の参考記憶benchmark

再現用sampleは、既存の**実3 call記憶lifecycle**です。構成は一つに固定し、新しいmodel比較や
意味採点は行いません。[結果JSON](../examples/reference-memory-result.json)は、実測した
**`e4f5d76` / service 0.0.27 / schema 13**、変更していない
[test recipe](../tests/test_processing_live.py)、worker profile digest、
保存JUnitのchecksumに結び付けています。[provider profile](../examples/reference-memory-profile.json)に
含むのはmodel pinであり、credentialではありません。既存証跡の集約で、
**schema 18上の新しい実行とは扱いません**。

| この1 caseで観測した項目 | 記録結果 |
|---|---|
| 実local model処理 | 抽出・768次元embedding・圧縮を各1回。commit済み予約3件 |
| 受理した抽出 | inferred preference 1件、重複/隔離0件。真実の検証ではない |
| 圧縮後の保持 | typed checkpoint JSONの完全一致、原文/revision参照、coverage 1–1、未圧縮tail 1件 |
| Restore/hook | typed stateと受理した未信頼summaryを保持。必須tail 1件。保留承認/unknown effectを実行許可へ昇格しない |
| 再送/削除 | 意味的job IDを保持して追加callなし。削除snapshot/candidateは404、予約3件を保持 |
| 保存/転送量の観測 | working snapshot JSONは2,405 bytes。hook予算2,404 bytesなら明示拒否 |
| 時間 | fixtureとlocal推論を含むpytest case全体1標本で**9.621秒**。request単位のlatencyやp95ではない |
| 未測定 | 意味的主張の保持率、人手/task成功、DB増分、process RSS、実課金 |

成功case以前の失敗した抽出/診断call 4件は別に保持し、下記の履歴と結果JSONのpipeline JUnit
checksumから区別できます。無料retryや、なかったことにした試行ではありません。
別のheld-out検索600問は同じmodel構成でhybrid Recall@20 **0.9981818182**でしたが、
この実3 call caseに含めません。S資源測定は制御した応答を使い、これらmodelの資源測定ではありません。

**再現手順:** 当時の実装を再現する場合は記録SHAの隔離checkoutを使い、現行codeでの実行は
新しい証跡と明示します。固定modelと認証付きloopback relayをrunnerのnetwork namespace内へ
準備してください。この手順はmodel download、remote/有料backend、全interface待受、
自動retryを許可するものではありません。repositoryのLinux test環境へ、
**新規の使い捨てPostgreSQL cluster**（fixtureはmemory schemaを削除し、cluster-wide roleを作る）、
owner専用profile copy、指定環境変数経由のrelay credentialを渡します。

```bash
# PGAG_TEST_DATABASE_URL: 新規の使い捨てclusterだけを指す管理URL。
# PGAG_M2_RELAY_TOKEN: 非公開で渡し、JSONやcommitには含めない。
export PGAG_LIVE_PROVIDER_CONFIG=/absolute/private/reference-memory-profile.json
PGAG_M2_LIVE_PROCESSING=1 pytest -q -o junit_family=legacy \
  --junitxml=/absolute/private/new-run/processing.xml tests/test_processing_live.py
```

試行ごとに新しい非公開出力directoryを使い、非0終了・不正出力・途中の会計も保持します。
testは合成model応答へ差し替えず、失敗callをretryしません。生成summary、時間、JSON byte数は
変動するため、古い数値へ合わせるのではなく記録した契約を維持します。
実測SHA、実際の設定、install済みmodel digest、全JUnit、失敗logを一緒に保存してください。
credentialや使い捨てDB/model processのcleanupはoperatorの責務です。
意味的な合格点は設けず、本番認定とも扱いません。

### M2-A 本体契約一覧（2026-09-19）

| 境界 / 実際のsurface | 決定的な責務 | 回帰証跡 |
|---|---|---|
| Observe、構造化remember、capture/batch、明示revision | 原子的な永続受付、冪等性、source/revision対応、caller意図。暗黙のmodel jobなし | `test_integration`、`test_capture`、`test_capture_policy`、`test_revisions` |
| Recall/episode/entity/history/explain、SQL graph | 現在のtenant/scope/time filter、vector空間とRRF規則、上限付き完全item context、provenance。回答保証ではない | `test_recall_filters`、`test_episodes`、`test_graphs`、`test_vectors`、`test_lexical`、`test_required_context` |
| Processing/adoption/worker/jobs | profile/policyの既定拒否、永続予約、不正出力拒否、lease/epoch検査、人手確認を主張しない明示採用 | `test_processing`、`test_jobs`、`test_extraction`、`test_providers`、`test_processing_chaos` |
| Checkpoint/effect/working圧縮/復元 | typed state保持、checksum/head CAS、coverage/tail、現行権限。承認の推定や副作用のblind再実行なし | `test_checkpoints`、`test_effects`、`test_processing`の圧縮case、`test_recall_hook` |
| Native SDK、MCP、hook | 共通の認可と宣言version。MCPは4 toolで、全Native操作を公開するものではない | `test_sdk`、`test_mcp`、`test_recall_hook`、`test_contract`、`test_readiness`、packaged smoke |
| Scope/capture/synthesis管理 | 特権接続、CAS、caller barrier、送信の既定拒否、runtimeからpolicy改変不可 | `test_scope_access`、`test_capture_policy`、`test_processing` |
| Forget/receipt/deletion-history export | Nativeはpreview/purgeのみ。依存closure、barrier、receipt別の全対象記録、manifest確定、対応不明履歴の明示拒否 | `test_integration`、各domainのpurge case、新規`test_deletion_history`、`test_recovery_drill` |
| Processing-recovery export/check | 固定table全体、epoch、keyed系統の照合。不一致を明示し、payload出力・DB変更・再開許可なし | `test_processing_recovery`、v3実backup drill、packaged管理CLI smoke |
| Recovery-apply export/apply | 管理者専用認証bundle、対象CAS/本文一致、会計/履歴の単調性、原子的適用と事後照合。自動再開なし | `test_recovery_apply`、migration 15 rollback/runtime role検査、v4実backup/適用CLI drill |

これは対象契約の一覧であり、万能な保証ではない。
schema 13の直前の完全一致SHA baselineは`1ea3f6c`で、native各1,754 passed / optional 8 skips。
schema 14開発では関連198 case、その後に早期constraint評価後の過剰追加も含む最終対象83 caseが
成功した。先行worktree distributionは1,783 passed / optional 8 skipsと全packaged smokeが
成功したが、ordinal guardとpackaged export smokeの追加前なので最終source認定ではない。
重複件数を加算しない。公開後の完全一致commitのnative認定は別途記録する。

その後、完全一致実装**`99e71bd74445c2eab6fb82fe62c25b1678cdc69b`**の
local distributionは**1,784 passed / optional live 8 skips**、550.73秒で成功した。
[Native CI 35445005807](https://github.com/rioriost/pg_agmemory/actions/runs/35445005807)
も両architectureで同じ1,784/8（amd64 1022.91秒、arm64 967.58秒）、
`deletion-history export`を含む全packaged smoke、実単一purge backup復旧が成功した。
drillは`m2_qualified=false`のまま、manifest target 4件と35 canonical tableの
fingerprint一致を確認し、modelを呼んでいない。drillの46契約caseはfull suiteの内数。
最初の対象開発実行は65 passed / 2 failedで、Nativeのsuppress対応とpreviewのHTTP
statusに関するtest側の誤った想定を修正した。suppress有効化やpreview動作変更はしていない。

旧evaluation/QA/human report schemaは診断契約のまま保持する。
固定の`m2_qualified=false`や品質`NOT_MEASURED`で本体engineeringを足止めしたり、
測定したようにtrueへ変えたりしない。release判定は改訂M2-B/C/Dの証跡による。
新しいmodel品質scorerや人手評価作業は不要である。

### 限定した複数receipt復旧（2026-09-20）

完全一致code `84871e085131aa673543ac9455386874d9eadf77`でlocal offline復旧契約64件と、
元cluster削除後の実旧backup復元が成功しました。
[Native CI 35479478777](https://github.com/rioriost/pg_agmemory/actions/runs/35479478777)
もamd64（1012.79秒）、arm64（975.62秒）の両方で**1,802 passed / optional live 8 skips**、
全production smokeと同じ複数receipt drillが成功しています。
64件はfull suiteの内数で、合算しません。

local/amd64/arm64の各復旧reportは`exact_commit_inputs=true`で、
backup前receipt 1件を保持、追加purge 2件と順序付きACL変更2件を再適用し、
tombstone 6件、35 latest/restored canonical fingerprint一致、失効readerの拒否、
可視なcontrolの保持を確認しました。差分receiptは元IDとの対応を記録し、
audit/idempotencyまで完全同一とは主張しません。API/worker/model callは起動しません。
metadata v2とprefix/target完全性検証で未対応履歴を拒否し、
[運用手順](operations/README-jp.md)の上限を適用します。
policy/model会計、任意履歴、全派生物、HA/PITR、M2全体は引き続き未認定です。

### 読取り専用の処理状態照合（2026-09-20）

完全一致実装 **`d3b1b222784c544410bb9b4eda956e7b602bda62`** はservice 0.0.29 /
schema 14です。local対象は新規processing-state 44件とrecovery-drill 65件を含む
**109件**が成功し、別途実backup復元も成功しました。実PostgreSQLでunknown call予約、
syntheticな既知失敗/成功結果、identity保持、policy予算変更を扱い、
live modelの品質やprovider実課金を測定したものではありません。

[Native CI 35483209713](https://github.com/rioriost/pg_agmemory/actions/runs/35483209713)
はamd64（1017.45秒）、arm64（1022.56秒）で**1,847 passed / optional live 8 skips**、
新しい管理CLIを含む全packaged smokeと実v3 backup drillが成功しました。
対象caseはfull件数の内数です。local/両nativeのreportは完全一致commitを記録し、
復元旧processing baselineの一致と、限定replay後の最新状態との5差分
（scope-access event、idempotency、tombstone、削除receipt、削除target）を検出しました。
これは**不一致検出の成功**であり、最新状態適用の成功ではありません。
35 canonical fingerprintは一致しても、再生成された運用ID/時刻は一致しません。
`restore_authorized=false`、`m2_qualified=false`を維持します。
初期開発ではtest fixtureのconstructor引数誤りによる失敗1件を修正しました。
provider動作は変更せず、上記の最終完全一致sourceでは成功しています。

### 運用状態の原子的適用（2026-09-20）

完全一致code **`21187702c43aff55c83341aa45f0de4d285fd64e`**、service 0.0.30 /
schema 15でlocal対象81件と実v4復旧/適用drillが成功しました。
最終local packaged runは**1,861 passed / optional 8 skips**、551.02秒でした。
[Native CI 35498710001](https://github.com/rioriost/pg_agmemory/actions/runs/35498710001)
もamd64（975.19秒）、arm64（1000.80秒）で同じ件数、全production smoke、
実packaged apply CLIが成功しています。対象81件と別実行したdrill契約65件はfull件数の内数です。
先行worktree distributionは最終の管理鍵分離前に中止し、認定証跡とはしていません。

最終local/両native reportは`exact_commit_inputs=true`で、35 canonical fingerprint、
**21運用fingerprint**、tombstone 6件、元receipt ID 3件、synthetic予約3件
（unknown/失敗/成功）の一致/保持を確認しました。unknown retryは拒否し、
重複した意味的jobは元IDを保持し、新callは消費済みquotaで拒否します。
probeはrollbackして認証済み参照状態を変えません。復元旧baselineも完全一致します。
管理者専用復旧鍵はlogical backupで保持し、runtimeは鍵を読めず、履歴書込みも有効化できません。

以前のv3の不一致からの変化は比較条件の緩和ではなく、実際の状態適用です。
同じ厳密な照合を、元運用rowを保持した適用transactionの後に検証しています。
synthetic provider呼出しは3回、**外部model requestは0回**で、model品質や実課金の結果ではありません。
本文不一致、job集合変更、旧履歴欠落、過大bundleは未対応として明示拒否します。
一般audit履歴/sequence、本番HA/PITRは認定せず、`m2_qualified=false`と手動の配置判断を維持します。

### 派生記憶のbackup復元（2026-09-21）

完全一致**`593087f51b87ecec8edb942c637bb7dd8af0e497`**、service 0.0.35/schema 18の
Linux v5実backup drillが`exact_commit_inputs=true`で成功しました。
元clusterを削除してから旧dumpを復元し、混在baseline receipt 2件を保持、
その後のpurge 3件をreplayします。packaged認証付き適用で元receipt 5件、
35 canonical/21運用fingerprintが一致し、tombstone対象20件は読めません。
metadata-only anchor 43件は残り、byte消去とは主張しません。
unknown/失敗/成功を含むsynthetic予約11件も、quota返還なしで保持します。

抽出derivation/candidate、原文/assertion embedding、working snapshot/event、
entity/evidence/relation/revision、tool-effect/revision/referenceの13 tableに
保持/purge双方の非空caseがあります。生存provenance/vector/graphは読め、
typed restoreは保留承認とunknown effectを保持します。
epochが古いsnapshotのread/resumeは明示errorとし、suppress本文は物理的に残っても読めません。
検証による変更はrollbackし、最終fingerprintは不変です。外部model requestや自動起動はありません。

関連Linux suiteはdrill契約68件を含む149件が成功し、lint/typeも通過しました。件数は重複します。
初回開発drillでは同一principalの複数scope membershipの比較順が曖昧な点を検出し、
完全なkey順へ修正しました。一致条件は緩めていません。失敗/成功開発logと完全一致reportを保持します。
両nativeは`6d967c4`の
[run 35563611773](https://github.com/rioriost/pg_agmemory/actions/runs/35563611773)で、
各1,945/8と同じ宣言report条件の照合を完了しました。
新しいsuppress replay、対象重複、prefix改変、過大suffix、欠落/新canonical本文、
任意履歴、HA/PITRは認定しません。

### Native削除/上限とguest-cold probe（2026-09-21）

完全一致実装 **`51293b4bc743c97130e45d70d2f9350a079178d0`**
（service 0.0.34/schema 18）で、保存したS全量schema 16 snapshotを隔離clusterへrestoreし、
通常migrationを適用しました。probe前とcold再起動前の`ANALYZE`を記録します。
同じ共有M4 Max host、DB 6 vCPU/24 GiB、API+2 worker 2 vCPU/8 GiB、別clientと
制御local providerを使用し、実model呼出しや課金は測定しません。
probe plan digest:
`f50ec1733095062ffa3b4726ccdfb5497fa705c028fce0855caf98315270e4b2`。

| Probe | 完全一致実測 |
|---|---|
| 小規模Native purge | 100件、並行上限5。transaction p95 **125.22 ms**、E2E p95 **127.80 ms**。変更しない1,000 ms未満 |
| 混合background window | 30秒、recall 600件+observe 150件、2 worker。今回は150 job成功、stale-context拒否0 |
| Read barrier / replay | purge後の拒否100件、同じreceiptを返す冪等replay 100件 |
| 大規模closure | source 1件+派生assertion 9,999件。preview **1.08秒**、purge **4.43秒**で900秒未満。tombstone/manifest各10k、元source/assertion本文は残存なし |
| Input / body上限 | 過大processing input 20件を永続変更なしで拒否。過大bodyはHTTP 413 |
| Queue / call上限 | 並行20要求中2件受付・18件拒否、pause中callなし。call quota 1で10 jobから予約は正確に1件 |
| Output / context上限 | 128-token policyに対する256-token profileを予約/送信前に拒否。512-byte recall pack 20件を上限内に収め、過大implicit budgetを拒否 |
| 結果不明会計 | 不正な制御応答のcallをunknownのまま保持し、明示retryはHTTP 409 |
| DB待機上限 | lock待機4要求が**5.03秒**で明示HTTP 503。lock解除後はrecall成功 |
| Guest-cold検索 | 異なるguest/postmaster起動12回、3 mode×4選択率。初回transaction **122.10–242.90 ms**、後続60 sampleは**147.01 ms以下** |

cold測定は大規模purge後です。Linux guest/PostgreSQLの新規起動を示しますが、
物理host/device cacheの排除は保証しません。各層の初回1件は**cold p95ではなく**、
30秒の削除windowも30分steady gateの代用ではありません。
同じexact 51293b4で変更しない**1,800秒S steady**も完了しました。recall 36,000件、
observe 9,000件で不正応答/timing欠落/dropは0、warmup込み9,300 jobが成功しました。
observe transaction/E2E p95は**37.08/42.63 ms**、recallは**106.24/110.47 ms**です。
12層すべて3,000 sample、最遅transaction p95はvector・100%の**124.03 ms**で、
全steady gateを満たしました。

unique-key projection計画は、tombstoneのない旧steady baseline（9c7db01のrecall p95
76.98 ms）より全条件で速いわけではありません。500 ms gateを維持しつつ、
削除に左右される遅延を抑える変更です。
DBは926,176,959 → 993,900,223 bytes、indexは184,778,752 bytes、
WAL増加552,113,776 bytes、論理backup 467,449,947 bytesでした。
worker queue/processing p95は399.84/84.56 ms。guestのsampled nonavailable memory peakは
DB 1,785,622,528 bytes、application 408,043,520 bytesで、process RSSではありません。
全sample期間のCPU busy secondsは2604.91/1514.26です。

[Native run 35558748669](https://github.com/rioriost/pg_agmemory/actions/runs/35558748669)
は両architectureで**1,940 passed / optional 8 skips**でした。arm64は全distribution/
production/復旧smokeも完了しました。amd64は後続smoke中に旧CI上限25分へ達したため、
run全体は**cancelledでありpassではありません**。増えたsuiteとpackaging/復旧を収めるため、
workflow上限を40分に変更します。製品遅延、DB timeout、S時間の閾値は変更しません。
後続の証跡/workflow commit **`7a2fd88587755aab030496082054121bb4897cde`** は51293b4と
runtime/migration/package/test/script/example入力が同一です。
[Native run 35560939791](https://github.com/rioriost/pg_agmemory/actions/runs/35560939791)
は両architectureで全production/schema 18運用状態復旧smokeまで成功し、
各**1,940 passed / optional 8 skips**でした（pytest: amd64 1344.87秒、arm64 1246.19秒）。
先行runの時間切れを成功に書き換えず、そのまま記録します。
reportの`resource_qualified=false`/`m2_qualified=false`を維持し、
より広い復旧/deployment/参照benchmark作業の完了も主張しません。

失敗と途中結果も保持します。`6beb38c`はtombstone追加後のprojection joinが約1億組を比較し、
DB接続が飽和して混合削除probeに失敗しました。JIT無効化だけでは解消しませんでした。
schema 17で同等のtenant内tombstone集合に変更し、unique-key embedding lookupで
低選択率のprojection交差走査も避けました。最初のlookup run（`00f7854`）は
`stale_context`拒否2件で停止しました。並行purgeが保存済みdeletion epochを変える条件で
全worker成功を要求したharnessの誤りでした。改訂planはepoch失効・未公開・既知の会計を
確認した拒否を成功とは別に数えます。`cacb47b`は149件成功+その拒否1件を記録しましたが、
大規模purge後のwarm sampleには500 ms超過が残りました。schema 18は
read/admin/期限規則を変えず、残るscalar tombstone membership確認を集合処理にします。
旧失敗/中間artifactを最終runと混同しません。

### 固定S資源測定とrecall修正（2026-09-20）

計測checkpoint `1d898c999379422f84d89043b0ae83ace734976e` は
[native CI 35508695585](https://github.com/rioriost/pg_agmemory/actions/runs/35508695585)
の両architectureで1,871 passed / optional 8 skipsでした。
しかし固定全量データの30秒preflightは150/500 msを満たさず、
observe p95 1146.85 ms、recall 1453.98 msでした。225 controlled jobと応答契約は正常です。
この失敗は、先行の縮小開発runや修正後のrunと分離して保存します。

runtime roleのplanから、順位/coverageの候補二重走査と行ごとの可視性SQLを特定しました。
`9c7db01289a07ea6ce7f7ded37c485d4110e95d1`で候補materializationを共有し、
schema 16の同等な集合read policyを適用しました。対象240件には、
20 actor ×100 protected source ×5操作の実Native拒否10,000件、positive control、
元のscalar認可式との比較、prepared contextを含みます。
初期のfixture設定誤り2件（migration transaction境界、tenant index）は修正しましたが、
認可条件やprovider契約は緩めていません。
[Native CI 35513420525](https://github.com/rioriost/pg_agmemory/actions/runs/35513420525)
はamd64（1033.34秒）、arm64（1265.09秒）で**1,916 passed / optional 8 skips**、
全production/運用状態復旧smokeが成功しています。
10,000 requestは一つのtest内の検査で、pytest件数へ10,000を加算しません。

修正後preflightは全steady gateを満たし（observe p95 37.92 ms、recall 79.03 ms）、
続いて同じ完全一致commitのimmutable archiveで**S 1,800秒本測定**を行いました。
profile digestは変更せず
`0252ea68cc89276b0e6bf4f51ec402f062b1006c4296a2c829a3c85e333a6b48`です。
hostは共有・非専有のApple M4 Max/128 GiB、guestはDB 6 vCPU/24 GiB、
API+worker 2本が2 vCPU/8 GiB、clientは別です。
10 tenant、512-byte episode/chunk 100k、assertion 10k、768次元dense projection 110kを使用しました。
providerは10 ms待機して妥当な空抽出を返す制御fixtureで、model品質や実課金の測定ではありません。

| S steady指標 | 結果 |
|---|---|
| 時間 / request | 1,800秒、recall 36,000 + observe 9,000 |
| observe transaction / E2E p95 | 40.83 / 47.38 ms |
| recall transaction / E2E p95 | 76.98 / 82.10 ms |
| 最遅mode/選択率のtransaction p95 | 100% hybridの102.40 ms。全層別が500 ms以内 |
| 不正応答 / commit timing欠落 / schedule drop | 0 / 0 / 0。steady 45,000 request IDを独立照合 |
| warmup+steady worker | 9,300成功、drain後pendingなし |
| worker queue / 処理p95 | 379.14 / 84.98 ms。warmup+steady+drain期間 |
| DB前 / 後 | 926,553,791 / 994,694,847 bytes |
| index / WAL増分 / logical backup | 185,622,528 / 520,918,936 / 467,449,236 bytes |
| guest非available memory標本peak: DB / API側 | 1,844,879,360 / 420,028,416 bytes。process RSSではない |
| guest CPU会計: DB / API側 | 各採取期間全体で2417.07 / 1493.31 busy秒。host overheadではない |

3 mode × tenant内4選択率の12層別は、それぞれsteady 3,000 sampleです。
request/server照合、入力hash、container割当を非公開artifactへ保存し、
credentialと所有containerは片付けました。hardware counter、物理cold cache、専有host容量は主張しません。
**測定・達成したのはsteady負荷部分だけ**です。small-forget barrier、10k-object purge、
並行limit/failure probe、参照記憶benchmark、広い復旧/配置範囲は残っています。
reportは`resource_qualified=false`、`m2_qualified=false`を維持します。

### 過去のschema 13証跡

証跡日: **2026-09-18**。実験ごとに実装SHAを固定する。ソフトウェア検査の成功は
M2受入れの完了ではない。

本書は [ADR 0028](adr/0028-background-processing-jp.md) と対応する。実装、
決定的 fixture、採点コマンドがあることは M2 評価の完了ではない。
MVP 完了、production readiness、人手 assertion precision、compaction fidelity、
性能認定を主張しない。認定済み v0.0.26/schema 11 の履歴とは分離する。

| 証跡 | 現時点で意味すること |
| --- | --- |
| Push 済み基盤 commit `e60d6e3`: 独立した Apple Container 検証、Ruff、26 ソースファイルの mypy、対象 389 passed / live 1 skip | 依存機能の証跡のみ。統合 background-processing/schema 13 ツリーではない |
| 基盤 `e60d6e3` の native arm64 CI | 既存 memory gate で失敗。基盤 CI 全体が green という認定ではない |
| Push 済み基盤 checkpoint `7343272` | 全体 720 / held-out 600 question の fixture 生成、公開データ整列、dev split、`VmHWM` 修正を含む。統合 backend の認定ではない |
| [Native CI 35313803636](https://github.com/rioriost/pg_agmemory/actions/runs/35313803636) | 基盤 `7343272` で**両 native architecture 成功**。後の統合 M2 ツリーではない |
| 過去の migration 統合前 real-PostgreSQL 対象実行: 258 passed / 2 failed | 認定ではない。Block する heartbeat-test 同期と古い schema assertion が失敗 |
| 対象作業内の評価 subset: unit 238 passed、Native SDK integration 3 passed | Offline/fake provider による契約証跡。実 model 指標ではなく、重複する件数を加算しない |
| Push 済み統合実装 `6ac31c31b6a8e248a21de551a41469510d9354b1` | Schema 13 実装 checkpoint。M2 全体の認定ではない |
| Push 済み後続 `101993a6d40679c73899ee2454f6b2ad0dadafff` | Live harness の正確な `ollama-sha256:` revision prefix を修正 |
| 統合全体の試行: 1437 passed / live 7 skips / 1 failed | 古い SDK route inventory の期待値で失敗。全体認定成功ではない |
| 修正対象検証: 27 passed / live 3 skips、Ruff、32 ソースファイルの mypy、strict SDK 検査成功 | 期待 route 数 38、新 SDK 七 method と provider extraction の型付き結果を修正。修正後の全体再実行ではない |
| Fresh `101993a6d40679c73899ee2454f6b2ad0dadafff` の生成 HTTP ACL 実験 | **正確に 10,000 case 成功**。対象は下記 matrix に限定 |
| 実装 `2288fdc4757518e1f3bbd79f115ae46c85532266` の [core native CI 35317028037](https://github.com/rioriost/pg_agmemory/actions/runs/35317028037) | 両native architecture成功。後続prompt/crash test/QA予算/production M2 smoke変更を含まない |
| `e4f5d76`の[CI 35321226191](https://github.com/rioriost/pg_agmemory/actions/runs/35321226191)、`0552151`の[CI 35321615670](https://github.com/rioriost/pg_agmemory/actions/runs/35321615670) | 両architecture成功。それぞれ1,485 / 1,487 passed、各8 optional skips。packaged M2 smokeの追加前 |
| Fresh local Apple Container distribution検査 | **1,485 passed / opt-in live 8 skips**、Ruff、mypy 32+1、adapter-extra install検査、全production smoke成功。unit imageは`e4f5d76`、runtime imageは`0552151`後に再build |
| `9458034a47b6f7c9901e569a32f198f56369fbf7`として公開したproduction M2 smoke | non-root runtimeでsynthetic HTTP 3 call、inferred 1 / quarantined 1、768次元embedding、typed state、2,448 byte hook context、10 object purge、管理policy復元が成功 |
| `0552151` QA予算回帰対象 | **127 passed**。distribution件数に合算しない独立した対象証跡 |
| 完全一致`9458034a47b6f7c9901e569a32f198f56369fbf7`の[native CI 35322238611](https://github.com/rioriost/pg_agmemory/actions/runs/35322238611) | **両architectureで1,487 passed / optional 8 skips**、Ruff、mypy 32+1、install profile、M2を含む全production smoke成功。amd64 test 924.26秒、arm64 914.22秒 |
| ローカル Ollama 0.34.1、固定 qwen2.5:7b / qwen3 embedding profile | 実検索と3 callのprocessing lifecycleを測定。過去の失敗も保持 |
| Synthetic corpus 生成と scorer 契約 | 再現可能な構造診断。人手・実 task の受入れではない |
| Dev: 120 question / 10 group / 実 embedding 440 call | 開発専用の実測 retrieval。Held-out の受入れではない |
| 固定held-out: `101993a`、600問/50 group/embedding 2,200 call | hybrid Recall@20 **99.818%**、vector-onlyと同値。このsynthetic集合ではtemporal順位非劣化も合格 |
| `e4f5d76ad2a4919349054165ce531b92fa650818`の実model lifecycle | **合格**。extract/embed/compactの正確に3 callとsnapshot復元/hook/purgeを検査 |
| 予約後/応答後/公開commit後の実SIGKILLと復旧前purge | `e4f5d76`の対象154検査中の4 crash/recovery caseが合格。この範囲で二重公開・source復活なし |
| `055215168c83501f676e643853d9d7b58e9f0c5d`のPublic oracle | 修正予算で412 call完了。不正回答72 / 試行144、別途予算skip 24。**QA合格ではない** |
| `9c958162f9f61bfb8f75b8747d3133c5afcb80a3`のQA送信schema修正 | 新規412 callで契約違反0、abstention 136、機械的完全一致22 / 回答試行144。**意味的品質の認定ではない** |
| 完全一致`9c20909bc67dacf8e0fd77a52d1caa46c2340e45`の[native CI 35326089452](https://github.com/rioriost/pg_agmemory/actions/runs/35326089452) | 両architectureで**1,531 passed / optional 8 skips**、全production smokeと実bounded backup復旧成功。amd64 test 1015.18秒、arm64 939.33秒 |
| 人手評価tooling `1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6` | Linux対象検査は**340 passed / DB依存6 skips**、Ruffと35 source fileのmypy成功。新規offline review/collector/pilot 223 caseを含み、重複件数は合算しない |
| 同じ`1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6`の実Wikipedia pilot | revision固定の日英6抜粋、local text呼出し24回。要約5件、QA 11件（見送り6件含む）、保持した生成失敗8件。契約通過の抽出claim・人手評定は0件。[引き継ぎ・digest・失敗履歴](HUMAN_REVIEW-jp.md)を参照 |
| 完全一致`1ea3f6c55b1c72fe6262731ca5d4bf49c76ccfe6`の[native CI 35331248426](https://github.com/rioriost/pg_agmemory/actions/runs/35331248426) | 両architectureで**1,754 passed / optional 8 skips**、全packaged production smokeと限定backup復旧成功。amd64 test 1021.93秒、arm64 925.23秒。上記subset/新規caseは内数 |
| 人手、実task、一般災害復旧の受入れ | 人手/実taskは**未測定**。限定的process復旧や単一purge backup実験は一般DRの認定ではない |

既存 memory gate の測定上の問題は `ru_maxrss` に由来する。報告された worktree 修正は
`/proc/self/status` の `VmHWM` を使い、**256 MiB** の上限を維持する。報告された
検査には **139360 KiB** の cold peak と **320 MiB** の親 process regression case
がある。修正を含む `7343272` は両 native architecture の CI に成功した。
この基盤の結果を後の統合実装に流用してはいけない。Candidate adoption は未公開 migration 014 から
migration 013 に統合した。現在の対象は schema 14 でなく **schema 13** である。

省略した model digest は再現性の pin にならない。今後の run には完全な model
revision、承認済み profile digest、**実際に検証した実装 SHA** を記録する。
後の文書専用公開 commit を実験実行時の SHA と記載してはいけない。

### 実測に使用したlocal model profile

Ollama **0.34.1**と、独立して確認した配置済みmodel artifactを使用した。
以下の非secret profileは記録済みlocal実験で共通であり、別掲のworker/評価digestは
recipe/prompt変更によって異なる。loopback endpointはoperator所有の認証付きrelayで、
公開serviceではない。credential値は含めない。実験後、relay/model processは停止し、
一時relay/DB credentialも削除した。

```json
{
  "backend": "local_http",
  "endpoint": "http://127.0.0.1:11435/v1",
  "text_model": {
    "name": "qwen2.5:7b",
    "revision": "ollama-sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
  },
  "embedding_model": {
    "name": "qwen3-embedding:0.6b",
    "revision": "ollama-sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1"
  },
  "timeout_seconds": 120,
  "max_output_tokens": 512,
  "api_key_env": "PGAG_M2_RELAY_TOKEN"
}
```

revision labelはoperatorのpinであり、weightの自動証明ではない。
再現には許可されたlocal環境を用意し、container接続のためにmodel serverを
全interfaceへ公開したりendpoint検証を弱めたりしない。

## 記録された実測と制限

### 生成 ACL 実験: 測定済み PASS、範囲は限定

Fresh 実装 `101993a6d40679c73899ee2454f6b2ad0dadafff` で正確に
**20 actor × 実 source 100 個 × 5 operation = HTTP 10,000 case** を実行した。

- Cross-tenant が 5,000 case。
- 同一 tenant 内の cross-scope が 5,000 case。
- 記録された結果は `not_found` 10,000 件、予期しない status はゼロ、
  書込み後の変更もなし。

親が保管する集計は session files 内の `m2-acl-101993a/summary.json` であり、
corpus の bundle や repository artifact ではない。Synthetic group label や retrieval
の外部 ID ゼロから推定した結果でなく、実際に実行した生成 ACL である。
この限定した生成 ACL 実験の成功に限り、網羅的な認可証明、人手品質、M2 認定ではない。

### Local embedding dev run: 実測だが held-out ではない

報告された run は **dev 120 question / 10 group**、**実 local embedding 440 call**。
Unauthorized ID はゼロで、full-context skip もなかった。

| Run の束縛 | 記録値 |
| --- | --- |
| 正規化 synthetic dataset digest | `7f34f11c375fc4121ea1ed526345e34ccd416a3c83a8a973d2998b52fd21ee33` |
| 独立した評価 profile digest | `de5685812c514e7dcd424ccd6850e90f0c3753d85011212daa11e220aec60b90` |
| Split | `dev` |

| Baseline | Recall@20 | nDCG@10 | MRR | Truncated observation |
| --- | --- | --- | --- | --- |
| `no_memory` | 0 | — | — | 0 |
| `recent_window` | 0.2727272727272727 | — | — | 120 |
| `full_context` | 0.6818181818181818 | — | — | 0 |
| `vector` | 1 | 0.900985737166785 | 0.8943722943722944 | 120 |
| `hybrid` | 1 | 0.900985737166785 | 0.8943722943722944 | 120 |
| `temporal_provenance` | 1 | 0.9211168415174328 | 0.9216450216450216 | 120 |

「—」はこの報告要約にない値であり、ゼロや架空の測定ではない。Truncation は
20-item 上限を含む window/Native context の制約を示し、Recall@20 が完全でも隠さない。
Full-context Recall@20 は context 全体が収まっても提出順序の最初の 20 ID を採点する
ため、その値は full-context の予算 skip を意味しない。
これらは synthetic **dev** の測定であり、内部 held-out gate、人手 precision、
回答品質、real-task replay ではない。

Dev 後、親は profile、dataset、settings と
`101993a6d40679c73899ee2454f6b2ad0dadafff` の Git archive を固定してから、
600-question testを開始した。held-out結果を見て設定を調整していない。
public datasetは独自digestを持ち、synthetic digestを流用しない。

### Held-out synthetic検索: 測定済みPASS

固定実装で**600問/50 group**、**local embedding 2,200 call**、
6 armそれぞれ600 queryを測定した。unauthorized IDは0、full-context予算skipも0。
各groupは32 episodeであり、template生成sourceであって50件の独立した人間のtask履歴ではない。

| Baseline | Recall@20 | nDCG@10 | MRR |
| --- | --- | --- | --- |
| `no_memory` | 0 | 0 | 0 |
| `recent_window` | 0.2727272727 | 0.1403306615 | 0.0839393939 |
| `full_context` | 0.6636363636 | 0.1668115936 | 0.1722305288 |
| `vector` | 0.9981818182 | 0.8892563047 | 0.8795083980 |
| `hybrid` | 0.9981818182 | 0.8892563047 | 0.8795083980 |
| `temporal_provenance` | 0.9981818182 | 0.9130237727 | 0.9116296101 |

Native 3 armのRecall@20の95% session-group bootstrap区間は
**[0.9945454545, 1]**。recent-windowと各Native armは600 observationすべてを
truncatedと記録する。Nativeの20 item上限は網羅的検索を意味しない。
hybrid/vectorの同値をそのまま報告し、hybrid改善とは主張しない。
scorerの標本数・検索・順位gateだけの合格であり、内部held-out集合では回答生成、
意味的支持、実task継続を測定していない。

### Live processing: 失敗を保持し、修正後のlifecycleを測定

永続 pipeline の最初の real extraction call は `invalid_provider_response` で失敗。
別の Bob source に対する追加の明示許可済み診断では、引用は完全一致したが
`end=16` と返り、正しくは `end=28` だった。**失敗した model/診断 call の両方を
accounting に残す**。Dev embedding 440 call とは別であり、自動 retry や
extract/embed/compact smoke の成功ではない。

`2288fdc`の修正はモデルの wire proposal を四 field
（`subject`、`predicate`、`value`、`evidence_quote`）へ変更し、host が一意な完全一致
引用からだけ `start` / `end` を導出する。重複・重なりによる曖昧さも拒否する。
公開結果は六 field のままで、厳密な `parse_extraction` も変えない。
先頭一致の選択、曖昧 grounding、意味的権限は導入しない。
この版の最初のpipelineと別Cora-source診断もsubjectを含まないquoteで失敗した。
成功前に**抽出/診断4 callの失敗**を記録しており、隠したり実task成功に数えたりしない。

`e4f5d76ad2a4919349054165ce531b92fa650818`は検証を緩めず完全なquoteの指示を
明確にした。新規lifecycleは正確に**3 call**で、抽出（inferred preference 1公開、
隔離/重複0）、canonical 768次元embedding、未信頼summary/compactionを行った。
commit済み予約、typed checkpointの完全保持、tail保持、明示復元、
snapshot予算2,405 byte、必須tail 1 item、replay、purgeを検査した。
worker profile digestは
`739b306984c93b892df0b4ac2c00556d865d4864a48ce20ee7f74ee0cb010ed5`。
synthetic lifecycle一つの検査であり、人手precision率や実task成功の結果ではありません。
旧20-task gateは現在のproject要件ではありません。

### 限定した論理backup復旧実験

[`test-recovery-containers.sh`](../scripts/test-recovery-containers.sh)と
[`smoke-recovery.py`](../scripts/smoke-recovery.py)は、別々の使い捨て
PostgreSQL 18.6/pgvector 0.8.6 clusterで実`pg_dump`/`pg_restore`を実行する。
復元前に元clusterを削除し、独立exportした正式なtombstone/receipt/ACL metadataを
使う。test driverが覚えている削除IDをledgerとして代用しない。

古いsnapshotにはsynthetic source、assertion、checkpoint、待機structured jobを含める。
backup後にsourceをpurgeし、readerを失効させる。復元先を隔離したまま既存
service/admin interfaceでreplayし、削除payload/checkpoint/jobの不可視性、
失効readerの拒否、無関係なpositive control、最新状態との**35 tableの件数/digest一致**
を確認する。metadata-only object anchorは残るため、tombstone 4件を全object行や
backup copyの消去と呼ばない。

空の削除baseline後の完了purge 1件と、その後の失効1件に意図的に限定する。
混在/複数削除履歴、principal変更、権限を失ったreplay actor、model処理状態は
推測せず拒否する。schema 13にtargetごとのreceipt/mode対応がないため、
汎用replay toolではない。API/workerを起動せず、model call 0件は予約/quota復旧の
認定ではない。working compaction、抽出、vector、graph、tool effect、
HA/PITR、保持期限はこのcaseの対象外である。

初期local開発runは31件、その後39件のcontractと実restoreに合格し、
対象4件の遮断、reader 1件の拒否、control 1件の保持を観測した。
これは**dirty worktree実行**であり、完全一致commitの認定ではない。
実装は`58bad2991ddf3ee3e118ec47ad1e58839396cddb`として公開した。
reportにはsource SHA、helper/test三fileのhash、dirty-input flagを記録する。
一時dump/metadataは削除し、集計reportは別途保存する。

最初の`58bad29`全体packaged実行ではshell helper fixtureの同梱漏れが見つかり、
**1,527 passed / 4 failed / optional 8 skips**だった。native runは意図して
cancelし、合格とは扱っていない。`9c20909bc67dacf8e0fd77a52d1caa46c2340e45`で
両helperをtest imageへ同梱し、**host source mountなし**のimageから39 contract caseが
成功した。過去のdirty-run成功を、失敗したpackaging checkpointへ流用しない。

修正後`9c20909`のlocal distributionは**1,531 passed / optional live 8 skips**、
全production smoke、隔離復旧drillに成功した（test部分557.67秒）。
復旧reportは`exact_commit_inputs=true`で、helper hashに変更がなく、
全35 tableの最新/復元fingerprintが一致し、tombstone 4件、reader拒否1件、
control保持1件を確認した。access/deletion epochは3/1から4/2になった。
宣言した単一caseだけの認定であり、`m2_qualified=false`とmodel accountingの
未認定は維持する。

同じ修正SHAの[native CI 35326089452](https://github.com/rioriost/pg_agmemory/actions/runs/35326089452)
もamd64/arm64の各**1,531 passed / optional 8 skips**、全production smoke、
実復旧drillに成功した。両reportが`exact_commit_inputs=true`で、
全35 tableの最新/復元fingerprint一致、対象4件の遮断、reader拒否1件、
control保持1件を確認した。別途実行する39 contract caseは全体suiteにも含むため、
重複加算しない。local証跡は`m2-recovery-9c20909.json`、
native runは独立したreportを保持する。

### Public評価の試行と予算修正

最初の`101993a`公開実行はmodelの不正abstention/citationで停止した。
`2288fdc`は不正回答を明示的失敗として残すが、そのrunは304 call予約後に中断し、
最後の結果は不明だった。resume/認定していない。続く`e4f5d76`は412 call、
全168回答record（予算skip 24、不正回答72、機械的完全一致16）を完了したが、
v1 QA recipeによる診断に限定する。Native recallのIDからsource全文を復元すると
snippet用予算を超え得た。

`055215168c83501f676e643853d9d7b58e9f0c5d`のrecipe v2は、検索順の完全なsource
envelopeを**UTF-8 8,000 byteの根拠予算**に収め、回答ごとのsource ID/byte数/truncationを
元のNative順位と別に記録する。先頭sourceが大きい場合に後のsourceを選んで埋めず、
QA prompt/schema、abstention検証を変更・緩和せず、不正出力を再試行しない。
予算に固定system promptとquestionは含めず、recent-windowは2,000 byteのまま。
過去runも台帳に残し、同一予算によるQA比較とは扱わない。

### 修正済み公開oracle実測

`055215168c83501f676e643853d9d7b58e9f0c5d`の新規v2 runは
**14問/14 group/254 source**、六arm、seed 17/29を完了した。
dataset digest:
`f81f3442d8a9bfb9020d4923f2c9535771d8f4f2369b1f3fa3efaddcd305cf3c`、
評価profile digest:
`59ab9826be24fec46f6a6ac96f6299a21c76118ac56086ea9874d1ece06d1b5f`。
**412 call**（embedding 268、回答試行144）を実行し、全168予定回答recordを保持した。
full-context予算skip 24と**`invalid_answer_contract`失敗72**を含む。
失敗は非skip分母に残し、retryや成功abstentionとして扱っていない。

| Arm | Recall@20 | nDCG@10 | MRR | 機械的完全一致 / 非skip回答 | 不正回答 | QA skip | 最大根拠byte |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| no_memory | 0 | 0 | 0 | 4/28 | 0 | 0 | 2 |
| recent_window | .104166667 | .085109150 | .125 | 6/28 | 8 | 0 | 1963 |
| full_context | 1 | .530803156 | .375 | 0/4 | 4 | 24 | 4446 |
| vector | .958333333 | .724359805 | .697222222 | 2/28 | 20 | 0 | 7712 |
| hybrid | .958333333 | .724359805 | .697222222 | 2/28 | 20 | 0 | 7712 |
| temporal_provenance | .958333333 | .724359805 | .697222222 | 2/28 | 20 | 0 | 7712 |

full contextは**12/14問**をskipしたため、その検索平均は非skipの回答可能2問のみで、
公開sample全体の平均ではない。他のarmの検索平均は回答可能12問を用いる。
全84 rankingの外部IDは0。Nativeは12/14、recentは14/14 rankingにtruncatedを記録した。
追加のQA envelope制限は選択済みprefixをさらに切り詰めず、全非skip根拠が
宣言予算以内だった。QA完全一致は**16/144**、予定168件中24件のskipは別掲する。
機械的回答結果は低く、**品質合格ではない**。Unsupported claim率や公式
LongMemEval scoreではなく、回答不能caseへの非abstentionが0でも不正回答72件は消えない。

証跡はoperator-localの`m2-eval-public-0552151/{journal.jsonl,answers.json,
retrieval-run.json,retrieval-report.json}`。journalは144件のraw回答と失敗を保持し、
source payloadやraw回答をrepositoryに同梱しない。
全人手review fieldは`not_reviewed`のままである。

### 記録済みv2実測後のQA schema修正

v2の144応答の形を確認すると、72件の契約違反はすべて空回答かつ
`abstained=true`なのに**citationが空でない**形だった。受信validatorは正しく
拒否していたが、送信schemaにfield間のabstention条件が表現されていなかった。
recipe `native-retrieval-grounded-qa-v3`は完全なobject二つの`anyOf`を送る。
一方は`abstained=false`、空でないtext、重複のない非空citation配列であり、
他方は`abstained=true`、literal空回答、空citation配列である。
実際の送信schemaもprofile digestに束縛する。

prompt、seed、根拠予算、意味的採点は変更しない。受信側のUTF-8、空白、
citation所属、abstention検証を維持し、schemaを無視したproviderもretryや
出力修復なしで拒否する。schema/validatorの不一致修正であって、意味的品質の
認定ではない。過去v2結果を書き換えず、v3は版を分けた新規測定を必要とする。

新しい`9c958162f9f61bfb8f75b8747d3133c5afcb80a3`実測は同じ14問matrixを
**412 call**で完了し、**回答試行144件の契約違反は0件**、full-context skipは別途24件だった。
profile digestは`10d7bb829ce181c379a86474baa8db7c9caa68d4747e9dace35d59b966af9688`。
全根拠予算を満たし、検索score/skip件数は変わらなかった。

| Arm | 完全一致 / 試行 | Abstention | 不正回答 | QA skip |
| --- | ---: | ---: | ---: | ---: |
| no_memory | 4/28 | 28 | 0 | 0 |
| recent_window | 6/28 | 26 | 0 | 0 |
| full_context | 0/4 | 4 | 0 | 24 |
| vector | 4/28 | 26 | 0 | 0 |
| hybrid | 4/28 | 26 | 0 | 0 |
| temporal_provenance | 4/28 | 26 | 0 | 0 |

機械的完全一致は合計**22/144**にとどまり、**136/144がabstention**である。
不正abstentionの解消は**回答品質の認定ではない**。以前の公開応答から見つけた
構造上の不整合を直した再測定であり、新しいblind品質実験とは扱わない。
全人手reviewは`not_reviewed`のまま。証跡は`m2-eval-public-9c95816/`。
その前に独立したsynthetic schema smokeを4 callで実行し、回答/abstentionの
両branchに成功した。この4 callは公開benchmarkには数えない。
隔離した評価回帰対象は132 testに合格し、live測定とは別証跡である。

## Scorer が実際に行うこと

[`evaluation.py`](../src/pg_agmemory/evaluation.py) は正規化 dataset と実測した
source ID 順位を入力とし、その順位から指標を計算する。事前計算した成功率を受け取る
機能でも、retrieval/model の結果を生成する機能でもない。Service の実行、順位の取得
方法の証明、model identity の認証、請求照合、回答の意味的採点は自身では行わない。

### Dataset と run の整合性

- Origin は `synthetic`、`public`、`authorized_private` のいずれかを明示し、
  license、retrieval unit、source revision/file digest、variant を記録する。
- Source は一意な ID、group ID、timestamp、text を持つ。Question は source ID と
  重複しない一意な ID、category、language（`en`/`ja`）、dev/test split、query、
  gold relevance、answer、任意の `as_of` を持つ。
- Gold 参照は実在し、question の許可対象 group に属さなければならない。一つの
  group は dev/test に跨がれない。この構造検証は service の tenant ACL 強制を
  実証するものではない。
- `EvaluationDataset.digest()` は question/gold/provenance metadata を含む
  canonical な正規化 dataset の SHA-256。Run は完全一致が必須である。
  公開 raw file の SHA-256 と正規化 dataset digest は別物で、代用できない。
- Run は 40 桁 hex の実装 SHA、model name/revision、profile digest、
  context byte budget、乱数 seed を記録する。これは宣言した run metadata への
  束縛であり、独立して保存した実行証跡も必要である。
- Run は `split="dev"` または `split="test"`（既定 test）を宣言する。選択した
  split の全 question に**六つすべての baseline observation** が必要。
  Pair の重複、欠落、未知 question、別 split の observation を拒否する。
  各 ranking は最大 100 個の重複しない source ID。Report は `measured_questions` /
  `measured_groups` と `held_out_questions` / `held_out_groups` を区別し、
  dev run の held-out 件数は両方ゼロとする。

Baseline identifier は次のとおり。

| Identifier | 実験側の責務 |
| --- | --- |
| `no_memory` | 取得 evidence なし。`ranked_ids` は空 |
| `recent_window` | 実際に選んだ上限付き recent context とその順序を記録 |
| `full_context` | 宣言予算内の実 full context 選択、または明示した予算 skip |
| `vector` | 固定 embedding/profile を用いた実 vector-only retrieval を記録 |
| `hybrid` | 実 lexical/vector hybrid retrieval を記録 |
| `temporal_provenance` | 実 temporal/provenance-aware 設定と順位を記録 |

名称だけではその方式を実行した証明にならない。Runner は gold を順位選択に使わず、
設定、response と source ID の対応、時間、cost/footprint metadata、失敗を保持する。
`relevant` から順位を作ったり、未実装の baseline を名前だけで代用したりしない。

`skipped_reason="full_context_over_budget"` を使えるのは `full_context` のみで、
ranking は空とする。架空の score を付けず、明示的に未測定とする。
Scorer は宣言 byte budget を記録するだけで、model tokenization や context が実際に
収まったことを検証しない。Full-context skip を含む完全な measurement matrix は、
六方式の全件実行と同じではない。
Observation には `context_truncated` も記録し、baseline/category の測定ごとに
`truncated` 件数を報告する。切り詰められた context を完全なものに見せかけず、
明示的に skip した full-context row とも混同しない。

### 指標と不確実性

- **Recall@20:** 上位 20 件の一意な relevant source 数を、その question の全 gold
  source 数で割る。
- **nDCG@10:** gain は `2^relevance - 1`、順位に対する対数 discount を使う。
  Gold relevance は厳密な整数 1–3。
- **MRR:** 最初の relevant source の順位の逆数。見つからなければゼロ。
  提出 ranking は最大 100 件。
- 未知 ID と question の group 外の ID は unauthorized として数え、score を
  改善するために黙って除外しない。
- Gold evidence がない question の retrieval 指標は未定義で、満点ではない。
  `unanswerable_with_results` はそのような question に evidence が返った件数であり、
  hallucination や根拠のない回答を**測定した値ではない**。

Report は全体と category 別の baseline 指標を持つ。95% interval は **session group
全体を復元抽出**し、各 group 内の question 測定を保ったまま、各再標本で question
数に応じた平均を計算する。Seed を記録し、bootstrap sample は既定 1,000、
許容範囲 100–10,000。独立 question bootstrap、人手品質の信頼区間、システム間の
統計的非劣性の証明ではない。現在の gate は信頼区間の下限でなく平均値を比較する。
Language field は保持するが、英日 corpus であるだけで言語別実験を実施済みに
してはいけない。

### 機械的ゲートであって M2 認定ではない

非 public dataset の test split について、scorer は次を報告する。

| Gate | 実装されている検査 |
| --- | --- |
| `internal_sample` | Held-out 500 question 以上、50 group 以上 |
| `observed_scope_leakage` | 提出 retrieval run 内の unauthorized ID がゼロ |
| `recall_at_20` | 十分な sample、hybrid 平均 ≥ 90%、かつ実測 vector 平均以上 |
| `ranking_non_regression` | 十分な sample、temporal/provenance の平均 nDCG@10・MRR がそれぞれ hybrid 以上 |

Synthetic data もこれらの**機械的**検査を満たせるが、独立した実世界の受入れ集合には
ならない。`origin="public"` または `split="dev"` では sample、内部 Recall、
ranking gate を明示的に `not_measured` とする。公開結果も開発用結果も、
内部 held-out の受入れに代用できない。

`human_assertion_precision`、`human_compaction_fidelity`、`answer_quality`、
`real_task_replay`、`public_baseline`、`generated_acl_cases`、`worker_chaos`、
`deletion_recovery`、`cost_and_footprint` は本 report では `not_measured` のまま。
Public retrieval report でも `public_baseline` の受入れを自動判定しない。
完全な実験とその解釈を別途記録する必要がある。
**`m2_qualified` は常に `false`。**
別途記録した ACL 10,000-case 実験によって、retrieval-only report の
`generated_acl_cases` を書き換えるわけではない。Scorer の結果を捏造せず、
独立した証跡を添付する。

CLI は不正・不完全な入力を exit 2 で拒否する。各入力 file の上限は 32 MiB。
Report を出力し、gate に失敗があれば exit 1。Exit 0 は機械的 gate の失敗がない
という意味であり、未測定 gate の合格や M2 認定ではない。

## 実 Native を使う独立 runner prototype

[`evaluation_runner.py`](../src/pg_agmemory/evaluation_runner.py) は scorer や
background worker とは別の、明示 opt-in の実行 prototype。認定は継続中で、
実測dev/held-outと独立したpublic試行を上記で区別する。明示的な Native
ingestion/embedding/retrieval を扱い、自動抽出 job や compaction は実行しない。
Run が成功しても、それだけで background backend の経路を認定しない。

### 隔離 scope、実経路、baseline 構築

- 運用者が選択 dataset group ごとに**異なる空の隔離 scope** を事前 provision する。
  Scope-map JSON は対象 group ID だけを一意な scope UUID に対応させる。
  通常の workload や別 writer と共有してはいけない。Runner は scope を作成せず、
  管理者権限も取得しない。
- Native 接続設定は `PGAG_EVAL_API_URL` / `PGAG_EVAL_API_TOKEN` から供給する。
  取込み前に Native recall で既存 item や truncated coverage を確認する。
  空でない scope の拒否では取込みも削除もしない。この事前検査は運用者の
  隔離責任の代わりにはならない。
- 実 `AsyncMemoryClient` で **source text のみ**を observe し、canonical embedding
  input を取得、承認済み local embedding provider を呼び、canonical digest/model
  に束縛した `PutEmbedding` で保存する。Source ID を返却 Native memory ID に
  対応付ける。Question/gold/category annotation は取り込まず、gold を ranking や
  model prompt の構築に使わない。
- Provider backend は embedding model を持つ `local_http` に限定し、外部への
  自動 fallback は禁止する。明示的に許可した local 評価操作であり、capture 受付から
  許可を推定するものではない。
- `no_memory` は evidence なし。`recent_window` は完全な source envelope を
  **2,000 UTF-8 byte** 以内で選び、`full_context` は全 envelope が **8,000 UTF-8
  byte** 以内の場合だけ実行し、超えれば question/baseline を明示 skip する。
  Envelope は source ID、全文、source timestamp を含み、UTF-8 source の一部を
  切り出して予算に収まったように見せない。Recent 選択は最初の予算超過 envelope
  で停止する。
- `vector` / `hybrid` は既存の英日 lexical profile を使う実 Native vector/hybrid recall。
  最大 20 item、Native context budget は 8,000 byte。`temporal_provenance` は
  hybrid recall に、存在すれば question の `as_of` を加える。この名称は新しい
  provenance-ranking algorithm の実装を意味しない。`as_of` がなければ hybrid と
  同じ request 経路になる。Native coverage と window 選択の truncation を記録する。
- 未知・他 group の返却 ID は retrieval journal に残し、モデルへ渡さず回答生成を停止する。

既知の source 時刻には timezone-aware ISO 値を要求し、一つの group 内で既知と
未知の時刻が混在する場合は拒否する。公開 corpus の `timezone-unknown` については、
記録した**benchmark 受付時刻**を Native `occurred_at` に使い、元の日付は text に
残す。架空の UTC source date を与える操作ではない。公開 adapter は session の
対応を保って raw benchmark date 文字列で整列し、opaque ID で同値順序を決める。
Turn の元の text/date 対応を維持した、決定的な benchmark 順序であって、
timezone を確定した event timeline ではない。これらの公開 case は Native
valid-time/as-of 動作を認定しない。

### 独立 profile、呼出し上限、crash accounting

評価profile digestは`native-retrieval-grounded-qa-v3`、全文source-prefix選択、
実際のanswer-or-empty-abstention送信schema、
正規化provider settings、QA system prompt/schema、context budgetを束縛する。
過去の検索runはv1 digestを維持する。Background
`WorkerProfile` の digest **ではなく**、その policy 許可として提示してはいけない。
Model identity、dataset digest、実装 SHA、split、受付時刻、QA seed は journal に
記録する。宣言した revision の検証は引き続き運用者の責務である。

`--max-calls` は既定 3000、範囲 1–10000。全 source embedding、question ごとに
一回の query embedding、要求された各 baseline/answer-seed の組に十分な予算を
事前に要求する。不足時は matrix を黙って減らさず run を拒否する。
そのため任意の QA は retrieval-only より大きい明示予算を必要とする場合がある。

各 provider call は **network I/O 前**に非公開 `journal.jsonl` へ
`billing_unknown=true` として予約し、flush と `fsync` を行う。
検証済み完了時に completion record を追加し、失敗・crash では結果不明が残り得る。
自動 retry、fallback、使用済み runner instance/journal の resume はない。
意図的に別 run を始める前に不明な実行を照合する。新しい出力 directory は、
前回の call が実行されなかった証明ではない。この run 単位の journal/上限は、
background DB policy の永続 epoch 単位予算でも exactly-once 課金保証でもない。

Run ごとにランダムな source namespace と idempotency key を使い、以前の run の
dedup 済み episode を再利用しない。`finally` では**今回の run が受付結果として
追跡した Native ID のみ**を上限付き batch で purge し、scope 全体は削除しない。
Scope を空にするために既存 object を削除することもない。Hard crash、
結果不明の Native 書込み、cleanup 失敗は運用者の照合が必要であり、prototype は
disaster recovery や provider log/cache の削除を証明しない。

### 任意の回答診断であり品質 judge ではない

`--answers` には text model と明示 output-token limit の設定が必要。
Skip されていない question/baseline ごとに、seed **17 と 29**、temperature 0、
一つの固定 system prompt で二つの回答を要求する。Provider が制御を受理しても、
model 実行の決定性を証明したことにはならない。

未知 field を拒否する answer schema は `answer`、厳密 boolean の `abstained`、
最大 20 個の重複しない `citations` を持つ。Abstention では answer/citation が空、
回答では空でない text と、供給した source ID 内の citation が必要である。
Prompt は evidence を未信頼として扱い、否定・不確実性を維持し、根拠がなければ
回答を控えるよう要求する。Schema/citation の集合検査は意味的支持や人間の承認を
確立しない。

不正な応答・contract・citationは`failure_code`と`exact_match=false`で明示的に
記録し、skipや成功abstentionに数えない。失敗caseをretryせず他の宣言済みcaseを
続行する。transport障害は引き続きrunを停止し、partial journalを保持する。

`exact_match` は回答可能 question では strip/case-fold 後の文字列の機械的一致、
回答不能 question では abstention の検査である。後者で回答を控えなかったことを
`unanswerable_nonabstention` に記録する。全 record は
`human_review="not_reviewed"` のまま。上流 LongMemEval の LLM judge、
意味的回答品質、人手 assertion precision、人手 compaction review **ではない**。
Context budget は evidence 選択/Native pack の上限であり、provider request 全体や
model tokenization の上限ではない。

出力先 directory は新規でなければならない。成功時には `journal.jsonl`、
`retrieval-run.json`、`retrieval-report.json`、`answers.json` を保存する
（QA無効時のanswer listは空）。Runner完了（`measured`または`measured_with_answer_failures`）は
gate 判定ではない。Report を確認するか、独立 scorer の gate に応じた exit status を
使う。Dev 診断を含め、出力は `m2_qualified=false` を維持する。

### 明示 opt-in の live 評価 harness

[`tests/test_evaluation_live.py`](../tests/test_evaluation_live.py) は実 Native
SDK/HTTP 経路と承認済み local model で prototype を実行する。明示 opt-in がなければ
skip する。Standalone CLI と異なり、disposable PostgreSQL/runtime fixture を用い、
異なる空の test scope を harness 自身が作る。通常の workload DB に向けてはいけない。

| 環境変数 | 必要な用途 |
| --- | --- |
| `PGAG_M2_EVALUATION_MODE` | 明示的な `dev`、`test`、`public`。未設定なら skip |
| `PGAG_LIVE_PROVIDER_CONFIG` | Harness の固定 model に一致する承認済み local profile file |
| `PGAG_M2_EVALUATION_OUTPUT` | アクセス制御された新規operator artifact directory。既存は不可 |
| `PGAG_M2_IMPLEMENTATION_SHA` | Run で使用する実装の完全な 40 桁 hex SHA |
| `PGAG_M2_LONGMEMEVAL_ORACLE` | Public mode で追加必須。完全一致の固定 oracle artifact |

Harness は運用者が宣言する `qwen2.5:7b` / `qwen3-embedding:0.6b` の完全な
`ollama-sha256:` revision、
local-only backend、`max_output_tokens=512` を検査する。これは配置済み model weight
の独立した証明ではない。呼出し上限は 3000、context byte budget は 8000。

全 mode で六つの retrieval arm を実行する。Dev/test は synthetic corpus を使い、
この harness では QA を要求しない。**Public mode は QA seed 17 と 29 を追加する**。
Standalone runner の独立した `--answers` は適切な承認・予算がある場合だけ利用する。
Public の full-context 超過も明示 skip とし、架空の回答結果にしない。

まず dev を実行して artifact を記録し、held-out test/public の**前**に dataset/split、
実装、model/profile/prompt、予算、選択・採点方法を freeze する。Test 結果で調整した
同じ run を独立した held-out 受入れと呼び替えてはいけない。Test mode は機械的な
600 question / 50 group と retrieval gate を assert するが、M2 完了を assert する
mode はない。Dataset、scope-map、journal、ranking、report、任意の answer は管理
された local artifact とし、repository payload にしない。

```bash
# 必須の profile/output/implementation 環境変数を設定した、
# 承認済み disposable DB/runtime test 環境だけで実行する:
PGAG_M2_EVALUATION_MODE=dev pytest tests/test_evaluation_live.py -q
# Dev freeze 後、次の run ごとに新しい出力 directory を選ぶ:
PGAG_M2_EVALUATION_MODE=test pytest tests/test_evaluation_live.py -q
# Public mode では PGAG_M2_LONGMEMEVAL_ORACLE も必要。
PGAG_M2_EVALUATION_MODE=public pytest tests/test_evaluation_live.py -q
```

これらは実行インターフェースであって、**追加の実行記録ではない**。
実際の実行を示すのは、上記で別途報告した測定のみである。
Offline 契約 test や fake-provider Native integration の結果は、live ranking、
answer、実コストの代わりにならない。

### 独立した三回呼出し real-model processing smoke

[`tests/test_processing_live.py`](../tests/test_processing_live.py) は
`PGAG_M2_LIVE_PROCESSING=1`、`PGAG_LIVE_PROVIDER_CONFIG`、harness の固定 local
model、承認済み disposable `PGAG_TEST_DATABASE_URL` で明示 opt-in する。
Retrieval 評価とは別であり、成功した一つの smoke は永続 `extract` / `embed` /
`compact` job を通じ、抽出・embedding・要約の**三回だけ実 provider を呼ぶ**。
自動 retry は行わない。

Smoke は network 前の予約、result/model/input lineage、inferred または quarantined
の抽出、canonical embedding 永続化、正確な checkpoint state、未信頼 summary、
保持された tail、明示 snapshot restore/hook context と予算、call accounting を残す
purge を検査する。Smoke が通っても quarantined candidate が人間の承認済みには
ならない。この一件・三回の call は precision、compaction fidelity、実 task の完了、
retrieval 品質、性能受入れを測定しない。

```bash
# 実 local-model call を行う opt-in。通常の test では有効にしない。
PGAG_M2_LIVE_PROCESSING=1 pytest tests/test_processing_live.py -q
```

初期の抽出失敗と診断は上記に保持する。`e4f5d76`の新しい3 call pipelineは成功した。
dev/held-out検索とpublic診断は別の実験であり、相互に品質認定を代用しない。

## 内部 synthetic fixture: 構造カバレッジのみ

[`evaluation_fixtures.py`](../src/pg_agmemory/evaluation_fixtures.py) は本プロジェクトの
MIT synthetic source を生成する。私的会話でも公開 benchmark のコピーでもない。
現在の generator の宣言は次のとおり。

- 既定 seed は 42。明示する整数 seed の範囲は 0–2147483647。
- Dataset ID は `pg-agmemory-internal-synthetic-v1-seed-{seed}`、template revision は
  `internal-synthetic-templates-v1`。Template label は Git SHA ではない。
- `source_file_digest=null`。Synthetic generator が hash を主張する、ダウンロード済み
  source-corpus file は存在しない。
- 60 group、**1,920 source**（group ごとに 32）、**720 question**。Dev は 10 group / 120 question、
  held-out は 50 group / **600 question**。以前の計画見積りの全体 600 / held-out
  500 を置き換えた fixture 件数であり、評価結果ではない。
- 12 category: `same_name`、`exact_reference`、`temporal_history`、
  `temporal_current`、`negation`、`uncertainty`、`preference`、`source_update`、
  `unanswerable`、`quoted_injection`、`failed_approach`、`next_steps`。
- 英日は全体で各 360 question、dev 各 60、test 各 300。各 category は 60 question、
  各言語 30、test は各言語 25。言語/category ごとに source/query template が 3 系統ある。
- 認可 group 間の同名人物、正確な識別子、timezone-aware ISO source timestamp、
  日付付き更新、否定・不確実な主張、引用された敵対的指示を含む。Scope と時刻による
  可視性を適用した候補集合にも、近い話題の distractor を含む 20 超の source があり、
  top 20 が単に適格 group 全体になる構成ではない。
- Seed による opaque な 128-bit ID を `g_`、`s_`、`q_` の別 namespace で生成する。
  Source は question/answer/gold の描画より前に独立して生成し、question や gold の
  template/mapping を変更しても source corpus は変わらない。Question ID、
  gold answer、category、relevance annotation は memory への取込み対象ではない。
  Gold は template 由来で、人間による意味的判定ではない。
- 過去の `as_of` は旧・新 source の間に置き、現在の gold は新 source のみを選ぶ。
  Source 訂正の旧・新 evidence は 1/3、next steps の失敗した方法・後の計画は 2/3
  の grade を持つ。Unanswerable 60 question は gold/answer が空で、injection の
  answer は引用に権限がないことを明示する。

Run を固定する前に、**実際に生成した dataset** の digest と generator 実装を記録する。
上記の dev digest と retrieval 測定は一つの記録済み実体を示すもので、generator
だけでは model score を予測しない。Held-out question に
合わせた調整、seed 変更後の同一 dataset 扱い、group 数の real task replay への
読み替え、模擬 group 境界を 10,000 件の実 ACL test と数えることは禁止する。
自然対話の多様性、モデル抽出精度、人手の圧縮レビュー、実 task の成功は未実証である。
Fixture 生成は基盤 checkpoint `7343272` に含まれる。上記の unit/integration 契約
検査の報告は retrieval/model 品質の結果ではない。正確な正規化 dataset/profile digest
と実 dev 結果は上記に記録した。基盤 native CI は両 architecture で成功したが、
いずれも統合 M2 ツリーの認定や、synthetic dev 結果の held-out 受入れへの変更ではない。

## 公開診断: pin した LongMemEval oracle

[`evaluation_public.py`](../src/pg_agmemory/evaluation_public.py) は運用者が提供する
artifact の独立 normalizer。Dataset のダウンロードや、正規化 document 全体の
memory への取込みは行わない。

| Provenance 項目 | 固定値 |
| --- | --- |
| Dataset | `xiaowu0162/longmemeval-cleaned` |
| Artifact | `longmemeval_oracle.json` |
| Dataset revision | `98d7416c24c778c2fee6e6f3006e7a073259d48f` |
| Raw SHA-256 | `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c` |
| 正確な byte 数 | `15388478` |
| 宣言 license | MIT |
| Upstream code | `xiaowu0162/LongMemEval` |
| Upstream code revision | `9e0b455f4ef0e2ab8f2e582289761153549043fc` |
| Selector seed | `pg-agmemory-public-v1` |
| 正規化 ID | `longmemeval-cleaned/oracle/pg-agmemory-public-v1` |
| Variant / unit | `oracle-reader-diagnostic` / `turn` |

出典: [固定 dataset card の MIT 宣言](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/blob/98d7416c24c778c2fee6e6f3006e7a073259d48f/README.md)、
独立した [code の MIT notice](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/LICENSE#L1-L13)、
[公式 artifact link](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/README.md#L34-L42)。
Code revision と dataset revision は別物で、code license だけでは dataset の条件を
証明しない。実際の[固定 artifact](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_oracle.json)
は `resolve` URL にある。対応する `raw` は LFS pointer であって dataset ではない。

Loader は正確な raw byte 数、SHA-256、上流 file の 500-record 形式を強制する。
Abstention、knowledge-update、multi-session、single-session-assistant、
single-session-preference、single-session-user、temporal-reasoning の 7 strata
から各 2 question を決定的に選ぶ。固定 seed と question ID を用いて採点前に選択し、
**14 question** の reader diagnostic を作る。内部の 500-question 受入れ集合ではない。

各 question に opaque group、history の各 turn に opaque source ID を与える。
Source text は raw source-date 文字列、role、turn content のみ。
`has_answer` は **scorer 専用の turn-level gold** であり、gold answer、
answer-session annotation、category、question を memory や retrieval 選択の
ヒントとして取り込んではいけない。Abstention question の gold evidence は空。
正規化 dataset は source と gold の両方を持つため、取込みは **source record のみ**
とし、JSON 全体を model context に渡さない。

日付は source/question text に保持し、source `occurred_at` は
`timezone-unknown`。Adapter は UTC offset や benchmark の valid-time 意味を
捏造しない。これらの日付だけでは Native の temporal/as-of retrieval を検証できない。
上流の session array は時系列順とは限らない。Adapter は date/session ID/turn array
を対応付けて zip した**後**、raw date 文字列で整列し、opaque session ID で同値順序を
決める。Timezone を推測することはない。
Opaque ID は元の session ID や abstention suffix からの annotation 漏洩を防ぐが、
oracle corpus を現実的な大量 distractor 付き retrieval benchmark には変えない。

**Oracle は LongMemEval-S retrieval ではない。**
[公式 variant 定義](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/README.md#L74-L88)
では oracle は evidence session のみを含み、現実的な S retrieval workload と
異なる。S artifact は `longmemeval_s_cleaned.json` で、本評価用には取得していない。
宣言した oracle artifact を S、M、V2、pin のない dataset-viewer subset に黙って
差し替えてはいけない。

本 scorer の gold 件数に対する Recall@20 は、上流の
[recall_any / recall_all](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/src/retrieval/eval_utils.py#L24-L29)
とは異なる。[公式 QA evaluator](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/src/evaluation/evaluate_qa.py#L24-L43)
は LLM judge を使うため、これらの retrieval score は公式の回答品質 protocol を
再現したものではない。結果はその reader diagnostic として報告し、公開 benchmark
全体と同等、内部 Recall 90% gate、人手 assertion precision、20 real task replay
として報告しない。公開runの版と根拠予算修正は上記の記録を参照し、
完走した診断を人手回答品質の認定とは扱わない。

### LoCoMo は今回の対象外

[LoCoMo](https://snap-research.github.io/locomo/) data には
[CC BY-NC 4.0](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/LICENSE.txt#L116-L155) が適用される。
利用には**実際の非営利目的**の審査が必要であり、企業の実験を「研究」と呼ぶだけでは
十分でない。Data を commit しなくても使用目的の制限はなくならない。
Evaluation runner は LoCoMo を取得・評価していない。別途の provenance 調査では
小さい JSON をメモリ上で読み、形式・metadata を確認したが、file 保存も評価・model
呼出しも行っていない。このため一律に「一度もダウンロードしていない」とする主張は
不正確である。LoCoMo は本評価用に未承認、未評価、bundle されていない状態である。

Data や evaluator code を本プロジェクトの MIT license で bundle してはいけない。
Repository の code license は dataset の再許諾ではない。今後利用するなら目的の
承認、適用される帰属・license 表示、固定 artifact、独立した実験宣言が必要である。
[固定 dataset 説明](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/README.MD#L8-L26)
も公開 conversation data と未公開 image を区別している。Text-only のアクセスで
multimodal coverage を主張してはいけない。

## 結果を捏造せずに再現する

次は既存インターフェースの例であり、評価を実行したという主張ではない。

```bash
# Scorer 専用 question/gold を含む synthetic dataset を出力する。
python -m pg_agmemory.evaluation_fixtures --seed 42

# ORACLE_FILE は運用者が提供した完全一致の pinned artifact。
python -m pg_agmemory.evaluation_public \
  --longmemeval-oracle "$ORACLE_FILE"

# DATASET_FILE は正規化 JSON、RETRIEVAL_RUN_FILE は実測 ranking。
python -m pg_agmemory.evaluation \
  --dataset "$DATASET_FILE" --run "$RETRIEVAL_RUN_FILE" \
  --bootstrap-samples 1000
```

次の runner は**実際に local model を呼ぶ**。データ利用の承認、空の隔離 scope の
provision、profile review 後にのみ使用する。この例は dev を選ぶが、実行済みの
記録ではない。既定 split は test。接続認証情報は literal でなく環境から供給する。

```bash
# PGAG_EVAL_API_URL / PGAG_EVAL_API_TOKEN は運用者の環境から供給する。
# Scope map は選択した dev group だけを含める。
python -m pg_agmemory.evaluation_runner \
  --dataset "$DATASET_FILE" \
  --scope-map "$SCOPE_MAP_FILE" \
  --profile "$LOCAL_PROFILE_FILE" \
  --implementation-sha "$IMPLEMENTATION_SHA" \
  --output "$NEW_RUN_OUTPUT_DIRECTORY" \
  --split dev --max-calls 3000
# 二つの seed による QA call が承認され、予算確保済みの場合だけ --answers を加える。
```

生成 dataset、question 単位の run、provider 出力は、承認済み・アクセス制御済みで
version controlから除外したoperator artifact領域に保管する。大きな生成
corpus/result、認証情報、私的会話、benchmark payload のコピーを commit しない。
Repository に置く証跡はレビュー済み metadata、provenance、集計結果、管理された
artifact への参照だけとする。

実 run 前に dataset digest/group split、全六 baseline の定義と予算、実装 SHA、
完全な model pin、承認済み local profile、recipe version、selection/bootstrap seed、
許可されたデータ用途を固定する。Gold は service 取込み、model prompt、ranking
構築に渡さない。実 request/result、失敗、明示 skip を記録し、モデル配置、
mock response、収集件数、scorer 成功から品質を推測しない。
Capture policy だけでは provider egress を許可しない。

## 現在の受入れ証跡とrelease引き継ぎ（2026-09-21）

自動生成された inferred assertion、隔離 proposal、caller が採用した reported
assertion は別々の評価 cohort とする。実装済みの
`POST /v1/jobs/{job_id}/candidates/{ordinal}/adopt` は caller の
`explicit_intent=true`、`expected_input_digest`、`reason` を受け付けるが、lineage には
`human_review_verified=false` を記録する。これは caller の明示宣言であり、
人間のレビュー確認や意味的な真実の証明**ではない**。採用成功や reported という
ラベルは、独立した意味的precision labelを供給しない。
別途の人手レビュー用 sample を選ぶ際も、サーバーが記録した
source/span/model/prompt/job lineage を保持する。採用しても元の disposition は
`quarantined` のままで、別の `adopted_assertion_id` と `adopted_by` が一度の採用を
識別する。この proposal を自動公開として数えたり、採用を他の assertion の
supersession とみなしたりしてはいけない。

| 本体の義務 | 現在の証跡 / 残る要件 |
| --- | --- |
| M2-A 契約/証跡一覧 | 宣言core profileについて完了。`af878fc`のnative各1,945/8と全packaged smoke。万能な認可/意味保証ではない |
| State、provenance、明示更新 | typed値、revision/span/coverage参照、model空間分離、CAS、時点oracleの一致。要約/回答の意味品質を構造上の正しさと混同しない |
| 10,000 件の実敵対的 ACL case | `101993a6d40679c73899ee2454f6b2ad0dadafff` の**記録済み生成 HTTP matrix は PASS**。範囲を限定した証跡で、網羅的な認可や M2 の証明ではない |
| Worker chaos | `e4f5d76`で実SIGKILL/復旧/purgeの4 case合格。決定的lease/cancel/失効/policy回帰とは別で、網羅的分散障害保証ではない |
| M2-B 削除/ACL/policy/call会計の復旧 | 本文一致を要求する宣言v5 profileで完了。local `593087f`、最終native `af878fc`で混在baseline、35 canonical/21運用fingerprint、保持/purge対象派生物、予約11件を保持し、tombstone対象20件を拒否。欠落/新本文や未対応履歴は引き続き拒否し、自動起動しない |
| M2-C 資源認定 | **`51293b4`で測定:** S・30分steady、混合小規模削除、10k purge、並行limit/failure、宣言したguest-cold sampleが各checkを達成。runtime同一の`7a2fd88`で両native distributionも完了。物理host/device coldや専用本番容量は主張しない |
| M2-D 一つの参考記憶benchmark | 完了。固定profile、当時の正確なsource/JUnit対応、typed state/coverage/tail、時間/量、失敗履歴と現行配置引き継ぎを公開。新規live model実行や意味的閾値なし |
| M2 release packaging | 完了。固定0.1.0 buildを`af878fc`で両native認定し、`v0.1.0`では公開文書だけを追加。upgrade/restore制限を明示し、過去の資源/参考観測は元SHAを保持 |

旧人手precision/fidelity、自然言語更新、根拠なし回答、実task成功の目標は、
**プロジェクト受入れから除外**したのであって合格ではない。
任意の意味的観測は実際に評価しない限り未測定である。
未記入Wikipedia pilotはblockerでなく採点不要。provider単体の呼出しだけであり、
記憶経路のbenchmarkの代用にはしない。

資料利用、provider送信、配置policyの許可は引き続き人/operatorが行う。
人手品質gateの除外は、同意・license・権限・公開制御の除外ではない。
制御したprovider応答は境界契約の検証に使い、実callだけをlive証跡とする。
provider側の保持はoperatorの外部依存であり、PostgreSQL serviceが暗黙に消去を
保証できるものではない。

M2のv0.1.0引き継ぎを完了し、**M3 graph連携**が次の実装milestoneです。
SQL oracleとの一致、世代/再構築、認可/削除barrierを扱います。
認定したM2復元/資源の制限を明示し、任意履歴や本番保証へ拡大解釈しません。
一般HA/PITRとRPO/RTOはM5のままです。
