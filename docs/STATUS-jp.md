# 現在の契約と制限

[English](STATUS.md) | [プロジェクトREADME](../README-jp.md) | [実装プラン](PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**v0.0.2のM1 assertion revisionを実装済みで、ローカルとnative Docker CIが合格しました。
M0/M1全体の完了、MVP完成、本番適格性の確認を意味しません。**
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
| `POST /v1/assertions/{memory_id}/revisions` | expected head、明示的intent、reason、revision固有のepisode根拠を使い、同一assertionへ全置換revisionを追加 |
| `POST /v1/recall` | PostgreSQL全文検索で許可済みepisode/assertionを検索し、byte予算内の決定的context packを生成 |
| `POST /v1/explain` | 指定したassertion revisionと根拠を返す。省略時は最新ではなく引き続き`1`。episodeはrevision `1`のみ。ranking trace APIはない |
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
`purge`は1〜100件のIDを受け付け、**全revision**の
**episode → assertion**依存関係を最大10,000件の派生assertionまで辿ります。
これを超えるclosureは一部削除せず`422`で拒否します。
過去のどのrevisionでも使われたsourceを削除すると、現revisionがもうそのsourceを
使っていなくても、保守的にassertion全体・全revisionの値/理由・全根拠をpurgeします。
再生成はしません。assertionの削除ではsource episodeを削除しません。

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

## Schema互換性

変更しない`001_initial.sql`に続いて`002_assertion_revisions.sql`を適用します。
既存assertionの値・期間・status・根拠をrevision 1へ移し、
実際のtimestampと既存idempotency記録を維持します。
完全一致再送のため、旧requestのserialization順序との互換性も維持します。
新版runtimeはledgerが厳密に`[1, 2]`であることを要求し、
旧版・将来版・不完全な履歴を拒否します。

migrationにはforced RLSをbypassできるDDL権限付き管理者と`btree_gist`が必要です。
`row_security = off`はbackfillがRLSでfilterされる場合にfail-closedにする設定で、
bypass権限を付与するものではありません。旧版・新版すべてのAPI trafficを停止し、
backup、原子的migrationの後に、対応する新版APIだけを起動してください。
**旧APIにはこの起動guardがないため、必ず停止を維持します。**
旧APIとのrolling共存やdowngradeは非対応です。
[運用](operations/README-jp.md#v002の保守migration)に従ってください。

## 検証証拠

公開repository: [rioriost/pgag_memory](https://github.com/rioriost/pgag_memory)。
**v0.0.2**の実装commit
[5458402](https://github.com/rioriost/pgag_memory/commit/5458402)について、
2026-09-16に実装sessionから次の成功結果が提供されました。

| 環境 | Command | 結果 |
|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | Ruff、mypy、32件のテスト、runtime HTTP health smokeが合格 |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | Ruff、mypy、32件のテスト、runtime HTTP health smokeが合格 |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | Ruff、mypy、32件のテスト、runtime HTTP health smokeが合格 |

Dockerの両jobは
[GitHub Actions run 35082970968](https://github.com/rioriost/pgag_memory/actions/runs/35082970968)
で成功しました。実装sessionはjob状態だけでなく、各jobの実際のlogで
テスト数、lint/型検査、production HTTP health smokeまで確認しています。
報告された実装/CIの実行結果であり、文書作業での独立した再実行ではありません。

migration fixtureは実際の変更しない001 schemaから開始し、
2 tenantsの旧値、timestamp、根拠、HMAC再送記録、tombstoneを設定してから002を適用します。
runtimeによるmigration前schemaと将来/不正ledgerの拒否も検査します。

境界fixtureは有効な999-revision prefixを一括投入し、
anchorによる遅延history/根拠検査とFK/GiST制約を維持します。
続いて公開HTTPで999から1000への採用、1001の`422`拒否、上限到達後の再送、
revision 1000のexplain、現在のrecallを検査します。
prefix全体をHTTP経由で作成したわけではありません。

これらの検査はM0/M1完了、性能/品質の測定、DR保証、完全消去の適格性を示すものではありません。
過去の参考として、19テストの**v0.0.1 baseline**は
[run 35078936073](https://github.com/rioriost/pgag_memory/actions/runs/35078936073)であり、
現在のmilestoneの証拠ではありません。

## 今後の実装対象

worker、job enqueue/status API、自動synthesis、compaction、vector embedding/
pgvector、日本語tokenizer、AGE、SQL/PGQ、checkpoint/recovery、
別assertion間のsupersession/fact調停、MCP、SDK、postgresem連携はありません。
この同一assertionのrevision milestoneだけで、
計画上の二時点・graph・provenance・削除architectureが完了したとは扱いません。

選択理由は[ADR 0001](adr/0001-initial-slice-jp.md)、
安全な管理は[運用](operations/README-jp.md)、
container検証workflowは[貢献方法](../CONTRIBUTING-jp.md)を参照してください。
