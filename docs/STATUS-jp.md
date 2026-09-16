# 現在の契約と制限

[English](STATUS.md) | [プロジェクトREADME](../README-jp.md) | [実装プラン](PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**M1の初期sliceであり、M0/M1全体の完了、MVP完成、本番適格性の確認を意味しません。**
実装プランは将来の要求を示すもので、現在のAPIそのものではありません。
性能、記憶品質、災害復旧、完全消去の受入目標は未測定または未認定です。
ローカルとCIの検査が合格しても、これらのgateが完了したとは扱いません。

## 実装済みの範囲

アプリケーションの永続化先はPostgreSQLのみです。外部memory DB、
モデルサービス、durable queue、ファイルベースのmemory indexはありません。

| Endpoint | 現在の動作 |
|---|---|
| `POST /v1/observe` | caller指定の発生時刻・同意参照とともにepisodeを1件保存。revisionは`1`、`synthesis_job_id`は`null`で、job enqueueは行わない |
| `POST /v1/remember` | 同一scopeの読取り可能なepisodeからの原文引用を根拠とし、明示的に要求された構造化assertionを保存 |
| `POST /v1/recall` | PostgreSQL全文検索で許可済みepisode/assertionを検索し、byte予算内の決定的context packを生成 |
| `POST /v1/explain` | episodeまたはassertionの根拠を返す。revisionは`1`のみ。過去訂正やranking traceのAPIはない |
| `POST /v1/forget` | 明示IDによる`preview`または`purge`。任意selectorや`suppress` modeは受け付けない |
| `GET /v1/deletions/{receipt_id}` | 許可された削除receiptと、未解決のoperator-managed backup状態を返す |
| `GET /v1/capabilities` | 現在の機能、上限、未対応機能を返す。認証必須 |

型付きrequest/response modelで定義したOpenAPI schemaを
`/docs`と`/openapi.json`で公開します。schemaの生成済みファイルは不要です。
`/healthz`は起動検証後のprocess livenessであり、
PostgreSQLへの継続的なreadiness検査ではありません。

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
  migration/provision用の管理者資格情報を分離します。
- JWKS discovery/rotation、delegated identity、複数issuerのidentity管理、
  公開membership管理APIは未実装です。

認証済みAPI requestごとに短命の新規connectionを開き、connection poolは使いません。
runtimeの`psycopg-pool`依存もありません。
**tenant単位のsession advisory lock**で処理を直列化し、transactionのcommitと
buffer済みHTTP応答の送出が終わるまで保持します。この正しさ優先のdrainにより、
同一tenantの先行応答をサービスがまだ送出中にpurgeがbarrier完了を通知することを防ぎます。
配信済みのdataやnetworkへ渡したbyteを回収することはできません。
遅いclientはtenantの処理を妨げ得ます。throughputは未測定です。

管理者によるmembership変更も**同一session lock**を取得し、
transaction内で権限と`access_epoch`を更新してcommitした後に、
lockを解放するかconnectionを閉じる必要があります。
この手順を使わない変更はrequest/drainの競合保証の対象外です。
[運用手順](operations/README-jp.md#membership変更とrequest-drain)を参照してください。

## 根拠、同意、時間

`remember`は`explicit_intent: true`と、重複のない1〜32件のepisode根拠IDを
必須とします。各引用はepisode本文に文字列として含まれていなければならず、
両objectは同一scopeに属します。このsliceではassertionを別assertionの
sourceにはできません。subjectとvalueはtextであり、解決済みentity graphではありません。

原文引用の検査はprovenanceを確認するだけで、**意味的な支持や真実を認定しません**。
引用が指定assertionを証明するかをサービスは推論しません。
記憶は`reported`、confidenceは`score: null`、`method: "uncalibrated"`です。
現在の外界の事実として断定するにはsourceへの再照会が必要です。
取得本文は根拠資料であり、信頼できる指示ではありません。

`consent_reference`はcallerによる同意の申告を記録します。同意台帳の検証、
capture-policy engine、secret/PII自動除去はありません。
callerは許可済み・除去処理済みのdataだけを送信してください。

assertionにはvalid/system intervalがありますが、revisionは`1`のみです。
`as_of`と`known_at`はその期間をfilterし、episodeは発生・記録時刻でfilterします。
省略したvalid boundは無限端です。これは**訂正履歴ではありません**。
supersession、過去revisionの更新、fact間の競合解決はありません。
過去時点の照会で現在の認可や削除を回避することはできません。

## 検索と予算

全文検索はPostgreSQLの`simple`設定、`plainto_tsquery`、`ts_rank_cd`を使います。
BM25、日本語の分かち書き、vector検索、hybrid retrievalではありません。
空の`query`を許可し、scope・時間条件で参照可能なitemを、
件数とbyteの上限内で取得します。

request field名は`token_budget`ですが、`utf8-bytes-v1`はmetadata・引用を含む
serialized context packの**UTF-8 byte数**を予算として扱います。
応答には`budget_unit: "utf8_bytes"`、`token_count: null`、
`exact_token_count: false`を明示します。保守的なfallbackであり、
モデルの正確なtokenizerやHTTP応答全体のsize制限ではありません。
item単位で除外し、packのmetadataすら収まらない場合は`422`を返します。

request bodyは256 KiB、返却itemは最大100件、予算値は64〜8,000
（implicit modeは最大2,000）です。件数・予算による省略を`coverage.truncated`で
示します。空の選択結果は`not_found`または`budget_exhausted`です。
`retrieval_complete`は世界の知識の完全性を意味しません。
implicit modeはrequest optionであり、自動harness hookの実装ではありません。

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
`purge`は1〜100件のIDを受け付け、現在の**episode → assertion**という
依存関係だけを最大10,000件の派生assertionまで辿ります。
これを超えるclosureは一部削除せず`422`で拒否します。
複数sourceのassertionもsourceを一つ消せば再生成せず削除します。
assertionの削除ではsource episodeを削除しません。

purgeは対象episode/assertion本文と依存する根拠引用を同期削除した後、
**同じtransaction内で**scopeに束縛されたopaqueな削除markerと時刻を
`memory_ops.object_tombstone`へ挿入します。`memory.object`に`deleted_at`列はなく、
SELECT RLSがtombstoneのあるobjectを除外します。
同じtransactionで`deletion_epoch`を進めてreceiptをcommitします。
`active_store_purged`を含むHTTP `202`は、**queue上のpurge jobや完全消去証明ではありません**。
opaque object記録、object tombstone、audit/receipt metadata、
tenant-keyed HMACのsource/idempotency tombstoneはtenantの存続期間中保持します。
自動期限切れやtenant完全消去workflowはありません。

receiptは`backup_status: "operator_managed"`、
`backup_retention_deadline: null`を返します。旧DB page、WAL、replica、backup、
配信済みcontextの消去は証明しません。完全消去保証や本番complianceへの適格性は未確認です。
restoreは最新の削除台帳とACL失効を再適用するまで隔離してください。
自動recovery/replayとDR適格性確認は未実装です。

## 検証証拠

公開repository: [rioriost/pgag_memory](https://github.com/rioriost/pgag_memory)。
2026-09-16に実装sessionから次の成功結果が提供されました。

| 環境 | Command | 結果 |
|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | Ruff、mypy、19件のテスト、runtime HTTP health smokeが合格 |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | Ruff、mypy、19件のテスト、runtime HTTP health smokeが合格 |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | Ruff、mypy、19件のテスト、runtime HTTP health smokeが合格 |

Dockerの両jobは
[GitHub Actions run 35078936073](https://github.com/rioriost/pgag_memory/actions/runs/35078936073)
で成功しました。報告された実装/CIの実行結果であり、
文書作業での独立した再実行ではありません。初期test suiteの検証であって、
M0/M1全体、性能/品質目標、災害復旧・完全消去の適格性確認の完了は意味しません。

## 今後の実装対象

worker、job enqueue/status API、自動synthesis、compaction、vector embedding/
pgvector、日本語tokenizer、AGE、SQL/PGQ、checkpoint/recovery、訂正/supersession、
MCP、SDK、postgresem連携はありません。現在の期間filterとepisode根拠だけで、
計画上の二時点・graph・provenance・削除architectureが完了したとは扱いません。

選択理由は[ADR 0001](adr/0001-initial-slice-jp.md)、
安全な管理は[運用](operations/README-jp.md)、
container検証workflowは[貢献方法](../CONTRIBUTING-jp.md)を参照してください。
