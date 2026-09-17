# 現在の契約と制限

[English](STATUS.md) | [プロジェクトREADME](../README-jp.md) | [実装プラン](PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**v0.0.8/schema 7のlocal stdio MCPを実装しました。
ローカル/native Docker検証に合格しています。
M0/M1/M2/M3全体の完了、MVP完成、本番適格性の確認を意味しません。**
実装プランは将来の要求を示すもので、現在のAPIそのものではありません。
性能、記憶品質、災害復旧、完全消去の受入目標は未測定または未認定です。
ローカルとCIの検査が合格しても、これらのgateが完了したとは扱いません。

## 実装済みの範囲

durable job queueとlexical projectionを含め、アプリケーションの永続化先はPostgreSQLのみです。
外部memory DB、モデルサービス、外部queue、ファイルベースのmemory indexはありません。
Janome同梱辞書はsoftware依存であり、保存されたapplication memoryではありません。

| Endpoint | 現在の動作 |
|---|---|
| `POST /v1/observe` | caller指定の発生時刻・同意参照とともにepisodeを1件保存。revisionは`1`、`synthesis_job_id`は`null`で、job enqueueは行わない |
| `POST /v1/remember` | 同一scopeの読取り可能なepisodeからの原文引用を根拠とし、明示的に要求された構造化assertionを保存 |
| `POST /v1/jobs` | 構造化記憶publicationを明示queue化。`202`はjob参照であり完了ではない |
| `GET /v1/jobs/{job_id}` | 現在読取り可能なstate、安全なerror/時刻、正確な入力参照、元の結果revision 1を返す |
| `POST /v1/jobs/{job_id}/retry` | 全intentと現在のアクセス権を検査し、所有するfailed jobのchildを一つ作成/重複抑止 |
| `POST /v1/assertions/{memory_id}/revisions` | expected head、明示的intent、reason、revision固有のepisode根拠を使い、同一assertionへ全置換revisionを追加 |
| `POST /v1/entities` | episode根拠付きの不変・caller申告entity identityをrevision 1で作成 |
| `GET /v1/entities/{memory_id}` | 現在読取り可能なentity metadataとepisode原文根拠を返す |
| `POST /v1/relations` | 同一scopeのentity UUID間のtyped relationを一つのcanonical assertionとして作成 |
| `POST /v1/relations/{memory_id}/revisions` | assertion revision-CASでtarget、根拠、valid interval全体を置換 |
| `POST /v1/graph/expand` | 認証付き読取り専用の上限付きSQL探索。canonical relation revisionを使用し、`Idempotency-Key`は不要 |
| `POST /v1/checkpoints` | branch headのCASでtyped stateを保存し、不変checkpoint参照/checksumを返す |
| `GET /v1/checkpoints/{checkpoint_id}` | 現在のアクセス権と完全性を確認し、state・参照・epoch・照合hintを返す |
| `POST /v1/checkpoints/restore` | 互換checkpointを新target branchへコピー。コード実行や外部副作用の再実行はしない |
| `POST /v1/tool-effects` | 既存checkpoint run内のintentを記録/重複抑止し、初期revision参照を返す |
| `POST /v1/tool-effects/{memory_id}/transitions` | CAS付きledger遷移を追記。toolは呼び出さない |
| `GET /v1/tool-effects/{memory_id}` | 現在の状態、不変event履歴、参照、HMAC識別子、run失効flagを返す |
| `POST /v1/recall` | 既定simpleまたはopt-in日本語lexical profileで許可済みepisode/assertionを検索し、`search_profile`とbyte予算内の決定的context packを返す |
| `POST /v1/explain` | 指定したassertion revisionと根拠を返す。省略時は最新ではなく引き続き`1`。episodeはrevision `1`のみ。ranking trace APIはない |
| `POST /v1/forget` | 明示IDによる`preview`または`purge`。任意selectorや`suppress` modeは受け付けない |
| `GET /v1/deletions/{receipt_id}` | 許可された削除receiptと、未解決のoperator-managed backup状態を返す |
| `GET /v1/capabilities` | 現在の機能、上限、未対応機能を返す。認証必須 |

型付きrequest/response modelで定義したOpenAPI schemaを
`/docs`と`/openapi.json`で公開します。schemaの生成済みファイルは不要です。
`/healthz`は起動検証後のprocess livenessであり、
PostgreSQLへの継続的なreadiness検査ではありません。

## Local stdio MCP

このmilestoneは`pg-agmemory mcp`を追加します。**stdio専用の信頼するlocal Native API
client**であり、別の永続化/認可serviceではありません。任意の`pg-agmemory[mcp]`は公式
`mcp==2.2.0`と`httpx==0.28.1`を固定し、repositoryのDocker test/runtime両stageにextraを
含めます。remote MCP HTTP/SSE listener、OAuth、caller identity委譲、
semantic cache、response cacheは提供しません。

### ToolとNative semantics

次の4 toolだけを公開し、input/output JSON Schemaは別の手書きrequest契約でなく、
Native Pydantic modelから生成します。

| Tool | Input wrapper | Native route / 成功status |
|---|---|---|
| `memory_recall` | `{request: <Recall>}` | `POST /v1/recall` / `200` |
| `memory_remember` | `{request: <Remember>, idempotency_key: "..."}` | `POST /v1/remember` / `201` |
| `memory_explain` | `{request: <Explain>}` | `POST /v1/explain` / `200` |
| `memory_forget` | `{request: <Forget>, idempotency_key: "..."}` | `POST /v1/forget` / preview・purgeとも`202`（変更なし） |

`request`は既存Native bodyであり、新しい自然言語形式ではありません。rememberは引き続き
`explicit_intent: true`、構造化field、同一scopeのepisode原文根拠を要求します。
episode captureはNative `observe`に残し、MCP toolにはしません。
job、graph、revision、checkpoint/effect実行、削除receipt照会も追加MCP toolではありません。
recallは現在のscope/ACL、時間選択、根拠/coverage、既定`search_profile: "simple-v1"`、
明示`"ja-janome-0.5.0-v1"` opt-inを維持します。`tokenizer_id: "utf8-bytes-v1"`と
Native `token_budget` fieldは引き続き**UTF-8 byteであり、model tokenではありません**。
explainのrevision省略時は**最新ではなく1**です。memory本文は信頼できない根拠資料であり、
指示や検証済みの現在の外部事実ではありません。

両mutation wrapperはforget previewも含め、**1〜256文字のvisible ASCII
（`0x21`〜`0x7e`、空白不可）**の`idempotency_key`を要求します。
空白はtrimせず拒否し、keyを書き換えません。正確に256文字は許可、257文字は拒否します。
adapterはNative `Idempotency-Key`として転送します。callerは結果不明時に**stdio再起動や
token更新をまたいでも同じkeyと同じbodyを保持/再利用**しなければなりません。
key自動生成、自動retry、adapter側のdurable retry storeはありません。
同じkeyでbodyを変えるとconflictし得ます。新keyは結果不明からの復旧手段ではありません。
Native idempotency参照はfresh readでなく過去の記録で、現在の認可と削除がreplayに優先します。
replayでpurge済みdataを復活させてはいけません。
MCP session/request IDはdurable memory run IDでもHTTP idempotency keyでもありません。

### 結果、失敗、上限付きtransport

成功時は`structuredContent: {result: <検証済みNative result>, error: null}`を返します。
tool失敗時は`isError: true`と
`structuredContent: {result: null, error: {code, retryable, outcome_unknown,
native_status, request_id}}`を返します。`native_status`と検証済みNative UUIDの
`request_id`はnullableで、後者はMCP request IDではありません。
短いtextを添えますが、根拠を重複収録したりraw request/response body、URL、credentialを
echoしたりしません。Native errorは安全なcodeに限定し、不正応答を根拠として転送しません。

mutation時のtransport障害/timeout、Native 5xx、不正または予期しない応答は、
保守的に**`outcome_unknown: true`**とします。APIは既にcommitしているかもしれず、
rollback済みと記述してはいけません。`retryable`はhintにすぎず、自動retry、rollback証明、
key/body変更の許可ではありません。local validation失敗はHTTP送信前に発生します。
stdio切断によりNative APIの書込み完了後にacknowledgementだけが失われる場合もあります。

各HTTP交換は**合計20秒**、**I/O 10秒**、**connect 5秒**、poolは**4 connection**に制限します。
serializeしたNative request bodyは**256 KiB**、受信HTTP responseは**2 MiB**が上限です。
HTTP上の上限であり、model token予算や全host/stdio bufferに同じ上限があるという主張では
ありません。redirectとproxy環境設定を無効にし、TLS検証は有効のままです。
semantic/response cacheはありません。

### 固定identityと削除境界

`PGAG_MCP_API_URL`と`PGAG_MCP_API_TOKEN`は信頼する起動設定だけから渡します。
URLはHTTPS originまたはloopback HTTP originに限定し、URL credential、application path、
query、fragmentは禁止です（root `/`は許可）。
bearer tokenは**Native API audience用**で、Native APIがissuer/audience/署名/時刻を検証し、
subjectを解決します。MCP caller tokenの転送やidentity委譲の仕組みではありません。
tool引数でURL/header/token/identityを上書きできません。`mcp`の`--subject`と`--once`は
拒否します。固定subjectのDB workerと混同しないでください。

stdio提供前に、認証付き`GET /v1/capabilities`で`api_version: "v1"`、
`service_version: "0.0.8"`、`schema_version: 7`を要求します。
設定/認証/versionのerrorはsanitized診断だけで非zero終了します。
**migration 008はありません**。v0.0.8はschema 7を維持します。
固定tokenの更新にはadapterを再起動し、refresh grantは提供しません。
起動検証は認可のcacheではなく、全callでNative認証、現在のACL、削除を検査します。

**信頼identityごとにadapterを一つ**動かし、異なるtrust domainとstdio接続を共有したり、
network serviceで包んだりしないでください。local hostを制御できる者は、
設定Native identityの権限を行使できます。Native response-drain barrierはadapterへのHTTP
配信で終了し、**stdio・host UI・LLMまでの原子的配信barrierではありません**。
adapterにresponse cacheがなくても、転送中bufferや配信済みcontextは回収できません。
forget/ACL変更後はhostがcached contextを破棄する必要があり、それを代行するMCP削除通知は
ありません。active-store purgeはhost context、WAL、replica、backupの完全消去ではありません。

### Protocol証拠の境界

公式[Python SDK v2.2.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0)
は**2026-09-07**公開です。上流の
[protocol文書](https://py.sdk.modelcontextprotocol.io/protocol-versions/)は、
`2026-07-28`の`server/discover`と`2025-11-25`までのlegacy `initialize`を説明します。
これはSDKについての事実であり、**adapter/clientの適格性確認結果ではありません**。
限定的な検査では、実stdio SDK `Client`接続とraw JSON fixtureで両modeを実行しました。

- **Modern `2026-07-28`:** `Client(mode="auto")`は`server/discover`を使います。
  raw requestは毎回`params._meta`に`io.modelcontextprotocol/protocolVersion`、
  `io.modelcontextprotocol/clientInfo`、`io.modelcontextprotocol/clientCapabilities`を
  含め、version値は`"2026-07-28"`です。legacy初期化handshakeではありません。
- **Legacy `2025-11-25`:** `Client(mode="legacy")`とraw fixtureは
  `protocolVersion`、`clientInfo`、`capabilities`付きの`initialize`を送り、
  `notifications/initialized`後にtoolを呼び出します。

runtime smokeはnon-root production image内で実`pg-agmemory mcp` childを起動し、
固定tokenとprovision済みscopeで同じloopbackのNative APIへ接続して、
**両mode**で4 tool一覧とrecallを検査します。regression coverageには、
**rememberのcommit後**の実HTTP応答喪失、その後のsame-key/body再送、
assertionが一つだけであることの検査を含めます。自動retryはありません。
正確な256/257文字のkey境界と、空白をtrimせず拒否することも対象です。

最終local/native CI結果を以下に記録しています。
実行済み経路から、未検証の旧client、特定host application、
全protocol versionの適格性を主張してはいけません。
[ADR 0008](adr/0008-local-mcp-jp.md)と
[運用](operations/README-jp.md#local-stdio-mcpの運用)を参照してください。

## Identityと認可

- 2048 bit以上の静的なPEM RSA公開鍵でRS256署名を検証します。
  設定したissuerとaudience、必須claimの`sub`、`iss`、`aud`、`iat`、`exp`、
  tokenの時刻上の有効性を検査します。
- 検証済みexternal subjectをPostgreSQL上のprincipalとtenantへ対応付けます。
  配置ごとにissuerを一つ設定し、callerにtenantを選ばせません。
  未登録subjectは未認証扱いです。
- request bodyでtenant/principal identityを指定できません。要求scopeは範囲を
  狭めるだけで、サービスとRLSがmembership・権限を強制します。
  非公開または削除済みobjectへのアクセスは、存在を区別せず`404`です。
- runtime資格情報はsuperuser、RLS bypass、アプリケーションtable ownerに
  できません。owner role経由の所属も禁止します。
  migration/provision/rebuild用の管理者資格情報を分離します。
- JWKS discovery/rotation、delegated identity、複数issuerのidentity管理、
  公開membership管理APIは未実装です。

認証済みAPI requestごとに短命の新規connectionを開き、connection poolは使いません。
runtimeの`psycopg-pool`依存もありません。
**tenant単位のsession advisory lock**で処理を直列化し、transactionのcommitと
buffer済みHTTP応答の送出が終わるまで保持します。この正しさ優先のdrainにより、
同一tenantの先行応答をサービスがまだ送出中にpurgeがbarrier完了を通知することを防ぎます。
配信済みのdataやnetworkへ渡したbyteを回収することはできません。
遅いclientはtenantの処理を妨げ得ます。throughputは未測定です。
APIとworkerは`principal_connection`のidentity lookupと`bind_identity`の再検査を共有し、
**同じtenant session lock**を取得します。APIの応答drainは引き続きcommit後まで続き、
workerのclaim/publication transactionは短命で、payload準備はその外で行います。
workerのsubjectは信頼する配置identityであり、公開の偽装interfaceではありません。

管理者によるmembership変更も**同一session lock**を取得し、
transaction内で権限と`access_epoch`を更新してcommitした後に、
lockを解放するかconnectionを閉じる必要があります。
この手順を使わない変更はrequest/drainの競合保証の対象外です。
[運用手順](operations/README-jp.md#membership変更とrequest-drain)を参照してください。

## 根拠、同意、時間

`remember`は`explicit_intent: true`と、重複のない1〜32件のepisode根拠IDを
必須とします。各引用はepisode本文に文字列として含まれていなければならず、
両objectは同一scopeに属します。このsliceではassertionを別assertionの
sourceにはできません。free-text subject/valueをentity IDへ解決しません。
明示entity/relation endpointだけがtyped graph dataを作成します。

原文引用の検査はprovenanceを確認するだけで、**意味的な支持や真実を認定しません**。
引用が指定assertionを証明するかをサービスは推論しません。
記憶は`reported`、confidenceは`score: null`、`method: "uncalibrated"`です。
現在の外界の事実として断定するにはsourceへの再照会が必要です。
取得本文は根拠資料であり、信頼できる指示ではありません。

`consent_reference`はcallerによる同意の申告を記録します。同意台帳の検証、
capture-policy engine、secret/PII自動除去はありません。
callerは許可済み・除去処理済みのdataだけを送信してください。

### Assertion revisionの契約

`POST /v1/assertions/{memory_id}/revisions`には`Idempotency-Key`と、
assertionのscopeに対するread/write権限が必要です。置換内容全体を指定します。

| Body field | 契約 |
|---|---|
| `expected_revision` | 現headに一致する厳密な整数1〜1000 |
| `value` | 空でないtext、最大65,536文字 |
| `evidence` | 同一scopeの重複しない1〜32件のepisode IDと、空でない最大4,096文字の原文`quote` |
| `explicit_intent` | `true`必須 |
| `valid_from`, `valid_to` | timezone付きの任意bound。省略/nullは無限端であり「旧boundの維持」ではない。両端があれば開始は終了より前 |
| `reason` | 空でない訂正理由、最大256文字 |

subject、predicate、scopeは不変で、このbodyには指定できません。
`201`で`memory_id`、revision `expected_revision + 1`、
`epistemic_status: "reported"`を返します。head不一致は`409 revision_conflict`、
一致するheadが1000のとき追加を試みると`422 revision_limit_exceeded`です。
上限は初期revisionを含む**全1000 revision**です。
非公開/削除済み/assertion以外の対象は`404`です。
typed relationには専用revision endpointが必要で、
汎用訂正は`409 relation_revision_required`を返します。

idempotency request hashには対象の`memory_id`も含めます。
同一keyの完全一致再送は、後続訂正があっても元のcommit済みrevision参照を返します。
別revisionを作ったり最新headに差し替えたりしません。
同じkeyで対象/bodyを変えるとidempotency conflictです。
すべての再送に現在の認可とtombstoneを適用します。

### 時間と根拠の意味

INSERT triggerがDB clockを使い、旧system intervalの終了、assertion headの更新、
新intervalの設定を原子的に行います。callerはsystem timeを指定できません。
system rangeは連続する`[)`で、`btree_gist`によるGiST非重複制約を適用します。
DBの遅延制約で全revisionの根拠を必須化し、値・理由・根拠をrevisionごとに保持します。

各訂正は**valid interval全体の置換**であり、部分期間の変更ではありません。
例として、無限端のGold assertionを10月1日から有効なPlatinumへ置換すると、
新しい`known_at`での9月16日照会では、このassertionは一致しなくなります。
未来の変更予約のように10月1日までGoldを維持する動作では**ありません**。
訂正前の`known_at`なら以前のGold revisionを選択できます。
自動期間分割、別assertion間のsupersession、競合するfact間の調停はありません。

`recall`は`as_of`/`known_at`で選択し、不変のidentity textと該当revisionのvalueを
検索して、正確なrevisionとそのrevision自身のsourceを返します。
旧値と新しい根拠を混ぜません。episodeは引き続き発生/記録時刻でfilterします。
過去の読取りにも現在のACL/tombstoneを適用します。
context本文は`recorded_at`を`recorded=`と表示し、assertion revisionのsystem採用時刻、
またはepisodeのサービス記録時刻を示します。`observed=`ではなく、
新たな外部観測を主張しません。`occurred_at`は別の時刻です。

`explain`では1〜1000のrevisionを明示できますが、
互換性のため**省略時は引き続きrevision 1**で、最新ではありません。
存在しないrevisionは`404`、episodeはrevision 1のみです。
assertionの説明には、該当revisionの根拠に加え、
`recorded_at`（system開始）、`known_until`（system終了、現headはnull）、
`correction_reason`（revision 1はnull）を含めます。
[ADR 0002](adr/0002-assertion-revisions-jp.md)を参照してください。

正確な`known_at`のrevision境界検査には、host/VMのwall-clock値ではなく、
serverが返したassertionの`recorded_at`を使ってください。

## 検索と予算

recallの既定は`search_profile: "simple-v1"`で、PostgreSQLの`simple`設定、
`plainto_tsquery`、`ts_rank_cd`を維持します。下記の任意日本語profileは分割を追加しますが、
BM25、vector検索、hybrid retrievalではありません。応答は`search_profile`を返し、
未対応profileは`422`です。空の`query`はlexical projectionが不完全でも、
scope・時間条件内のcanonical itemを件数とbyteの上限内でbrowseします。
entity自体はrecall/explainから除外します。relation assertionはFTS候補のままで、
recallが自動的にgraphを展開することはなく、`graph_used: false`を維持します。
itemとassertion説明には返却revisionに対応するnullableな
`relation: {source_entity, target_entity}`を含めます。
relationのcontext本文は両entity UUIDを同じbyte予算内に含めます。
`coverage.jobs_pending`は要求scopeで現在読取り可能なpending/running jobを示し、
queryとの関連性、過去のqueue状態、synthesis完了を意味しません。
`synthesis_pending: false`と`graph_used: false`は維持します。
job自体はrecall/explainとcheckpoint/effect参照から除外します。

request field名は`token_budget`ですが、`utf8-bytes-v1`はmetadata・引用を含む
serialized context packの**UTF-8 byte数**を予算として扱います。
応答には`budget_unit: "utf8_bytes"`、`token_count: null`、
`exact_token_count: false`を明示します。保守的なfallbackであり、
モデルの正確なtokenizerやHTTP応答全体のsize制限ではありません。
このcontext `tokenizer_id`は日本語検索の分割とは無関係です。
item単位で除外し、packのmetadataすら収まらない場合は`422`を返します。

request bodyはcheckpoint作成のみ1 MiB、他endpointは256 KiBです。
recallの返却itemは最大100件、予算値は64〜8,000
（implicit modeは最大2,000）です。件数・予算による省略を`coverage.truncated`で
示します。空の選択結果は`not_found`、`budget_exhausted`、
または下記の`index_incomplete`です。
`retrieval_complete`は世界の知識の完全性を意味しません。
implicit modeはrequest optionであり、自動harness hookの実装ではありません。

### 日本語lexical profile

`search_profile: "ja-janome-0.5.0-v1"`を明示選択します。厳密に固定した依存
**Janome 0.5.0**は、Janome追加語を含む同梱**mecab-ipadic-2.7.0-20070801**を使います。
source/query textの対象日本語script連続部分だけをsurface/wakati分割し、
ASCII識別子や英語はsegmenterをそのまま通過してPostgreSQLのlexical処理へ渡します。
Unicode/全半角正規化、原形化/stemming、同義語展開、分割/recall品質の保証はありません。
漢字script範囲は中国語文字にも及びますが、中国語recallの適格性は未確認です。
外部model/providerは呼び出しません。
Latin textを維持しても`simple-v1`が部分文字列検索になるわけではなく、
token境界なしで埋め込まれた`Gold`は単独の`Gold`に一致するとは限りません。

Janomeは日本語script連続部分の分割が必要なときだけlazy importし、
API importや英語だけの分割ではloadしません。matcherの入力prefix cacheは
`max_cached_word_len=0`で無効にし、source text/token streamでなく同梱辞書resourceの
cacheだけを保持します。test/runtime両container buildは辞書moduleを含む静的Janome package
bytecodeだけを逐次事前compileします。code準備でありmemory index/cacheではありません。
fresh Linux subprocessのregression guardは、英語だけの操作でJanomeをimportしないことと、
初期化peak RSSが**256 MiB未満**であることを要求します。
配置時のmemory上限やrequest/backfillのmemory使用量上限ではなく、単独でrelease適格性を示しません。
事前compileのないcold host installationやresource sizingは適格性未確認です。

`memory.episode_lexical`と`memory.assertion_lexical`は、このprofileの派生`tsvector`
payloadを保存します。episode行はrevision 1、assertion行は正確なrevisionに対応します。
forced RLSと同一scope canonical外部keyを適用し、`ON DELETE CASCADE`を使い、
runtime権限は`SELECT`/`INSERT`だけです。`UPDATE`や直接`DELETE`は付与せず、
canonical parent purgeは子tableのDELETE権限なしでcascadeします。
episode本文とassertionのsubject/predicate/正確なrevisionの
valueを分割後、`to_tsvector('simple', ...)`でindex化します。queryは分割textを
`plainto_tsquery('simple', ...)`と`ts_rank_cd`へ渡します。
simple profileは既存canonical vectorを維持し、entity/jobはrecall itemになりません。

observeと全assertion publication/revision経路は同一transactionでprojectionを書き込み、
typed relationとdurable job publicationも含みます。canonical ID、timestamp、根拠、
同期正規化JSON/HMAC、過去revisionの選択は変わりません。migrationは現headだけでなく、
全保持episodeと**全assertion revision**をbackfillし、tombstoneをskipします。
offline管理rebuildも同じcanonical sourceを使います。
日本語episode本文も**65,536文字**上限を維持し、**65,537文字**は切り詰めず拒否します。
JSON encoding overheadを含む別の256 KiB HTTP body上限も適用します。

日本語profileでは、現在認可済み・要求scope内・時間条件内のcanonical候補に一つでも
projection欠落があると、`coverage.lexical_incomplete: true`と
`coverage.retrieval_complete: false`を返します。この検査はquery関連性やitem上限とは独立です。
**simple検索への黙ったfallbackはなく**、自動修復workerもありません。
利用可能な一致結果を不完全flag付きで返せ、空queryはcanonical itemをbrowseします。
query候補なしでprojection欠落があれば`empty_reason: "index_incomplete"`、
候補がcontextに収まらなければ従来の`"budget_exhausted"`、結果があれば`empty_reason`はnullです。
projection欠落がなければ`lexical_incomplete`はfalseで、通常の`not_found`/予算規則を使います。
projection coverageはquery関連性、queue状態、知識/品質の完全性ではなく、
`jobs_pending`は独立したflagです。
Janomeの破損辞書診断は入力textを含まない`japanese_dictionary_error`へ除去処理します。
libraryの`SystemExit`はtokenizer-unavailableへ変換し、index不完全の成功応答ではなく
APIの`503 dependency_unavailable`となります。
workerは入力をechoせず既存の上限付き`dependency_unavailable` retry経路を使います。

capabilitiesはstage `m2-japanese-fts`、feature `japanese_fts`、両`search_profiles`、
`default_search_profile: "simple-v1"`、固定tokenizer/辞書metadata、
`normalization: "none"`と`segmentation: "japanese-script-runs"`を返します。
context予算は`utf8-bytes-v1`のままで、`vector_search`、`auto_synthesis`、
recallの`graph_used`はfalseです。stage名はM2全体の受入を意味しません。
[ADR 0007](adr/0007-japanese-fts-jp.md)、
[offline再構築](operations/README-jp.md#lexical-profileとreindexの運用)、
[依存ライセンス](../README-jp.md#依存ライセンス)を参照してください。

## Durable job

### 明示的な構造化publication

`POST /v1/jobs`は`Idempotency-Key`と
`{kind: "structured_remember", memory: <Remember request>}`を要求します。
recipeは`structured-remember-v1`だけです。`memory`は変更しない同期`Remember`契約で、
scope、subject、predicate、value、同一scopeの読取り可能で重複しないepisode ID 1〜32件と
各1〜4,096文字の原文quote、`explicit_intent: true`、任意のtimezone付きvalid boundを使います。
enqueueには現在のscope read/write権限が必要です。これは非同期の**構造化publication**であり、
自動synthesis、自然言語抽出、LLM/provider呼出し、embedding、compactionではありません。
`observe`は引き続きenqueueせず`synthesis_job_id: null`を返します。
同期`remember`とlegacy正規化JSON/HMACは変更しません。

`202`は`{job_id, kind: "structured_remember",
recipe_version: "structured-remember-v1"}`を返し、commit済みjob参照の受理を示します。
assertion公開完了ではありません。canonical intentとrecipeで同一tenant/principal/scope内の
重複をHTTP keyをまたいで抑止し、job identityでは根拠順を正規化します。
同じHTTP keyには同じ正規化requestが必要で、変更は`409 idempotency_conflict`です。
別principalは自身のjobを投入でき、異なるsource identity間の意味的重複は抑止しません。
**scope当たりpending/runningは最大100件**（`422 job_limit_exceeded`）、
**job当たり最大5試行**です。
capabilitiesは`durable_jobs`、`job_kinds: ["structured_remember"]`、
`auto_synthesis: false`と、100 job/5試行/30秒lease上限を公開します。
この上限付きjobはM2全体の受入を意味しません。

`GET /v1/jobs/{job_id}`には現在のread権限が必要です。同一scopeのreaderは他principalの
jobを読めますが、claim、publish、retryはできません。応答は次のfieldを含みます。

| Field | 契約 |
|---|---|
| `job_id`, `kind`, `recipe_version`, `retry_of` | opaque job identity、固定kind/recipe、nullableなretry parent |
| `state` | `pending`、`running`、`succeeded`、`failed` |
| `attempt`, `max_attempts` | claim済みの試行回数。最大は5 |
| `available_at`, `lease_until`, `created_at`, `updated_at` | scheduling/leaseとserver時刻。running以外のleaseはnull |
| `error_code` | null、または`dependency_unavailable`、`stale_context`、`invalid_input`、`attempt_limit` |
| `input_refs` | 不変の正確なepisode revision 1参照 |
| `result` | null、または後続訂正後も元のassertion `{memory_id, revision: 1}` |

GETはrequest payload、lease token、owner principalを返しません。
succeeded/failedの両terminal jobはrequest JSONを消去し、入力ID参照はpurgeまで保持します。
terminal記録は不変です。

### Terminal失敗の明示retry

`POST /v1/jobs/{job_id}/retry`は`Idempotency-Key`と元の`EnqueueJob` body全体を
要求します。failed payloadは保存されていないため、ownerが再送する必要があります。
現在の権限/根拠を再検査し、HMACでintentを確認します。
intent変更は`409 job_intent_conflict`、failed以外のparentは`409 job_retry_conflict`、
非ownerは`404`です。

retryは新たな5試行枠を持つ**一つの新child**を作り、旧terminal記録をresetしません。
同じfailed parentへの再送はHTTP keyをまたいでもそのchildを再利用します。
child自体が失敗したらchild IDを指定して別の明示cycleを開始します。
このendpointで固定recipeをreset/差替えすることはできません。
retry lineageはpurgeの対象です。

### 固定principal workerとpublicationの拒否境界

`pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT [--once]`は
制限付き`PGAG_DATABASE_URL`資格情報とAPIの起動role/schema検査を使います。
subjectは設定issuer内でprovision済みの信頼する配置設定です。
JWT署名/公開鍵やadmin URLは不要で、superuser、owner role、`BYPASSRLS`のruntime資格情報を拒否します。
この初期profileはそのprincipalのjobだけをclaimし、global multi-tenant schedulerではありません。
公平性やcost pool動作の適格性は未確認です。

claimは短いtransactionで`FOR UPDATE SKIP LOCKED`を使い、
**commit後、transaction外でpayloadを検査**します。
新UUID lease tokenで試行回数を増やし、現在のaccess/deletion epochを保存して30秒のleaseを
付与します（内部の1〜300秒claim上限は制御されたテスト用で、CLI設定ではありません）。
現在の権限と完全な不変episode入力を確認します。

publicationはprincipal/scope、準備した元bodyとの完全一致、入力根拠、
lease/token/期限、保存epochを再検査します。共有assertion-publication helperがassertion、
provenance、job成功、auditを原子的にcommitします。最後のjob更新でも期限を再検査し、
publication途中の期限切れは出力をrollbackします。lease失効/引継ぎは古いpublisherを拒否します。
assertionの`recorded_at`/system intervalは**enqueueでなくworker publication時**に始まります。
jobの`created_at`はassertionの採用時刻ではありません。
caller指定のvalid-time boundはserver管理のsystem timeとは独立です。
内部heartbeatはlease/epochを確認して30秒leaseを更新します。
公開claim/publish/heartbeat endpointはありません。決定的processorは外部呼出しをせず、
長時間heartbeat taskを必要としません。

再試行可能な失敗は`2^attempt + [0,1)`秒のjitter付きbackoffを設定し、最大5試行とします。
再試行不能な`invalid_input`は即時失敗です。5回目のclaimが期限切れになると
`failed`/`attempt_limit`になり、6回目は与えません。payloadを含めず安全なcodeをlogに残します。
at-least-onceの試行と**job当たり最大一つのcommit済み結果**であり、外部exactly-onceではありません。

継続modeはidle時1秒ごとにpollし、一時的なDB loop障害後は2秒待ちます。
`--once`は実行時刻に達したjobを最大一つ処理し、JSON outcome
（`idle`、`succeeded`、`pending`、`failed`、`lease_lost`）と該当opaque ID/結果参照を
返して終了します。stdout/logのoutcome参照はopaqueな過去記録であり、
現在のread許可やlive state snapshotではありません。
job GETと正確なrevisionのexplainは現在のアクセス権/削除状態を検査します。
queue全体は処理せず、他CLI commandでの指定は拒否します。
[運用](operations/README-jp.md#durable-jobとworkerの運用)と
[ADR 0006](adr/0006-durable-jobs-jp.md)を参照してください。

## EntityとSQL graph oracle

### Entity identity

作成には`Idempotency-Key`、scopeの現在のread/write権限、次のfieldが必要です。

| `POST /v1/entities` field | 契約 |
|---|---|
| `scope_id` | 必須scope UUID |
| `entity_type` | literalの`person`、`organization`、`project`、`component`、`incident`、`task`、`decision`、`other` |
| `canonical_label` | 1〜256文字 |
| `evidence` | 同一scopeの読取り可能で重複しない**episode** ID 1〜32件と、各1〜4,096文字の原文`quote` |
| `explicit_intent` | `true`必須 |

`201`で`memory_id`と`revision: 1`を返します。identity metadataと根拠は
不変のcaller申告であり、検証済みfactではありません。alias、entity merge、
名前ベースの解決、意味的重複抑止、label訂正endpointはありません。
同じHTTP key/bodyは現在の認可の下でanchorを再利用しますが、
別keyなら同じlabelの別entityを作成し得ます。名前/typeは非信頼dataであり指示ではありません。

`GET /v1/entities/{memory_id}`はID/revision、scope、type、canonical label、
記録時刻、episode根拠を返します。entityはrecall/explainには出さず専用GETを使います。
entity revision 1をcheckpoint/tool-effectの`memory_refs`に指定できます。
コピーしたすべてのentityまたはassertion revision依存を宣言してください。

### Canonical relation assertion

`POST /v1/relations`は`Idempotency-Key`、`scope_id`、`source_entity`/
`target_entity` UUID、`predicate`、重複しないepisode原文根拠1〜32件、
`explicit_intent: true`を必須とします。両endpointと**根拠はすべて同じscope**で
現在読取り可能でなければなりません。quoteは1〜4,096文字です。
任意の`valid_from`/`valid_to`はtimezone付きで、省略/nullは無限端、
開始は終了より前とします。predicate allowlistは`depends_on`、`part_of`、
`affects`、`works_for`、`decides`です。すべて複数のreportedな申告を許し、
調停、検証済みtruth、逆向きfactの推論はありません。

relationは独立object IDを持たず、**一つのcanonical assertion identity
（`memory_id`）**です。`memory.relation`がsourceを固定し、
`memory.relation_revision`が各assertion revisionの正確なtargetを記録します。
subjectは不変のsource label、各revisionの不変valueはそのtargetのcanonical labelです。
valid/system time、truth status、episode根拠は既存assertion revisionに属し、
並行したgraph履歴ではありません。作成応答は既存の`RememberResult`
（`memory_id`、revision 1、`epistemic_status: "reported"`）です。
free-text `remember`はlabel/predicateが一致しても自動的にrelationになりません。

`POST /v1/relations/{memory_id}/revisions`は`Idempotency-Key`、
厳密な整数1〜1000の`expected_revision`、`target_entity`、置換episode `evidence`、
`explicit_intent: true`、任意のtimezone付きvalid bound、1〜256文字の`reason`を要求します。
source/predicate/scopeは固定です。汎用assertion訂正と同じく**valid interval全体**を
置換し、期間を分割したり、新bound外に旧値を残したりしません。
旧target ID、根拠、intervalは正確な過去revisionに維持します。
CAS、過去結果の冪等再送、全1000 revision上限は同じです
（`409 revision_conflict`、`422 revision_limit_exceeded`）。
正確なrelation根拠には`explain`を使い、revision省略時は引き続き**1**です。

### 上限付き展開

認証必須の`POST /v1/graph/expand`は読取り専用で、`Idempotency-Key`は不要です。

| Field | 契約 |
|---|---|
| `scope_ids` | 必須、重複しないscope UUID 1〜32件 |
| `seeds` | 必須、重複しないentity UUID 1〜16件 |
| `relation_types` | 必須、上記allowlistから重複しないpredicate 1〜5件 |
| `purpose` | 必須、1〜256文字のtext |
| `direction` | `outgoing`（既定）、`incoming`、`both` |
| `max_hops` | 厳密な整数1〜2、既定2 |
| `max_paths` | 厳密な整数1〜100、既定100 |
| `as_of`, `known_at` | 任意のtimezone付きtimestamp。既定値は展開ごとに一度だけ取得 |

唯一のbackendはcanonical PostgreSQL SQL joinです。graph request/探索に
AGE、SQL/PGQ、Cypher、動的SQL、動的labelを使いません。
固定parameterized neighbor queryで時間と現在のRLS可視性をseed、edge、中間node、
根拠に適用し、同一scope外部keyを使います。scope/seed filterはアクセスを狭めるだけです。
非公開、不在、時間条件で利用不能なseedは黙って除外し、応答に再掲しません。
既存のtenant transactionとresponse-drain lockで読取り境界を保護します。

探索は決定的な幅優先**simple path**で、seed UUID順、続いて各hopの
assertion ID/revision/次entity ID順です。同一path内でentityを繰り返さず、
意味的なcycle edgeもnodeを反復するpathを作りません。
全prefixをglobal path予算に数え、limit-plus-one probeで追加可能なpathを検出します。
返却は最大`max_paths`件です。incoming/bothは探索方向だけを変え、
edgeのsource/targetやreported factを反転しません。

応答は`backend: "sql"`、`projection_watermark: null`（graph projection/lag/watermark保証は不要）、
実効`as_of`/`known_at`と次のfieldを含みます。
- `nodes`: canonical entity summary。完全な根拠quoteは含めない。
- `edges`: canonical assertion ID/revision、source/target UUID、predicate、
  valid interval、記録時刻、`epistemic_status: "reported"`。
- `paths`: `{nodes: [UUIDs], assertions: [{memory_id, revision}]}`。
- `coverage`: `max_hops`、`truncated`、`complete_within_bounds`。
- `consistency`: 現在のaccess/deletion epoch。
- `empty_reason`: pathがなければ`not_found`、あればnull。

可視の孤立seedはpathがなくても`nodes`に現れ得ます。pathなしや上限内の完全性は
**factが存在しない証明ではありません**。根拠はentity GET/relation explainで取得し、
展開summaryには含めません。最終再検査でnodeが欠ければ`409 graph_invalidated`で
fail-closedとなり、DB障害は空の成功応答でなく`503`です。
capabilitiesは`graph_backend: "sql"`、entity/relation type allowlist、graph上限を公開します。
将来backendの適合性を比較する正しさの基準であり、graph有用性の測定や
M1/M3全体の受入ではありません。[ADR 0005](adr/0005-relational-graph-jp.md)を参照してください。

## Checkpointの契約

checkpoint作成とrestoreには`Idempotency-Key`とscopeの現在のread/write権限が必要です。
GETには現在のread権限が必要です。run/branch UUIDはtenant/scope内でcallerが指定する
identityであり、global sessionではありません。

| 作成field | 契約 |
|---|---|
| `scope_id`, `run_id`, `branch_id` | scope内のrunとbranchを識別するUUID |
| `expected_head` | 必須UUIDまたは`null`。空branchだけnull、それ以外は正確な現checkpoint ID |
| `harness_id`, `harness_version` | 必須の空でないtext、各最大256文字。run内で固定 |
| `state_schema_version` | `1`のみ。既定も1 |
| `event_watermark` | 必須の非負64-bit整数。parentから減少できない |
| `state` | typedな`goal`、`constraints`、`completed_actions`、`decisions`、`unresolved_questions`、`next_actions`、`pending_effects`。任意object/pickleは不可 |
| `memory_refs` | 同一scopeの重複しない`(memory_id, revision)`を最大100件。episode/entityはrevision 1、assertion（relationを含む）は存在するrevision。list省略は空、revision省略は最新ではなく1 |

goalとstateのtext要素は空でなく最大4,096文字です。
constraints、decisions、unresolved questions、next actionsは各64件、
completed actionsとpending effectsは各100件までです。pending effectは
一意のUUID `operation_id`、1〜256文字の`description`、
`planned / dispatched / unknown`のstatusを持ちます。
snapshot hintであり、外部actionの実行・確認の証拠ではありません。

checkpoint UUIDとbranch内のsequence（1から）はサーバーが付与します。
古いheadはCASで`409 checkpoint_head_conflict`、
watermark減少は`409 checkpoint_watermark_conflict`です。
失効branchは再開できません（`409 checkpoint_invalidated`）。
既存checkpoint payloadは不変です。HMAC checksumは`hmac-sha256-v1`で、
参照・保存時epochを含むenvelopeを対象にします。
GETはchecksum、typed state、可視参照、現在の認可を確認します。
不正envelopeはfail-closed、非公開/削除済みcheckpointは`404`です。

envelopeは`saved_access_epoch`、`saved_deletion_epoch`、
`current_access_epoch`、`current_deletion_epoch`を含みます。
保存時epochはmetadataであって旧権限の利用許可ではありません。
checkpointはrecall/explainの対象外で、stateにはcheckpoint GETを使います。

### Restoreと依存境界

restoreには`checkpoint_id`、未使用の`target_branch_id`、
完全一致する`harness_id`、`harness_version`、`state_schema_version`を指定します。
同一scope/runでsequence 1の新checkpointを作り、別branch上でも元checkpointを
parentにします。元branch/checkpointは変更せず、headを巻き戻しません。
既存target branchは`409 checkpoint_branch_conflict`、
harness/schema不一致は`422 checkpoint_incompatible`です。

stateと参照をコピーしますが、dispatched snapshot hintはunknownに変更します。
同じtransaction内でrunの全生存dispatched effectへ
`origin: "checkpoint_restore"`の`unknown` eventを追記してからforkを作成します。
新revisionのCASで古いledger writerを拒否しますが、進行中の外部呼出しは取り消せません。
完全一致restore再送ではforkもjournal eventも重複作成しません。
保存済みassertion参照は正確な過去revisionを維持します。
restoreで最新assertion revisionを選び直したり、現在の外部事実を自動更新したりはしません。
必要な場合は別途、新しい観測を取得してください。
GET/restoreは別branchや保存後に追加したeffectも含め、**runの全生存effect**を統合します。
`tool_effects`は現在のsummaryであり、このlive viewで保存state/checksumは変更しません。

| 現ledger状態 | Snapshot hint | このoperationの照合 |
|---|---|---|
| `dispatched` / `unknown` | 任意またはなし | 必須 |
| `confirmed` / `failed` | 任意またはなし | caller申告のterminal記録で解決 |
| `planned` | なし、または`planned` | 不要。ただし実行許可ではない |
| `planned` | `dispatched` / `unknown` | 必須。不確実性を記録してreceiptを照合 |
| 未追跡 | **`planned`を含む**全hint | 必須。`untracked_effects`にも列挙 |

`requires_reconciliation`に全blocking operation IDを列挙し、
一つでもあれば`resume_allowed: false`です。v0.0.3のsnapshot-only動作から
意図的に厳格化しています。`automatic_reexecution`は常にfalseです。
権限検査・承認・provider照合はhostの責任です。
provider照会サービス、自動実行、harness adapterは未実装です。

同一keyの完全一致再送は新headでなく元のcheckpoint参照を維持します。
再送記録にstateは保存せず、読取り/restore再送は現在の認可でenvelopeを再構成するため、
現在のepoch metadataは変わり得ます。purge済み参照の再送は`404`です。

**callerは全memory依存を`memory_refs`へ宣言しなければなりません。**
依存DAGは宣言済み参照、parent lineage全体、下記のrun全体のeffect-to-checkpoint依存を対象にし、
未宣言のコピー本文をsemantic scannerが発見することはありません。
同意とsecret/PII除去もcallerの責任です。
working snapshot、compaction、harness連携は別の将来課題です。
[ADR 0003](adr/0003-checkpoints-jp.md)と[ADR 0004](adr/0004-tool-effects-jp.md)を参照してください。

## Tool-effect ledger

planと遷移には`Idempotency-Key`とscopeの現在のread/write権限、
GETにはread権限が必要です。**先にbootstrap checkpointを作成してください。**
planはrunを作らず、存在しないrunは`404`です。

| Plan field | 契約 |
|---|---|
| `scope_id`, `run_id`, `operation_id` | caller UUID。operation identityはtenant/scope/run/operation内でありglobalではない |
| `tool_name` | 空でないtext、最大256文字 |
| `action_hash` | callerの正規化actionの小文字64桁hex digestが必須。serverは外部呼出しとの一致を検証できない |
| `memory_refs` | 同一scopeの重複しない正確な参照を最大100件。episode/entityはrevision 1、assertion（relationを含む）は存在するrevision 1〜1000。既定は空、revision省略は最新でなく1 |

**actionが利用した全memory依存を宣言してください。**
checkpoint/effect/jobは参照kindにできず、未宣言コピーは発見しません。
tenant-HMACの`action_fingerprint`と安定した64桁hex `external_idempotency_key`のみを
永続化し、生のaction hashや引数は保存しません。
GETはこれらの識別子、参照ID、最新revision/status、`run_invalidated`、
最大4 eventの不変履歴を返します。tool名、reason、receipt参照の機密情報除去はcallerの責任です。
effectはrecall/explainの対象外であり、専用GET endpointを使ってください。

planは`201`で`memory_id`、`revision: 1`、`status: "planned"`を返します。
異なる冪等性keyでもoperation identityと正規化bodyが同じなら、後の遷移後も
元のrevision 1参照へ重複抑止します。intent変更は`409 operation_conflict`、
同一冪等性keyでのbody変更は`409 idempotency_conflict`です。
非公開/purge済みidentityは再送で復元できません（完全一致再送は`404`）。
runの存続期間中の上限は**terminal記録を含め100 effect**で、
超過は`422 effect_limit_exceeded`です。purgeはrunを封鎖するため容量を再利用できません。
新run/operation IDは意味的な重複抑止ではありません。

遷移は厳密な整数1〜4の`expected_revision`、`status`、
空でない最大256文字の`reason`を要求します。成功は次revision/statusを含む`201`、
古いCASは`409 revision_conflict`、禁止遷移は`409 effect_transition_conflict`です。

| 現状態 | 許可する次状態 |
|---|---|
| `planned` | `dispatched`, `unknown` |
| `dispatched` | `unknown`, `confirmed`, `failed` |
| `unknown` | `confirmed`, `failed` |
| `confirmed`, `failed` | なし。terminal状態は不変 |

`planned → unknown`はprotocol外/legacyの実行試行に関する不確実性を記録するもので、
実行許可ではありません。`unknown → dispatched`はありません。
terminal遷移は空でない最大256文字の`receipt_reference`と、
`receipt_source: "provider_receipt"`または`"operator_review"`を必須とし、
他状態では両receipt fieldを省略またはnullにします。**caller申告の参照であり、server検証済み結果ではありません。**
GET履歴は記録時刻、reason、receipt field、`origin`を含みます。
DBが時刻とactorを付与し、FSM、連続revision、head更新、参照制約を強制します。
RLSと複合tenant/scope外部keyを維持し、特権helperは使いません。

冪等性記録は結果参照/revision/statusとkeyed request digestのみで、
receiptやbodyのコピーは含みません。dispatch応答を含むplan/遷移の応答は
**過去revisionの参照であり、現在状態のsnapshotや実行許可ではありません**。
現在の認可の下で、同一intentのplan再送や完全一致する遷移再送は、
run封鎖後でも生存effectの元の参照を返し得ます。runの封鎖を解除するものではなく、
新たなdispatchは拒否し、purge済みeffectの完全一致再送は引き続き`404`です。
harnessは外部呼出し前にdispatchを永続記録し、
providerが対応する場合は安定した外部keyを使ってください。
外部exactly-once、承認、自動再試行/実行、provider receipt照会の保証はありません。

## 冪等性と削除

mutationには`Idempotency-Key`が必要で、変更と冪等性結果を同じtransactionで
commitしてから応答します。keyの範囲はtenant・principal・operationです。
正規化後のrequestが一致すれば現在のアクセス権を再検査して結果を再利用し、
payloadが変われば`409`です。削除済みmemoryへの同一request再送は、
以前の本文ではなく`404`を返します。

source eventの重複抑止は**namespace + event ID**の組をtenant-keyed HMACにし、
PostgreSQL上でtenant/scopeごとに管理します。event ID単独のhashではありません。
同一eventは新しいidempotency keyでも生存episodeを再利用できますが、
payloadが衝突すれば`409`です。purge済みeventの完全一致再送は`404`であり、
元のsource identityを再利用してepisodeを復活させることはできません。

`preview`は現在の対象件数を示すだけで、状態変更やselector予約token発行はしません。
`purge`は1〜100件のroot IDを受け付け、
**episode → assertion（全revision）→ checkpoint参照 → 子孫/fork checkpoint**
を辿ります。episodeからcheckpointへの直接参照も対象です。
entity根拠により**episode → entity → sourceまたは過去のどのtargetとしてでも
そのentityを使うrelation → assertion全履歴**も辿ります。entityの直接purgeも同じ
relation closureを持ち、checkpoint/effectへの直接entity参照も対象です。
relationが消えただけで他の生存entity identityは削除しません。
entityはepisodeだけに依存するため、意味的graph cycleがprovenance cycleを作ることはありません。
宣言済みepisode/entity/assertion-to-effect参照により、
**source → tool effect → 同一scope/runの全checkpoint**も辿ります。
参照が空の旧checkpointやeffect作成前のsnapshotも対象です。
jobは不変入力による**episode → job**、**result assertion → job**、
**parent job → retry子孫**を追加し、同じclosure上限に数えます。
result assertionの後続revisionだけで使われたsourceを含め、どのsourceでも削除時は
assertion全履歴と依存jobをpurgeします。
**job/control記録やfailed parentのretry chainを消しても、公開済みの独立assertion出力や
source episodeは消しません。** 出力には直接episode provenanceがあるため、
factを消すにはoutput/sourceを明示purgeしてください。job → resultの依存cycleはありません。
上限は要求rootに加えて依存物全体で10,000件であり、層ごとの上限ではありません。
超過時は一部削除せず`422`です。過去のどのsourceでも、その削除は保守的に
assertion全履歴と全依存checkpoint payloadを削除し、後のsnapshotがそのsourceを
省略していても対象です。すべての子はparent lineage全体を引き継ぎ、forkでも逃れられません。

**どのeffectでも**purgeするとrunの`effects_invalidated`を永続設定します。
新effect plan/dispatchは`409 effect_run_invalidated`、
新checkpointは`409 checkpoint_invalidated`で拒否し、そのrunのcheckpointは再開できません。
独立した他effectは自動purgeせず、GETで`run_invalidated: true`と履歴を返します。
`unknown → confirmed/failed`を含む許可済み照合遷移は可能ですが、dispatchはできません。

影響するbranch headは永続的に失効し、同じIDで再開できません。
checkpoint削除は祖先やsource episodeを削除しません。再生成も行いません。
既存tenant session lockでclosure、payload purge、run/branch失効、
read barrierを原子的に扱います。

purgeはassertion/episode payloadとtombstoneより先にjob入力/request行を削除し、
同じtenant barrierで実行中publisherを拒否します。purge済みjobのGET/完全一致HTTP再送は
`404`となり、保持job identityが同じexact jobの復活を防ぎます。
purgeは対象episode/entity/assertion/checkpoint/effect/job payload、entity根拠、
typed relation link、reason/receipt参照を含むeffect event、依存する引用/参照を同期SQL削除した後、
**同じtransaction内で**scopeに束縛されたopaqueな削除markerと時刻を
`memory_ops.object_tombstone`へ挿入します。`memory.object`に`deleted_at`列はなく、
SELECT RLSがtombstoneのあるobjectを除外します。
同じtransactionで`deletion_epoch`を進めてreceiptをcommitします。
`active_store_purged`を含むHTTP `202`は、**queue上のpurge jobや完全消去証明ではありません**。
opaque operation registry/run flag、run/branch metadata、object記録、tombstone、audit/receipt metadata、
job identity、tenant-keyed HMACのsource/idempotency tombstoneはtenantの存続期間中保持します。
自動期限切れやtenant完全消去workflowはありません。
過去参照や再送からpurge済みlabel、value、receiptを復活させることはできません。
canonical削除は同じtenant barrierで対象episode/assertionの全lexical revisionへもcascadeし、
tombstone commitより先に消去します。これらは派生payloadであり、
別memory identityやprovenance vertexではありません。
rebuildはtombstoneをskipし、purge済み本文を再生成しません。

receiptは`backup_status: "operator_managed"`、
`backup_retention_deadline: null`を返します。旧DB page、WAL、replica、backup、
配信済みcontextの消去は証明しません。完全消去保証や本番complianceへの適格性は未確認です。
backupから復元したDBは最新の削除台帳とACL失効を再適用するまで隔離してください。
自動backup recovery/台帳replayとDR適格性確認は未実装です。

## Schema互換性

**v0.0.8はschema 7を維持し、v0.0.7への追加migrationはありません。**
MCP adapterはHTTPだけを使い、DDL/backfillは行いません。
認証付き起動検査は単なるDB版互換ではなく、対応するv0.0.8/schema 7 Native APIを要求します。
以下のmigration履歴はschema 7より古いDBに引き続き適用します。

変更しないmigration 001〜006に続き、追加的な`007_japanese_fts.sql`を適用します。
二つのlexical projection tableを作成し、migration runnerがschema 7記録前の
**同一transaction**内でPython backfillを行います。全保持episode/assertion revisionを
対象にし、canonical ID/system time、receipt、tombstoneは変えません。
backfill完了後の失敗でもprojection DDL/dataとschema ledgerをまとめてrollbackし、
schema 6からのupgradeは6のままです。一方、明示reindexの失敗は既存schema 7の
projectionを維持します。
typed graph/job/effect/checkpoint履歴とguard、legacy `Remember` JSON/HMAC順、
source identity、checkpoint checksumは維持します。projectionはcheckpoint/effect参照kindを
追加しません。v0.0.8のAPI**とworker**は厳密な履歴`[1, 2, 3, 4, 5, 6, 7]`を要求し、
旧版・将来版・不完全な履歴と安全でないruntime roleを拒否します。

migration/rebuildにはforced RLSをbypassできる適切な権限の管理者が必要で、
migrationにはDDL権限と`btree_gist`も必要です。`row_security = off`はbackfillがRLSで
filterされる場合にfail-closedにする設定であり、bypass権限を与えません。
`pg-agmemory reindex-lexical`は選択DBの**全tenantを対象とするoffline管理操作**です。
`PGAG_ADMIN_DATABASE_URL`を使い、schema 7を要求し、migration lock下でprojectionだけを
原子的に置換します。source本文ではなく`profile`と`episodes`/`assertion_revisions`件数を
出力します。`--subject`はprincipal/scope filterではなく明示拒否し、
`--once`もworker専用として拒否します。
旧版・新版の全API**とworker**を停止/drainし、backup、原子的migration/rebuildの後に、
対応するv0.0.8 processだけを起動してください。保守中はadapterも停止します。
**すべての旧imageを停止してください。v0.0.1にはschema起動guardがありません。**
rolling共存やdowngradeは非対応です。
[運用](operations/README-jp.md#v007の保守migration)に従ってください。

## 検証証拠

公開repository: [rioriost/pg_agmemory](https://github.com/rioriost/pg_agmemory)。

### v0.0.8 / schema 7

実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)を
**2026-09-17 JST**に検証しました。

| 環境 | Command | テスト | テスト所要時間 |
|---|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | 214合格、既存warning 1件 | 240.83秒 |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | 214合格、既存warning 1件 | 415.46秒 |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | 214合格、既存warning 1件 | 389.47秒 |

3環境で**Ruff、strict mypy（source 13ファイル）、non-root productionの
日本語tokenizer、API HTTP、worker CLI、MCP stdio smoke**も合格しました。
MCPはSDK 2.2.0と独立したraw wire fixtureで`2026-07-28`・`2025-11-25`の両方を検査しています。
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)の
両jobは上記SHAと完全一致し、実logで件数とsmokeを確認しました。
所要時間はテストの観測値であり、性能benchmarkではありません。
残るwarningは既存のanyio BlockingPortal aliasに関するものです。

別途Apple Containerでfreshなcore-only install
（`uv sync --frozen --no-dev --no-editable`）も検証しました。
`mcp`・`httpx`なしでNative APIをimportでき、`pg-agmemory mcp`は
extra未導入の明示的な診断付きで終了しました。この追加検査はローカルだけであり、
別のDocker CI検査の主張ではありません。
M0〜M3/MVP/本番/性能/品質/DR/完全消去の全受入gateは未完了です。
以下の過去結果をMCP検証として扱ってはいけません。

### 過去のv0.0.7 / schema 7

**v0.0.7/schema 7だけ**の実装commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)について、
**2026-09-17 JST**に最終結果を確認しました。

| 環境 | Command | テスト | テスト所要時間 |
|---|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | 144合格、既存warning 2件 | 206.54秒 |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | 144合格、既存warning 2件 | 386.32秒 |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | 144合格、既存warning 2件 | 331.59秒 |

3環境の最終実行で**Ruff、strict mypy（source 12ファイル）、
non-root productionの日本語tokenizer、API HTTP、実CLI workerという全3種のsmoke**も合格しました。
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)
の両native Docker jobは上記SHAと完全一致し、job状態だけでなく実logで件数と各検査を確認しました。
所要時間はテスト実行の観測値であり、性能benchmarkではありません。
最終bilingual docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)も、
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899)で
両native jobが合格しました。これは過去の最終docs CIであり、上記実装runの所要時間とは別です。
どちらのrunもv0.0.8を検証していません。

既定/opt-in lexical動作、日本語/ASCII処理、正確な65,536文字のindex化と65,537文字の拒否、
lazy load/fresh Linux初期化guard、時間/RLSとindex不完全/予算動作、
子table DELETE権限なしのcanonical purgeを検査しています。
実際の初回backfill後のschema 6へのrollback、一部reindex失敗時の旧projection維持、
reindexのscope/worker flag拒否、job/graph/effect/checkpointとlegacy冪等性の維持も対象です。
正確な過去照会検査にはVMのwall-clock値でなくserver記録のassertion時刻を使います。

tokenizer smokeは`東京都` → `東京` / `都`を検査し、
`Production Japanese tokenizer smoke passed`をlogに出します。API smokeはHTTP healthを検査します。
worker smokeでは使い捨てprincipalとruntime専用資格情報で、
実際の`pg-agmemory worker --subject ... --once`をnon-root production image内で実行し、
`{"outcome":"idle"}`を確認して`Production worker smoke passed`をlogに記録しました。
CI step名は`Test containers and smoke-test production API and worker`です。
これらのsmokeは同梱tokenizer動作、API liveness、worker起動/idle実行を確認するもので、
end-to-end recall品質やqueue済みpublicationの正しさを認定しません。
publication動作は別途test suiteの検査対象です。

v0.0.7最終lockは従来のpackage-feed registryを維持しました。全**36 package**のversion、
依存metadata、artifact hashはテスト済みPyPI解決lockとbyte単位で同一と確認しました。
v6との差分はJanome 0.5.0の追加とprojectのv0.0.7へのversion更新だけで、
無関係なupgradeやregistry移行はありません。
native CIは最終retained-registry lockからbuildしました。
このpackage件数/比較は過去のもので、v0.0.8 MCP lockについての主張ではありません。

それ以前のv5証拠は[ADR 0005](adr/0005-relational-graph-jp.md)に過去のものとして残し、
v6の決定/証拠は[ADR 0006](adr/0006-durable-jobs-jp.md)に保持します。
検査はM0/M1/M2/M3全体の完了、性能/品質の測定、外部exactly-once、MVP、本番readiness、
backup/DR、完全消去の適格性を示すものではありません。

## 今後の実装対象

自動enqueue/自然言語抽出/synthesis、LLM/provider処理、global multi-tenant scheduling/
公平性/cost pool、別のworking snapshot/compaction、
embedding/pgvector、vector/hybrid retrieval、AGE、SQL/PGQ、
provider receipt検証、実際のharness連携/実行/recovery、
別assertion間のsupersession/fact調停、remote MCP HTTP/SSE/OAuth/delegation、
application SDK、postgresem連携はありません。
local stdio MCP、opt-in lexical分割、明示structured job、上限付きSQL graph oracle、
typed checkpoint envelopeだけで、
計画上の二時点・graph・provenance・削除architectureが完了したとは扱いません。

選択理由は[ADR 0001](adr/0001-initial-slice-jp.md)、
安全な管理は[運用](operations/README-jp.md)、
container検証workflowは[貢献方法](../CONTRIBUTING-jp.md)を参照してください。
