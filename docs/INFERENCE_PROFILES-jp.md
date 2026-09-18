# Local Ollama, OpenAI, and Azure SQL inference profiles

[English](INFERENCE_PROFILES.md) | [README](../README-jp.md)

このprofileはprovider呼出しを明示的に選択するもので、自動synthesis、extraction、
ingestion、公開、compactionを有効化しません。現在のv0.0.25/schema 10 follow-up向けの実用guideであり、
**M2完了ではありません**。
記録したlive実行はservice 0.0.24、API v1、schema 10です。
別途のTLS packaging follow-upは、その実行結果で適格性確認したものではありません。
**v25 local適格性確認全体は合格し、native CI待ちです。** Docker baseは`SSL_CERT_FILE`を設定し、
runtime production smokeはその値とroot CA store読込みを検査します。
対象のlocal実libpq TLSとruntime CA検査は合格しました。
Apple Containerのfull `./scripts/test-containers.sh`は
**999合格、live skip 5件、既知warning 1件、494.49秒**でした。
Ruff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入smoke、
**新system CA smokeを含む全production smoke**も合格しました。
完全一致SHAのv25 native CIはまだ実行していません。Azure推論は再実行していません。
stage `m2-selectable-inference`、Native/SDK resource 31、MCP tool 4、
`auto_synthesis: false`、globalな`live_provider_qualified: false`は不変です。
[ADR 0025](adr/0025-live-provider-qualification-jp.md)、
[v25適格性確認の状態](STATUS-jp.md#v0025--schema-10)を参照してください。
契約の正本は[provider契約](STATUS-jp.md#selectable-inference-providers)と
[operator reference](operations/README-jp.md#selectable-inference-providers)です。

## Evidence boundary

**正確なOllama/Azure Flexible Server profileについて上限付きlive契約の証拠があり、
Azure cleanupも確認済みです。品質gate合格やprovider全体の認定ではありません。**
公開済みOllama/OpenAI profile baselineは両native CI architectureで合格しました。既存の
[v24適格性確認](STATUS-jp.md#v0024--schema-10)はsynthetic HTTP/SQL fixtureが対象で、
以下のlive実行とは別です。そのCIやtemplateだけではlive互換性や人による品質認定を示しません。

| Route | 現在のscopeと証拠 |
| --- | --- |
| Local Ollama | 下記の別々の契約/Native lifecycle実行でlive 5 caseが合格し、実operator CLI検査も成功しました。すべてsynthetic textで、**実model呼出しは8回**です。通常suite、公開baselineのnative CI、所有local resourceのcleanupは完了しました。設定内のdigest labelはinstalled weightの自動検証ではありません。 |
| OpenAI | 利用可能な設定templateのみです。利用者の`OPENAI_API_KEY`は未設定で、live OpenAI確認は主張しません。 |
| Azure Foundry + Flexible Server | 正確な`azure_ai` 2.0.1 profileで**live 5合格、54 deselected、既知warning 1件 / 20.12秒**、CLI検査も成功しました。application推論8回、application retryなしです。**英語入力の要約が一度スペイン語になり**、品質は認定していません。cleanupは**12:07:30 JST**に確認済みです。 |
| HorizonDB | このscopeの個別live testは明示的に免除されています。免除は合格・適格性確認ではありません。既存synthetic SQL coverageもlive HorizonDBの証拠ではありません。 |

各live結果にはruntime、model artifact、profile、topology、観測結果を対応付けます。
shape/digest検査の合格は、検索品質、要約の忠実性、privacy、費用、本番適合性の確認ではありません。
`model_inference.live_provider_qualified`は**false**のままです。
記録済み検査はその正確なprofile/runtime/model artifactだけが対象であり、
全version/model/providerの包括的認定ではありません。OpenAIとHorizonDBはlive未試験です。

### Recorded local contract evidence

2026-09-18 JSTに、以下の別々の検査を**Apple ContainerのLinux arm64 client →
guard付きhost bridge → host-loopbackのOllama 0.34.1**という経路で実行しました。
入力はすべてsynthetic textです。

| Check | 観測結果 | 実model呼出し |
| --- | --- | --- |
| 英語・日本語の単体要約/embedding契約4 case | **4合格 / 23.97秒** | 4回 |
| Native embedding lifecycleの`[synthetic]`と`[live]` | live 1件を含む**2合格 / 3.98秒** | live側の2回 |
| 実operator CLIの`inspect`、`summarize`、`embed` | 全成功。要約は未承認の否定を保持し、embeddingは768値 | 2回。`inspect`は推論しません |

これは**二つのtargeted実行を合わせたlive 5 caseと、別途のCLI検査**であり、
5 case一括実行の所要時間やfull suite結果ではありません。
実model呼出しの合計は**8回：契約4＋Native 2＋CLI 2**です。

Native実行には、所有する固定版**PostgreSQL 18.6 / pgvector 0.8.6のtmpfs container**を使い、
test後に削除しました。実canonical入力 → Qwen 768-vector → `PutEmbedding` →
完全一致replay → vector recall → source purge →
replayとstale uploadの`not_found`を確認しました。
実CLI検査は、この明示Native公開workflowと別です。

選択したQwen2.5 modelの契約testでの要約は以下で、
いずれも`status: "untrusted"`、入力digestは完全一致しました。

| Sample | 観測された要約 |
| --- | --- |
| English | Project Cedar is paused. Deployment has not been approved. The next review is scheduled for September 20. |
| Japanese | Cedar計画は一時停止中です。デプロイは承認されていません。次回のレビューは9月20日です。 |

agentの確認では**この2 sampleだけ**について否定と日付が保持されていました。
人による品質認定や他の入力についての証拠ではありません。
英語・日本語のQwen3 embedding呼出しは、有限値768個と有限かつ非zeroなnormを返しました。
serverのMRL次元選択による生成であり、applicationでのpadding/truncateではありません。
所要時間はtest実行記録であり、性能benchmarkではありません。
この限定された結果で、他の入力、provider、model空間の適格性を認定しません。

### Ordinary-suite qualification

過去のv24・Apple Containerでのfull `./scripts/test-containers.sh`実行は
**989合格、live skip 5件、warning 1件、500.46秒**でした。
Ruff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入check/smoke、全production smokeも合格しました。
通常実行のlive skip 5件は意図的なものであり、上記の別途live検証を
989合格へ暗黙に含めてはいません。
500.46秒はこのlocal実行の測定値であり、native CIの時間ではありません。

### Published baseline native CI

公開v24 commit
[`aa364c3969fda48b52c8fef19c8794ec99cd354b`](https://github.com/rioriost/pg_agmemory/commit/aa364c3969fda48b52c8fef19c8794ec99cd354b)
の完全一致SHAで[CI 35298758297](https://github.com/rioriost/pg_agmemory/actions/runs/35298758297)が合格しました。
**amd64 989合格、skip 5件 / 685.52秒、arm64 989合格、skip 5件 / 796.17秒**です。
両方でRuff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入check、全production smokeも合格しました。
CIではlive 5 caseをskipし、上記の実model 8呼出しは別途のlocal Ollama実行です。
このCIはAzure template/試行より前のものであり、それらや後続変更の適格性を認定しません。

## Configuration files

Python 3.12+の分離環境で、repository rootからexampleを実行してください。
このcheckoutからinstallします。別packageの公開を主張するものではありません。

```sh
python3 -m pip install '.[providers]'
```

任意extraはHTTPX 0.28.1を追加します。既存web/database依存を含む、
同じcore `pg-agmemory` distributionです。

| File | 用途 |
| --- | --- |
| [ollama.json](../examples/inference/ollama.json) | `local_http`、`http://127.0.0.1:11434/v1`、選択したlocal text/embedding model、timeout 120秒、要約output最大512 token。API key設定はありません。 |
| [openai.json](../examples/inference/openai.json) | `openai_compatible`、`https://api.openai.com/v1`、`OPENAI_API_KEY`からのbearer資格情報、timeout 60秒、要約output最大512 token。 |
| [azure-flexible-server.json](../examples/inference/azure-flexible-server.json) | 限定されたlive契約の証拠がある`azure_ai` SQL profile。DSNは環境変数参照だけで、extension 2.0.1固定、timeout 60秒。試験resourceは削除済みで、言語品質/TLS packagingの制約は下記に記録します。 |
| [.env.example](../examples/inference/.env.example) | placeholderのみで、環境変数の説明用です。有効な資格情報ではありません。 |

CLIはprocess環境変数を読み、**`.env` fileを自動loadしません**。
実際のOpenAI keyは承認済みsecret managerまたはlocal環境で渡し、
JSON、commit対象file、command-line引数、取得logに記録しないでください。
placeholderでrequestを送らないでください。
実際の`.env`と生成outputはversion controlの対象外にします。
Azure DSN参照も同じ規則で、JSONに資格情報やprivate server endpointを入れません。

commandごとに一つのprofileを選びます。local profileで要約し、OpenAI profileで
embeddingを生成することや、信頼する単一operation用profileの作成も可能です。
これは明示選択でありfallbackではありません。少なくとも一つのmodelが必須です。
embedding専用profileを作る際は`max_output_tokens`を削除してください。

## Local Ollama

| Role | 選択model | operatorから提供されたartifact metadata |
| --- | --- | --- |
| Summary | `qwen2.5:7b` | 7.6B、Q4_K_M、**4683087332 bytes**（約4.7 GB）。 |
| Embedding | `qwen3-embedding:0.6b` | 595.78M、Q8_0、**639150858 bytes**（約639 MB）。 |

対応するprofile revisionは以下です。

- `qwen2.5:7b`: `ollama-sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`
- `qwen3-embedding:0.6b`: `ollama-sha256:ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`

artifactの記録であり、必要RAM、推論結果、latencyの保証ではありません。
選択した両modelのlicenseはApache 2.0です。
upstreamはQwen2.5の英語・日本語対応と、Qwen3 embeddingの100以上の言語対応を説明しています。
0.6B embedding modelのMRL次元は32〜1024に対応し、このprofileでは**768**を要求します。
次元数は[Qwen3 0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)を
参照し、8B modelの次元数を0.6Bに適用しないでください。
applicationは次元数が違うresponseを拒否し、1024次元のresponseを
padding/truncateして適合させることはありません。

host loopbackだけにbindしたserverを使います。承認済みserverがまだ稼働していない場合、
別terminalで起動します。

```sh
OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

以下の明示downloadは今回のlocal実験で承認済みです。
意図したartifactが既にinstall済みなら省略してください。

```sh
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen2.5:7b
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3-embedding:0.6b
```

downloadはmodel registryへ接続し、applicationの自動処理ではありません。
tagは変わり得ます。**pull後、embedding公開前**にlocal tagのdigestを手動確認してください。

```sh
curl --fail --silent --show-error http://127.0.0.1:11434/api/tags
```

選択した各modelについて、返された`sha256:...` digestを、
profile revisionから`ollama-` prefixを除いた値と照合します。
adapterはこの照合やremote weightの検証を**実施しません**。
不一致時はmodel空間について明示的な判断が必要です。
変わった空間のvectorを古いidentity/revisionで公開したり、
古いvectorのlabelだけを変えてmodel不変として再公開したりしないでください。
承認されたdownload済みweightは合計**約5.32 GB（10進）**で、
将来のM2 test用に保持し、**commitしません**。
Native lifecycle用の使い捨てDB containerは削除済みです。
実験前のOllamaは停止状態でした。所有するOllama server、host relay、client relayは
追跡していたprocess IDで停止し、名前を特定したlive client containerとtest imageは削除しました。
full test scriptも自身のcontainer/imageをcleanup済みです。
一時runtime setupから残すものは承認済みmodel weightだけです。
操作対象は所有するprocess/resourceだけにし、無関係なものを停止・削除しないでください。

以下はOllamaと**同じhost network context**で実行します。入力はraw textではなく
一つのJSON objectです。sampleはsynthetic textのみです。

```sh
pg-agmemory infer inspect --config examples/inference/ollama.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused. Deployment is not approved."}' |
  pg-agmemory infer summarize --config examples/inference/ollama.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused."}' |
  pg-agmemory infer embed --config examples/inference/ollama.json
```

HTTP `inspect`はclientを構築・closeし、設定と資格情報headerの形式を検証するだけです。
requestを送らず、接続やmodel利用可否を確認しません。
後続の二つのcommandは実際にmodelを呼び出します。

### Apple Container is a different network context

containerの`127.0.0.1`はhostの`127.0.0.1`では**ありません**。
checked-inのhost profileをApple Containerで使っても、container localhostから
host Ollamaに直接到達できません。
検証したlocal実験では、Ollamaのbindや本番`local_http`検証を広げず、
loopback/network境界を越える二つのguard付き一時relayを使用しました。
live caseとCLIの合格は上記のclient → guard付きhost bridge → host-loopbackという
限定された経路の証拠であり、一般的なcontainer network設定手順ではありません。
一時relayは停止済みです。relay設定の詳細はここに掲載していないため、
未報告のinterfaceやportを推測しないでください。

exampleを動かすためにloopbackを`0.0.0.0`へ置き換えたり、
provider URL検査を弱めたりしないでください。
host上でCLIを動かすか、別途review・検証したtest topologyを使います。
relayはinstallされるproduct機能ではありません。

## OpenAI template

[OpenAI profile](../examples/inference/openai.json)はtext snapshot
`gpt-4.1-mini-2025-04-14`を選び、operator revisionは`2025-04-14`です。
embeddingは`text-embedding-3-small`で768次元を要求し、operator revisionは
`operator-openai-text-embedding-3-small-768-v1`です。
embedding revisionはlocalな宣言であり、不変なremote modelのpinではありません。
model access、現在のservice規約、quota、課金はoperatorの責任です。
ここにkeyやlive OpenAI結果は含みません。

`OPENAI_API_KEY`を設定し、外部への課金され得る呼出しを承認した後、
同じinterfaceを使えます。

```sh
pg-agmemory infer inspect --config examples/inference/openai.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused. Deployment is not approved."}' |
  pg-agmemory infer summarize --config examples/inference/openai.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused."}' |
  pg-agmemory infer embed --config examples/inference/openai.json
```

OpenAI互換`chat/completions`と`embeddings`を使いますが、
全vendor/modelに対応するadapterではありません。
applicationによるretry、redirect、proxy環境変数の利用、自動provider切替はありません。
response消失時は課金結果が不明になり得ます。
cancelは推論や課金が停止した証拠ではありません。

## Azure Flexible Server template

### Recorded Azure live evidence

承認済み試行は**West US 3**、AI Services **S0**、
PostgreSQL **18.6 / B1ms / 32 GiB**、installed `azure_ai` **2.0.1**を使いました。
要約は`gpt-4.1-mini`、version `2025-04-14`、GlobalStandard capacity **10**、
embeddingは`text-embedding-3-small`、version **1**、GlobalStandard capacity **1**で768次元を要求しました。
serverのmanaged identityにaccount scopeの**Cognitive Services OpenAI User**を付与し、
**API key認証を無効化**して、別の制限付きSQL loginを使いました。

**`aa364c3` codeをmountしたApple Container client**で選択したlive 5 caseを実行し、
**5合格、54 deselected、既知warning 1件 / 20.12秒**でした。
実CLI `inspect`/`summarize`/`embed`も成功しました。
**application推論は8回**（pytest 6＋CLI 2）で、**application retryはありません**。
これは課金されるupstream呼出し数やその保証ではありません。
SQL `inspect`は`contract_verified: true`、`inference_tested: false`を返し、
inspection自体はmodelを呼び出しません。
要約は`untrusted`のままで、embeddingは有限値768個、有限かつ非zeroなnormでした。
Nativeのcanonical入力/upload/replay/recall/purgeとpurge後拒否のlifecycleは、
**別のlocal PostgreSQL 18.6 / pgvector 0.8.6**で合格しました。
cloud serverにMemoryDB schemaは作成していません。

**実際の品質逸脱：**元言語を維持するinstructionにもかかわらず、
英語入力の要約が一度**スペイン語**で返されました。
合格したtestは契約の検査であり、言語忠実性、grounding、意味的品質の検査ではありません。
この逸脱を隠したり、言語/grounding/品質gateの合格として扱ったりしないでください。
生成要約にはreviewが必要で、untrustedのままです。
`model_inference.live_provider_qualified`も**false**を維持します。
HorizonDB、Azure Languageの呼出しは未試験で、OpenAI APIへの直接呼出しも未試験です。

Azure実行では**TLS 1.3、`verify-full`、明示的なOS CA bundle path**を使いました。
live caseをskipしたCI 35298758297とは別で、新しいDockerの`SSL_CERT_FILE`既定値を試験したものではありません。
先行する**2.0.0 template**はhost mountなしで同梱を確認し、
Ruff、mypy **22 + 1**、**unit 157合格、integration 13件deselected / 2.26秒**でした。
既存coverageにtemplate parse caseを1件追加した検査であり、live Azure実行ではありません。
過去の2.0.0文書/fixture証拠と、今回観測・試験した**2.0.1**は区別して保持します。
TLS packaging follow-upの新しいfull suite/CI結果は主張しません。

[Azure profile](../examples/inference/azure-flexible-server.json)は以下を選びます。

| Setting | 宣言値 |
| --- | --- |
| Backend/product | `azure_ai` / `flexible_server` |
| SQL接続参照 | `database_url_env: "PGAG_AZURE_INFERENCE_DATABASE_URL"` |
| 必須extension版の固定 | `azure_extension_version: "2.0.1"` |
| Summary | `azure_summary_mode: "generate"`、deployment `pgag-summary`、revision `gpt-4.1-mini-2025-04-14` |
| Embedding identity | `text-embedding-3-small`、revision `azure-openai-text-embedding-3-small-1-768-v1`、768次元、cosine、`l2-f32-v1` |
| Embedding deployment | `embedding_target: "pgag-embed"` |
| 呼出しごとのtimeout | `timeout_seconds: 60` |

template内のdeployment名は再利用可能なoperator選択labelであり、
account識別子やresourceの現存保証ではありません。試験resourceは削除済みです。
revision labelはoperatorの意図を記録するもので、adapterはcloud model版や
alias更新を暗号学的に検証しません。
Flexible Serverの`embedding_target`はdeployment名です。
HorizonDBのregistry alias契約とは異なり、ここではHorizonDBを試験しません。
SQLでは`max_output_tokens`を禁止しており、HTTP profileの512 token設定をコピーしないでください。

### Privilege separation and setup

operations担当者がcanonical MemoryDBとは別に、**新規・一時・専用のPostgreSQL 18
推論serverとcloud model resource**を準備します。入力はsynthetic textだけにします。
provisioning、管理者setup、model呼出し、cleanupはその一人の担当範囲であり、
profile/CLIがAzure resourceを作ることはありません。
subscription ID、resource名、実service endpoint、DSN、資格情報は公開docsやexample出力に含めません。

1. **管理者setup：** `azure_ai`をallowlist/installし、installed versionを2.0.1固定値と
   照合して、承認modelのdeploymentとroutingをapplication外で設定します。
   network accessは承認されたtest client/経路に制限し、testを通すため広く公開しないでください。
   [extension setup](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-overview)を参照してください。
2. **Upstream identity：** Flexible Serverのsystem-assigned managed identityを有効にし、
   **Cognitive Services OpenAI User**を専用model resourceのscopeで付与します。
   subscription全体には広げません。管理者が`azure_openai.auth_type`を
   `managed-identity`にし、承認endpointを設定します。
   [managed identity setup](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-enable-managed-identity-azure-ai)を参照してください。
   これはserverからmodelへの認証であり、CLIのPostgreSQL接続認証やSQL権限付与ではありません。
3. **別runtime login：** setup管理者とは別に、新しい最小権限SQL loginを作ります。
   必要なDB `CONNECT`、schema `USAGE`、deployed互換推論functionの`EXECUTE`だけを与え、
   継承/PUBLICを含む実効権限も確認します。superuser、`BYPASSRLS`、
   canonical memory所有権、`azure_pg_admin`、`azure_ai_settings_manager`、
   `model_registry_manager`への所属を与えないでください。
   runtime検査の失敗をadmin DSNへの差替えで回避しないでください。
4. **Client接続：** そのruntime loginのDSNを承認済みsecret経路で
   `PGAG_AZURE_INFERENCE_DATABASE_URL`へ渡します。
   adapterはsystem CAまたは明示承認したCA fileによるTLS `verify-full`を強制します。
   専用autocommit接続はcanonical memory transaction/session lockとは別ですが、
   個々のfunction statementにはSQL transactionがあります。
   TLSを無効化したりextension固定/catalog guardを弱めたりしないでください。

### TLS trust-store known issue

試験したpsycopg-binary clientでは`sslrootcert=system`で失敗しました。
Linux clientのDSNに`sslrootcert=/etc/ssl/certs/ca-certificates.crt`を明示すると、
Azure live実行を通じて`sslmode=verify-full`とTLS 1.3を維持して接続できました。
operatorが選択したCA fileであり、自動fallbackやTLS downgradeではありません。
完全なDSNはprocess環境変数で渡し、共通JSON profileやrepositoryには入れません。
offline調査で二つのbundled OpenSSL buildのcompiled-in既定CA fileが存在しないことを確認し、
両方が`SSL_CERT_FILE`に対応することも確認しました。
このOS bundleが存在して読取り可能なLinux環境では、trust storeを明示します。

```sh
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
```

v25 Docker baseはこの環境変数を設定し、non-root runtime smokeはその値と
root CA storeにCAがあることを検査します。
**local実libpq TLS 9 caseと対象Ruffが合格し、別のnon-root v25 runtime検査で
環境変数値とtrusted CA 150件を確認しました**。
local full suiteと全production smokeも合格し、native CIは結果待ちです。
Azure live実行で使ったのは明示DSN CA fileであり、**この環境変数修正ではありません**。
後者がlive Azureで試験済みとは主張しないでください。
証明書/hostname検証は維持します。local TLS regressionのためのcloud再provisioningや追加呼出しは予定していません。

adapterはextensionのinstall/configureやkey設定/model registryの読取りをしません。
各operation前に厳密なextension版、extension所有の互換non-set-returning overload、
SQL権限を確認します。Native lifecycle testの`PGAG_TEST_DATABASE_URL`は
**別のlocal使い捨てcanonical test DB**であり、このAzure推論DSNや本番dataではありません。
MemoryDB全体のAzure hosting認定ではなく、canonical側の
PostgreSQL 18.6 / pgvector 0.8.6要件は不変です。

### Inspect before explicit inference

管理者setup後、最小権限loginでliveのread-only catalog検査をします。
modelは呼び出しません。

```sh
pg-agmemory infer inspect --config examples/inference/azure-flexible-server.json
```

SQL `inspect`の成功だけではupstream identityの反映、model access、quota、
service稼働、推論成功を確認できません。
課金され得る呼出しを承認した後だけ、synthetic入力で明示実行します。

```sh
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused. Deployment is not approved."}' |
  pg-agmemory infer summarize --config examples/inference/azure-flexible-server.json
printf '%s\n' '{"text":"Synthetic note: Project Cedar is paused."}' |
  pg-agmemory infer embed --config examples/inference/azure-flexible-server.json
```

これは将来の別途承認済みsetup用に残したexampleであり、
削除済みtrial resourceの再作成や現時点の追加呼出しを促すものではありません。
要約はuntrustedのままで、embeddingは有限値768個と非zero normが必須です。
SQL embeddingは`dimensions => 768`と`max_attempts => 1`を要求します。
applicationはretryしませんが、`azure_ai.generate`には検証済みretry/output-token knobがありません。
一度の`MATERIALIZED`呼出しでも課金upstream呼出しが一度だけとは限らず、
cancelは課金停止の証拠ではありません。後続Native writeの冪等retry用に正確な生成payloadを保持します。
[Flexible embedding](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-openai)と
[AI functions（preview）](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-azure-ai-functions)を参照してください。
文書上の機能はinstalled catalogやlive証拠の代わりにはなりません。

### Two-hour cleanup plan

承認windowは**2026-09-18 11:52〜13:52 JST**で、作成・test・cleanupを含み、
cleanup期限は**13:52 JSTより前**でした。**12:07:30 JST**にcleanupを確認し、
両model deployment、AI Services account、SQL server、新規resource group、
account scopeのRBAC assignmentを削除しました。
所有するsoft-deleted Foundry account記録もpurgeし、
local test DBと**secret file 6件**を削除しました。
所有するAzure trial resourceは残っておらず、追加cloud呼出しの予定もありません。
windowはoperatorの期限であり、自動resource TTLではありません。

operations担当者は呼出しを停止し、所有model deploymentを先に削除してから、
専用SQL serverと他の所有trial resourceを削除し、
soft-deleteされたFoundry/AI Services accountをpurgeします。
resource削除とsoft-deleted accountの不存在を両方確認し、
resource group削除だけをpurgeの証拠にしないでください。
purgeは不可逆で別途management-plane権限が必要ですが、
SQL runtime loginや推論identityにその権限を与えるものではありません。
[Microsoftのrecovery/purge手順](https://learn.microsoft.com/en-us/azure/ai-services/recover-purge-resources)は、
provisioned deploymentの課金がpurgeまで続く場合があることも注意しています。
private識別子を含めずsanitizedなtest/cleanup証拠を記録してください。
account purgeはprovider/platform log保持についての包括的な消去保証ではありません。

## Model space, exact input, and explicit publication

三つのembedding profileはいずれも`dimensions: 768`、`distance_metric: "cosine"`、
`normalization: "l2-f32-v1"`を宣言します。同じ次元数や似たmodel名でも
Qwen、OpenAI、Azureのvectorを相互交換できるわけでは**ありません**。
生成、保存済みembedding、vector queryの間でmodel identity/revision全体と
入力形式を一致させてください。model空間の変更には新しいidentity/revisionを割り当て、
古いvectorのlabelだけを変更しないでください。
digest形式でも、revision文字列による暗号学的な強制検証はありません。

`InferenceInput`は渡されたtext bytesを維持し、`input_digest`は
その正確なUTF-8 textのSHA-256です。Qwenなどにtask prefixを暗黙追加しません。
canonical memoryでは`embedding_input`が返したtextをそのままproviderへ渡してください。
instructionの前置、空白の正規化、異なる入力用digestの捏造をしないでください。
別の入力形式の設計には明示的なversioningと互換性対応が必要であり、
このprofileへの隠れたprefix追加で代用できません。

CLI outputはsuccess/error envelopeです。成功した要約も
`status: "untrusted"`であり、根拠付きmemoryや承認ではありません。
生成embeddingは自動uploadされません。公開する場合は
`embedding_input` → provider `embed` → `PutEmbedding`を明示的に使い、
現在ACL、revision、digest、model空間、purge検査を維持します。
[明示upload workflow](operations/README-jp.md#selectable-inference-providers)を参照してください。
memory writeの結果不明時は正確な生成payloadとcaller idempotency keyを保持し、
retry再構成のためmodelを再実行しないでください。

## Opt-in live checks

[live test](../tests/live/test_inference.py)は`PGAG_LIVE_PROVIDER_CONFIG`で
profileを選び、**この変数が未設定ならlive推論をskipします**。
選択したprofileの内容が不正、または必要なAPI key/SQL DSNが未設定なら失敗し、
黙ってskipすることはありません。provider failureはsanitizedなままです。
textまたはembedding modelがないprofileでは、対応するcaseをskipします。
意図的に承認したlive実行の場合だけこの変数を設定してください。

両model capabilityを設定した場合、`-m live`は
**5 case・6回のmodel呼出し**を選択します。

| Case | Model呼出し | Scope |
| --- | --- | --- |
| `tests/live/test_inference.py`の4件 | 4回：英語・日本語の要約とembedding | 要約のidentity/digest/空でないuntrusted output、embeddingのidentity/digest/正確に768個の有限値・有限かつ非zeroなnorm。 |
| [test_providers.py](../tests/test_providers.py)の`[live]` 1件 | 追加のembedding 2回 | 既存Native embedding lifecycleは`[synthetic]`とlogicを共有し、canonical入力、明示upload、同一key replay、vector recall、purge後のreplay/stale upload拒否を確認します。 |

Native caseには、test harnessのsetup権限を持つ**使い捨てPostgreSQL DB**を指す
`PGAG_TEST_DATABASE_URL`が必要です。本番memory DBは使わないでください。
DBがなければfixtureがskipするため、model単体4 caseだけでは
5 case全体の適格性確認になりません。

記録済みOllama/Azure profile検査は、model単体4 caseも含めて**Apple Container**で実行しました。
Azure推論は明示DSN CA fileと別local canonical test DBを使いました。
別途承認して準備したtest container内のrepository rootで、
`PGAG_LIVE_PROVIDER_CONFIG`にcontainer内で利用可能なprofile pathを設定し、
使い捨てDBと、host Ollamaの場合はreview済みrelay topologyを準備してください。
hostの環境変数やchecked-inのhost-loopback endpointだけでは、そのtopologyは構成されません。
test依存がinstall済みなら、selectorは以下です。

```sh
python3 -m pytest -q -m live tests/live/test_inference.py tests/test_providers.py
```

synthetic textによる契約/lifecycle検査であり、意味的な品質評価ではありません。
test memoryを明示公開するのはNative lifecycle caseだけです。
公開済みOllama/OpenAI baselineのprofile unit case 2件は、確認済みの**通常989合格
（既存987件＋profile 2件）**に含まれ、その実行ではlive 5件をskipしました。
実測結果は[通常suiteの適格性確認](#ordinary-suite-qualification)を参照してください。
opt-in live実行や新しいAzure作業とは別の結果です。
OpenAIへの切替には利用者のkeyが必要で、課金され得ます。live OpenAI実行は主張しません。
CI全体でlive推論を有効化せず、commandの記載を実行成功の証拠と解釈しないでください。

## Licenses and upstream references

repositoryのMIT licenseはmodel weightや外部API serviceを再licenseしません。
選択したQwen artifactは別途Apache 2.0の対象です。
該当noticeを保持し、使うartifact自体の規約を確認してください。
Qwen3-Embedding-0.6Bの公式model-card metadataでも`apache-2.0`を確認済みです。
OpenAI/Azure serviceにはそれぞれの規約、access control、data取扱い、保持、課金が適用されます。
local downloadの承認は、機密memoryを任意providerへ送る許可ではありません。

- [Ollama Qwen2.5 7B](https://ollama.com/library/qwen2.5:7b)
- [Ollama Qwen3 embedding 0.6B](https://ollama.com/library/qwen3-embedding:0.6b)
- [Qwen3 embedding 0.6B model cardとlicense](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Ollama OpenAI互換性](https://docs.ollama.com/api/openai-compatibility)
- [OpenAI GPT-4.1 mini snapshot](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [Azure SQL境界と公式reference](adr/0024-selectable-inference-jp.md)
