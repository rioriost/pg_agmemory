# ADR 0012: 型付き非同期Python SDK

[English](0012-python-sdk.md) | [契約](../STATUS-jp.md#python-sdk) | [運用](../operations/README-jp.md#python-sdk-operations)

- 日付: 2026-09-17
- 状態: 上限付きv0.0.12/schema 8で採用・検証済み。localと両native architectureで確認
- 拡張対象: [共有Native adapter境界](0009-implicit-recall-hook-jp.md)、[capture](0010-atomic-capture-jp.md)、[pgvector retrieval](0011-pgvector-retrieval-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MITは不変、二言語文書
- 受入: M0〜M3、MVP、本番、性能、記憶品質、DR、完全消去gateの完了ではない

## 決定

`pg_agmemory.sdk`から`AsyncMemoryClient`と`MemoryClientError`を公開し、
`pg_agmemory.models`の型付きNative request/response modelを再利用します。
[契約](../STATUS-jp.md#型付きresource-method)に列挙した**全24 public memory resource method**を対象にします。
observe/capture/remember/revision、recall/explain/forget/削除進捗、
embedding input/upload、entity/relation/graph、job/retry、
checkpoint/restore、tool-effect intent/history/transitionです。
CLI管理やworker実行のwrapperではありません。
capabilitiesは内部handshakeであり、SDKのhealth/OpenAPI downloadやraw任意requestの入口ではありません。
server resource endpointは追加しません。

任意の`pg-agmemory[sdk]` extraが追加するのは**httpx==0.28.1だけ**です。
FastAPI、psycopg、Janomeを引き続き含む**同じcore distribution**であり、
独立公開の軽量packageではありません。PyPI公開は主張しません。
PEP 561の`py.typed` markerを同梱します。
HTTPXがなければSDK importは固定の導入案内`ImportError`になります。
`mcp`/`hook` extra由来のHTTPXでも依存を満たし、Pythonは導入元extraを区別しません。
Docker test/runtimeは`sdk`・`mcp`・`hook`を含み、
分離したsdk-only導入はMCP SDKを導入してはいけません。

## Lifecycleと権限

`async with AsyncMemoryClient(api_url, api_token) as memory:`を必須とします。
constructorの明示引数は固定`NativeSettings`を使い、HTTPS originまたは
loopback HTTP originとし、application path、userinfo、query、fragmentを禁止します。
検証するのはtokenの形でありidentityではなく、serverが実際に認証します。
設定errorは`MemoryClientError`でなく既存のsanitized `ValueError`です。
scopeは現在のserver ACLを狭めるだけで、bearer identityを変更できません。

entryは所有HTTP clientを作成し、認証付きcapabilitiesで厳密な
**service 0.0.12 / API v1 / schema 8**を確認し、entry失敗時は`finally`でresourceを閉じます。
entry前/exit後は`client_not_open`、再entryは`client_already_used`で拒否します。
exitは接続resourceの解放だけで、**保存memoryの消去ではありません**。
callerはexit前に未完了taskをawaitするかcancel後にawaitします。
client closeはrequestのschedule/cancel管理でもDB rollbackでもありません。
環境変数の暗黙読込み、DB資格情報、cache、provider呼出し、host登録、
delegation、自動capture/job、token refreshは追加しません。
同期clientとTypeScript SDKはありません。

## 型付きexchange、上限、結果不明

mutable instanceの不正を検出するためrequest modelをcall時に再検証し、
最初のoutbound network await前にrequestのsnapshotを作ります。
model構築ではSDKの外で別に通常のPydantic `ValidationError`が発生し得ます。
そこに含まれるprivate入力詳細をlogに出さないでください。
UUID path引数とcaller管理keyword-only `idempotency_key`を送信前に検査します。
keyは**1〜256文字の可視ASCIIで、trimしません**。
`forget`の両modeを含むすべての変更にkeyが必要です。
Native型付きmodelを返し、`explain`はepisode/assertion型、
`forget`はrequest modeに対応するpreview/purge型を選びます。
Nativeの期待200/201/202 statusを維持し、`forget`はpreview/purgeとも202です。

MCP/hook semanticsを変えず上限付きNative HTTPを再利用します。
**exchange全体20秒、I/O 10秒 / connect 5秒、4接続、TLS検証あり、
proxy環境不使用、redirectなし、応答2 MiB**です。
requestは**256 KiB**で、SDK `create_checkpoint`だけ**1 MiB**です。
adapterの上限は引き上げません。

`MemoryClientError`は既存`AdapterFailure`のaliasで、安全なdataは`.error`内の
`code`、`retryable`、`outcome_unknown`、`native_status`、`request_id`です。
raw応答/入力/tokenの診断は返しません。SDKだけが現在のNative domain error codeを許可し、
MCP/hookの既存safe code集合を維持します。未知server codeは`native_api_error`へ写像します。
local不正request/key/UUIDは送信前にsanitized `invalid_request`、
`outcome_unknown: false`を返します。

**自動retryや新keyへの交換はありません**。
変更のnetwork/5xx/不正応答/想定外成功応答は保守的に結果不明と扱います。
送信前に正確なcaller key/bodyを保持し、現在ACL、削除、replay guardに従って
同じ組で照合します。未commitを推測してはいけません。
cancelはSDK errorへ変換せず伝播します。処理中の変更cancelにも照合が必要で、
rollbackではありません。`retryable`は情報であり、自動retry指示ではありません。

## 互換性と帰結

**schema 8のapplication-only更新**であり、schema 9 migrationはありません。
固定PostgreSQL **18.6**と**`public`内の`vector` 0.8.6** image、
schema履歴/extension起動検査、既存vector/lexical/capture/checkpoint/job/effect契約を維持します。
旧API、worker、adapter、hook、SDK callerを停止/drainし、
版を混在させず対応v0.0.12 componentを導入します。
stageは`m2-python-sdk`で、capabilitiesに`python_sdk` metadataとして
`installation: "sdk-extra"`、`async: true`、`automatic_retry: false`を追加します。

返されたmemoryは根拠であり、信頼する指示や現在の事実の保証ではありません。
Native JSON全体のUTF-8 byte予算、不完全coverage、現在ACL、purge/replay、
host/backup/WAL消去の制限は不変です。contextを閉じても返却済みcopyを撤回できません。

## 検証境界

最終v0.0.12 **local Apple Containerとnative Docker amd64/arm64検査は合格**しました。
各環境426テスト、既存warning 1件、Ruff、strict mypy（source 18ファイル）、
別のstrict型付きconsumer（1ファイル）、真のcore/hook/sdk wheel導入、
同梱`py.typed`、non-root production全smokeが合格しました。
SDK unit 76 + integration 5テスト（新規81、既存345）を含み、実HTTPで全24 methodを検査しています。
合格したfixtureは全routeの実HTTP検査、commit済み応答喪失からの復旧、
request/error/lifecycle上限、core/hook/sdk分離導入、non-root production SDK lifecycleを対象にします。
SDK smokeはcapture/replay、pending job読取り、embedding input/upload、
exact vector recall、preview/purge/削除進捗、capture replay拒否を検査します。
workerは呼ばず、既存の実capture-worker smokeは別です。
実装[88e1206](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)は
[CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945)に合格しました。
実logで完全一致SHA、件数、検査、production全smokeを確認しています。
所要時間は**local 292.76秒 / amd64 484.79秒 / arm64 472.49秒**です。
[検証済み証拠](../STATUS-jp.md#v0012--schema-8)を参照してください。
所要時間は性能benchmarkや本番適格性の認定ではありません。
検証済みv0.0.11実装CI 35189448403と最終docs CI 35190495385は
別々の[過去記録](../STATUS-jp.md#v0011--schema-8)であり、SDK検証ではありません。
