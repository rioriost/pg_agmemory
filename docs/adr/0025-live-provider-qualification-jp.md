# ADR 0025: Live provider証拠とCA image修正

[English](0025-live-provider-qualification.md) | [現在の状態](../STATUS-jp.md#v0025--schema-10) | [Profiles](../INFERENCE_PROFILES-jp.md)

- 日付: 2026-09-18
- 状態: v0.0.25/schema 10 local適格性確認済み・完全一致SHAのnative CI待ち
- 拡張元: [ADR 0024: 選択式推論基盤](0024-selectable-inference-jp.md)
- 境界: 正確なprofileの契約証拠であり、provider全体の認定やM2完了ではない

## Decision

上限付きのCA image/既定trust store修正と観測したprovider profile文書を
**v0.0.25**とし、**API v1/schema 10**を維持します。
Docker baseは`SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`を設定します。
non-root production smokeはその環境変数値と
`ssl.create_default_context().cert_store_stats()["x509_ca"] > 0`を検査します。
別のlocal実libpq TLS regressionとnon-root runtime CA検査は下記のとおり合格しました。
local適格性確認全体も**999合格、live skip 5件、既知warning 1件 / 494.49秒**で合格しました。
完全一致SHAのnative CIは**結果待ち**で、検証済みの過去v24 runとは別です。

最小限のpackaging/transport修正であり、新しい推論APIではありません。
provider library/operator CLI契約、SQLのTLS `verify-full`、明示CA file対応、
厳密なrole/catalog guard、I/O上限、sanitized error、application retry/fallbackなしを維持します。
証明書/hostname検証は無効化しません。
HTTP clientは`trust_env=False`を維持し、環境変数のproxyを使いません。
SQL migration、Python依存version変更、
canonical **PostgreSQL 18.6 / pgvector 0.8.6** image変更は不要です。

stageは`m2-selectable-inference`、Native/SDKは**memory resource 31**、
MCPは**4 tool**を維持し、hookも不変です。
`auto_synthesis: false`とglobalな
`model_inference.live_provider_qualified: false`を維持します。
自動ingestion、extraction、要約、公開、compactionはありません。
停止/drain後に対応する**service 0.0.25 / API v1 / schema 10** componentを使い、
混在版rollout互換性は主張しません。

## Why the CA selection changes

Azure試行のpsycopg-binary clientは`sslrootcert=system`で失敗しました。
offline調査で二つのbundled OpenSSL buildのcompiled-in既定CA file不在を確認し、
両方の`SSL_CERT_FILE`対応も確認しました。
既存OS CA bundleの選択により、TLSを弱めずtrust store探索へ対応します。

成功したAzure live実行は、環境変数で渡すDSNに
`sslrootcert=/etc/ssl/certs/ca-certificates.crt`を明示し、
**verify-full/TLS 1.3**を維持しました。使用したのは**v24 `aa364c3` client code**であり、
v25 imageの環境変数既定値ではありません。
したがって、そのlive結果はENV修正の適格性確認ではありません。
下記local TLS regressionとruntime CA検査は独立した証拠であり、
Azureで環境変数のvariantを試した結果ではありません。
そのためのAzure resource再作成は行っていません。
[operator手順](../operations/README-jp.md#tls-trust-store-selection)を参照してください。

## Separate local v25 TLS evidence

**実psycopg/libpqのTLS startup全9 caseがApple Containerで合格**し、
対象Ruff検査も合格しました。
[Regression](../../tests/test_provider_tls.py)は一時CA/server keyとloopback上の
最小PostgreSQL startup fixtureを使い、socketとthread teardownに上限を設けています。
実TLS/client libraryを動かす検査であり、live Azure serverや完全なPostgreSQL DB engineではありません。

`sslrootcert=system`での`SSL_CERT_FILE`利用、明示DSN root CAの優先、
CA不在/未信頼CA/hostname不一致のfail-closed、
`verify-full`から`require`へのdowngrade防止、sanitized errorを確認しました。
**別のnon-root v25 runtime検査**でも期待する`SSL_CERT_FILE`値と
**trusted CA 150件の読込み**を確認しました。150件は観測値で、新たな最低件数契約ではありません。
これらの対象検査は下記local full suite結果とは別であり、native CIの代わりにはなりません。

## Full local v25 qualification

Apple Container `./scripts/test-containers.sh`は
**999合格、live skip 5件、既知warning 1件、494.49秒**でした。
Ruff、mypy **source 22 + strict SDK consumer 1ファイル**、
core/hook/sdk/providersの全4導入smoke、
**新system CA smokeを含む全production smoke**も合格しました。
opt-inのlive 5 caseはskipし、cleanup後のAzure推論再実行はありません。
CA ENV修正はlocal検証済みであり、Azureで環境変数のvariantを試した結果ではありません。
完全一致SHAのv25 native CIはまだ実行しておらず、新CI結果は主張しません。

## What was actually observed

[Profile guide](../INFERENCE_PROFILES-jp.md)に正確なmodel identity、設定、topology、
output、制約を記録しています。以下は過去のv24実行です。

| Scope | 証拠 |
| --- | --- |
| Local Ollama 0.34.1 | Qwen2.5 7B/Qwen3 embedding 0.6Bで英語・日本語の契約4 caseが23.97秒、別のNative synthetic/live 2 caseが3.98秒で合格しました。二つの実行を合わせたlive 5 caseとCLI検査で実model呼出しは8回でした。 |
| Azure Flexible Server | `aa364c3` codeをmountしたApple Containerで**5合格、54 deselected、既知warning 1件 / 20.12秒**、CLI inspect/summarize/embedも成功しました。application推論8回、application retryなしです。 |
| 公開baseline CI | `aa364c3969fda48b52c8fef19c8794ec99cd354b`の[CI 35298758297](https://github.com/rioriost/pg_agmemory/actions/runs/35298758297)は、amd64 **989合格、skip 5件 / 685.52秒**、arm64 **989合格、skip 5件 / 796.17秒**、全check/smokeが合格しました。live caseはskipしており、v25適格性確認ではありません。 |

AzureはWest US 3のAI Services S0、**PostgreSQL 18.6/B1ms/32 GiB**、
観測した利用可能/installed extension **azure_ai 2.0.1**を使いました。
operator承認後に提案版**2.0.0**からprofileを更新し、
従来の公式文書/synthetic fixtureの2.0.0証拠は維持します。
要約は`gpt-4.1-mini` version `2025-04-14`、GlobalStandard capacity 10、
embeddingは`text-embedding-3-small` version 1、GlobalStandard capacity 1で768次元です。
revision labelはoperatorの宣言であり、将来のalias/model更新への暗号学的保証ではありません。

serverのmanaged identityにaccount scopeの**Cognitive Services OpenAI User**を付与し、
API key認証を無効化して、別の制限付きSQL loginを使いました。
SQL inspectは`contract_verified: true`、`inference_tested: false`でした。
その後の推論はuntrusted要約、有限値768個・有限かつ非zero normのembeddingを返しました。
canonical入力、明示upload、完全一致replay、vector recall、purge、purge後拒否は
**別local**のcanonical PostgreSQL 18.6/vector 0.8.6で合格しました。
AzureにはMemoryDB schemaを作成しておらず、MemoryDB全体のAzure hosting認定ではありません。

## Quality and provider limits

**元言語を維持するinstructionにもかかわらず、Azureの英語入力要約が一度スペイン語になりました。**
shape、digest、lifecycle契約の合格は、言語忠実性、grounding、意味的品質gateの合格ではありません。
逸脱を隠したり品質認定済みとしたりしないでください。
要約はuntrustedのままで、reviewが必要です。

OpenAI APIへの直接呼出しは利用者keyが未設定で未試験です。
個別HorizonDB live試験は免除であって合格ではなく、Azure Language呼出しも未試験です。
正確なOllama/Azure profileでの成功は全model/version/providerや将来のdeploymentを
認定しないため、global flagは不変です。
M2/MVP/本番/性能や人による品質確認の完了は主張しません。

application推論8回は課金upstream呼出し8回の証明ではありません。
applicationはretryせず、SQL embeddingは`max_attempts => 1`を要求します。
`azure_ai.generate`に検証済みretry/output-token knobはなく、
一度の`MATERIALIZED` SQL評価もextension内部の課金を制約しません。
cancelは課金停止を証明しません。Native write結果不明時はmodelを再実行せず、
生成payloadとcaller idempotency keyを保持して使います。

## Cleanup and qualification gate

Azure cleanupは13:52期限より前の**2026-09-18 12:07:30 JST**に確認しました。
両deployment、account、SQL server、新規resource group、account scopeのRBACを削除し、
所有soft-deleted Foundry記録をpurgeしました。local test DBとsecret file 6件も削除しました。
所有Azure trial resourceは残らず、追加cloud呼出しも予定していません。
別途承認されたlocal Ollama weightは将来のM2用に保持しますが、Gitには入れません。

資格情報はoperatorのprocess環境変数参照とし、共通JSON/repository fileには入れません。
公開文書にprivate resource識別子やendpointを含めません。
purgeはprovider/platform log全体の消去を意味しません。

local TLS 9 case、対象Ruff、別のruntime CA検査は確認済みです。
v25 full suiteとruntime/導入/型/lint検査全体も合格し、完全一致SHAのnative CIは結果待ちです。
先行template unit 157件、v24通常suite 989件、live試行、公開CIは別の証拠であり、
記録したv25 local結果や結果待ちのnative CIの代わりには使いません。
新結果は[現在の適格性確認状態](../STATUS-jp.md#v0025--schema-10)で管理します。
