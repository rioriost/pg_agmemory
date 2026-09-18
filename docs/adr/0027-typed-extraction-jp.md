# ADR 0027: 上限付き・原文根拠付きの型付き抽出

[English](0027-typed-extraction.md) | [推論 provider 基盤](0024-selectable-inference-jp.md) | [capture 受付](0026-scope-capture-policy-jp.md)

- 日付: 2026-09-18
- 状態: 実装済み。コンテナ検証結果は別途記録
- 境界: 推論の追加依存機能であり、**M2 完了ではなく**自動公開でもない

## 背景

選択可能な provider は既に要約と embedding を生成できるが、いずれも
正確な原文位置を持つ型付き assertion 候補を返さない。抽出には、memory の
権限をモデルが指定できない、上限付きで未知フィールドを拒否する出力が必要である。
原文の完全一致引用でも、predicate/value の意味的な裏付けは証明できない。

観測されたローカルモデルの応答は、正しい引用に誤った終了 offset を付け、
設計どおり拒否された。モデルに文字数の計算を求めると、引用の選択を不確かな
算術に依存させてしまう。このため wire proposal から位置を除き、信頼する adapter
コードで解決する。モデルの誤った span を修復するものでも、品質認定でもない。

## 決定

`InferenceProvider`、`HTTPProvider`、`AzureAIProvider` に
`async extract(data: InferenceInput) -> ExtractionResult` を追加し、
operator CLI を提供する。

```sh
printf '%s\n' '{"text":"東京はデプロイを承認していない。"}' |
  pg-agmemory infer extract --config operator-provider.json
```

text model を設定した既存の operator 管理 profile を使用する。
入力は引き続き閉じた `{text}` で、元の空白・Unicode・UTF-8 digest を保持し、
既存の 65,536 文字 / 256 KiB 上限を適用する。この adapter 機能自体は、
設定、依存 package、route、SDK method、MCP tool、DB schema、job、
公開経路を追加しない。core-only import は optional な `providers` extra に
依存しない。summarize/embed の request/result は変更しない。
`configured_operations` / `inspect.operations` は対応時だけ既存操作の末尾に
`extract` を追加する。
既存の HTTP 要約指示は内容を変えず `SUMMARY_SYSTEM_PROMPT` として公開する。
`EXTRACTION_SYSTEM_PROMPT` と合わせて、operator は指示文を重複定義せず実際の内容を
pin できる。Azure の要約 prompt の文言は変更しない。

### 閉じた候補・結果契約

共通 memory model ではなく `pg_agmemory.providers` に以下を定義する。

公開の位置付き候補・結果契約は変更しない。
provider の wire 契約には、独立した 4 フィールドの proposal を使用する。

| 型 / フィールド | 契約 |
| --- | --- |
| `ExtractionProposal` | `subject`、`predicate`、`value`、`evidence_quote` のみ。文字列の制約は下記と同じ |
| `ExtractionProposals.candidates` | 必須配列、4 フィールドの proposal 0–16 件。モデル応答専用 |
| `ExtractionCandidate.subject` | 既存 `ShortText`、1–256 文字 |
| `ExtractionCandidate.predicate` | 既存 `Predicate`、`^[a-z][a-z0-9_]{0,63}$` |
| `ExtractionCandidate.value` | 1–4096 文字 |
| `ExtractionCandidate.evidence_quote` | 1–4096 文字 |
| `ExtractionCandidate.start` | strict 整数 0–65535、原文の開始位置を含む |
| `ExtractionCandidate.end` | strict 整数 1–65536、原文の終了位置を含まない |
| `ExtractionCandidates.candidates` | 必須配列、候補 0–16 件 |
| `ExtractionResult` | 候補と operator 指定 `TextModel`、正確な `input_digest`、`status: "untrusted"` |

すべての object は未知フィールドを拒否する。候補文字列は trim / 正規化を
行わず元の文字を保持し、UTF-8 として encode 可能で、空白のみであってはならない。
offset の boolean、数値文字列、浮動小数は拒否する。
offset は **Unicode codepoint** 数であり、UTF-8 byte、UTF-16 code unit、
書記素 cluster 数ではない。`0 <= start < end <= len(original_text)` と
`original_text[start:end] == evidence_quote` を満たす必要がある。
subject/value はそれぞれ同じ引用内の大文字小文字を区別した完全一致の部分文字列
でなければならない。引用内の **subject または value** の出現回数は 1 回に
限らない。結合文字列や全角文字は正規化しない。

6 フィールドすべてが同一の重複候補は **response 全体を拒否**する。
暗黙の重複除去や正常部分だけの採用は行わない。既存の strict な 6 フィールド
parser の呼び出し側では、同じ引用でも offset が異なれば別の位置付き候補となる。
同一の 4 フィールド wire proposal も response 全体を拒否する。
候補順序は維持する。空配列は有効な abstention
（抽出見送り）であり、retry / fallback は発生しない。

**wire proposal** の `evidence_quote` は、正規化していない原文に **ちょうど 1 回**
現れなければならない。adapter は完全一致箇所を探し、その開始位置から 1 codepoint
後ろを起点にもう一つの一致がないか確認して、不在または曖昧な引用を拒否する。
重なった一致も検出する。例えば `ababa` 内の `aba` は 2 回である。
この確認後にだけ、信頼するコードが Unicode codepoint 単位の `start` / `end` を計算する。
prompt は前後の文脈を含む一意の逐語引用、または abstention を求め、
モデルに位置の計算は求めない。最初の一致を勝手に選んだり、引用を拡張・正規化したり、
query/gold の文脈を使ったり、offset を修復したりしない。
**wire に `start` または `end` があれば、数値が正しくても error とする。**

モデルへ渡す schema が許可するのは **`candidates` だけ**である。
モデル識別子、digest、trust status、ID、source ID、scope、ACL、permission、
approval、explicit intent、time、confidence、位置フィールドは指定できない。
配列要素は proposal の 4 フィールドだけである。
adapter が digest を計算し、設定済み text-model identity を付与する。
上流の model alias は model/revision の真正な証明ではない。
推論を await する前に入力と model の snapshot を作り、呼び出し側の後続 mutation
によって送信済み request の identity が変わらないようにする。

`extraction_schema() -> dict[str, Any]` は `ExtractionProposals` から共通 strict
**wire** schema を生成する。
`parse_extraction_proposals(response: Any, data: InferenceInput, model: TextModel)
-> ExtractionResult` は 4 フィールド proposal と引用の一意性を検証し、
位置を付けた 6 フィールド辞書を既存の strict parser に渡す。
`parse_extraction(response: Any, data: InferenceInput, model: TextModel)
-> ExtractionResult` はサイズ制限、閉じた候補、重複、原文 slice の一致を
検証してから binding フィールドを付与する。引き続き 6 フィールドを必須とし、
誤った span は修復せずに拒否する。公開処理や既存呼び出し側の検証契約は維持する。
source を持たない result model の構築だけでは原文照合できず、provider method は
proposal parser の後にこの strict parser を使用する。
重複 JSON key、末尾の余分な内容、NaN/Infinity、不正 Unicode、
過剰な nesting は fail closed で拒否する。

`EXTRACTION_SYSTEM_PROMPT` の変更は、既存 `WorkerProfile` の抽出 prompt digest、
ひいては profile digest に自動的に反映される。新しくロードした worker を使う前に、
operator は `worker --provider-config FILE --print-profile-digest` で新 digest を取得し、
最新の administrator CAS を使って scope synthesis policy を明示的に再 pin する。
policy/profile 照合の迂回や、過去の失敗・課金不明な呼び出しの暗黙 retry はしない。
要約・embedding の動作と原文 hash は変更しない。

### 信頼境界

指示文は原文を **データ**として扱い、元の言語・否定・不確実性を保持し、
権威ある事実ではなく候補を要求する。埋め込まれた指示への追従、tool 呼び出し、
memory 公開を禁止する。prompt の文言は injection 耐性や意味的正しさの証明ではない。

server-side 検証が保証するのは **字面の根拠だけ**である。例えば `approved` は
`not approved` 内に存在するため、意味的に誤解を招く候補でも字面の検証を通過し得る。
predicate は構文制約があるだけで、validator が意味を推論・証明するわけではない。
parse 成功から confidence、approval、intent、公開権限、真実性は導けない。
下流の review / 受付 / 公開設計はこの境界を維持し、source access と権限を
独立に検証しなければならない。

### HTTP provider

loopback local HTTP と HTTPS OpenAI-compatible adapter は、
`response_format.type: "json_schema"` と 4 フィールド proposal model から生成した
`{name, strict: true, schema}` を使用する `chat/completions` request を 1 回送る。
抽出の `max_tokens` default は **4096** で、既存 `max_output_tokens` 設定が
1–4096 の範囲で上書きする。要約の default は既存の 1024 のままである。
最大候補数は検証上限であり、token 制限内で全フィールドを最大まで埋められる
保証ではない。

原文は user message とし、system 指示へ補間しない。
tool、stream、自動 retry、redirect、provider/model fallback は使用しない。
既存の serialized request 256 KiB、raw response 2 MiB、timeout、
transport/TLS、proxy 環境無効化、credential guard を維持する。
schema/prompt の overhead により最大サイズ入力が HTTP request 上限を
超える場合は、client 作成前に billing ambiguity なしで失敗する。

assistant choice はちょうど 1 個、`finish_reason: "stop"`、文字列 content が必要で、
空でない refusal、tool calls、旧形式の function call を拒否する。
provider が strict schema 準拠を主張しても、JSON と原文根拠をローカルで再検証する。
structured output 非対応は error であり、弱い request への切り替え理由にしない。

### Azure SQL

抽出は、明示的な `generate` mode と互換 `azure_ai.generate` catalog signature が
ある場合 **のみ**対応する。既存の extension/version/member/overload/result/
privilege 検証、専用 TLS `verify-full` 接続、statement timeout、parameter binding、
1 回の `MATERIALIZED` function 評価、SQL result 転送 2 MiB guard を再利用する。
raw source を `prompt` とし、設定 model、生成 JSONB schema、共通 system prompt を
bind する。互換 text/JSONB result も同じ候補・原文根拠検証後にだけ受け入れる。

Language 要約 mode は `extract` を advertise しない。generate function が DB に
存在していても、呼び出しは接続や SQL 実行前に
`provider_capability_unavailable` で失敗する。抽出を要約へ代行させない。
embedding-only profile も推論前に失敗する。generate の retry / output-token
parameter を捏造しない。既存 deployed contract に検証済みの制御 knob はない。

### 失敗と課金

不正、原文不一致、引用の不在・曖昧さ、上限超過、重複、refusal、truncation、
モデルが位置を指定した response、tool を含む response は
全体として失敗し、sanitized `ProviderFailure` の
`invalid_provider_response`、`retryable: false`、`billing_unknown: true` を返す。
原文、SQL、credential、provider 診断、不正 response は error にコピーしない。
既存の HTTP status/transport、SQL error 分類を維持する。
preflight / 非対応 mode の失敗で曖昧な課金を主張せず、SQL 送信後は
保守的に課金の不確実性を保持する。

adapter request 1 回や materialized SQL 評価 1 回は、課金対象の上流試行が
1 回である証明ではない。extension 内部動作、cancellation、logging、
retention、consent、model 品質、provider 予算強制はこの機能の範囲外である。
課金の不確実性を解消するためだけに request を繰り返してはならない。

## 検証と制限

synthetic test は HTTP 両 backend、Azure text/JSONB・両 product の契約、
実 disposable PostgreSQL 上の synthetic function、schema/catalog guard、
CLI dispatch、abstention、source/model/digest binding、
日本語・emoji・結合文字の codepoint、adapter が計算する offset、重複・重なった引用、
strict な 6 フィールドの呼び出し側契約、禁止された wire 位置・権限フィールド、重複、
上限、不正出力、billing uncertainty、非対応 mode での no-network を扱う。
有料 service の呼び出しや model download は行わず、live Azure extension の
互換性や抽出の意味的品質も認定しない。

コンテナ実行と正確な結果の記録は統合変更側の責務であり、この ADR は成功を
先取りしない。この adapter 依存機能だけでは M2、MVP、production 完了、
品質評価、human review の有効性、自動 capture/synthesis、memory 公開の
許可を意味しない。
