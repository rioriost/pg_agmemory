# 初期sliceの運用

[English](README.md) | [プロジェクトREADME](../../README-jp.md) | [現在の契約](../STATUS-jp.md)

**本番runbookや検証済み災害復旧手順ではありません。**
この初期releaseでは、許可済み・除去処理済みの使い捨てtest dataを使用してください。
purge訓練、schema reset、restore実験を含む破壊的操作は、
使い捨てtest DBだけを対象とし、業務DBや実userの履歴には実行しないでください。

## 初期設定とrole分離

PostgreSQL 18と、repositoryの`Dockerfile`から構築したimageを使用します。
CLI名は`pg-agmemory`、import package名は`pg_agmemory`です。
ローカルcheckoutは`pg_agmemory`、GitHubは`rioriost/pg_agmemory`です。
上限付きmilestoneである**v0.0.10/schema 7のatomic structured capture**を実装しました。
**最終localとnative amd64/arm64検査に合格**しました。
migration 008/009/010や新依存はなく、project版/lock metadataだけを変更します。
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
v0.0.10は両stageに`hook`も維持します。`pg-agmemory[hook]`は
`httpx==0.28.1`を固定し、**MCP SDKは含めません**。
container検査scriptは使い捨てsmoke設定を含め、**Apple ContainerとDockerの両方**で
runner側の`jq`を必須とします。

| 設定 | 利用者 | 用途 |
|---|---|---|
| `PGAG_ADMIN_DATABASE_URL` | 管理CLIのみ | migration、offline全tenant lexical rebuild、private tenant/principal/scopeの作成 |
| `PGAG_DATABASE_URL` | API/worker runtime | `pgag_runtime`に所属する専用の制限付きlogin |
| `PGAG_JWT_PUBLIC_KEY` | API runtime | 2048 bit以上の静的PEM RSA検証公開鍵。署名用秘密鍵は渡さない |
| `PGAG_JWT_ISSUER` | API runtime | 信頼するissuerの完全一致値 |
| `PGAG_JWT_AUDIENCE` | API runtime | 本サービスのaudienceの完全一致値 |
| `PGAG_MCP_API_URL` | Local MCP adapterのみ | 信頼する固定Native API HTTPS originまたはloopback HTTP origin。URL credential/application path/query/fragmentは禁止 |
| `PGAG_MCP_API_TOKEN` | Local MCP adapterのみ | Native API audience用の固定bearer token。callごとでなく起動時に安全に渡す |

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

## Atomic structured captureの運用

**v0.0.10の最終localとnative両architecture検査に合格しました。**
上記に従いNative APIとruntime roleを準備します。
厳密なservice `0.0.10`、API `v1`、schema `7`を維持し、
実装済みstageは`m2-atomic-capture`であってM2全体の完了ではありません。
captureはNative routeであり、**MCP toolや自動recall-hook操作ではありません**。
schema 7 DBに新依存やDDLは不要です。

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
LLM/provider、抽出、自然言語synthesis、pgvector、新しい意味品質の適格性認定はありません。

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
6. 起動時に`GET /v1/capabilities`へ認証し、API `v1`、service `0.0.10`、schema `7`の
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
v0.0.10は両protocol時代、semantics、MCP上限、共有Native HTTP clientを維持します。
v0.0.9はlocalとnative Docker両architectureで合格しました。
v0.0.10の最終localとnative両architecture検査も合格しました。
これらの特定経路の検査は、未検証の旧clientや特定hostとの互換性を証明しません。
[全契約](../STATUS-jp.md#local-stdio-mcp)と
[ADR 0008](../adr/0008-local-mcp-jp.md)を参照してください。

## Implicit recall hookの運用

**既存の読取り専用hookです。v0.0.10最終localとnative両architecture検査に合格しました。**
vendor-neutralな一回実行のharness側commandであり、MCP、model caller、
自動登録host pluginではありません。Copilot/Claude/Codex連携を主張しません。

### 信頼するoperator環境からの導入と設定

1. 上記role分離に従ってNative APIのsubject/scopeをprovisionします。
   hookには**DB資格情報**、admin URL、JWT署名key、外部model keyは不要です。
   設定されたNative audience tokenだけを使います。
2. `pg-agmemory[hook]`を導入するか、`mcp`・`hook`両extraを含むv0.0.10 repository imageを
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
5. 呼出しごとに新しく認証付き`GET /v1/capabilities`で厳密なservice `0.0.10`、
   API `v1`、schema `7`を検査し、`POST /v1/recall`へ`mode: "implicit"`、
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

## v0.0.10のapplication更新（schema変更なし）

既存v0.0.7/v0.0.8/v0.0.9のschema 7 DBには**migration 008/009/010も新backfillもありません**。
application/schema版を記録し、backupと現在の削除/ACL記録を保全します。
自動再起動を含む旧API/worker/MCP adapterを停止/drainしてhook起動も止め、
対応するv0.0.10 imageへ置換します。
厳密なschema履歴`[1, 2, 3, 4, 5, 6, 7]`を確認後、制限付きNative API/workerを起動し、
認証付きcapabilitiesを検査して各固定identity adapter/hookを起動します。
schemaが同じでもrolling混在互換性や対応済みdowngradeの根拠にはなりません。
v0.0.10のpackage化runtimeと既存schema契約はlocalとnative Docker両architectureで合格しました。
過去の結果は[検証証拠](../STATUS-jp.md#検証証拠)に分けて記録しています。
旧schemaには以下の既存schema 7 migration手順を現在のimageで適用します。
reindexは別のoffline保守であり、MCP/hook commandではありません。

<a id="v003の保守migration"></a>
<a id="v004の保守migration"></a>
<a id="v005の保守migration"></a>
<a id="v006の保守migration"></a>

## v0.0.7の保守migration

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
2. 対応するv0.0.10 imageと**`PGAG_ADMIN_DATABASE_URL`**を使用し、forced RLS bypassと
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
5. 対応するv0.0.10 API/workerだけを再起動します。traffic再開前に許可済みtest dataで
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

これは台帳であり、worker、tool実行用harness adapter、provider照会client、承認サービス、
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

**v0.0.10の最終localとnative結果を2026-09-17 JSTに確認しました。**
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
