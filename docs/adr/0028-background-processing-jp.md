# ADR 0028: デフォルト拒否のバックグラウンド処理と working memory 圧縮

- 状態: 実装済み、**M2受入れは未完了**
- マイルストーン: v0.0.27 / schema 13、capability stage `m2-background-processing`
- 日付: 2026-09-18
- [English](0028-background-processing.md)

## 背景と認定の境界

**2026-09-19受入計画改訂:** [計画1.3節・17–18章](../PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)
が、本ADRの過去の人手品質/実task milestone条件に優先します。
下記schema 13のruntime、既定拒否policy、生成物の未信頼扱い、実測証跡は変えません。
M2は未完ですが、その理由は本体の復旧/会計/資源作業であり、人手採点の不足ではありません。

[ADR 0026](0026-scope-capture-policy-jp.md) が認定したのは capture の受付であり、
自動モデル実行ではない。[ADR 0027](0027-typed-extraction-jp.md) は型付きの未信頼
抽出を追加した依存機能であり、公開権限ではない。本マイルストーンは明示 opt-in の
永続的な抽出・embedding と、上限付き working memory 圧縮を追加する。
M2、MVP、人手品質ゲート、性能ゲートの完了を意味しない。

本書はschema 13契約であり、v26/schema 11の認定を流用しない。
実装`9458034a47b6f7c9901e569a32f198f56369fbf7`は
[CI 35322238611](https://github.com/rioriost/pg_agmemory/actions/runs/35322238611)で
両native architectureの各1,487 passed / optional live 8 skips、
packaged synthetic M2を含む全production smokeに合格した。
以前の`e4f5d76ad2a4919349054165ce531b92fa650818`の
完全quote指示と実SIGKILL復旧はlocal対象154 testに合格し、
実local modelの抽出/embedding/圧縮/復元を3 callで検査した。

別途固定した`101993a`実装ではsynthetic held-out 600問/50 groupと、
実Native APIの生成認可10,000 caseを測定した。正確なrun/model digest、失敗履歴、
public QA予算修正と結果は[EVALUATION](../EVALUATION-jp.md)に記録する。
ソフトウェア検査・synthetic測定は、人手assertion precision、重要claim fidelity、
実task 20件、backup復旧、M2全体の認定の代わりにはならない。

## 決定: 三つの権限を分離する

1. 現時点の scope 読取・書込権限は引き続き必須。
2. Capture policy は episode の受付を制御するが、モデルへの送信許可ではない。
3. 管理者が設定する独立した **scope synthesis policy** と、一致するローカル
   worker profile が特定のバックグラウンド recipe を許可する。ポリシーがなければ
   **拒否**する。既存の明示的 `remember`、構造化ジョブ、明示 provider adapter が
   自動モデル処理になるわけではない。

Worker が受理するのは `local_http` のみで、provider adapter が endpoint を
loopback に制限する。自動処理にリモート OpenAI 互換や Azure profile は選べず、
外部への fallback も追加しない。ただしローカルサーバー、モデル配置、ログ、
サーバーからの接続先は運用者の管理対象である。Loopback URL だけでサーバー内部の
動作まで証明できるわけではない。

Consent reference はポリシーと完全一致させるラベルであり、実際の同意の検証、
秘密情報・PII 検出、一般的なデータ持出し許可ではない。

## 管理ポリシーと固定 profile

`pg-agmemory scope-synthesis get|set` は `PGAG_ADMIN_DATABASE_URL` を使用する。
HTTP 認証情報や runtime DB ロールでは実行しない。Scope 管理と共通の特権接続、
schema/pgvector/role 検証、tenant session barrier を用い、barrier は commit と
コマンド結果の出力完了まで維持する。

未知のフィールドを拒否する policy object の既定値と上限は次のとおり。

| フィールド | 既定値 | 契約 |
| --- | --- | --- |
| `enabled` | `false` | 厳密な boolean |
| `profile_digest` | `null` | SHA-256 digest。有効化時は必須 |
| `consent_references` | `[]` | 正規化した重複なしラベル、最大 64。有効化時は空不可 |
| `publish_predicates` | `[]` | 重複なし predicate 名、最大 32 |
| `kinds` | `[]` | `extract`、`embed`、`compact` の重複なし部分集合 |
| `max_pending_jobs` | `20` | 厳密な整数 1–100 |
| `max_input_bytes` | `16384` | 厳密な整数 1–262144 |
| `max_output_tokens` | `1024` | 厳密な整数 1–4096 |
| `max_calls` | `100` | 厳密な整数 1–10000 |

リストは正規化・整列され、重複、不正 UTF-8、C0 制御文字を拒否する。
Consent label は trim 後 1–256 文字、predicate 名は既存の文法に従う。
比較は大小文字を区別する。Capture policy と異なり、synthesis のフィールドは
省略でき、その場合は既定値になる。`set` は置換であり、保存済みの値との merge
ではない。`enabled=true` でも `kinds=[]` ならジョブを受け付けない。

`set` は最新 tenant `expected_access_epoch` による CAS が必須。実変更は epoch
を一度だけ増加し、policy epoch と DB ロールを含む非公開の管理 before/after audit
を同一 transaction で保存する。同値設定も正しい CAS を要するが no-op である。
行がない状態で既定値を設定しても行・audit event は作らない。変更後に既定値へ
戻しても行は残す。Epoch 上限では no-op のみ可能で、実変更はできない。
FORCE RLS の policy table に対する runtime 権限は可読 scope の `SELECT` のみで、
管理 audit は非公開である。

Profile digest は正規化した全 `ProviderSettings`、recipe version、
`local-worker-v1`、**実際の extraction/summary system prompt** の SHA-256 を束縛する。
Prompt version label だけでは不足する。認証情報は値でなく環境変数名として設定する。
Text 処理には、policy 上限以下の
`max_output_tokens` の明示指定が必要。Embedding には embedding model が必要。
Profile 変更には管理者による意図的な再許可が必要である。宣言した model revision
だけでは、ローカルサーバーに読み込まれた重みを独立に検証したことにはならない。

承認済みローカルファイルと環境変数の DSN を使う運用例:

```bash
# 設定の識別子を計算する。モデル推論は実行しない。
pg-agmemory worker --provider-config local-worker.json --print-profile-digest

# PGAG_ADMIN_DATABASE_URL は運用者の秘密情報用環境から供給する。
pg-agmemory scope-synthesis get \
  --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID"

# 承認済み profile digest を含む置換ポリシーをレビューする。
# EXPECTED_ACCESS_EPOCH は最新の管理読取結果から得る。
pg-agmemory scope-synthesis set \
  --tenant-id "$TENANT_ID" --scope-id "$SCOPE_ID" \
  --expected-access-epoch "$EXPECTED_ACCESS_EPOCH" \
  --policy-file approved-synthesis-policy.json

# PGAG_DATABASE_URL と provision 済み worker subject を使い、管理ロールは使わない。
pg-agmemory worker --subject "$WORKER_SUBJECT" \
  --provider-config local-worker.json --once
```

Policy file は 64 KiB、provider configuration は 32 KiB が上限。デフォルト拒否に
戻すには `{}` を内容とする置換 policy file と新しい CAS を用いる。安全な管理エラーは
`{"error":{"code":...,"outcome_unknown":...}}`。変更結果が不明な場合は policy/epoch を
再読取し、古い CAS を盲目的に再送しない。

## 受付と公開インターフェース

| インターフェース | 結果 / SDK method |
| --- | --- |
| `POST /v1/processing` | 202 job receipt、`process_memory` |
| `GET /v1/jobs/{job_id}/candidates` | 未信頼 candidate review page、`get_extraction_candidates` |
| `POST /v1/jobs/{job_id}/candidates/{ordinal}/adopt` | 201 caller 宣言の reported assertion、`adopt_candidate` |
| `POST /v1/working/events` | 201 event receipt、`append_working_event` |
| `POST /v1/working/events/query` | Event page、`query_working_events` |
| `POST /v1/working/compact` | 202 job receipt、`compact_working` |
| `GET /v1/working/snapshots/{checkpoint_id}` | 型付き snapshot、`get_working_snapshot` |

変更操作は既存の idempotency-key 契約に従う。Event query は POST だが読取専用。
Processing は `{scope_id, source: {memory_id, revision}, kind}` を受け取り、
抽出の source は episode、embedding は episode または assertion とする。
Source は同一の許可済み scope 内で生存し、revision が完全一致する必要がある。
Assertion/checkpoint の依存参照と episode provenance も再検証する。
`ProcessMemory` と `CompactWorking` は、後述する結果が既知の明示再試行用に、
任意の UUID `retry_of` も受け付ける。
Capabilities は `auto_synthesis=false` を維持する。独立した
`background_processing` metadata が示すのは policy 付き opt-in であり、全体の
自動処理を既定で有効にするものではない。`human_quality_qualified=false` も明示する。

`Observe.auto_extract` / `Observe.auto_embed` は厳密な boolean で既定値は false。
要求したジョブと episode は一つの transaction で受付され、失敗時に一部だけ保存
しない。結果には任意の `synthesis_job_id` / `embedding_job_id` が加わる。
False の flag は canonical capture payload から省き、既存 request identity を維持する。
Atomic structured capture と batch capture はこれらの auto flag を拒否し、従来の
明示的な構造化ジョブの意味を保つ。

Embedding job は既存の canonical input、設定済み model、vector の検証と公開経路を
再利用する。`auto_embed` の対象は observe した episode であり、後から抽出された
assertion に自動的に処理を展開しない。Assertion の embedding には revision を
束縛した明示的な source 参照が必要で、生存性と現在の権限を再検証する。

明示 processing の replay/dedup より前に、現在の scope 権限、有効な policy/kind、
consent label、capture admission、source 生存性、参照、入力上限を検証する。
保存済み synthesis/capture policy が不正なら
503 `synthesis_policy_invalid` / `capture_policy_invalid` で fail closed、
synthesis の拒否は 403 `synthesis_policy_denied`。存在しない・アクセス不可の object
は既存の 404 境界に従う。入力には policy の UTF-8 byte 上限と既存の 65,536 文字上限
の両方が適用される。

Auto flag が一つでも有効な完全一致 observe replay は、旧 receipt を返す**前**に、
要求された kind ごとに現在の synthesis policy と source の適格性を再検証する。
そのため権限剥奪後の opt-in replay は拒否され、receipt で最新 policy を迂回できない。
Flag が無効な observe は互換性のため capture-only の replay 検証を維持する。
いずれの replay もモデル呼出しを追加しない。

Semantic processing identity は tenant/scope/principal、recipe、profile、正確な
source 参照、必要な compaction input identity を束縛するが、意図的に
**`policy_epoch` を除外する**。Job payload はその epoch を保持し、egress/公開時の
fence に使う。新しい idempotency key や policy epoch/予算の変更だけで、既存の
結果不明 call を新しい作業に変えてはいけない。本当に意味の異なる操作まで含めた
全期間の source ごとの一回限り保証ではない。

## 永続的な一回呼出し予算と公開時の再検証

1. 一致する profile の worker が provision 済み principal のジョブを取得する。
   Provider profile のない worker は構造化ジョブを処理し、モデルジョブを飛ばす。
2. 準備時に live lease、現在の access/deletion epoch、policy epoch、profile、
   source、recipe、canonical input digest を検証する。
3. Network I/O より前に、job を主キーとする `model_call` 行を transaction で
   永続予約する。Lease token、policy epoch、profile digest、tenant-HMAC input fingerprint、
   上限付き入出力 metadata を記録し、初期値は `outcome=unknown` /
   `billing_unknown=true` とする。
4. モデル呼出し前に DB session/barrier を解放する。モデルジョブの lease は 180 秒、
   provider deadline は最大 120 秒、worker の呼出し wrapper は 125 秒。
   Heartbeat は 20 秒ごとに短い独立 transaction で lease/epoch を再検証する。
   Heartbeat 失敗で待機を終了しローカルの call task を cancel するが、送信済みの
   provider 作業は取り戻せない。Provider task とその待機は DB 接続を所有せず、
   network I/O の間 session lock を保持しない。
5. 別 transaction で公開し、現在の権限、epoch/lease/policy/profile/source を再検証する。
   設定済み model と返された input digest の一致が必要。公開内容、call 結果、
   job 成功状態は原子的に commit する。

DB guard も予約時の最新 policy、lease、上限を検証する。専用の transaction-level
advisory lock で scope の予約を直列化し、policy は通常の `SELECT` で読む。
`UPDATE` grant や更新用 row lock は使わない。`max_pending_jobs` は scope の
pending/running 数を制限し、`max_calls` は job owner を跨いで scope の
**policy epoch ごと**に永続予約を数える。失敗・結果不明も含む。日次リセット、
通貨単位の課金保証、実測 token 使用量の保証ではない。実際の synthesis-policy
変更は新しい予算 epoch を作るが、無関係な tenant access-epoch 変更では reset しない。
新予算も semantic job identity を変えず、結果不明作業の replay を許可しない。
Source/job の purge でも永続予約の予算は払い戻さない。

予約済み job ごとのアプリケーションレベルのモデル呼出し試行は最大一回。
予約後の crash では実呼出しが **ゼロ回**か、完了したが結果不明かを区別できない。
予約のある期限切れ job を再取得すると、再呼出しせず `billing_unknown` で終了する。
Provider error 自体が retryable でも、モデル失敗の自動再試行は行わない。
自動 fallback もない。

同じ processing/compaction API への明示 request で `retry_of` を指定すると、
新しい子 job を作成できる。親は failed で現在の principal に属し、
recipe/profile/source 参照を含む信頼できる semantic intent が一致する必要がある。
以前の call が `outcome=unknown` **または** `billing_unknown=true` なら
409 `job_retry_unknown` で拒否し、強制 override はない。以前の call がない場合、
または結果が既知で課金の曖昧さがない場合は、現在の全受付検証を満たしたうえで
この検査を通過できる。子は独自の一回呼出し予約を持ち、永続予算を消費する。
再試行で親の accounting は消えない。結果が既知の失敗に対する子 job は現在の
policy epoch を保存するが、結果不明の retry は policy 変更後も拒否する。
本当に異なる profile/intent は同じ retry ではない。Job の `call` /
`processing_result` を確認してから
意図的に新しい処理を承認する。従来の構造化 job retry 契約は本契約の代わりにならない。

呼出し中の権限剥奪、cancel、削除、policy 変更は公開を阻止できるが、既にローカル
provider へ渡した byte は取り戻せない。Network 待機中に tenant DB barrier は
保持せず、外部実行・課金の exactly-once を装わない。

## 抽出: 字句ゲート、隔離、人間の権限

Candidate は [ADR 0027](0027-typed-extraction-jp.md) の上限を維持する。
最大 16 件、Unicode codepoint の `[start,end)` と一致する引用、引用内の
subject/value の字句的一致、`status="untrusted"` である。モデルは scope/source ID、
承認、時刻、provenance 権限、ACL 変更を供給しない。Worker が信頼できる job input
から参照を与える。

最初の real-model extraction は `invalid_provider_response` で失敗した。
別の Bob source による診断では引用は完全一致したが、end offset が正しい 28 でなく
16 だった。明示的に実行した二つの model/診断の失敗 call は accounting に残し、
自動 retry や三回呼出し pipeline の成功として扱わない。

実装済みadapterのモデル向けproposalは
`subject`、`predicate`、`value`、`evidence_quote` のみとし、host が元 input 内に
引用が**一度だけ完全一致**する場合に限り `start` / `end` を導出する。
存在しない引用、重複・重なりによる曖昧な一致は、先頭を選ばず拒否する。
その後は変更していない厳密な `parse_extraction` を適用し、公開 candidate/result は
六 field の Unicode codepoint span 契約を維持する。曖昧一致や意味的権限の付与ではない。
その後、subjectを省略したquoteへの指示を明確にし、`e4f5d76`の3 call lifecycleが
成功した。prompt変更でbackground worker profile digestも変わるため、
管理者の明示再許可が必要になる。過去の失敗を遡及して認定しない。

自動公開には policy の allowlist と、次の組込み predicate の**両方**が必要:
`preferred_language`、`preferred_editor`、`preferred_theme`、`preferred_format`。
Source input 全体と evidence quote の**両方**が、次の完全一致形式でなければならない。

```text
subject / predicate: value
```

前後の空白は不可で、value は `[\w .+#/-]{1,128}` に一致する必要がある。
長い source の一部だけがこの形式に一致しても不十分である。同じ subject/predicate に
異なる value を提案した candidate 間の曖昧さも、自動公開を阻止する。
保守的な deny-pattern で承認・権限、秘密情報、実行、否定、不確実性に関する語も
拒否する。これは意図的に厳しいリテラルゲートであって、**意味的な含意の証明**、
同意検証、網羅的内容検出、測定済み precision 保証ではない。通常の自然言語による
記述は原則として隔離される。

同じ subject/predicate/value が既にあれば duplicate とし、assertion や provenance
の寿命を追加しない。既存の value と競合すれば隔離し、worker は上書きしない。
新しく公開した assertion は `epistemic_status="inferred"` /
`explicit_intent=false` とし、未信頼の model/recipe/prompt-digest/input-digest/source derivation
を持つ。Candidate 記録には ordinal、disposition（`published`、`duplicate`、
`quarantined`）、reason、必要ならサーバーが作成した assertion ID を保存する。

### 明示的 candidate adoption は確認済みの人間の承認ではない

GET は未信頼 proposal、信頼できる input 参照、extraction derivation を返す。
専用 adoption POST は厳密な `explicit_intent=true`、`expected_input_digest`、
`reason` と通常の idempotency key を要求する。成功済み extraction job、
quarantined candidate、現在の source 権限、canonical source digest/evidence span
の一致が必要で、競合時は fail closed とする。

サーバーは **reported** assertion を作成して caller の宣言を記録するが、
人間のレビューを証明しない。Lineage には
`source_class="caller_explicit_adoption"` と `human_review_verified=false` を含め、
episode、span、model、prompt、profile、recipe、job の由来を保持する。
Assertion/actor ID はサーバーが割り当て、モデルから権限のある ID として受け取らない。
Adoption は承認や ACL 権限を与えず、別 assertion を上書き・supersede しない。

元の candidate disposition は `quarantined` のままで、独立した
`adopted_assertion_id` / `adopted_by` が一度の採用を示す。再採用は生存する既存の
adopted assertion を返し、二つ目を作らない。既存の明示 remember/revision 操作は
別途利用できるが、candidate lineage を保持する本インターフェースの代わりではない。
Adoption も自動 literal publication も、人手品質ゲートを満たした証拠にはならない。

## Working event、正確な state、上限付き圧縮

Event は有効な既存 run/branch 上で、同一 scope の生存 episode を参照する。
サーバーが branch を lock して連続 sequence を付与し、caller は番号を指定できない。
同じ branch への同一 source の追加は dedup する。Stream 上限は 1,000 event。
Query は sequence 昇順、排他的 `after_sequence`、page は 1–100 件（既定 20 件）。

圧縮は `expected_head` と `through_sequence` を指定する。この実装の対象は完全な
prefix **1..N、N ≤ 100** のみで、任意の rolling window ではない。Event が欠落・
不可視なら無効化する。旧 checkpoint 参照と対象 episode 参照の和集合は最大 100
object で、旧 checkpoint 自身をさらに処理依存参照へ含める。

要約入力はサーバー所有の参照と coverage digest を持つ canonical event text。
公開時に入力と旧 checkpoint checksum を再確認し、CAS で head を進める。
保存済み typed state は goal/constraints/approval/tool state、旧形式で省略された
optional field も含めて**完全に複写**する。モデルは state を書き換えられない。
独立した snapshot に未信頼 summary、信頼できる参照、coverage、model、
recipe/input digest、job ID、整合性 checksum を保存する。

Head が変われば古い公開を拒否する。対象 prefix より後に同時追加された event は
head を変えず tail として残せ、黙って取り込んだり失ったりしない。Tail 長の独立 CAS
はなく、圧縮で prefix/tail を削除することもない。Snapshot 読取では整合性、権限、
保存時・現在の epoch を再検証し、checkpoint と page 化した tail を返す。
Client は `next_after_sequence` に従い、一ページを完全な tail とみなしてはいけない。

### 明示的な after-compaction hook context

実装済みの hook input に任意の UUID `working_snapshot_id` を追加し、
**`event="after_compaction"` の場合だけ**受け付ける。運用者が独立した
`PGAG_HOOK_WORKING_SNAPSHOT_BUDGET_BYTES` を 1–65536 に設定して有効化する必要が
あり、既定 0 では snapshot context を無効にする。Hook input はこの予算や起動時の
scope allowlist を上書きできない。

Hook は Native version を検証して型付き `WorkingSnapshot` を取得し、tail の
source 参照を必須とする lexical recall を実行する。**Snapshot JSON 全体の serialize
結果**が独立した snapshot byte budget に収まる必要があり、従来の recall-pack budget
を拡張・置換しない。要求され、検証された snapshot がある場合だけ、出力に
`working_snapshot` を追加する。通常の hook request では既存の 4 field
（`status`、`event`、`result`、`error`）を変更しない。

Snapshot/checkpoint ID、設定済み scope allowlist、coverage、tail の scope/run/branch、
current/saved/tail/recall の access/deletion epoch の一致を検証する。Tail 参照の欠落、
必須 retrieval prefix の不一致は失敗させる。Page が続く tail は
`working_tail_incomplete`、異なる tail 参照が 16 件を超える場合、または設定の
`max_items` を超える場合は `budget_exhausted`。Snapshot/recall context の予算超過も
source を黙って落としたり切り詰めたりせず失敗させる。

Hook は最新 snapshot の発見、作業実行、行動の承認、圧縮実行を行わず、未信頼 summary
を正確な typed checkpoint state より上位の権威へ昇格させない。Epoch 検証は Native が
既に byte を配送した後の原子的な権限剥奪保証ではなく、host は従来どおり権限剥奪時に
context を破棄する必要がある。本実装と v0.0.27/schema 13 への version 更新は、
基盤の認定ではなく、引き続き**未認定**の統合マイルストーンに属する。

## 削除、upgrade、残作業

Purge closure は processing input/job、inferred・caller-adopted derivative、candidate publication、
snapshot/checkpoint、working event の依存を含む。Working source の purge は該当
run を無効化して working event を削除し、残りを欠番なしの prefix に見せかけない。
Closure 内の candidate/derivation、snapshot payload を削除し、圧縮 state から
削除済み source を黙って復活させない。予算強制用の内容を含まない call accounting
は保持する。Provider の log/cache、backup は別途の削除・復旧手順が必要であり、
DB purge はそれらの消去証明ではない。

Migration 012/013 は job schema を拡張し、policy/call/candidate/working table を
追加する。未公開の candidate-adoption migration 014 は
`013_working_compaction.sql` に統合して削除した。実際の対象は **schema 13** であり、
schema 14 の公開や、そこからの対応済み downgrade を意味しない。
Backup を取得し、旧 writer/worker を停止してから migration し、
schema 13 に一致する service/adapter を使う。Code revert だけでは DB downgrade
にならない。Migration 013 は nullable な episode `source_namespace` を追加するが、
過去の値を捏造しない。旧 episode も processing 時の厳しい namespace allowlist を、
列追加前の記録という理由だけで通過することはできない。Namespace に制限がなくても、
有効な synthesis、consent の一致、ほかのすべての受付検証が必要である。

上記の意図的な上限を完了宣言で隠さない。一般的な意味的公開 validator、
確認済みの人間の承認 endpoint、外部自動 egress、破壊的 log 圧縮、無制限 rolling
summary はない。上限を定めた生成 ACL 10,000-case 実験は成功したが、
人手precision/fidelity、実task replay、backup復旧、コスト受入れは未完了である。
4件の実process-crash/purge復旧は別途測定した。
[評価文書](../EVALUATION-jp.md) は、現在の scorer が測定できる内容と別途必要な
受入れ証跡を区別し、明示 opt-in の real-model 評価 harness と三回呼出し processing
smokeも説明する。測定済みdev/held-out/ACL、版ごとのpublic試行、失敗履歴と
成功したprocessing lifecycleを区別し、全体の成功を推定しない。
