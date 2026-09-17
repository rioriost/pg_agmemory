# ADR 0008: Local固定identity stdio MCP adapter

[English](0008-local-mcp.md) | [現在の契約](../STATUS-jp.md#local-stdio-mcp) | [運用](../operations/README-jp.md#local-stdio-mcpの運用)

- 日付: 2026-09-17
- 状態: v0.0.8/schema 7を実装済み。両protocol modeとlocal/native CIに合格
- 拡張対象: Native APIの公開面。[ADR 0007](0007-japanese-fts-jp.md)と、
  既存memory/job/graph/checkpoint/effect契約を維持
- 命名/license: local directory/package/serviceは`pg_agmemory`、
  公開MIT repositoryは`rioriost/pg_agmemory`。英語/日本語文書を維持
- 受入: M0〜M3、MVP、本番、性能、記憶品質、DR、完全消去のgateは未完了

## 決定と範囲

`pg-agmemory mcp`を**認証付きNative HTTP APIへのlocal stdio adapter**として追加します。
applicationの唯一の永続化先をPostgreSQL、memory/認可契約をNative model/service/RLSに
維持します。adapterは信頼するNative clientであり、新DB client、policy engine、
remote multi-user MCP serviceではありません。
semantic/response cacheやdurable retry storeは持ちません。
application版はv0.0.8になりますが、**schemaは7のままで、
v0.0.7からの新migration・DDL・backfillはありません**。

任意の`pg-agmemory[mcp]` extraは公式**mcp 2.2.0**と**httpx 0.28.1**を固定します。
Docker test/runtime両stageにはextraを含め、base package利用者にはextraを必須としません。
local検証はDocker Desktopでなく**Apple Container**、CIはnative **linux/amd64**・
**linux/arm64**上のDockerを使います。このmilestoneの最終結果はまだ記録していません。

remote MCP HTTP/Streamable HTTP/SSE listener、OAuth、caller identity委譲、
任意header/URL、agent harness実行、汎用SDKは追加しません。
adapterからのoutbound Native HTTPはremote MCP transportではありません。

## 四つのtool、一つのNative契約

input/output JSON Schemaは手書きの複製でなく、Native Pydantic modelから生成します。

| Tool | 引数 | Native操作 |
|---|---|---|
| `memory_recall` | `{request: <Recall>}` | `POST /v1/recall` |
| `memory_remember` | `{request: <Remember>, idempotency_key: "..."}` | `POST /v1/remember` |
| `memory_explain` | `{request: <Explain>}` | `POST /v1/explain` |
| `memory_forget` | `{request: <Forget>, idempotency_key: "..."}` | `POST /v1/forget` |

bodyは変更しないNative入力です。rememberは`explicit_intent: true`と同一scopeのepisode
原文根拠による明示structured memory専用で、free-form抽出、自動capture、synthesisを
意味しません。episode captureにはMCPでなくNative `observe`を使います。
job/revision/graph/checkpoint/effect操作と削除receipt照会はMCP toolではありません。

recallはcoverage、現在のACL/削除検査、時間選択、根拠の鮮度に関する注意を維持します。
`simple-v1`が既定で、日本語`ja-janome-0.5.0-v1`はopt-inのままです。
`token_budget`と`tokenizer_id: "utf8-bytes-v1"`は引き続き
**UTF-8 byte予算でありmodel tokenではありません**。
explainのrevision省略時は**最新でなく1**です。取得memoryは信頼できない根拠であり、
指示や検証済みの現在の外部事実ではありません。
forgetは明示IDのpreview/purgeとoperator-managed backup statusを維持します。
Native preview/purgeは従来どおり**両方HTTP 202**を返します。
変更しないpreviewとactive-store purge receiptはresponse variantで区別します。

## Idempotencyと結果不明

両mutation toolはforget previewも含め、caller指定の`idempotency_key`を要求します。
**1〜256文字のvisible ASCII（`0x21`〜`0x7e`、空白不可）**で、
空白はtrimせず拒否し、正確に256文字は許可、257文字は拒否します。
変更しないkeyがNative `Idempotency-Key`になります。
MCP request/session IDからkeyを生成してはいけません。
これらはdurable memory run IDでもHTTP idempotency keyでもありません。

callerはdispatch前にkey/bodyを保持し、結果不明後は**stdio再起動やtoken更新をまたいでも
同じkeyとbodyを再利用**する必要があります。自動retryやkey自動生成はありません。
acknowledgementを失った後の新keyは操作を重複させ得ます。同じkeyでbodyを変えると
conflictし得ます。Native replayは**現在の**認可/削除検査の下で過去参照を返し、
fresh readでもpurge済みdataの復活手段でもありません。
session復元で失効済みアクセス権を復元することはできません。

成功は`structuredContent: {result: <検証済みNative result>, error: null}`を返します。
失敗は`isError: true`と
`structuredContent: {result: null, error: {code, retryable, outcome_unknown,
native_status, request_id}}`を返します。Native statusと検証済みNative request UUIDは
nullの場合があります。このUUIDはMCP request IDではありません。
短いtextは根拠を重複収録せず、raw request/response、URL、credentialをechoしません。

transport障害/timeout、5xx、不正/予期しないmutation応答は保守的に
`outcome_unknown: true`とし、**rollbackを主張してはいけません**。
HTTP/stdio acknowledgement中断前にNative commitが済んでいる場合があります。
`retryable`はhintであり、未commitの証明やkey/body変更許可ではありません。
local入力検査はHTTP送信前に失敗できますが、送信後の結果不明操作には
caller管理のsame-key復旧が必要です。

## 固定起動identityと上限付きHTTP

信頼する起動設定からadapterの固定`PGAG_MCP_API_URL`と`PGAG_MCP_API_TOKEN`を渡します。
URLはHTTPS originまたはloopback HTTP originに限定し、
credential/application path/query/fragmentは禁止です。root `/`は許可します。
TLS検証を有効のまま維持し、redirectを無効化し、HTTP clientはproxy環境設定を無視します。

bearer tokenは**Native API audience用**で、Native APIがissuer/署名/有効期限/subject対応と
ともに検査します。MCP caller tokenの転送や委譲identityではありません。
callごとのURL/header/token/identity上書きはありません。
`mcp`の`--subject`と`--once`は拒否します。
adapterにDB資格情報やJWT署名keyは不要です。

認証付き起動`GET /v1/capabilities`で`api_version: "v1"`、
`service_version: "0.0.8"`、`schema_version: 7`の一致を確認してからstdioを提供します。
失敗はsecretを含まないsanitized診断で非zero終了します。
固定tokenの更新には再起動が必要で、自動refreshはありません。
Native認証、現在のACL、削除は全callで検査し、起動probeでcacheしません。

HTTP上限は**合計20秒**、**I/O 10秒**、**connect 5秒**、**4 connection**、
**serialize済みNative request 256 KiB**、**HTTP response 2 MiB**です。
transport上限であり、host/stdio bufferの保証、model token予算、
性能の適格性確認、配置時のsizingではありません。

## 信頼と削除の境界

**信頼identityごとにadapterを一つ**起動し、trust境界を越える共有やnetwork公開はしません。
local hostはそのidentityのNative権限を行使できます。
remote MCP認証/OAuth/delegationでその公開を安全にする機能はありません。

Native API response-drain barrierの対象は**adapterへの**HTTP配信までです。
stdio、host UI、LLM context消費まで原子的に拡張するものでは**ありません**。
response cacheがなくても、purge/失効前の応答がadapter/pipe/host bufferや配信済みcontextに
残る場合があります。それらのbyteは回収できません。forget/ACL変更後はhostがcached contextを
破棄して新しく認可済みdataを取得しなければなりません。
hostの操作を強制する**MCP削除通知はありません**。

この追加的なtrusted-client境界はNative active-store purgeや現在の認可付きreplayを
弱めませんが、hostまでのend-to-end削除barrierと表現してはいけません。
WAL、replica、backup、保持metadata、配信済みcontextは引き続き完全消去の適格性範囲外です。

## 依存とprotocolの証拠

[公式SDK v2.2.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0)は
**2026-09-07**公開（prereleaseではない）で、2026-09-17に公式release metadataで確認しました。
その[protocol文書](https://py.sdk.modelcontextprotocol.io/protocol-versions/)は、
modern **`2026-07-28` `server/discover`**と
legacy **`2025-11-25` `initialize`**時代を説明します。

上流の事実はadapter互換性の証明ではありません。これとは別に、限定的な検査では
実stdio SDK `Client` modeとraw JSON fixtureを実行しました。

| 検査したmode | Protocol交換 |
|---|---|
| Modern `2026-07-28`、SDK `Client(mode="auto")` | `server/discover`。raw requestには毎回`params._meta`の`io.modelcontextprotocol/protocolVersion`、`io.modelcontextprotocol/clientInfo`、`io.modelcontextprotocol/clientCapabilities`を含む |
| Legacy `2025-11-25`、SDK `Client(mode="legacy")` | `protocolVersion`、`clientInfo`、`capabilities`付き`initialize`後、`notifications/initialized`を送り、その後toolを呼び出す |

runtime smokeはnon-root production image内で実`pg-agmemory mcp` childを起動し、
固定tokenとprovision済みscopeで同じloopbackのNative APIへ接続します。
**両mode**で4 tool一覧とrecallを検査し、既存日本語/API/worker smokeも維持します。
現在のCI stepは`Test containers and smoke-test production API, worker, and MCP`です。
runnerはApple ContainerとDockerの両方で`jq`を必須とします。

最終Apple Container/native Docker結果を以下に記録しています。
この検査から未検証の旧client、全過去protocol版、特定hostとの互換性を主張してはいけません。

## 検証状態と帰結

過去v0.0.7の証拠は実装
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)の
**144テスト合格**です。最終docsは
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)で、
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899)の
両native jobが合格しています。**v0.0.8/MCP検証ではありません**。

v0.0.8の実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)は、
Apple Containerとnative Docker amd64/arm64で各**214テスト**（既存warning 1件）、
Ruff、strict mypy（source 13ファイル）、non-root productionの
日本語/API/worker/MCPの全smokeに合格しました。
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)の
両jobの実logで、完全一致SHAの検査を確認しています。上記MCP両protocol modeも合格しました。

regression coverageには、**rememberのcommit後**の実HTTP応答喪失、
same-key/body再送、自動retryなしでassertionが一つだけ残ることの検査を含めます。
正確な256/257文字のkey境界と、空白をtrimせず拒否することも検査します。

既存Native memory契約を維持しつつ、4 tool schema、固定identity/認証、
上限付きsanitized transport失敗、mutation結果不明とsame-key再起動復旧、
replayに優先する現在のACL/削除、実stdio protocol versionを検査しています。
MCP/httpxなしのfreshなcore-only installもApple Containerで別途検証しました。
これらの技術検査に合格しても、上記全受入gateは未完了です。
所要時間と範囲は[検証証拠](../STATUS-jp.md#検証証拠)を参照してください。
