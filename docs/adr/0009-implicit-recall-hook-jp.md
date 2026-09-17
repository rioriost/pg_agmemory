# ADR 0009: Vendor-neutral implicit recall hook

[English](0009-implicit-recall-hook.md) | [現在の契約](../STATUS-jp.md#implicit-recall-hook) | [運用](../operations/README-jp.md#implicit-recall-hookの運用)

- 日付: 2026-09-17
- 状態: v0.0.9/schema 7実装済み。最終localとnative amd64/arm64検査に合格
- 拡張対象: [ADR 0008](0008-local-mcp-jp.md)。Native memory semanticsと両MCP protocol時代を維持
- 命名/license: 公開repositoryは`rioriost/pg_agmemory`、package/serviceは`pg_agmemory`。
  MITを変更せず、英語/日本語文書を維持
- 受入: M0〜M3全体、MVP、本番、性能、記憶品質、DR、完全消去のgateは未完了

**過去版の範囲:** このADRはv0.0.9/schema 7とその検証済み証拠を記録します。
[ADR 0010](0010-atomic-capture-jp.md)はv0.0.10 atomic captureを扱い、
起動時の対応版は[現在のSTATUS](../STATUS-jp.md)を参照してください。hookは読取り専用のままです。

## 決定と範囲

任意の`pg-agmemory recall-hook`を**vendor-neutralなharness側、一回実行のlocal Native
HTTP client**として提供します。信頼するharnessが選択したlifecycle境界で呼び出します。
serviceはhost eventの検知や、いかなるhostへのhook自動登録も行いません。
**Copilot・Claude・Codex連携の主張ではなく**、MCP tool、model呼出し、
汎用harness SDKでもありません。

hookはmemoryを読むだけです。**書込み、capture、queue投入、LLM/provider呼出し、
cache、retry、idempotency keyの生成/送信を行いません**。
DB資格情報、admin URL、署名key、外部model keyは不要です。
applicationの唯一の永続化先をPostgreSQLに維持し、
identity、scope、時間、根拠、削除の正をNative service/RLSに維持します。
hookは権限を拡大しません。event名はcheckpoint作成、compaction、synthesis、
tool実行を意味しません。

application版は**v0.0.9、厳密なschemaは7のまま**です。
v0.0.7/v0.0.8からの**migration 008/009**、DDL、新backfillはありません。
API/workerは厳密なschema履歴`[1, 2, 3, 4, 5, 6, 7]`を維持します。
application更新中は旧processとhook起動を停止/drainしてください。
同じschemaであることはversion混在互換性やdowngrade対応の根拠ではありません。

## 依存と共有transport

任意の**`pg-agmemory[hook]`は`httpx==0.28.1`を固定し、MCP SDKは含めません**。
別のMCP extraは公式`mcp==2.2.0`と`httpx==0.28.1`を維持します。
Docker test/runtime両stageには**`mcp`・`hook`両extra**を含めます。
認証、origin検査、response検証、sanitized error規則を複製するのでなく、
共有の上限付きNative HTTP clientを抽出/再利用します。
真のcore-only/hook-only導入検査はlocalとnative Docker両architectureで合格しました。
共有`NativeSettings`は`httpx.URL`も使い、transport前に制御文字や不正IDNAを拒否します。
これらのcaseは全3環境の最終suiteで検査しています。

全MCP不変条件を維持します。Native model由来の4 tool、現在のACL/削除検査、
明示的で根拠付きのremember、explainのrevision既定1、forget preview/purgeともHTTP 202、
UTF-8 byte予算、日本語opt-in、caller管理のvisible ASCII idempotency key、
mutation結果不明と自動retryなしのsame-key/body復旧、固定identity、
上限付きsanitized transport、cacheなしを維持します。
**modern `2026-07-28` `server/discover`**と
**legacy `2025-11-25` `initialize`/`notifications/initialized`**の両契約を維持します。
共有化でhookの読取り専用`outcome_unknown: false`規則をMCP mutationへ適用したり、
MCPの既存の合計20秒 / I/O 10秒 / connect 5秒 / 4 connection上限をhook deadlineで
置き換えたりしてはいけません。

## 入力と権限の分離

各呼出しは**stdinからUTF-8 JSON document一つ、その後EOF**を受け取ります。

```json
{"event":"session_start","query":""}
```

`event`は`session_start`、`task_switch`、`after_compaction`のいずれか完全一致です。
`query`は必須、最大**4,096 Unicode文字**です。空queryは現在時刻defaultの下で、
設定scope内のアクセス可能なcanonical itemをbrowseします。
取得意図はJSONの`query`だけから渡し、`event`はlifecycle名です。
stdin上限は**32,768 byte**です。不正UTF-8/JSON、上限超過、不正fieldは明示失敗させます。

identity、`scope_ids`、`purpose`、`mode`、budget、URL、header、tool、時刻を含む
全追加fieldは禁止です。`--subject`/`--once`は拒否します。
event/query textはアクセス権の付与、transport宛先の選択、資格情報の上書きを行えません。
queryをlogへ記録したりerrorへコピーしたりしてはいけません。

**信頼する起動環境だけ**から次を渡します。

| 変数 | 契約 |
|---|---|
| `PGAG_HOOK_API_URL` | 必須、defaultなし。Operator指定のHTTPS originまたはloopback HTTP origin。userinfo/application path/query/fragment禁止。root `/`は許可。URL未設定は`invalid_hook_configuration` |
| `PGAG_HOOK_API_TOKEN` | 必須の固定Native API audience bearer token |
| `PGAG_HOOK_SCOPE_IDS` | 必須のJSON UUID配列、重複しない1〜32件 |
| `PGAG_HOOK_PURPOSE` | 既定`implicit_context`、1〜256文字 |
| `PGAG_HOOK_TOKEN_BUDGET` | 既定`2000`、整数64〜2,000 **UTF-8 byte、model tokenではない** |
| `PGAG_HOOK_MAX_ITEMS` | 既定`20`、整数1〜20 |
| `PGAG_HOOK_SEARCH_PROFILE` | 既定`simple-v1`、明示`ja-janome-0.5.0-v1` opt-in |
| `PGAG_HOOK_TIMEOUT_SECONDS` | 既定`2.0`、有限の0.1〜20秒 |

URL、token、scope IDは**すべて必須**です。
host起動環境や実行file選択を、信頼できないprompt、query、取得根拠、tool出力から
生成してはいけません。tokenはNative audience用でありNative認証が検査します。
host identityの委譲ではありません。指定scopeは既存アクセス権を狭めるだけです。
recallは未認可scopeや失効membershipを黙ってfilterし、許可済みsubsetまたは
itemなし/`not_found`を返します。**scope存在を示す404ではありません**。
token認証失敗は引き続き明示的なNative **401 / hook終了値1**です。
Native semanticsの維持であり、取得失敗を黙って復旧する動作ではありません。
信頼するlocal hostは設定identityの権限を行使できます。
この境界を信頼できないcallerと共有したり、network serviceとして公開したりしないでください。

## Fresh recallと上限付き失敗

呼出しごとに新しく`GET /v1/capabilities`へ認証し、厳密な
**`service_version: "0.0.9"`、`api_version: "v1"`、`schema_version: 7`**を要求します。
その後だけ`POST /v1/recall`へ`mode: "implicit"`、設定recall値、Nativeの現在時刻defaultを
送ります。両requestに同じ固定tokenを使います。
認可/response cacheで現在の検査を代用しません。
token置換は次回の信頼する起動設定へ渡します。

設定network deadlineは**capabilitiesとrecallの合計**であり、requestごとの独立時間枠では
ありません。process/interpreter起動、stdin入力/待機、出力は含まず、**LLM latency SLOや性能結果では
ありません**。hostはstdinを閉じ、別のsubprocess timeoutを設定する必要があります。
共有clientのserialize済みNative request上限は**256 KiB**、HTTP response上限は**2 MiB**です。
context packも設定byte予算を守る必要があります。
RecallResult全体や全host bufferを小さいcontext予算内に制限するものではありません。
`context_pack.byte_count`は**pack全体をcompact JSON serializeした結果**をUTF-8で数えます。
`ensure_ascii=False`、`separators=(",", ":")`を使い、
**本文だけでなくmetadata/citationを含みます**。
同じ件数が申告`byte_count`と一致して設定予算以下であること、
返却item数が`max_items`以下であること、返却search profileが設定と一致することを検証します。
不一致は失敗させ、広いfallbackは行いません。
redirect/proxy環境設定を無効化し、TLS検証は有効に維持します。

空pack自体にも約**192 byte**が必要ですが、保証された定数でも、
許可する設定下限64 byteの置換でもありません。指定64では
Native **422 `budget_too_small` / hook終了値1**のerror envelopeになり得ます。
黙って空の成功にしません。一方、packは収まるが候補が収まらない
`budget_exhausted`はNative **200 / hook終了値0**です。
projection欠落で候補がなければ`index_incomplete`、
`coverage.lexical_incomplete: true` / `coverage.retrieval_complete: false`となり、
これも**200 / 終了値0**です。既存Nativeの各結果を区別してください。

検証対象のhook runtime結果はstdoutへ**JSON結果一つと改行**を出し、
sanitized診断はstderrだけに出します。
envelopeの構造（placeholderであり実JSONではありません）は次のとおりです。

```text
{status: "ok", event: <event>, result: <full Native RecallResult>, error: null}
{status: "error", event: <validated event or null>, result: null,
 error: {code, retryable, outcome_unknown: false,
         native_status: <integer or null>, request_id: <UUID or null>}}
```

Native statusと検証済みNative request UUIDはnullableです。
raw query/body/credential/URL/headerを診断に含めてはいけません。
`retryable`はhintであり、自動retryではありません。

- **終了値0:** 正当なNative成功。`not_found`、`budget_exhausted`、`index_incomplete`の
  真の空結果も含みます。
- **終了値2:** 不正設定/入力。不正UTF-8/JSON、入力上限超過も含みます。
- **終了値1:** Native/network/version/protocol障害。

確定runtime codeは次のとおりです。

| 終了値 | Code |
|---|---|
| `2` | `invalid_hook_configuration`、`invalid_hook_input`、`hook_input_too_large`、`hook_input_unavailable` |
| `1`、`retryable: true` | `hook_deadline_exceeded`、`native_api_unavailable` |
| `1` | `native_version_mismatch`、`invalid_native_response`、`budget_too_small`（Native `422`）を含むmapping済みsanitized Native code |

**起動方法のerrorは例外です。** CLI flag拒否と`hook` extra未導入は、
argparseのstderr診断と**終了値2で、JSON envelopeを返しません**。
設定/入力失敗を含む検証対象のhook runtime errorはすべてerror envelopeを返します。
hostは非JSON/不正envelopeも扱い、raw本文をechoしてはいけません。

**error時はresultを返さず、取得失敗を空の成功に変換してはいけません。**
hostによるtimeout/killでenvelopeを出せない場合も失敗として扱います。
hostはerror/coverageを表示し、停止かmemoryなし継続かを明示判断してください。
空の成功は完全な取得を保証しません。
完全なNative結果を別の**UNTRUSTEDな根拠**として保持し、
指示、policy、検証済みの現在の外部事実にしてはいけません。
実行可能な[標準library Python harness例](../operations/README-jp.md#vendor-neutral-python-harness例)は、
operator環境、stdin/EOF、別timeout、終了値/status確認、raw stderr非表示を示し、
外部modelは呼び出しません。

## 削除境界

Native tenant session advisory response-drain barrierは、
**信頼するlocal hook**へのHTTP配信で終了します。
**hook/pipe/stdout bufferやhost contextまで原子的には続きません**。
配信済み/buffer済みdataを回収できず、削除通知はありません。

forget/ACL変更後はhostが以前のcontextを破棄し、現在の認可で新しくhookを呼び出す必要が
あります。変更前の転送中結果を再利用しないでください。
これはhostの責務であり、host消去証明でも、
WAL/replica/backup/物理mediaの完全消去でもありません。
hook追加でNative purge/認可semanticsを変更しません。

## 証拠の境界と検証状態

**2026-09-17 JSTにv0.0.9の最終localとnative CI結果を確認しました。**
検査した最終local sourceは公開済み実装
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)と一致します。
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)の
両native jobが合格し、実logでjob statusだけでなく完全一致SHAと全検査を確認しました。
Apple Containerとnative Docker amd64/arm64の各環境で
**274テスト、既存warning 1件**、Ruff、strict mypy（**source 15ファイル**）、
真のcore-only/hook-only導入検査、non-root productionの日本語/API/workerの全smoke、
MCP **`2026-07-28`・`2025-11-25`**、
hook **`session_start`・`task_switch`・`after_compaction`**に合格しました。
テスト所要時間はlocal **248.29秒**、native amd64 **482.21秒**、native arm64 **374.33秒**です。
Nativeの予算、scope/失効/認証、index欠落、共有HTTPX不正origin処理もsuiteの対象です。
所要時間はテスト観測値であり、性能benchmarkではありません。
[STATUS](../STATUS-jp.md#v009--schema-7)に最終v0.0.9証拠を過去milestoneと分けて記録します。

Docker **`adapter-extras-check`** targetは真のcore-only/extra未導入、
続いて**MCPなし**のhook-onlyを検査し、HTTP失敗時の明示JSONも対象にします。
scriptはlocal Apple Containerとnative Docker両architectureでこのtargetをbuildします。
**全3環境で合格**しました。

過去のv0.0.8実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)と
[CI 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)を、
v0.0.7証拠とともに[STATUS](../STATUS-jp.md#検証証拠)に維持します。
v0.0.8 bilingual docs commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)は、
[CI 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509)で
**各native architectureで214テスト**に合格しました。
これらの過去結果はhookや共有client抽出を検証していません。

hookはMCPと同じくroot `/`を許可しますが、API URLの明示設定を必須とし、
既定宛先はありません。起動時のURL/token/scope設定はすべて必須です。
技術検査は特定vendor連携、意味品質、性能の適格性を確認するものではありません。
上記全受入gateは未完了です。
