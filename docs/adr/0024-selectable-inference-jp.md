# ADR 0024: 選択式推論基盤

[English](0024-selectable-inference.md) | [契約](../STATUS-jp.md#selectable-inference-providers) | [運用](../operations/README-jp.md#selectable-inference-providers)

- 日付: 2026-09-18
- 状態: v0.0.24/schema 10の実装とsynthetic provider契約は適格性確認済み
- 拡張対象: [明示vector](0011-pgvector-retrieval-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 境界: provider基盤であり、M2/MVP/本番/性能/品質の完了ではない

## 決定

既存`httpx==0.28.1`を使う任意の`pg-agmemory[providers]` extra、
typedな`pg_agmemory.providers`、operator CLI
`pg-agmemory infer inspect|summarize|embed --config FILE`を提供します。
callごとにclosed/frozenな`ProviderSettings` profileを一つ選びます。
loopback限定`local_http`、HTTPS `openai_compatible`、または
`flexible_server`/`horizondb`とextension版を明示するSQL `azure_ai`です。
profileにはsecretそのものでなく環境変数名を指定します。
推論URLはNative SDK originと異なりbase pathを許可し、一つ以上のmodelを要求します。
別callでlocal要約とAzure embeddingを選べますが、fallbackはありません。
[完全な設定表](../STATUS-jp.md#closed-configuration-and-input)を参照してください。

server route、SDK memory method、MCP tool、hook actionは追加しません。
Native/SDKは**31 resource**、MCPは**4 tool**、hookも不変です。
推論はmemory保存、enqueue、assertion抽出、公開、compactionを行いません。
schema 10/履歴1〜10と依存versionを維持し、migrationはありません。
stage `m2-selectable-inference`は`optional_provider_adapters`と`model_inference`を追加します。
`interface: "operator_cli_and_python"`、`extra: "providers"`、3 backend、
両Azure product、`inspect`/`summarize`/`embed`、
`automatic: false`、`publishes_memory: false`、`live_provider_qualified: false`です。

## 信頼、課金、永続化の境界

closed入力は`{text}`で、正確なUTF-8 bytesを維持し、空白だけでない
1〜65,536文字/256 KiBです。設定は32 KiB以下、HTTP requestは256 KiB以下、
response/SQL serialize結果は2 MiB以下です。operator timeoutは1〜120秒、既定30です。
HTTP `max_output_tokens`は1〜4096、要約の既定1024で、`text_model`が必須です。SQLでは拒否します。
非defaultの`sentence_count`はFlexible Language modeだけで許可します。
applicationのretry、redirect、proxy環境、backend fallbackはありません。
HTTPは上限付きOpenAI互換chat/embedding shapeで、全vendor対応ではありません。

`SummaryResult`は宣言model、input digest、summary、`untrusted`状態を持ち、
根拠付きassertion、承認、compaction snapshotではありません。
`GeneratedEmbedding`は既存`VectorQuery` + input digestで、
有限・非zeroなnormを持つ正確に768個の有限値を要求し、padding/truncateはしません。
model revisionはoperatorの固定値で、remote aliasやAIMM upgradeの暗号的証明ではありません。
model空間が変わる場合は新identity/revisionが必要です。
明示的なNative input → provider embed → `PutEmbedding`は
現在ACL/digest/model/purge guardを維持します。
upload結果不明時は正確な生成payload/write keyを再利用し、model応答を作り直しません。
sanitizedな`ProviderFailure.error`は`code`、`retryable`、`billing_unknown`を持ち、
外部課金の可能性はNative mutationの`outcome_unknown`と別です。
cancelはrollbackや課金停止を証明しません。

SQL推論は専用autocommit、TLS `verify-full`接続とsystem CAまたはoperator指定CA fileを使い、
canonical transaction/session lockではありません。各SQL function statementにはtransactionがあります。
特権roleとcanonical所有権を拒否し、各callで厳密なextension版、
`pg_depend`のextension所属、一意で互換のnon-set-returning overload、
引数/結果型、SQL `USAGE`/`EXECUTE`を検査します。
一度の`MATERIALIZED`評価とserver側size guardで結果転送を制限します。
これは課金されるupstream呼出しが一度だけとの証明では**ありません**。
SQL embedding/Languageは`max_attempts => 1`を明示しますが、
`azure_ai.generate`には検証済みretry/output-token knobがありません。
extension内部の動作や課金はadapterが保証するものではありません。
adapterはinstall/configureせず、key設定/model registryも読みません。
HTTP `inspect`はnetwork呼出しなしにclientを構築・closeして設定と資格情報headerを検証し、
SQL `inspect`はread-only catalog検査です。
どちらも推論やmodel権限、quota、接続、品質を保証しません。
operatorがapplication外で資格情報/登録を準備し、対応環境では本projectはmanaged identityを推奨します。
query/provider log、retention、同意、budgetはoperator責任です。

## Azure reference boundary

公式参照の確認日は2026-09-18であり、**live service検証ではありません**。

- [Flexible embedding](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-openai): `azure_openai.create_embeddings`はdeployment名を取り、`real[]`を返します。表示signatureで省略されても、互換model向け`dimensions`は1.1.0以降と記載されています。配置overloadを検査し、全modelが768に対応するとはしません。
- [Horizon embedding](https://learn.microsoft.com/en-us/azure/horizondb/ai/generate-vector-embeddings): 第1引数はdeployment名でなく登録model aliasです。両adapterは`dimensions => 768`、`timeout_ms`、`throw_on_error => true`、`max_attempts => 1`を明示します。
- [Flexible AI function](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-ai-functions)と[Horizon AI function](https://learn.microsoft.com/en-us/azure/horizondb/ai/ai-functions): `generate`は`prompt`、`model`、`json_schema`、`system_prompt`と`{name, strict: true, schema}`を使います。文書は完全な配置overloadでなくtext/JSONB応答を説明しています。JSONB schema入力を要求し、text/JSONB結果型を検査してclosedな`{summary}`を検証します。token-limit、timeout、retry引数は作らずstatement timeoutを適用します。
- [Flexible Language](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-cognitive): 明示`language` modeは`azure_cognitive.summarize_abstractive`、任意language、sentence count 1〜20/既定3を使い、全`text[]` partを段落でjoinします。`disable_service_logs => true`、timeout、throw-on-error、1 attemptを要求しますが完全消去ではありません。Horizon Languageは未検証・非対応です。
- [Language lifecycle](https://learn.microsoft.com/en-us/azure/ai-services/language-service/summarization/overview): Summarizationは**2029-03-31**に終了予定で、新規projectはFoundryへ案内されています。そのため本projectは新規構成で`generate`を推奨し、Flexible Language modeは明示optionとして維持します。
- [Flexible版](https://learn.microsoft.com/en-us/azure/postgresql/extensions/concepts-extensions-versions#azure_ai): PG18は`azure_ai` **2.0.0**、PG12〜17は**1.3.1**と記載されています。[Horizon版](https://learn.microsoft.com/en-us/azure/horizondb/extensions/concepts-extensions-versions#azure_ai): PG17は**2.2.1**です。対応表は導入版やfeature同等性の証明ではありません。
- [Horizon概要](https://learn.microsoft.com/en-us/azure/horizondb/overview#limitations)はserviceをpreviewとし、[AIMM](https://learn.microsoft.com/en-us/azure/horizondb/ai/ai-model-management)は承認が必要なlimited previewです。Flexible AI functionsもpreviewです。model aliasは不変weightや自動互換性を保証しません。

推論DSNは別DBにできます。canonical MemoryDBはPostgreSQL 18.6 / `vector` 0.8.6固定のままで、
どちらのAzure productでもMemoryDB全体のhostingを認定しません。

## 検証境界

実装
[`88975a862ff97873c60e5ce53e066e1aa7b52686`](https://github.com/rioriost/pg_agmemory/commit/88975a862ff97873c60e5ce53e066e1aa7b52686)は
Apple Containerのfull `./scripts/test-containers.sh`で**987合格、warning 1件、493.68秒**でした。
完全一致SHAの[CI 35292285229](https://github.com/rioriost/pg_agmemory/actions/runs/35292285229)は、
**amd64 987合格 / 762.27秒、arm64 987合格 / 809.06秒**でした。
全3環境でRuff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入profile、全production smokeも合格しました。
これにはsynthetic HTTPに実operator CLIを接続し、
その後にNative vector upload/replay/purgeを明示するsmokeも含みます。
**987 = 既存821 + 新規166 case**で、HTTP/設定/CLI/lifecycleが51、Azure SQLが115です。
後者にはsynthetic SQL function/extension所属を使う実際の使い捨てPostgreSQL integration 11 caseを含み、
**vendor Azure extension binaryではありません**。
live Azure/実model呼出し、課金resource、private dataの外部送信は使っていません。
契約の適格性確認はlive互換性、人のreviewの有効性、provider budget制御、品質、M2完了の認定ではありません。
`live_provider_qualified`はfalseのままです。この更新の最終docs CIはまだ実行していません。
[適格性確認の証拠](../STATUS-jp.md#v0024--schema-10)と
[過去v23の適格性確認](../STATUS-jp.md#v0023--schema-10)を参照してください。

**UTF-8 identity追加修正 — synthetic providerの適格性確認済み。**
push済みcommit `e3c333e4713e943afaf9615fc15d4a9319101299`は設定起動時に
text/embedding modelの`name`/`revision`と`embedding_target`のUTF-8 encodingを検証し、
CLI serialize前に不正なUnicode surrogateを拒否します。
既存設定test内に回帰variantを追加し、件数は**987**のままです。
full Apple Container再検証は**987合格、499.65秒**で、
Ruff、mypy **22 + 1**、全4導入profile、全production smokeも合格しました。
完全一致SHAの[CI 35294519418](https://github.com/rioriost/pg_agmemory/actions/runs/35294519418)は、
**amd64 987合格 / 823.85秒、arm64 987合格 / 787.03秒**でした。
両native runでRuff、mypy **source 22 + strict SDK consumer 1ファイル**、
全4 optional導入profile、全production smokeも合格しました。
この追加修正の結果は、上記の初期実装とCI 35292285229とは別です。
**synthetic providerの適格性確認**であり、live Azure/model検証やM2完了ではありません。
