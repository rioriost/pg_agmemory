# 人手評価の準備

[English](HUMAN_REVIEW.md) | [実測証跡](EVALUATION-jp.md)

## 目的と境界

このworkflowは**未採点の開発用予備評価**を用意するもので、M2受入れではありません。
人が原文と生成された主張・要約・回答を比較します。toolは出典の保持、比較表示、
記入結果の検証、未評価・判定不一致の集計を担当します。実際に人がレビューしたことを
証明したり、callerによる採用を人手承認へ変えたりする機能ではありません。

Wikipediaは原文が支持する事実や要約の評価に適しますが、記述が現実世界で正しいことの
独立した証明ではありません。百科事典の記事だけでは、実際のpreference更新、
未完了taskの保持、行為の許可、20件の実task replayを評価できません。
それらには別の利用許可済みtask資料が必要です。provider要約はworking-memory圧縮の
実験ではなく、未公開の抽出proposalは採用済み・自動公開済みの事実ではありません。

## 原文とライセンス

初回対象は[`examples/wikipedia-review-plan.json`](../examples/wikipedia-review-plan.json)に
固定したPostgreSQL、太陽系、原子時計の日英記事です。
小規模で意図的に選んだpilotであり、代表性を保証する無作為標本ではありません。
画像、記事履歴全体、私的会話は取得対象にしません。

記事名、言語、正確なrevision、寄稿者履歴へのlink、license link、取得時刻、
内容hash、変換内容を原文と一緒に保持します。古いrevisionのrenderには取得時点の
templateが入る場合があり、固定するのは取得bytesであって全templateの過去状態ではありません。
revisionの公開時刻を記事内の全事実の発生時刻とみなしたり、版の差分を人手確認なしで
意味的な訂正とみなしたりしないでください。

Wikipedia由来の本文は**CC BY-SA 4.0**であり、repositoryのcodeに付くMITとは別です。
Wikipedia contributorsへの帰属、正確なrevisionと履歴へのlink、
[license](https://creativecommons.org/licenses/by-sa/4.0/)、抽出・抜粋・modelによる
翻案の表示を保持します。翻案した本文を再配布する場合は適用される継承条件を守ります。
記事個別の表示も確認してください。本文の一般licenseが第三者の引用やmediaに
そのまま適用されるとは限りません。原文payloadはgitに同梱せず、
除外済みで非公開の`.review-artifacts/`へ保存します。

収集では[Wikimedia利用条件](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content)、
[User-Agent方針](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)、
[API etiquette](https://www.mediawiki.org/wiki/API:Etiquette)を守ります。
projectを名乗り、件数を限定した逐次requestを使用します。拒否・rate limit時は停止し、
browser偽装や制限回避を行いません。

## 評価packetの準備

repositoryのPython commandは対応するLinux container内で実行します。
選んだcommitの不変archiveを使い、完全なSHAを記録します。CLIは指定されたSHAを
記録しますが、checkoutやmodel weightの同一性を認証しません。operatorが確認します。
出力先は毎回新しいdirectoryを指定し、既存結果を上書き・自動再開しません。

```sh
python -m pg_agmemory.evaluation_wikipedia \
  --plan examples/wikipedia-review-plan.json \
  --output .review-artifacts/wikipedia-corpus

python -m pg_agmemory.review_pilot \
  --corpus .review-artifacts/wikipedia-corpus/corpus.json \
  --profile /private/local-profile.json \
  --implementation-sha FULL_40_CHARACTER_COMMIT_SHA \
  --output .review-artifacts/wikipedia-pilot \
  --reviewer reviewer-a --reviewer reviewer-b \
  --max-calls 24 --allow-local-model-calls
```

先に`.review-artifacts/`をownerだけがアクセスできるdirectoryとして作成してください。
collectorはmodelを呼びません。記事ごとに冒頭の完全な段落を最大6段落・12,000 UTF-8
bytesまで取得します。pilotは最初の空でない完全な段落を最大1,600 UTF-8 bytesで
選びます。長すぎる最初の段落を飛ばしたり、文の途中で切ったりせず停止します。

6抜粋それぞれについて、抽出、provider要約、定義QA、次の改訂日のQAの4回を計画します。
最後の質問は回答見送りのprobeですが、自動的な人手判定やgold answerは付けません。
抽出・要約は既存providerの既定sampling、QAはtemperature 0・seed 17です。
決定的な再現性の保証ではありません。embedding呼出し、Nativeへの投入、採用、
worker jobは実行しません。再利用するQA profileはembedding modelの固定IDも
要求しますが、このpilotはそのmodelを呼びません。

`journal.jsonl`は予約と未信頼request/responseを保持し、認証headerは記録しません。
`measurements.json`はcorpus、抜粋、profile、質問recipe、呼出し一覧、失敗を結びます。
不正出力も残し、その他のprovider・通信失敗では途中journalを保持して停止します。
失敗したrunを隠すための削除や、成否不明の呼出しの自動retryはしません。
`review/`にはescape済みのoffline HTML、digest付きpacket、原文用2票、
出力採点用の**未記入2票**を生成します。`readiness.json`は未評価件数を示し、
品質合格ではありません。原文・翻案には出典とlicense表示を保持し、MIT codeとして
再配布しないでください。

## 人が行う手順

1. **model出力より先に原文を読む。** 資料を利用できることを確認し、原文だけの表示から
   重要claim・制約を記録します。空の一覧を「要約fidelity 100%」の根拠にしてはいけません。
   確定した原文側の一覧と出力の採点は分離します。
2. **固定済み出力を独立して判定する。** 評価者ごとに別ID・別の評価票を使います。
   単語の一致だけでなく原文の前後を読み、否定、不確実性、人物、日時、数値、
   主張の適用範囲を確認します。
3. **理由と重大度を記録する。** 不明なcaseは推測で埋めず不明とします。
   架空の承認・権限、禁止の反転、重要な対象・時刻・数値の置換は重大扱いの検討対象です。
   正式評価の前に、領域に応じた重大度の基準を固定します。
4. **不一致を明示的に調整する。** 元の両評価を残します。暗黙の多数決、一方の上書き、
   難しいcaseの除外、採点後の原文・出力の変更はしません。

評価者IDは申告であって、人間の身元認証ではありません。評価者の適格性と手順は
operatorが承認する必要があります。人手評価を終えてもserviceのcandidate採用flagを
書き換えたり、記憶を公開したり、行為を許可したりしません。

### 記入するfileとoffline command

最初に`review/sources.html`と担当する`source-reviewer-N.json`だけを開きます。
各annotationの`important_claims`に、人が決めた`claim_id`、`text`、
`grounding`（`source_id`と原文に完全一致する`quote`、または`start`/`end`）を
追加し、原文側の作業を終えたら`status`を`complete`にします。
offsetは0始まりのUnicode code point、endは含みません。
未作業なら空のまま残し、保持率100%と解釈しません。この記録を保存するまでは
`outputs.html`や`packet.json`を開きません。file自体は閲覧順や独立性を強制できません。

次に`outputs.html`と`sources.html`を比較し、担当する`reviewer-N.json`の
`outcome`、`severity`、`rationale`、`source_references`だけを編集します。
`instructions.txt`にkindごとのlabel一覧があります。`uncertain`や生成失敗を含め、
記入済みの判定には理由、重大度（`none`/`minor`/`major`/`critical`）と
対応するsource IDを含む`source_references`が必要です。
出力採点のquote/spanは任意ですが、書く場合は原文に完全一致させます。
未評価の行は全欄を未記入のままにします。ID、digest、kind、原文、model出力は
変更しません。HTMLは閲覧専用であり、JSONをtext editorで記入します。

```sh
python -m pg_agmemory.human_review validate-source \
  --packet .review-artifacts/wikipedia-pilot/review/packet.json \
  --form .review-artifacts/wikipedia-pilot/review/source-reviewer-1.json \
  --out .review-artifacts/source-review-1
# 評価者2についても、別の新しい出力先を指定して実行します。

python -m pg_agmemory.human_review score \
  --packet .review-artifacts/wikipedia-pilot/review/packet.json \
  --form .review-artifacts/wikipedia-pilot/review/reviewer-1.json \
  --form .review-artifacts/wikipedia-pilot/review/reviewer-2.json \
  --out .review-artifacts/review-report
```

これらはofflineで、modelやDBを呼びません。検証するのはfileの対応と形式であり、
意味的真実や人の手順の実施ではありません。集計では両者の判定と不一致を残します。
原文側の一覧と要約のclaim別保持判定はまだ接続していないため、要約全体を採点しても
重要claim保持率は`NOT_MEASURED`のままです。

## 判断できること・できないこと

生成失敗、空の抽出、回答見送り、未評価・不明、不一致を比率と併記します。
分母は採点前に決め、未評価を成功に数えません。すべて回答を見送ることで
根拠なし回答が0件になっても、有用な回答品質を示したことにはなりません。

自動inferred公開、callerの明示採用、未信頼proposalは別cohortです。
閾値を満たすために、良好な低impact群を他の群へ混ぜてはいけません。

M2目標は、assertion support 95%以上かつ重大な架空事実0、
重要claimの圧縮fidelity 98%以上かつtyped state完全保持、
自然言語更新95%以上、回答の根拠なし断定2%以下かつ重大case 0です。
要約全体への一つの判定だけでは、claim単位の保持率の分母になりません。
小規模Wikipedia pilotでマイルストーン全体の比率を証明せず、
生成reportでM2合格を宣言しないでください。

pilotは判定基準の明確化と失敗原因の調査に使います。正式受入れ前には別途選んだ標本、
sampling単位、cohort、分母、model/prompt/source版、評定手順を固定します。
同じ記事の出力を独立した多数の証拠とみなさず、session/source group単位で
不確実性を報告します。
