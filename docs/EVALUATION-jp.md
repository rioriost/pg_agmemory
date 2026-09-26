# 評価契約と証跡の境界

[English](EVALUATION.md)

## 発見plannerだけを変える比較

`bounded-lexical-v5`はopt-inの`discovery-v2` plannerを選び、
根拠採用はv4と同じ`round-robin-v1`を使います。topic一致で結果が埋まる場合に、
関係/動作/意図の手掛かりを区別し、限定した語形変化を検索仮説として扱い、
最初の根拠の発見を改善する狙いです。
Nativeのliteral検索/ranking、data、gold、採点、回答/保持prompt、
保持の実行、model/effortは変更しません。既存literal-v1と引数省略時の既定動作も維持します。

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-discovery-v5-01 gpt-6-astra high --allow-copilot \
  --cohort distractor-synthetic-v1 --query-policy bounded-lexical-v5 \
  --retention-policy review-v1
```

推論前に実装を固定し、後述のv4の16/20に対して**既知cohortで1回の回帰比較**を行います。
120 call上限、計画2 round、各caseで検索4回＋最新参照検査1回、
最大8 item / Native context 8,000 byteは同じです。
新しい指示で計画promptのbyte列とrecipe hashは変わります。
promptが不変という主張はreader/保持/controlだけで、plannerには適用しません。
model retry、診断用の追加model call、隠れたbrowse、gold依存のquery規則は追加しません。
以前のscalar文字列不一致も同じreader promptと厳密oracleで採点し、
検索発見と回答形式の変更を混同しません。
実際の検索/配信/引用coverage、全失敗、保持提案と実行、latency、usageを、
意味的な事後採点なしで報告します。実装だけでv5の実scoreを主張しません。

### V5結果: chainが1問改善する一方、検索と回答には後退もある

固定した**`9dfd867`**でGPT-6 Astra/highを1回実測し、
**120 model call / 60 arm結果**を完走しました。不正応答、retry、未測定armはありません。
[確認済み集計](../examples/copilot-memory-discovery-v5-result.json)は、
同じ既知cohortのv4と比較しています。reader/保持/control promptのhash、
case、gold、採点、採用方式、資源上限は変えず、planner指示だけを意図的に変えました。

| 指標 | V4 | V5 |
|---|---:|---:|
| Native厳密一致の正答 | 16/20 | **17/20** |
| 回答可能な問題の厳密正答 | 12/16 | **13/16** |
| 検索応答 / 配信 / 引用の必要source coverage | 81.25% | **84.375%** |
| 必要根拠が全て揃う回答可能case | 13/16 | 13/16 |
| 2 eventの根拠chainの正答 | 1/4 | **2/4** |
| 計画・検索・回答の合算p95 | 39.01秒 | 38.63秒 |

controlは**4/20**と**8/20**のままです。**06**は、
`Morrowquay crate approval`で最初の根拠を見つけ、続く`Heron desk`で接続先を取得し、
厳密正答へ改善しました。Native検索を変更せず、質問の語形変化と観測参照の追跡が役立った例です。
以前の厳密正答が不正答になったcaseはありませんが、集計scoreだけでは次の後退が見えません。

- **03:** 第1 roundは空で、第2 roundで最初の参照だけを発見しました。
  接続先をたどる計画roundが残らず、readerは求められた**`手書き連絡箱`**ではなく
  **`若紫受付`**と断定しました。v4では辞退していた問題での新しい誤答です。
  以前のscalar形式違反とは異なり、引用と最新参照検査が正しくても、
  質問に必要なchainが完結するとは限りません。
- **05:** 絞込みを強めた4 queryは全て空になり、必要source coverageは
  **100% → 0%**へ後退し、回答を辞退しました。
  以前の`640 tiles per file`対`640 tiles`の形式違反を**修復したわけではありません**。

**17**も根拠を取得できず辞退しました。語形指示だけでは適切な形を保証せず、
queryは`failure`、`prevent`、`prevents`、`procedure`を使い、
最初の根拠にある表現に届きませんでした。
scoreの改善や事後診断のための追加検索/model callは行っていません。
実際に検索で返った必要根拠の採用漏れはありませんが、全根拠が揃うcaseは13/16のままです。
1問で全根拠を回復し、別の1問で失い、もう1問では最初の参照だけを回復しました。
**v5はopt-inを維持し、v4の無条件な置換やプロダクト受入完了とはしません。**
過度に限定したquery、最後のroundで初めて見つかる参照、回答根拠の十分性を、
検索/model予算を暗黙に増やさず別々に改善する必要があります。

保持提案は**keep 504 / forget 136件**のgoldと全て一致し、
136件全て保留・読取可能を確認しました。Native Forget呼出しと物理purgeは0件です。
実検索は**58回、最新参照検査18回**で、空contextの2件には最終参照読取がありません。
計画2 roundは全caseで実行しています。usageは**入力462,965 / 出力20,907 token**、
cache-write 462,605、cache-read 0、報告されたAPI/premium requestは120件でした。
683,151,250,000 nano-AIUは確定金額ではありません。
読取経路p50/p95は**29.95/38.63秒**、runner全体は**1,210.156秒**です。
queue待ちを含む合算と保持/setup除外はv4と同じで、
cloud各1回の実行から因果的な高速化や本番SLAは主張しません。
実測後にsource、prompt、gold、採点は変更していません。

実測と同一sourceの[run 36220685730](https://github.com/rioriost/pg_agmemory/actions/runs/36220685730)は
8 job全て成功しました。両native coreは**5,446 passed / 134 skipped**、
別実行のCOMMIT 18件、request 13件、offline bridge 6件も成功しています。
packaged install、HA/PITR、patched AGEも両architectureで成功し、AGEは各218件でした。
localでもsourceを組み込んだimageで新plannerと実HTTP経路を確認しています。
これらの検証が成功しても、実測した振る舞いの不足が解消したとは扱いません。

## Version付きの根拠採用方式の修正

`bounded-lexical-v4`は**caller側の根拠採用**を変えます。
Native検索、model選択、計画promptは変えません。
`evidence_selection="round-robin-v1"`でqueryごとの結果から重複しないitemを交互に選び、
追加roundのlistを先に、各list内ではNativeの順位を維持します。
候補は最大4 list・各8 item、採用contextは引き続き最大8 item / Native 8,000 byteで、
最後の最新required-reference検査を1回行います。
既存の`bounded-lexical-v3`は先着順の動作を維持します。

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-admission-v4-01 gpt-6-astra high --allow-copilot \
  --cohort distractor-synthetic-v1 --query-policy bounded-lexical-v4 \
  --retention-policy review-v1
```

後述の初回13/20を受けた**既知cohortでの回帰比較**です。
case/gold、query-v2、計画/回答/保持prompt文、採点、review動作、model ID/effortは固定します。
**120 model call、計画2 round、各caseの検索4回＋最終検査1回**の上限も維持します。
採用根拠が変わると追加計画の入力やmodel出力も変わるため、
確率的な応答が同一になるとは約束しません。
model reranker、隠れた検索、retry、Native SQL変更は追加しません。

共通helperのsource hashは新しい選択分岐により正しく更新します。
過去の厳密な再現には記録したcommitを使い、
v3動作の維持を、現在のmoduleが過去と同じhashだという意味にはしません。
v4 recipeは選択方式と実helper sourceを明示的に結びます。
記録済み応答のoffline再入力で参照集合は比較できますが、
最新の認可検査や新しい回答品質測定ではありません。
実推論前にcleanなcommitへ固定し、以前の不十分な結果を事後採点し直しません。

### 既知cohortのv4結果: 採用時の欠落は解消、発見の不足は残る

固定した**`357a57b`**をGPT-6 Astra/highで実測し、
**120 model call / 60 arm結果**を完走しました。
不正応答、retry、未測定armはありません。case/gold、採点、計画/回答/保持prompt文、
review policyは変更せず、固定したcontrol/保持promptのhashも初回と全て一致しました。
これは**既知cohortの再利用**であり、新しいheld-out証跡ではありません。
[確認済み集計](../examples/copilot-memory-admission-v4-result.json)を参照してください。

| 指標 | 初回v3 | 既知cohortのv4 |
|---|---:|---:|
| Native厳密一致の正答 | 13/20 | **16/20** |
| 回答可能な問題の厳密正答 | 9/16 | **12/16** |
| 検索応答の必要source coverage | 81.25% | 81.25% |
| 回答者に届いた必要source coverage | 59.375% | **81.25%** |
| 引用された必要source recall | 56.25% | **81.25%** |
| 2 eventの根拠chainの正答 | 0/4 | **1/4** |
| 計画・検索・回答の合算p95 | 35.92秒 | 39.01秒 |

controlは記憶なし**4/20**、直近2 eventが**8/20**のままです。
以前捨てられた4問（01、02、05、08）の必要sourceは全て回答者に届き、
今回、検索で返った必要sourceの採用漏れはありません。
01、02、08が厳密正答へ改善し、以前正答したcaseの後退はありません。
二度訂正された履歴4件も引き続き正答しました。
推論前の元20履歴のoffline再入力では、v3の最終参照集合を厳密に再現し、
v4の選択coverageが59.375%から81.25%へ改善することを確認しました。
新しいmodel/DB呼出しは使わず、最新認可の確認や回答scoreの予測とはしていません。

未達4問は原因が異なります。**03、06、17**では引き続き必要sourceが二つとも取得できず、
回答を辞退しました。検索で返らない根拠は再選択だけでは回復できません。
**05**は正しいsourceを受け取り引用しましたが、
固定scalarの**`640 tiles`**ではなく**`640 tiles per file`**を返しました。
長い方の表現も引用source内にありますが、変更していない厳密な文字列一致では不正答です。
事後の意味評価で17/20に読み替えず、**16/20**を維持します。
既存採点はこの不一致を`unsupported_answer`にも数えますが、
これは独立したhallucination判定ではありません。
残る検索の未達3問とscalar出力契約は、別の改善課題です。

保持提案は今回も**keep 504 / forget 136件**のgoldと全て一致しました。
reviewは136件全てを保留し、引き続き読めることを確認しています。
Native Forget呼出しと物理purgeは0件であり、以前の別cohortの誤提案の修復や忘却完了ではありません。
実検索は**65回、最新参照検査20回**で、以前の63回・20回に対し、
各caseの4回＋1回という上限は同じです。
usageは**入力467,782 / 出力18,274 token**、cache-write 467,422、cache-read 0、
報告されたAPI/premium requestは120件でした。
報告値676,007,500,000 nano-AIUは、確定した金額ではありません。

読取経路のp50/p95は**28.92/39.01秒**です。
計画2回、実Native読取、回答呼出しの合計で、保持判断とsetupは除きます。
runner側のmodel時間にはqueue待ちを含め、全Copilot呼出しのpercentileはbridge報告durationを使います。
runner全体は**1,166.404秒**でした。確率的なcloud実行各1回では、
遅延差の因果関係、本番SLA、未見dataへの汎化は認定できません。
実測後のhelper、prompt、model policy、採点の変更や診断目的の追加model callはありません。

local検証の留保として、scripted modelを使う最初の実HTTP実行では、
v3/v4双方にまたがる7件でcontextが空になる失敗がありました。
sourceを変えずに対象8件と889件の全体実行2回は成功し、
最後の実行は診断の追加やsource overlayなしです。
read-onlyの時計診断でも失敗を再現できず、根本原因は未確定です。
sleep、時刻filterの緩和、cacheへのfallbackは追加していません。
この実装検証の再実行と、retryなしの実model測定1回は区別します。

実測と同一sourceの[run 36213207824](https://github.com/rioriost/pg_agmemory/actions/runs/36213207824)では、
native 8 job全てが成功しました。両coreは**5,365 passed / 134 skipped**、
別実行のCOMMIT 18件、request 13件、offline bridge 6件も成功しています。
packaged install、HA、PITR、patched AGEも両architectureで成功し、AGEは各218件でした。
これは実装の検証であり、プロダクトの受入完了ではありません。

## Policyを固定した長いdistractor履歴の評価

`--cohort distractor-synthetic-v1`は、別versionの合成cohortを選びます。
**20 case / 640 event**、英日各10件、各履歴32 eventです。
各caseには、既存の保持policyで残すべき継続的な類似topicのdistractorを24件含めます。
保持判断後にreaderから消える一時的なrowを増やしただけの評価ではありません。
従来の5 categoryを均等にし、scalar回答16問と回答辞退4問を含めます。
根拠位置を変え、回答可能な4問では直近2 eventにも必要根拠を全て置きます。
2 eventの根拠chainと二度訂正される履歴を各4件含め、
rowの存在だけでなく根拠選択を評価します。goldは**keep 504 / forget 136件**です。

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-distractor-01 gpt-6-astra high --allow-copilot \
  --cohort distractor-synthetic-v1 --query-policy bounded-lexical-v3 \
  --retention-policy review-v1
```

新cohortだけに明示的な拡張case型を使います。元の6～8 event契約、
以前のdataset二つ、保持/回答prompt、採点式は変更しません。
推論前に履歴とgold labelをreviewし、cleanなcommitへ固定します。
bounded planner、review helper、model ID/effort、資源上限も維持し、
**最大120 model call**、各caseで検索4回と最新参照検査1回、
最大8 item / Native context 8,000 byteとします。
結果を見たquery調整、自動retry、隠れたbrowseは行いません。

初回利用の**projectを知るAIによる合成stress data**であり、
独立した人間の作業、盲検、代表的な実務、大規模corpus benchmark、
学習data除外の証明ではありません。作成したdistractor patternは実agent履歴の代替にはなりません。
以前の既知cohortの20/20は参考値で、この異なるcase集合の期待値や合格基準ではありません。
結果にかかわらず3 arm、失敗、直接取得coverage、引用source recall、
保持提案の誤り、実際の保留/削除件数、usageとlatencyを報告します。
同じcohortを再実行した場合は再利用と明記します。
runnerはこのcohortの初回利用/再利用を不明と記録します。
確認済みreportが実行履歴から区別し、新しい出力directoryだけで初回とは判定しません。

### 長い履歴の初回結果: 有用な追加検索結果が採用されない

**`f12a3f4`**の初回実測は、**120 model call / 60 arm結果**を完走しました。
不正応答、retry、未測定armはありません。
最初の記録された推論より前にdatasetをreview・commitし、
推論後にmodel、query/helper、保持policy、prompt、case、採点式を変更していません。
[確認済み集計](../examples/copilot-memory-distractor-result.json)には、
新cohortのidentityと変更していないpolicy/scorerのhashを記録しています。

| arm | 正答 / 20 | 回答可能な問題の正答 / 16 | 回答辞退 |
|---|---:|---:|---:|
| 記憶なし | 4 | 0 | 20 |
| 直近2 event | 8 | 4 | 16 |
| Native bounded memory | **13** | **9** | **11** |

必要な回答辞退4件と二度訂正された履歴4件は全て正答しました。
一方、回答可能な7問で回答を辞退し、**根拠chain 4問は全て辞退**しました。
断定した誤答はありませんが、以前の20/20だけでは、
継続的なdistractorが多い場合の検索信頼性を示せません。
異なるcohortなので、対応のある因果比較や実務品質の認定でもありません。

journalと固定済み実装から、二つの失敗経路を分けられます。
記録済み応答を未変更のhelperへ再入力し、DB/modelの追加呼出しなしに、
全20 caseの最終参照集合を再現しました。

- **4 case（01、02、05、08）**では、追加Native検索が不足する正解根拠を実際に返しています。
  しかしcallerの8 item枠が埋まっており、`BoundedRecall.record()`は先に採用したitemを残して、
  後から来た根拠を再選択せず捨てます。case 08ではplannerが正しく`空輪審査経路`を追跡し、
  Nativeも担当部署のentryを返しましたが、最終contextには入りませんでした。
  最新参照検査は採用済みrowを検証するだけで、関連性や根拠の十分さは保証しません。
- **3 case（03、06、17）**では、どの検索も必要根拠を返しませんでした。
  広いtopic検索で経路/教訓が埋もれ、追加検索にも語彙の不一致が残ります。
  例えばqueryの`approve`に対し保存文は`approval`、
  `failure`に対し保存文は`failed`で、固定のsimple profileにはstemmingがありません。

回答可能な16問の必要根拠coverageは、**Native検索応答全体の和集合では81.25%**ですが、
回答者へ渡る段階では**59.375%**です。従来の直接取得metricは渡したcontextを測り、
途中で返った全結果を意味しません。引用ベースrecallは別に**56.25%**でした。
全根拠が届いた9問、chainの半分だけが届いた1問、根拠が届かない6問に分かれます。
切捨てflagは全20 caseで保持しています。

保持提案は、継続的なdistractorを含めて**keep 504 / forget 136件すべて正解**でした。
以前のcohortの誤提案が直ったことや、保持判断の一般的な正しさを意味しません。
**136提案は全て保留し、rowが読めることを確認**しました。
Native Forget送信・物理purgeともゼロで、削除の承認・完了はありません。

根拠取得は**検索63回＋最新参照検査20回**で、各caseの4＋1上限は維持しました。
検索p95は**66.68 ms**、最終検査p95は**73.83 ms**、
計画2回/検索/回答の合計は**p50 27.87秒 / p95 35.92秒**です。保持判断/setupは除き、
計測時計は後述のbounded/review結果と同じです。
usageは**input 467,877 / output 17,862 token**、
reported premium request 120件、追加診断model callはゼロでした。
作成時usageを含まず請求額でもありません。高速化や製品の本番実用性は認定せず、
初回結果を事後調整しません。根拠の採用方式と残る語彙の不一致は製品側の残作業です。

## 上限付き根拠探索とreview-only保持の比較

opt-inの`bounded-lexical-v3` / `review-v1`は、観測したquery/根拠chainと
不可逆な保持操作の課題を扱います。Native SQL、RLS、既存case、
query-v2 module、回答/採点式は変更しません。
測定済み合成cohortの再利用であり、**新たな未使用評価ではありません**。
二つのpolicyを同時に変えるため、改善を検索だけの効果と分離して認定しません。

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-bounded-review-01 gpt-6-astra high --allow-copilot \
  --cohort unseen-synthetic-v1 --query-policy bounded-lexical-v3 \
  --retention-policy review-v1
```

v3は**最大120 model call**です。従来と同じ保持提案20件、
control回答40件、Native最終回答20件に、計画が最大40件です。
従来100 callからの明示的な予算増加で、隠れたretryではありません。
各caseは計画2 round、各roundのliteral query最大2個、
Native検索4回と最後のreference-only検査1回までに制限します。
scope/filter/profileと`as_of`/`known_at`を固定し、
pending review IDを追加計画・回答contextへ渡す前に除外します。
whole-item mergeは最大8 item / Native context 8,000 byte、
計画promptにも8,000 byte上限があり省略itemを明示します。
index不完全、epoch変更、応答矛盾、最新の参照検査失敗ではcaseを停止し、
cached成功へのfallbackや空query browseをしません。
最終の空queryは非空exact required refsと同数の`max_items`を指定する検査だけです。

`review-v1`はNative Forgetのpreview/purgeを**呼びません**。
modelのforget提案を全てreview待ちとし、rowが権限あるoperatorから引き続き読めることを検査します。
このworkflow内の除外であり、全体のアクセス失効や忘却完了ではありません。
後の削除には、既存Native機構を使う独立した信頼できる判断が必要です。
終了時の所有lab破棄はoperator cleanupであり、model指示による削除ではありません。

保持scoreは誤ったforgetも含む**提案品質**として表示し、
purge済みを意味する`unsafe_deleted`という名前をそのまま流用しません。
実行Native purgeゼロ、保留件数、`deletion_completed=false`を別に記録します。
gold labelで操作を承認/拒否しません。回復可能なrowを残すことを、
完全な保持選択、privacy消去、永続review queue、
helperを使わないclientも含めた本番削除保護とは扱いません。

flagなしは従来の`lexical-v2` / `model-purge-v1`と100 call上限を維持します。
v3を選び保持flagを省略した場合はreview modeになり、
明示的なoverrideは別version比較用に残します。
raw計画、実行query、最新参照検査、提案/操作、資源上限をrecipe v4で結び、
失敗も分母に残します。model ID/effortは固定し、
正確なweightはprovider管理下で未検証です。

### Bounded/review実測結果

**`6610f0b`**で両policyの初回比較を行い、**120 model call / 60観測**を完走しました。
不正応答・retry・未測定armはありません。
[確認済み集計](../examples/copilot-memory-bounded-review-result.json)はcase/scorer、
両helper module、非公開artifactに結び付いています。
GPT-6 Astra/highと元の回答/保持promptは変更していません。

| 同じcohortでの測定 | 前回の単発query/purge | Bounded/review |
|---|---:|---:|
| 記憶なしの正答 | 4/20 | 4/20 |
| 直近履歴の正答 | 8/20 | 8/20 |
| Native正答 | 9/20 | **20/20** |
| 回答可能な問題のNative正答 | 5/16 | **16/16** |
| 必要根拠の直接取得coverage | 34.375% | **100%** |
| 誤ったmodel forget提案 | 1 | **1** |
| 実行Native purge | 91 object | **0** |
| Model call | 100 | **120** |

保持partitionは全20 caseで前回と同じでした。正しいkeep提案48件とforget提案91件で、
そのうち`unseen-02-e5`の別teamの継続制約を消そうとする1件は**model側では直っていません**。
review modeは91提案すべてを保留し、権限あるcallerからrowが読めることを検査して、
Native Forgetを一度も送信しませんでした。削除の完了・承認はありません。
planner/readerからの除外はこのworkflow内だけで、全体のprivacy消去保証ではありません。

**検索48回＋最新参照検査20回**で、各caseは検索最大4回・検査1回を守りました。
以前失敗した根拠chain二つも正答しています。
例えば`Copperwheel billing`と`Copperwheel`でrouting ruleを見つけ、
追加の`Ledger review`と`Ledger`でdirectory entryを取得しました。
最新検査後の実source二つを引用し、最終回答は`Billing desk`です。
query/referenceは観測根拠から作り、gold labelは使いません。
boundedな切捨てflagも全caseで残し、gold根拠100%を全corpus取得とは呼びません。

追加計画の費用は増えています。model計画2回、全検索/最終検査、最終回答の合計は
**p50 28.96秒 / p95 37.89秒**で、前回の**19.34 / 23.25秒**より遅くなりました。
Native検索単独は**p50 50.20 ms / p95 69.77 ms**、
最新検査は**52.64 / 67.36 ms**です。Copilot単発p95はCLI/bridge込み**13.00秒**でした。
Copilot単発の集計はbridge報告のduration、回答callとread-path合計は
queue待ちを含むrunner観測の経過時間を使います。合計は保持判断/setupを除きます。
評価input/outputは**421,659 / 12,220 token**、reported premium request 120件、
追加診断呼出しゼロです。実装/作成時usageを含まず、請求額ではありません。

予算と二つのpolicyを変えた既知cohortの回帰比較としては成功ですが、
一般化、本番保持判断、end-to-end高速化を認定しません。
元の9/20の結果と実際の誤削除も、変更せず公開を維持します。

## 別途固定した評価cohortの選択

既定の`--cohort pilot-v1`は従来の20件を選びます。
`--cohort unseen-synthetic-v1`は新規に作成した20履歴を選択し、
元caseや固定済み`lexical-v2` query plannerを変更しません。
model呼出し前に、datasetと実装をcleanなcommitへ固定します。

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-unseen-01 gpt-6-astra high --allow-copilot \
  --query-policy lexical-v2 --cohort unseen-synthetic-v1
```

新cohortは**139 event / 20 case**、英日各10件で、
sessionをまたぐ嗜好、project制約、訂正値、明示忘却、失敗からの手順知識を扱います。
各履歴6～8件、根拠位置の変更、類似名のdistractor、根拠chainが必要な2問、
二度訂正される3履歴を含みます。回答可能な4件は直近履歴にも根拠を置き、
controlを構成上いつも不利にすることを避けます。
goldは**keep 49 / forget 90、scalar正答16件、回答辞退4件**です。

作者は公開event/保持契約から作成し、評価modelの回答や以前の非公開traceは使っていません。
履歴とlabelは推論前に確認しましたが、**projectを知るAIによる合成作成**であり、
独立した人間の履歴、盲検review、公開benchmark、学習data除外の証明ではありません。
「未使用」はこのcohortの初回評価を指し、再実行時には既使用です。
新出力directoryだけで未使用とは証明できず、reportも外部held-out/一般的認定をfalseとし、
modelの過去の接触は認定しません。

model、query計画source、保持/回答prompt、case単位の採点式は維持します。
採点を重複実装せず、reportへcase集合を指定できる最小限の一般化を行いました。
source hashの変更は正しく記録し、`pgag-agent-memory-cohort-recipe-v3`でdataset、
実scorer、保護したprompt/case/採点prefix、query recipeを結び付けます。
過去の公開結果は元identityを維持し、厳密な再現には現在のfile hashではなく
各結果の記録source revisionを使います。

legacy/v2は21個の隔離tenant identity、最大100 model call、
retry・query拡大・空query fallbackなしを維持します。
不正・未測定を含む全arm/caseを残します。toolなしのmodelへ選択根拠だけを渡すことで
隠れたfileからの取得を防ぎますが、自律した実務agentやbackground workerの試験ではありません。

**検索とcitationの測定を分離します。** 過去の`required_source_recall`は
必要IDのうち*引用された*IDから算出しており、直接の取得coverageではありません。
新しい`evidence_coverage`の`retrieved_required_source_recall`は、
検証・予算制限後に回答側へ渡すcontextから算出します。
有効な分母、検索前の不明結果、回答辞退問題の非適用を区別し、
検索済みなら後段の回答失敗でも観測contextを消しません。
過去の指標や公開scoreを別ルールで再計算しません。

### 新cohort初回結果: 一般化はなお不十分

**`c521329`**で`unseen-synthetic-v1`初回を実行し、
GPT-6 Astra/high・Copilot CLI 1.0.88で**100 call / 60観測**を完走しました。
model呼出しの失敗・応答修復・retryはゼロです。
query計画module hashは、前回20/20となった回帰評価と同一です。
[確認済み集計](../examples/copilot-memory-unseen-result.json)は別途固定したcohort、
実scorer、recipe、非公開artifactに結び付いています。

| arm | 正答 / 20 | 回答可能な問題の正答 / 16 | 回答辞退 |
|---|---:|---:|---:|
| 記憶なし | 4（20%） | 0 | 20 |
| 直近2 event | 8（40%） | 4 | 16 |
| Native lexical memory | **9（45%）** | **5** | 14 |

明示忘却の4問は全armが適切に回答辞退しました。
Nativeでは回答可能な5件で必要根拠が全部届き、1件は2 eventのchainの半分、
10件は必要根拠なしでした。取得coverageは、分母不明なしの16問平均で**34.375%**です。
今回の引用recallは偶然同じ値ですが、二つは別計算です。
何らかのcontextが返ったのは12 queryであり、非空結果だけでは取り逃しを見落とします。
直近履歴との差5 percentage pointsは広く信頼できる記憶の証拠ではなく、
異なる旧cohortとの因果的な比較でもありません。

traceから三つの課題を区別できます。

- **語彙と訂正context:** `triage inbox`は保存文の「support shifts」と一致せず、
  `Pebblegate warranty`は保持した訂正文の複数形「warranties」と一致しません。
  短いqueryでも全lexeme一致が必要で、訂正文は元の説明をすべて繰り返すとは限りません。
- **根拠chain:** `Copperwheel billing`はrouting ruleだけを取得し、
  担当deskを示す別directory entryを取得しませんでした。
  必要な`Billing desk`ではなく`Ledger`と回答し、
  実在sourceへのcitationがあっても回答は誤りです。誤った非辞退回答は1件です。
- **保持判断:** gold keep 49件中48件を保持し、gold forget 90件は全て消去しましたが、
  継続的な制約をもう1件余分に消去しました。
  `unseen-02-e5`は別teamの継続review方針で、質問の回答根拠そのものではありません。
  回答根拠は残っていましたが検索で取り逃しています。
  keep recall 98.33%、forget precision 99.17%はcase平均であり、全件合算の比率ではありません。

これは基盤障害ではなく製品品質の課題です。本番dataは使わず、
model選択purgeは明示同意した所有合成fixtureだけに適用しました。
本番自動削除の承認でも、model交換だけで解決する証拠でもありません。
今回の結果を見てcase・gold・query prompt・採点を調整していません。
次の検索/保持変更では別recipeを使い、このcohortは測定済みdataの再利用として報告します。

Native recall 20回は**p50 43.10 ms / p95 56.27 ms**、
新CLI/bridge込みCopilotは**p50 9.41秒 / p95 12.78秒**でした。
query生成・recall・回答の合計は、保持/準備を除いて
**p50 19.34秒 / p95 23.25秒**です。
usageは**input 344,416 / output 7,254 token**、reported premium request 100件で、
今回のincrementに追加診断model呼出しはありません。
観測usage単位であり、検証済み請求額や本番latency認定ではありません。
件数は評価transportだけを対象とし、実装やcohort作成に使ったassistantのusageは含みません。
セッション全体の請求額を測ったものではありません。

## 固定modelのCopilot agent-memory pilot（2026-09-25）

2026-09-25に依頼された製品評価はDB契約・本番HA認定とは別で、
実modelの保持/忘却判断と、Native memoryによる回答改善を測ります。
旧来の一律な意味的品質閾値やmodel比較を復活させるものではありません。
推論前にdataset/scorerをcommitし、測定結果で同じdatasetを密かに調整したり失敗を除外したりしません。

**合成20 scenario / 140 event**を英日各10件とし、sessionをまたぐ嗜好、project制約、
訂正値、明示忘却、失敗から得た手順知識を扱います。scalar正答16件と回答辞退4件です。
保持・根拠・正答のgoldはscorer専用で、model promptへ渡しません。
同一modelで`no_memory`、末尾2件の`recent_window`、`pg_agmemory`を比較します。
各Copilot呼出しはCLI memory、custom instruction、利用可能toolなしの新sessionで、
bridgeが出力metadata上のtool数ゼロとmodelを確認します。

Native armはmodelがkeep/forgetに分け、所有合成DBだけでpurgeを実行します。
別のmodel呼出しが検索queryを生成し、実Native HTTP/RLS/SQLの結果を回答へ渡します。
別tenantのscope/object拒否も検査します。API鍵、管理URL、署名鍵をmodel promptや
host Copilot processへ渡しません。

```bash
mkdir -p .review-artifacts
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-pilot-01 gpt-6-astra high --allow-copilot --query-policy legacy-v1
```

cleanなcommit済みcheckout、Apple Container、Node.js、認証済みhost Copilot CLIが必要です。
modelとreasoning effortは引数で、製品に固定しません。初期設定はGPT-6 Astra/highです。
互換性のある利用可能modelへ明示変更できます。CLI version、要求/報告model、usageは記録しますが、
Copilot管理下の正確なweight revisionは**独立検証していません**。
application呼出し上限は**100件**（保持20、query20、回答60）です。
application retryや不正回答の修復はせず、Copilot subscriptionの消費、失敗、診断probeを隠しません。

guest全体40分、各Copilot process150秒を上限にします。
回答prompt全体は8,000 UTF-8 byte、recentは2,000 byteで、
Copilot自身のsystem promptはこのbyte予算外です。
実CLI token/cache/API usageは報告された分だけ別記録し、不明値はnullとします。
credit単位を検証済み金額へ換算せず、CLI起動・transport時間をNative検索時間と偽りません。

全arm/caseと失敗・不正・不明呼出しを残し、keep/forget、機械的回答、
citation、必要source coverage、latencyを測ります。失敗込み正答率と
有効回答のみの正答率は分母を明記し、不明な削除結果を安全と採点しません。
完走は製品合格と同義ではなく、`release_qualified`はfalseです。

初回は**lexical retrieval**であり、Copilot embeddingを捏造しません。
caller側の保持/query/回答を対象とし、既存background抽出/compaction worker、
semantic vector品質、自由記述推論、multimodal memory、公開benchmarkを認定しません。
既存provider profileは変更可能ですが、workerの`local_http`制約と
768次元の宣言embedding契約は維持します。CLI bridgeは評価専用で、製品推論backendの追加ではありません。
raw prompt/responseとjournalは新規出力先で非公開保持し、公開は確認した集計値だけにします。
harnessは所有DB/APIと一時credentialだけをcleanupし、外部datasetは削除しません。

### Lexical-v2 query planning comparison

改訂policyは`--query-policy lexical-v2`で明示し、wrapperの新defaultもv2です。
実Native APIが公開する製品側query計画契約を使い、選択modelへliteral term 1～3個を要求します。
構造化計画を検証し、結合したqueryを通常SDKで送信します。
server契約不一致はmodel dispatch前に失敗し、fixture purge前にも再確認します。
serverのmatching/rankingや認可規則は変えません。

```bash
bash scripts/evaluate-agent-memory-containers.sh \
  .review-artifacts/copilot-pilot-v2-01 gpt-6-astra high --allow-copilot \
  --query-policy lexical-v2
```

測定した`0a8be6e`の比較では、元の`agent_evaluation.py`、scenario/gold、
保持/回答prompt、採点をbyte単位で維持しました。
そのrevisionの`legacy-v1`は元のrecipe digestと非構造化query生成を維持します。
v2は元recipe、query計画module、公開契約を含む別recipe digestへ結び、
raw計画・結合query・policyを非公開で記録します。
100 call上限、context、model選択、retry禁止、失敗の分母は同じです。
これは**初回診断後の同一回帰cohort再測定**であり、盲検held-out評価、
model比較、一般的な製品受入れではありません。初回結果は上書きしません。

#### Lexical-v2実測結果

**`0a8be6e`**でv2初回実行が**100 model call / 60観測**を完走しました。
**GPT-6 Astra/high / Copilot CLI 1.0.88**で、不正/失敗model応答、
application retry、browse fallbackはゼロです。
[確認済みv2集計](../examples/copilot-memory-lexical-v2-result.json)は新recipe/moduleと
非公開artifact checksumを記録し、case digestとbase recipe digestは初回と一致します。

| 測定 | 初回query policy | Lexical-v2 |
|---|---:|---:|
| 記憶なしの正答 | 4/20 | 4/20 |
| 直近履歴の正答 | 4/20 | 4/20 |
| Native記憶の正答 | 5/20 | **20/20** |
| 回答可能な問題での正答 | 1/16 | **16/16** |
| 回答可能な問題の必要source recall | 6.25% | **100%** |
| 正しいkeep / forget判断 | 40 / 100 | 40 / 100 |
| 必要eventの誤削除 | 0 | 0 |

適切な回答辞退4件も維持しています。具体例ではreport localeについて膨らませた自然文queryが
`{"terms":["report","locale"]}`、結合後`report locale`となり、
実Native応答に必要な保持eventが含まれました。oracleはtermや正答を供給しません。
RLS、時間/削除filter、SQLのAND一致はそのままです。

Native recall 20回は**p50 40.99 ms / p95 52.75 ms**、
Copilot呼出しは**p50 8.07秒 / p95 11.39秒**でした。
query生成・Native recall・回答呼出しの合計は、CLI/bridge込み・保持/準備を除いて
**p50 18.38秒 / p95 22.98秒**です。初回の**15.95 / 16.63秒**より遅く、
今回の改善は正答・検索適合性であり、**end-to-end高速化ではありません**。
input/outputは**344,886 / 6,401 token**、reported premium requestは100件で、
請求金額は未検証です。

既知の合成caseに対する回帰比較の成功であり、独立held-outの証明ではありません。
一般的な製品/release/本番の認定flagはfalseを維持し、初回の悪い結果も変更せず公開しています。

### 初回Copilot実測: 記憶の実用性は未認定

変更していないscenario/recipeを**`862fb2b`**で完走しました。
Copilot CLI **1.0.88 / GPT-6 Astra / high**で**100 model call / 60 arm観測**が完了し、
不正・失敗model応答はゼロです。全呼出しで指定model/effortとtool数ゼロを確認しました。
[確認済み集計結果](../examples/copilot-memory-pilot-result.json)はsource、case/recipe digest、
非公開report/journal checksumを記録しています。

| arm | 機械的正答 / 20 | 回答辞退 | 回答可能な16問の正答 |
|---|---:|---:|---:|
| 記憶なし | 4 / 20（20%） | 20 | 0 / 16 |
| 直近2 event | 4 / 20（20%） | 20 | 0 / 16 |
| Native lexical memory | 5 / 20（25%） | 19 | **1 / 16（6.25%）** |

baselineの正答4件は、必要な回答辞退であり知識の復元ではありません。
保持判断は全20件で明示合成policyと一致し、**keep 40 / forget 100**、
precision/recall 1.0、必要eventの誤削除ゼロでした。
しかしNative検索で何らかの結果が返ったのは**3/20 query**、
回答可能な問題での必要source recallは**6.25%**です。
保持と回答形式が正しくても、この構成で**十分に役立つ記憶とは確認できません**。
5 percentage pointsの差を、代表的な実務負荷での統計的改善とも主張しません。

固定traceには具体的な連携不整合があります。短いreport localeの質問に対し、
modelは追加語を多数含む自然文検索queryを生成しました。
Native lexical検索は`plainto_tsquery('simple', ...)`で全lexemeを要求しますが、
保持eventにはそれらの一部しかありません。query promptはこのAND契約を説明していませんでした。
保持modelの変更が必要という証拠ではなく、query生成と検索の接続が改善点です。
結果を見てprompt/dataset/採点は変更していません。修正後は別versionの独立した測定が必要です。

Native recall 20回は**p50 49.68 ms / p95 62.22 ms**、
Copilot 100回は新CLI起動・bridge込みで**p50 7.31秒 / p95 9.47秒**でした。
各caseのquery生成・Native recall・回答生成の合計は**p50 15.95秒 / p95 16.63秒**で、
保持判断/fixture準備を除き、本番SLAではありません。
記録input/outputは**339,489 / 6,425 token**、reported premium requestは100件です。
creditを検証済み金額とは扱いません。

それ以前のharness失敗4回も保持しています。container address取得形式、queue path不一致
（guest dispatch一件は不明、host Copilot実行はゼロ）、strict JWT起動失敗、
正常な空RLS結果の誤拒否です。最後の試行はbaseline model呼出し2件を消費しました。
transport probeも別途2件を消費しており、**診断4 callは完走100 callと分けて記録**します。
fixtureだけの修正と実HTTP/SQL回帰はcase/recipe digestを変更していません。
所有serviceはcleanupし、本番data、model生成の外部effect、backup消去は使っていません。

## v0.2.0 release identity

release profileは`M3-bounded-native-graph-v2`、digestは
`37b0379d66341047d2def85621feff9f949cc5a42e3826d3746f51c175e0db0d`です。
v1との差は名前/service versionだけで、六つの負荷、warmup/sample数、割当、
厳密な1500ms閾値は維持し、新しいexact0.2.0 runが以下のとおり完了しました。
以下のv0.1.3結果は元の実装/profile bindingとM3全体flag=falseを変更しません。

固定**`4204892fa90fb93a62a24f78545ef89a14abbc2e`**の
[native run35807408792](https://github.com/rioriost/pg_agmemory/actions/runs/35807408792)は両architectureで成功しました。
core2,394/optional113 skips、全製品smoke/通常復元、修正AGE84+59+207確認、
実HTTPとcanonical-only復元を含みます。
独立したexact release資源runは396 samples/probe36件、error0で、
`resource_qualified:true`、意図どおり`m3_qualified:false`です。

| Shape / 可視node | SQL p95 ms | AGE p95 ms |
|---|---:|---:|
| Chain / 12 | 28.14 | 116.54 |
| Fanout / 12 | 36.30 | 140.71 |
| Multiseed / 12 | 83.66 | 146.98 |
| Chain / 64 | 29.00 | 238.18 |
| Fanout / 64 | 89.64 | 1,018.09 |
| Multiseed / 64 | 78.09 | 464.18 |

完全照合は有効なまま（AGE wall timeの24.3–61.8%）で、native path queryは33.4–61.7%です。
製品CLI/照合/構築/ANALYZE/commitを含む公開は542.01–1,037.13 ms、
投影1,466,368 bytes、DB12,244,671→22,943,423 bytes、生成WAL15,658,840 bytesでした。
新しい観測であり、version metadataの変更が探索を高速化したとは主張しません。
共有native ARM host、workload6/2 CPUs＋VMごとoverhead1 CPU、24/8 GiBと、
warm/静止/非HTTPの宣言制限を維持します。
非公開証跡は`.review-artifacts/graph-resource-v0.2.0-4204892/`です。
releaseは独立した認定契約を集約しますが、
旧/現行artifactのM3全体flagを起動許可へ書き換えません。

## 旧v0.1.3 native graph資源profile

独立oracleへの一致をSQL/native AGE対測定の資源評価より先に要求します。
固定recipeは可視node 12/64の六層と同数の非公開scope、
各3 warmup/30測定組、DB6 vCPU/24 GiB・app2 vCPU/8 GiBです。
warm・静止状態のservice callであり、HTTP、全量S、同時負荷、model性能の評価ではありません。
各backend/層のgateはp95 **1,500 ms未満**です。
初回非exact development runは396 samples、probe 36件、error 0で全時間gateを通過しましたが、
未認定のまま、元reportと後続raw証跡再判定を分けて保持します。

診断では完全な鮮度照合がAGE wall timeの約24–42%、native path queryが55–63%でした。
stage時間の合計比であり、percentile同士の比やhardware-counter profileではありません。
planner/権限の緩和や製品変更は不要でした。
mutation counterへ黙って置換せず上限付き完全照合を維持し、
拡大配置には別途宣言したprofileを要求します。commit固定のexact測定は以下のとおりです。
[runnerと範囲](operations/README-jp.md#native-graph-resource-profile)を参照してください。

### Exact warm graph run: d1b894d

clean archive **`d1b894dd4b5faa67c2e251604d1d9e9012cf7ba7`**のrunは、
宣言profileで**396 samples・意味契約probe 36件・error 0**となり、
`resource_qualified:true`、`m3_qualified:false`です。
後続の厳格なraw probe/digest再判定も成功し、別記録として保持します。
元reportを上書きしたり、新しい時間sampleを作ったりしません。

| Shape / 可視node | SQL p95 ms | AGE p95 ms | 投影node / revision | 公開 ms | 投影 bytes |
|---|---:|---:|---:|---:|---:|
| Chain / 12 | 22.30 | 111.89 | 24 / 24 | 599.98 | 163,840 |
| Fanout / 12 | 39.14 | 146.37 | 24 / 66 | 580.63 | 172,032 |
| Multiseed / 12 | 73.80 | 148.62 | 24 / 50 | 1,052.32 | 172,032 |
| Chain / 64 | 24.58 | 242.73 | 128 / 128 | 633.82 | 245,760 |
| Fanout / 64 | 73.37 | 1,292.52 | 128 / 482 | 662.30 | 417,792 |
| Multiseed / 64 | 68.63 | 705.78 | 128 / 258 | 652.75 | 294,912 |

このshapeではAGEはSQLより遅く、絶対上限の達成を**高速化とは主張しません**。
完全照合はAGE wall timeの24.2–42.3%、native path queryは54.5–62.9%で、
AGEの各readは19 statementsです。完全照合は有効なままにします。
公開時間はCLI起動/artifact照合/物理構築/policy導入/ANALYZE/commitを含み、単なるgraph挿入ではありません。
artifactは13,910–197,749 bytes、投影総量は1,466,368 bytes
（heap 507,904、index 540,672と補助storage）です。
DBは12,244,671→23,172,799 bytes、生成WALは15,808,832 bytesでした。

環境は共有Apple Container host上のnative Linux/aarch64、PostgreSQL18.6、pgvector0.8.6、
修正AGE72707aa固定image
`sha256:5edc81da67cf0a6f5620119dda3077de5d5b972d4ef214faeff89dfedd160a79`です。
設定したworkload割当はDB6/application2 CPUs、24/8 GiBですが、
Apple Containerは別に**VMごと1 overhead CPU**を記録し、applicationからは3 CPUsが見えます。
占有8-core hostの容量測定ではありません。
JITは既定threshold100000で有効、shared_buffers128 MiB、work_mem4 MiB、
statement/lock上限5秒を維持し、hardware perf counterは取得していません。
runtimeはUID10001・非owner/NOSUPERUSER/NOBYPASSRLSでlabel RLSも強制します。
build identity、raw timings、割当inspect、入力hash、cleanup結果は
`.review-artifacts/graph-resource-d1b894d/`へ非公開で保持します。
同時writer、物理host cold、全量S corpus同居、artifact上限規模への認定には拡張しません。

## v0.1.3 / schema 20 AGE隔離復元

明示的な復元＋無効化optionは署名付き最新状態/canonical照合の全要件を維持し、
その後、同じtransactionで一致した投影を無効化します。
最終照合は意図したregistry差分だけを報告し、全状態一致とは偽りません。
新規36契約は成功/無変更、認証/系統/CAS/本文/履歴変更拒否、callの単調性、隔離、
revision上限、原子的rollback、CLI出力を扱います。
現行sourceからの再生成とpublishは別の管理操作です。
限定復元の拡張で、自動再起動、新本文の任意取込み、一般HA/PITR認定ではありません。

全AGE catalog復元は明示的に**未認定**です。初回の実dump/restoreは正しい無効化まで進みましたが、
再構築が`ag_graph_graphid_index`重複で失敗しました。失敗証跡を残し、
canonical復元をAGE割当/catalog状態の復元成功へ読み替えません。
canonical-only経路は派生物理構造を破棄し、信頼済みextensionの新規導入と、
明示的・条件付きの欠落graph公開を使います。
実arm64 development runは復元前にsourceを削除し、canonical 35 fingerprintを照合、
鍵/ID/旧世代を維持してregistry 1→2→3を進めます。
disabled HTTP409、再構築後の現行/履歴native-SQL一致、purge 3件拒否、reader失効を確認し、
model呼出しは0です。offline 30契約、無効化36契約、新規22/既存22の公開caseも、
開発段階で別途成功しました。

その後、固定`5a21728`の
[run35725941968](https://github.com/rioriost/pg_agmemory/actions/runs/35725941968)が両architectureで成功しました。
core 2,335 passes/optional 113 skips、通常v7復元、
AGE profile 84契約/元の59確認/enabled 207件、実HTTP、canonical-only復元を含みます。
新復元両reportは固定commitと意図したregistry fingerprint差分一件を確認し、
full-AGE catalog復元、自動再起動、M3全体認定を主張しません。

## v0.1.2 / schema 20修正済みAGE

完全一致`0741713`の[run35709955807](https://github.com/rioriost/pg_agmemory/actions/runs/35709955807)は
core/AGE両native architectureが成功しました。
coreは各2,269 passes/optional 91 skipsとv7完全一致復元、
AGEはprofile 84契約、元のprobe 59確認、enabled 185件、実HTTP smokeが成功しました。
旧`6f2a8fa`のdescription lint失敗と折返しだけの修正もSTATUSに保持します。
この証跡は後続v0.1.3復元変更を含みません。

AGE source `72707aab7ce982bf13cad3d102bd869dab07d64b`を別途固定し、
変更していないnative/direct 19確認と固定template 40確認をすべて通過しました。
enabled-profile runnerは統合185件と非root実HTTP smokeが成功し、
native 2-hop/SQL一致、原子的公開/再構築、disable/stale拒否、明示SQL選択を扱います。
fixture代用品でなくimageに導入したpreload helperを使用します。
source/build identityと初回統合fixture失敗も、旧rc0失敗と分けて保持します。

任意AGEの正しさ/packaging証跡であり、全量S graphの費用benchmarkではありません。
native鮮度確認は上限付きですが定数時間ではありません。
現行復元は運用24 fingerprintで、enabled registry tenantは書込み前に拒否し、
active投影DR/自動再起動は範囲外です。固定amd64/arm64のcore/AGE jobは別の配布gateです。
[範囲と配置](operations/README-jp.md#patched-age-enabled-profile)を参照してください。

## v0.1.1 / schema 19 canonical graph artifact

完全一致`dc56d006edd0618dec05ec9cc6df0d3f6623f3c4`の
[native run 35692443083](https://github.com/rioriost/pg_agmemory/actions/runs/35692443083)は、
amd64/arm64とも2,132 passes/optional 31 skips、artifact製品smoke、完全一致v6復元が成功しました。
pytestは1035.55/1401.69秒で、artifact両reportはnode 3件/revision 2件、2,713 bytes、
同一再構築、source変更拒否を示します。復元両reportもpayload fingerprint 35件、拒否20件、
call予約11件と不変stale/非serving receiptを保持します。
後続schema 20 AGE追加をこの過去runで認定したことにはしません。

backend非依存artifact exporter/checkerに対象36契約（offline 6件・DB 30件）を追加しました。
非root製品imageのsmokeはnode 3件・relation一つの両revisionを扱い、
非公開・本文なしの出力、digest記録、同一bytes再構築、後続source変更の拒否を確認します。
決定論的なgraph build入力の構築/照合であり、新しいAGE実行、runtime権限cache、
serving認定ではありません。下記schema 19 coordinatorと汎用receiptの意味は維持し、
migrationやmodel呼出しも追加しません。
[artifact範囲](operations/README-jp.md#canonical-graph-artifacts)を参照してください。

## Schema 19世代metadata

M3 coordinatorのlifecycle/復元対象44件と、schema 19のmigration/互換性を確認しました。
development v6の実backup drillは非空の世代receipt一つを保持し、
削除/ACL replay後の入力をstaleとし、artifact検証/servingを無効に保ちます。
運用照合は23 tableで、世代履歴は完全一致を要求し、replacement rowとして取り込みません。
metadata契約の確認であり、AGE認定やextension上のprojection再構築ではありません。
完全一致`a017ae5`の[native run 35689800671](https://github.com/rioriost/pg_agmemory/actions/runs/35689800671)は
amd64/arm64とも2,096 passes/optional 31 skips、packaged smokeとv6復元が成功しました。
旧`50ed5e2`のsmoke失敗もSTATUSに保持します。この配布証跡はartifact追加前のもので、
その追加分の認定とはしません。
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
