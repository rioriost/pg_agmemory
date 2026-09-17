# pg_agmemory

[English](README.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**MITライセンスのPostgreSQLベースAgent Memory Serviceです。**
公開リポジトリは[`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory)、
ローカルcheckoutディレクトリ・Pythonパッケージ・サービス名は`pg_agmemory`です。
以下のコマンドはこのローカルcheckoutから実行してください。

**上限付きv0.0.10/schema 7のatomic structured captureを実装しました。
最終localとnative amd64/arm64検査に合格しました。検証済みv0.0.9結果は過去の証拠として維持します。
M0/M1/M2/M3全体の完了、MVP完成版、本番リリースではありません。**
認証付き観測保存、同一scopeのepisodeを根拠とする明示的な構造化記憶、
PostgreSQL全文検索、根拠表示、トランザクション内の冪等性、
稼働DBからの同期purgeを実装しています。tenant/scope権限をサービスと
PostgreSQL RLSの両方で強制し、変更はcommit後に応答します。
assertion revisionはサーバー管理のsystem-time履歴とrevision固有の根拠を維持します。
typed checkpointは新branchへのrestore envelopeとdurableなtool-effect台帳を提供します。
明示entityとrevision付きrelation assertionは上限付きの読取り専用SQL graph探索を提供します。
固定principal workerによる明示queue型の構造化publicationと、
opt-inのversion付き日本語lexical search、Native API上のlocal・固定identity MCP adapterも提供します。
任意のvendor-neutral implicit recall hookは読取り専用を維持します。
v0.0.10差分はepisode一つと明示要求された構造化publication job一つを原子的にcommitします。
assertion公開済み、自動capture、自然言語synthesis、検索品質の測定済み改善ではありません。

別assertion間のsupersession/fact調停、provider receipt検証、vendor固有harness adapter、
自動enqueue/抽出、汎用multi-tenant scheduling、pgvector、
AGE/SQL/PGQ、remote MCP HTTP/SSE/OAuth/delegation、application SDK、
postgresem連携は今後の実装対象です。
性能・記憶品質・災害復旧・完全消去の受入は未測定または未認定です。
利用前に[現在の契約と制限](docs/STATUS-jp.md)を確認してください。

## コンテナ検証

ローカル開発ではDocker Desktopではなく**Apple Container**を使います。
[Apple Container](https://github.com/apple/container)をインストールし、実行します。

```bash
container system start
./scripts/test-containers.sh
```

runnerには`jq`も導入してください。使い捨てsmoke設定を含め、
scriptは**Apple ContainerとDockerの両方**で`jq`を必須とします。

このスクリプトは固定したPython依存関係をビルドし、Ruff、mypy、unitテスト、
PostgreSQL integrationテスト後にproduction APIのHTTP healthを確認し、
**non-root production image**内で実際の`pg-agmemory worker --subject ... --once`を実行します。
worker smokeは使い捨てのprovision済みprincipalとruntime専用資格情報を使い、
`{"outcome":"idle"}`を検査して`Production worker smoke passed`をlogに出します。
non-root runtime imageのtokenizer smokeは`東京都` → `東京` / `都`も検査し、
成功時に`Production Japanese tokenizer smoke passed`を出力します。
end-to-end recallや分割品質の評価ではありません。
v0.0.8 runnerはnon-root production image内で実`pg-agmemory mcp` childも実行します。
固定tokenとprovision済みscopeでloopback Native APIへ接続し、4 toolの列挙とrecallを
modern `2026-07-28`・legacy `2025-11-25`の**両mode**で検査します。
日本語/API/worker smokeも維持しています。
専用の使い捨てPostgreSQLコンテナを利用し、
自分が作ったコンテナ・ネットワークだけを片付けます。
既存のDBやコンテナは変更しません。Python/PostgreSQL/uvのimage版とdigestは固定しています。

GitHub Actionsではnative **linux/amd64**・**linux/arm64** runner上のDockerで
同じスクリプトを実行します。
過去のv0.0.8 step名は`Test containers and smoke-test production API, worker, and MCP`です。

```bash
./scripts/test-containers.sh docker
```

商用モデルのAPI keyや外部memory DBは不要です。
初回はコンテナimageとPython依存packageを取得できる必要があります。
**v0.0.10/schema 7の最終localとnative結果を2026-09-17 JSTに確認しました。**
Apple Containerとnative Docker amd64/arm64は各**304テスト、既存warning 1件**に合格しました。
**Ruff、strict mypy（source 16ファイル）、真のcore-only/hook-only導入検査、
non-root productionの全smoke**も全3環境で合格しました。
日本語/API/worker、MCP両時代、hook全3 eventに加え、
新しい原子的な**capture → 実worker → recall → replay → purge** workflowが対象です。

| 環境 | テスト所要時間 |
|---|---|
| ローカルApple Container | **275.53秒** |
| Docker、native `linux/amd64` | **467.75秒** |
| Docker、native `linux/arm64` | **434.40秒** |

最終local sourceは公開済み実装
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f)と一致します。
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)は
両native jobとも合格し、実logでjob statusだけでなく完全一致SHA、件数、所要時間、検査を確認しました。
所要時間は性能benchmarkではありません。
[v0.0.10証拠](docs/STATUS-jp.md#v0010--schema-7)を参照してください。

**過去のv0.0.9/schema 7の最終結果を2026-09-17 JSTに確認しました。**
全3環境で**274テスト、既存warning 1件**、Ruff、strict mypy（**source 15ファイル**）、
真のcore-only/hook-only導入検査、non-root productionの日本語/API/worker、
MCP **`2026-07-28`・`2025-11-25`**、
hook **`session_start`・`task_switch`・`after_compaction`**の全smokeに合格しました。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **248.29秒** |
| Docker、native `linux/amd64` | **482.21秒** |
| Docker、native `linux/arm64` | **374.33秒** |

最終local sourceは公開済み実装
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)と一致します。
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)の
両native jobの実logで、job statusだけでなく完全一致SHAと上記全検査を確認しました。
所要時間はテスト観測値であり、性能benchmarkではありません。
範囲は[v0.0.9検証証拠](docs/STATUS-jp.md#v009--schema-7)を参照してください。
これらの検査で元のmilestoneや受入gateが完了したとは扱いません。
最終v0.0.9 docs commit
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)も、
[CI run 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689)で
両native architecture各**274テスト**に合格しました。
どちらのv0.0.9 runもv0.0.10 atomic captureの検証ではありません。

**過去のv0.0.8/schema 7:** 実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)は、
Apple Containerとnative Docker amd64/arm64で各**214テスト**（既存warning 1件）、
Ruff、strict mypy（source 13ファイル）、全production smokeに合格しました。
上記MCPの両protocol modeも合格しています。
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)と
[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。
その後のbilingual docs commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)は、
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509)で
**両native architecture各214テスト**に合格しました。
どちらのv0.0.8 runもv0.0.9 hookや共有client抽出の検証ではありません。

**過去のv0.0.7/schema 7限定の証拠です。** 実装commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)は、
Apple Containerとnative Dockerの**linux/amd64**・**linux/arm64**で、
それぞれ**144テスト**（既存warning 2件）、Ruff、strict mypy（source 12ファイル）、
non-root productionの日本語tokenizer、API HTTP、実CLI worker `--once` idle実行という
全3種のsmokeが合格しました。最終結果は**2026-09-17 JST**に確認しました。
両CI jobは同じ完全一致SHAで実行し、実logで全検査を確認しています。
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)と、
[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。
最終bilingual docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)も
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899)で
両native jobが合格しました。これらの過去runはv0.0.8/v0.0.9の結果ではありません。

**過去のv0.0.7**最終lockは既存package-feed registryを維持し、全**36 package**のversion、依存metadata、
artifact hashはテスト済みPyPI解決lockとbyte単位で同一です。v6との差分はJanome 0.5.0の
追加とprojectのv0.0.7へのversion更新だけで、無関係なupgradeやregistry移行はありません。
native CIはこのretained-registry lockからbuildしました。v0.0.8 MCP extraには追加依存があり、
旧package件数とlock比較は新lockを説明するものではありません。

## APIの起動

PostgreSQL 18を使用します。以下のアプリケーションコマンドは
`Dockerfile`で構築したimage内で実行します。最終stageがruntime imageです。

1. 対象の空Memory DBを`PGAG_ADMIN_DATABASE_URL`に指定し、
   `pg-agmemory migrate`を実行します。migrationはtransactionalで再実行可能です。
   管理者はforced RLSをbypassできる必要があります
   （superuserまたは適切な権限を持つ`BYPASSRLS`）。
   role/schema/tableのDDLと`btree_gist`導入に必要な権限も必要です。
   runtimeに付与する権限ではありません。
2. `NOSUPERUSER NOBYPASSRLS IN ROLE pgag_runtime`の専用loginを作成し、
   passwordを安全に設定します。**migrationのtable owner roleへの所属を与えないでください。**
   このloginの接続先を`PGAG_DATABASE_URL`に設定します。
3. admin URLで`pg-agmemory provision --subject YOUR_VERIFIED_SUBJECT`を実行し、
   private tenant、principal、scopeを作成します。返された`scope_id`を保存します。
   subjectは設定したissuer内で一意です。
4. `PGAG_JWT_PUBLIC_KEY`に2048 bit以上のPEM RSA公開鍵、
   `PGAG_JWT_ISSUER`に正確なissuer、`PGAG_JWT_AUDIENCE`に本サービスのaudienceを設定します。
   tokenはRS256署名と`sub`、`iss`、`aud`、`iat`、`exp`を必須とします。
   request bodyのtenant/principal指定は拒否します。
5. `pg-agmemory serve`を実行します。TLSは信頼できるreverse proxyで終端してください。
   port 8000自体はHTTPです。信頼できないnetworkへ直接公開しないでください。

runtime環境にadmin URLや署名用秘密鍵を渡さないでください。
組込みcredential、既定token、認証回避設定はありません。
migration/provision/rebuildは管理操作であり、public endpointとして公開してはいけません。
起動時にsuperuser、RLS bypass、table ownerのruntime接続を拒否します。

**v0.0.10は厳密なschema 7を維持し、v0.0.7/v0.0.8/v0.0.9からのmigration 008/009/010、
DDL、新backfillは不要です。** 新依存はなく、Python project版とそのlock metadataだけをv0.0.10へ更新します。
旧API/worker/adapterを停止/drainしてから、対応するv0.0.10 processに置換してください。
異なるversionの混在互換性を想定しないでください。
schema 7より古いDBには保守停止とbackupが必要です。
旧版・新版すべてのAPI**とworker**を停止/drainし、`007_japanese_fts.sql`までの
未適用migrationと原子的Python lexical backfillを適用してから、
対応するv0.0.10 API/workerだけを起動します。
両者とも厳密な履歴`[1, 2, 3, 4, 5, 6, 7]`を要求します。
旧imageは停止を維持してください。v0.0.1にはschema互換性guardがありません。
rolling共存やdowngradeは非対応です。
[migration手順](docs/operations/README-jp.md#v007の保守migration)に従ってください。

shellに`MEMORY_URL`、`TOKEN`、作成済みの`SCOPE_ID`を設定して実行します。

```bash
curl --fail-with-body "$MEMORY_URL/v1/observe" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-observation-1' \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"source_namespace\":\"demo\",\
\"source_event_id\":\"contract-1\",\"occurred_at\":\"2026-09-01T00:00:00Z\",\
\"content\":\"ACME contract is Gold\",\"consent_reference\":\"demo-consent\"}"

curl --fail-with-body "$MEMORY_URL/v1/recall" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"scope_ids\":[\"$SCOPE_ID\"],\"query\":\"Gold\",\
\"purpose\":\"demo\",\"token_budget\":2000}"
```

`consent_reference`はcallerによる同意の申告を記録します。現段階では外部の同意台帳の
検証やsecret/PIIの自動除去は行いません。保存が許可され、除去処理済みのdataだけを送ってください。
対話的schema表示は`/docs`、OpenAPIは`/openapi.json`です。
`/healthz`は起動検証後のprocess livenessであり、継続的なDB readinessではありません。

## Atomic structured capture

**v0.0.10の最終localとnative両architecture検査に合格しました。**
Native `POST /v1/captures`は`Idempotency-Key`と
`{episode: <変更しないObserve>, memory: <一つの構造化intent>}`を要求します。
memory intentは`subject`、`predicate`、`value`、一つの`evidence_quote`、
`explicit_intent: true`、任意のtimezone付き/nullの`valid_from`/`valid_to`を持ちます。
**scope、根拠ID、identity fieldは受け付けず**、scopeと一つのepisode根拠IDをtransaction内で導出します。
1〜4,096文字のquoteは正規化済みepisodeに原文として含まれる必要があります。
既存Rememberのfield上限/valid-time規則とreported・未校正semanticsを維持します。

**HTTP 201**は`{memory_id: <episode UUID>, revision: 1,
synthesis_job_id: <job UUID>}`を返します。
episodeと一つの`structured_remember` / `structured-remember-v1` jobのcommitを確認するだけで、
**assertion publicationの完了ではありません**。
既存jobやterminal jobを再利用する場合があり、**201は新規/pending jobを保証しません**。
返却値は過去参照であり、現在のstatusはGETを正とします。
`GET /v1/jobs/{synthesis_job_id}`と既存の固定subject workerで後続publicationを扱います。
scope当たりactive job 100件、job当たり5試行、lease、epoch検査、publication fencingを維持します。
`POST /v1/observe`は変更せず`synthesis_job_id: null`で、自動jobはありません。
明示`/v1/jobs`と同期`/v1/remember`も変更しません。

同じkey/正規化bodyは同じepisode/job組を返し、同じkeyでbodyを変えるとconflictします。
同じepisode/intent/principalなら新HTTP keyでも組を重複抑止します。
別の明示intentは生存episode上に別jobを作れ、別の認可済みprincipalは独立したjob identity/所有権を持ちます。
episode/projection、job data/identity、idempotency、auditを一つのtransactionで扱います。
transaction失敗は新規変更をrollbackし、以前から独立して存在したepisodeは削除しません。

replayは**両IDの現在のACL/削除**を確認します。episode/job/resultの依存purgeで組は
`404`になり得て、新keyでもpurge済みjob identityを再作成できません。
failed jobは既存job-retry操作で明示retryし、capture replayはretry childでなく元jobを返します。
明示retry childを作成した後も変わりません。
生存source上の新しい別intentを永久禁止するものではありません。
IDは過去参照であり、fresh stateはGETで取得します。

captureは**MCP toolではなく**、recall hookも自動captureしません。
LLM/provider、抽出、自動synthesis、pgvector、意味的な真実の認定はありません。
Native HTTP response-drainとhost消去の境界は変更しません。
[全差分契約](docs/STATUS-jp.md#atomic-structured-capture)、
[operator向けcurl例](docs/operations/README-jp.md#atomic-structured-captureの運用)、
[ADR 0010](docs/adr/0010-atomic-capture-jp.md)を参照してください。

## Local stdio MCP

通常の`pg-agmemory` package導入では任意の`mcp`/`hook`依存を**導入しません**。
MCPには`pg-agmemory[mcp]`、recall-hookには`pg-agmemory[hook]`を選択してください。
repositoryのDocker test/runtime imageは意図的に両extraを含みますが、
**base packageの既定ではありません**。

任意の`pg-agmemory[mcp]` package extraを導入するか、v0.0.10のtest/runtime両stageに
`mcp`・`hook`両extraを含むrepository imageを使用します。
MCP extraは公式**mcp 2.2.0** SDKと**httpx 0.28.1**を固定しています。
checkoutでは`uv sync --frozen --extra mcp`でlock済み環境を準備できます。
信頼するlocal MCP hostから次を起動します。

```bash
pg-agmemory mcp
```

**`PGAG_MCP_API_URL`**と**`PGAG_MCP_API_TOKEN`**は信頼する起動設定から渡し、
tool引数やcommitするhost設定には含めないでください。URLはHTTPS originまたはloopback HTTP
originに限定し、credential/path/query/fragmentは禁止です。tokenは**Native API audience用**で、
Native APIが検査します。MCP caller identityを転送するものではありません。
起動時に認証付きcapabilitiesを照会し、API `v1`、service `0.0.10`、schema `7`の一致を要求します。
設定/認証/versionの失敗はsecretを出さず非zero終了します。
固定tokenの更新には再起動が必要です。`--subject`と`--once`は拒否します。

Native Pydantic model由来のschemaを持つ次の4 toolだけを公開します。

| Tool | 引数 | Native操作 |
|---|---|---|
| `memory_recall` | `{request: <Recall body>}` | `POST /v1/recall` |
| `memory_remember` | `{request: <Remember body>, idempotency_key: "..."}` | `POST /v1/remember` |
| `memory_explain` | `{request: <Explain body>}` | `POST /v1/explain` |
| `memory_forget` | `{request: <Forget body>, idempotency_key: "..."}` | `POST /v1/forget` |

両mutation toolはforget previewも含め、caller管理の**1〜256文字のvisible ASCII key**
（空白不可）を必須とします。keyのtrim/書換えはせず、正確に256文字は許可、257文字は拒否します。
Native forgetは従来どおり**preview/purgeともHTTP 202**です。
結果不明時は**stdio再起動をまたいでも同じkeyとbodyを再利用**
してください。自動retryやkey生成はありません。mutationのtransport障害、5xx、不正応答は
`outcome_unknown`であり、**rollbackではありません**。成功は
`structuredContent: {result: <Native result>, error: null}`、失敗は`isError: true`と
`{result: null, error: {code, retryable, outcome_unknown, native_status, request_id}}`
を返します。短いtextには根拠を重複収録しません。

rememberは明示structured publication専用です。episode単体保存にはNative `/v1/observe`、
明示episode/jobの原子性にはNative `/v1/captures`を使い、どちらもMCP toolではありません。
UTF-8 byte予算（model tokenではない）、日本語recallのopt-in、現在のNative認証/ACL、
削除検査、過去のidempotency参照は維持します。MCP session/request IDはmemory run IDでも
HTTP idempotency keyでもありません。

**信頼identityごとにadapterを一つ使い、共有やnetwork公開はしないでください。**
callごとのheader/identity/URL上書き、remote MCP HTTP/SSE、OAuth、delegationはありません。
adapterは信頼するlocal Native API clientです。Native response-drain barrierはadapterへのHTTP
配信で終了し、**stdio・host UI・LLM contextまでの原子的barrierではありません**。
buffer済み/配信済みcontextは回収できません。forget/ACL変更後はhostがcached contextを破棄する
必要があり、MCP削除通知やadapterのresponse/semantic cacheはありません。
protocol検証の制限を含む[全契約](docs/STATUS-jp.md#local-stdio-mcp)、
[起動と復旧](docs/operations/README-jp.md#local-stdio-mcpの運用)、
[ADR 0008](docs/adr/0008-local-mcp-jp.md)を参照してください。
v0.0.10はmodern `2026-07-28`とlegacy `2025-11-25`の両protocol契約と全MCP semanticsを維持します。
過去のv0.0.9 regressionと両protocol smokeはlocalとnative Docker両architectureで合格しました。
v0.0.10の最終localとnative両architecture検査も合格しました。

## Implicit recall hook

**既存の読取り専用hookです。v0.0.10最終localとnative両architecture検査に合格しました。**
過去のv0.0.9 local/native検査は合格しています。
任意の`pg-agmemory[hook]`を導入します（checkoutでは`uv sync --frozen --extra hook`）。
固定依存は**httpx 0.28.1だけで、MCP SDKではありません**。
Docker test/runtimeには両extraを含めます。
`pg-agmemory recall-hook`は一回実行のvendor-neutralなlocal Native HTTP clientです。
hostへの自動登録はなく、Copilot・Claude・Codex連携を**主張しません**。
外部model呼出しやDB資格情報は不要です。

stdinへUTF-8 JSON documentを一つ送り、**stdinを閉じます**。

```json
{"event":"session_start","query":""}
```

`event`は`session_start`、`task_switch`、`after_compaction`のいずれかです。
`query`は必須、最大4,096 Unicode文字で、空なら設定scope内をcanonical browseします。
取得意図はJSONの`query`だけから渡し、`event`はquery本文ではなくlifecycle名です。
stdin上限は32,768 byteです。identity、scope、purpose、mode、予算、URL、header、tool、
時刻を含む追加fieldは禁止です。event textはアクセス権を付与しません。
`--subject`/`--once`は拒否します。

prompt/event dataでなく**信頼する起動環境だけ**から必須の`PGAG_HOOK_API_URL`、
必須の固定Native audience用`PGAG_HOOK_API_TOKEN`、
必須の`PGAG_HOOK_SCOPE_IDS`（重複しないUUID 1〜32件のJSON配列）とrecall設定を渡します。
既定はpurpose `implicit_context`、予算**2,000 UTF-8 byte（model tokenではない）**
（64〜2,000）、max items 20（1〜20）、search profile `simple-v1`
（日本語`ja-janome-0.5.0-v1`はopt-in）、timeout 2.0秒（有限の0.1〜20秒）です。
purposeは1〜256文字です。[全環境設定表](docs/STATUS-jp.md#implicit-recall-hook)を参照してください。
HTTPS originまたはloopback HTTPだけを許可し、URL credential/application path/query/fragmentを
禁止しますが、root `/`は許可します。URL/token/scope-ID設定はすべて必須で、
URL未設定は既定宛先でなく`invalid_hook_configuration`になります。
共有`NativeSettings`は`httpx.URL`も使い、transport前に制御文字や不正IDNAを拒否します。
redirect/proxy環境を無効化し、TLSを検証します。

呼出しごとに新しく認証付きcapabilitiesで厳密な
**service `0.0.10` / API `v1` / schema `7`**を検査し、
`mode: "implicit"`とNativeの現在時刻defaultでrecallをPOSTします。
deadlineは**両HTTP処理の合計**に適用し、process起動・stdin入力/待機・出力は含みません。
LLM latency SLOではありません。harness側には別のsubprocess timeoutが必要です。
共有の上限付きNative HTTP transportでMCPの不変条件を維持します。
request/response上限は256 KiB/2 MiBです。`context_pack.byte_count`は**本文だけでなく、
metadata/citationを含むcontext pack全体のcompact JSON serialize結果**を数えます。
UTF-8、`ensure_ascii=False`、`separators=(",", ":")`を使い、
hookがその件数と設定byte予算を検査します。返却item数は`max_items`以下、
返却search profileは設定と一致しなければなりません。不一致は失敗させ、広いfallbackは行いません。
書込み、capture、queue、LLM provider、cache、retry、idempotency keyは追加しません。

検証対象のhook runtime結果はstdoutへJSON結果一つと改行を出し、stderr診断はsanitizedです。
成功は`{status: "ok", event, result: <完全なNative RecallResult>, error: null}`、
失敗は`{status: "error", event: <検証済みeventまたはnull>, result: null,
error: {code, retryable, outcome_unknown: false, native_status, request_id}}`です。
Native status/request UUIDはnullableです。
[確定error code表](docs/STATUS-jp.md#上限結果失敗処理)を参照してください。
終了値**0**はNativeの正当な`not_found`/`budget_exhausted`/`index_incomplete`空結果も含みます。
**2**は不正設定/入力、**1**はNative/network/version/protocol障害です。
**取得失敗を空の成功に変換してはいけません。**
空pack自体にも約192 byteが必要で、有効な指定予算64でも
明示的なNative **422 `budget_too_small` / hook終了値1**になり得ます。
一方、packは収まるが候補が収まらない`budget_exhausted`は**200 / 終了値0**です。
index欠落による`index_incomplete`も不完全coverage付きの200/終了値0になり得ます。
recallは未認可scopeや失効membershipを黙ってfilterし、許可済みsubsetまたは
itemなし/`not_found`を返します。**scopeの存在を示す404ではありません**。
token認証失敗は引き続き明示的な**401 / hook終了値1**です。Native semanticsの変更ではありません。
**起動方法のerrorは例外です。** CLI flag拒否や`hook` extra未導入は、
argparseのstderr診断と**終了値2で、JSON envelopeを返しません**。
hostのtimeout/killでもenvelopeを返せない場合があるため、harnessは非JSON/不正出力も扱います。

hostはerror/coverageを表示し、停止かmemoryなしの継続かを明示判断してください。
取得memoryは**信頼できない根拠であり、指示やpolicyではありません**。
queryをlogへ記録したりerrorへコピーしたりしてはいけません。
Native session advisory barrierは信頼するhookへのHTTP配信で終了し、
buffer/stdout/host contextまで原子的には続きません。
回収、削除通知、host消去証明はありません。
forget/ACL変更後は前のcontextを破棄し、新しくhookを呼び出します。
hookは権限を拡大しません。実行可能な
[vendor-neutral Python harness例](docs/operations/README-jp.md#vendor-neutral-python-harness例)と
[ADR 0009](docs/adr/0009-implicit-recall-hook-jp.md)を参照してください。

## Opt-inの日本語lexical recall

`POST /v1/recall`の既定は`search_profile: "simple-v1"`で、
PostgreSQL `simple`/`plainto_tsquery`/`ts_rank_cd`を維持します。
日本語scriptのsurface/wakati分割には`"ja-janome-0.5.0-v1"`を明示選択し、
応答はそのprofileを返します。Janome **0.5.0**と、Janome追加語を含む同梱
**mecab-ipadic-2.7.0-20070801**を使います。対象の日本語script連続部分だけを分割し、
ASCII識別子/英語はsegmenterをそのまま通過します。Unicode/全半角正規化、原形化/stemming、
同義語処理、分割品質の保証はありません。漢字scriptの処理は中国語文字にも及びますが、
中国語recallの適格性は未確認です。

Janomeは日本語script連続部分がある場合だけlazy importします。
入力prefix cacheは無効（`max_cached_word_len=0`）で、保持するcacheは同梱辞書resourceだけです。
test/runtime container buildは**静的Janome package bytecodeだけ**を逐次事前compileし、
user textやmemory index/cacheは作りません。この事前compileのないcold host installationでは
初期化時のpeakが大幅に増える可能性があり、配置時のresource sizingは適格性未確認です。

`tokenizer_id: "utf8-bytes-v1"`は引き続きcontext byte予算用で、日本語token数ではありません。
scope、時間、根拠、ACL、削除、byte上限を維持します。
認可済み・時間条件内の日本語projectionが欠けると、黙ってfallbackせず
`coverage.lexical_incomplete: true`と`coverage.retrieval_complete: false`を返します。
projection欠落があり候補もなければ`empty_reason: "index_incomplete"`となり、
既存の予算不足とは区別します。空queryはindex不完全flagがあってもcanonical itemをbrowseします。
flagはprojection coverageであり、query関連性やqueue状態ではありません。

書込みはepisodeと全assertion revisionを原子的にindex化し、typed relation/job publicationも
含みます。lexical runtime権限は`SELECT`/`INSERT`だけで、`UPDATE`や直接`DELETE`は
付与しません。canonical parent purgeが同じbarrierでFK cascadeにより派生行を消し、
子tableのDELETE権限は不要です。別memoryとしては扱いません。
offline `pg-agmemory reindex-lexical`は`PGAG_ADMIN_DATABASE_URL`で
**選択DBの全tenant**を再構築します。`--subject`はscope filterではなく拒否し、
`--once`もworker専用です。API/workerを停止/drainし、backup、再構築後に
対応するv0.0.10 processだけを再起動します。
自動修復worker、外部model/provider、fileベースのmemory indexはありません。
[契約](docs/STATUS-jp.md#日本語lexical-profile)、
[保守](docs/operations/README-jp.md#lexical-profileとreindexの運用)、
[ADR 0007](docs/adr/0007-japanese-fts-jp.md)を参照してください。

## Durableな構造化publication job

`POST /v1/jobs`は`Idempotency-Key`と
`{kind: "structured_remember", memory: <変更しないRemember request>}`を要求します。
現在のread/write権限の下で明示intentと同一scopeのepisode原文根拠を指定します。
`202`は`{job_id, kind, recipe_version: "structured-remember-v1"}`という
job参照であり、**publication完了ではありません**。
同一principal/scope内でcanonical intent/recipeが同じならHTTP keyをまたいで重複抑止します。
job dedupでは根拠順を正規化しますが、同じHTTP keyには同じ正規化requestが必要です。

`GET /v1/jobs/{job_id}`は安全なstate、試行回数、時刻、入力参照、元のrevision 1結果参照を
返し、request、lease token、owner principalは返しません。
scope当たりpending/runningは100件、job当たり最大5試行です。
terminal jobはrequest JSONを消去します。ownerは`POST /v1/jobs/{job_id}/retry`へ
元のbody全体とkeyを送り、failed jobを明示再試行できます。
同じparentの再試行は一つのchildを再利用し、そのchildが失敗したらchildを再試行します。

subjectのprovision後、制限付き`PGAG_DATABASE_URL`資格情報で実行します。

```bash
pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT --once
```

`--once`省略時は継続実行します。workerはそのprincipalのjobだけをclaimします。
`--subject`は信頼する配置設定でありHTTP偽装機能ではなく、workerにJWT key/admin URLは不要です。
commit済みclaim、期限付きtoken lease、現在の認可/epoch再検査、
assertion/jobの原子的publicationで古い試行を拒否します。
at-least-once処理とjob当たり最大一つのcommit済み結果であり、外部exactly-once実行ではありません。
assertionのrecorded/system timeはenqueueでなくworker publication時に始まります。
worker stdout/logのoutcome参照は過去の記録であり、現在のread許可ではありません。
job GET/explainが現在のアクセス権と削除状態を再検査します。

`observe`は引き続きenqueueせず（`synthesis_job_id: null`）、同期`remember`も変更しません。
recallは読取り可能な待機作業を`coverage.jobs_pending`で示し、
`synthesis_pending: false`と`graph_used: false`を維持します。
jobはrecall/explain itemでもcheckpoint/effectの参照kindでもありません。
source/result purgeは依存jobとretry子孫を削除し、実行中publisherを拒否します。
**jobやretry chainだけの削除では、公開済みassertionやsource episodeは消えません。**
factを消すにはresult/sourceを明示purgeしてください。
[契約](docs/STATUS-jp.md#durable-job)、
[worker運用](docs/operations/README-jp.md#durable-jobとworkerの運用)、
[ADR 0006](docs/adr/0006-durable-jobs-jp.md)を参照してください。

## Assertionの訂正

`POST /v1/assertions/{memory_id}/revisions`は`Idempotency-Key`と、
`expected_revision`、`value`、同一scopeのepisode `evidence`、
`explicit_intent: true`、valid bound、`reason`を含む全置換bodyを要求します。
subject、predicate、scopeは変更できません。成功時は次のrevisionを含む`201`、
head不一致時は`409 revision_conflict`を返します。

これは**valid interval全体の置換**であり、省略したboundは無限端になります。
期間を分割したり、未来日付の開始前に旧値を維持したりしません。
過去の`known_at`では旧revisionを照会できます。
`explain`のrevision省略時は**最新ではなく**`1`です。
どのrevisionでも使われたsourceを削除すれば、assertionの全履歴をpurgeします。
[完全な契約](docs/STATUS-jp.md#assertion-revisionの契約)と
[ADR 0002](docs/adr/0002-assertion-revisions-jp.md)を参照してください。

## EntityとSQL graph oracle

`POST /v1/entities`はallowlist内のtype、canonical label、同一scopeのepisode引用
1〜32件、`explicit_intent: true`で不変のrevision 1 identityを作成します。
metadata/根拠は`GET /v1/entities/{memory_id}`で取得し、entityはrecall/explainから除外します。
caller申告のidentityであり、検証済みfactや信頼できる指示ではありません。
alias、merge、名前解決、意味的重複抑止、label訂正endpointはありません。
同じHTTP key/bodyはanchorを再利用しますが、別keyなら同名の別entityを作成し得ます。

`POST /v1/relations`は同一scopeのentity UUID間に同一scopeのepisode根拠を付け、
独立したrelation objectでなく**一つのcanonical assertion**を作成します。
predicateは`depends_on`、`part_of`、`affects`、`works_for`、`decides`で、
すべて複数値を許すreportedな申告であり、fact調停はありません。
`POST /v1/relations/{memory_id}/revisions`はrevision-CASでtarget/根拠を変更し、
valid interval全体を置換します。source/predicateは固定です。
汎用assertion訂正endpointはtyped relationを`409 relation_revision_required`で拒否します。
free-text `remember`はlabel/predicateが一致してもrelationになりません。
recallはFTSのまま`graph_used: false`で、relation item/説明に正確なentity IDを含め、
contextの既存byte予算に計上します。

認証必須の`POST /v1/graph/expand`は読取り専用で`Idempotency-Key`は不要です。
固定parameterized SQL joinを使い、AGE、SQL/PGQ、Cypher、動的label/queryは使いません。
明示scope/seed/predicate filterは現在のアクセス範囲を狭めるだけです。
決定的な幅優先simple pathを1〜2 hop・1〜100 pathに制限し、全prefixを数えます。
結果は`backend: "sql"`、`projection_watermark: null`、時間条件、整合性epoch、
上限内のcoverageを示すだけで、知識の完全性ではありません。
非公開seedは返さず、可視の孤立seedはpathなしでも現れ得ます。
incoming探索は逆向きfactを推論しません。

entity revision 1と正確なrelation assertion revisionをcheckpoint/effectの
`memory_refs`へ宣言できます。source purgeはentity根拠と**全過去relation target**から、
既存のcheckpoint/effect依存へ伝播します。
relationが消えただけで他の生存entityを削除しません。
[契約](docs/STATUS-jp.md#entityとsql-graph-oracle)と
[ADR 0005](docs/adr/0005-relational-graph-jp.md)を参照してください。
将来backendの正しさを比較する基準であり、graph有用性の測定やM1/M3全体の受入ではありません。

## Typed checkpoint

`POST /v1/checkpoints`はscope内のrun/branchにschema 1のtyped stateを保存します。
`expected_head`は必須（最初は`null`）で、非減少のevent watermark、
正確なmemory参照、HMAC checksumを使います。
`GET /v1/checkpoints/{checkpoint_id}`は現在の認可と検査を通したenvelopeを返します。
checkpointは`recall`や`explain`には出しません。

`POST /v1/checkpoints/restore`はharness/versionの完全一致と新しいbranchを要求し、
元branchを巻き戻しません。run台帳のdispatched effectはfork作成と原子的にunknownになります。
GET/restoreはsnapshot後に追加されたものも含め、runの生存effect全件を統合します。
未追跡hintはplannedでも再開を阻止します。旧動作を意図的に厳格化しています。
`automatic_reexecution`は常にfalseです。
保存済みassertion参照は正確な過去revisionを維持し、
restoreで最新revisionを選び直したり、現在の外部事実を更新したりはしません。
callerはコピーした全entityまたはassertion revision依存を`memory_refs`へ宣言し、
stateから機密情報を除去してください。
未宣言のコピー本文は自動発見しません。

source削除はassertion履歴、checkpoint参照、全子孫/fork lineageへ伝播します。
影響するbranch headは再開できません。
[checkpoint契約](docs/STATUS-jp.md#checkpointの契約)と
[ADR 0003](docs/adr/0003-checkpoints-jp.md)を参照してください。
M1全体や災害復旧の完成を意味しません。

## Tool-effect ledger

先にbootstrap checkpointを作成してください。`POST /v1/tool-effects`は
同一scopeの既存runを要求します。caller生成のoperation UUID、tool名、
正規化actionの小文字64桁hex `action_hash`、すべての正確なmemory依存を記録します。
生の引数/hashは永続化せず、GETはtenant-HMAC fingerprintと安定した外部冪等性keyを返します。
runの存続期間中のeffect上限は100件です。

`POST /v1/tool-effects/{memory_id}/transitions`はCAS付きの状態遷移を追記します。
harnessはtool呼出し**前**にdispatchを永続記録し、providerが対応する場合は
安定した外部keyを使ってください。plan/遷移の応答は過去revisionの参照であり、
現在状態のsnapshotや実行許可ではありません。
現在の認可の下で、生存effectはrun封鎖後も旧参照を再送できますが、
新たなdispatchは引き続き拒否します。
confirmed/failedにはcaller申告のreceipt参照が必要ですが、
serverは参照を検証せずproviderにも照会しません。
外部exactly-once保証、承認サービス、自動実行はありません。

effectを直接または宣言済みsource経由でpurgeすると、そのrunの全checkpoint payloadを削除し、
新effect、dispatch、checkpoint、再開を永続的に禁止します。
独立した生存effectの読取り/照合は可能です。新run/operation IDは意味的な重複抑止ではありません。
[台帳の契約](docs/STATUS-jp.md#tool-effect-ledger)、
[運用](docs/operations/README-jp.md#tool-effectの運用)、
[ADR 0004](docs/adr/0004-tool-effects-jp.md)を参照してください。

## ドキュメント

| 日本語 | English |
|---|---|
| [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) |
| [現在の契約と制限](docs/STATUS-jp.md) | [Current contract and limitations](docs/STATUS.md) |
| [初期アーキテクチャ決定](docs/adr/0001-initial-slice-jp.md) | [Initial architecture decisions](docs/adr/0001-initial-slice.md) |
| [Assertion revisionの決定](docs/adr/0002-assertion-revisions-jp.md) | [Assertion revision decisions](docs/adr/0002-assertion-revisions.md) |
| [Checkpointの決定](docs/adr/0003-checkpoints-jp.md) | [Checkpoint decisions](docs/adr/0003-checkpoints.md) |
| [Tool-effect ledgerの決定](docs/adr/0004-tool-effects-jp.md) | [Tool-effect ledger decisions](docs/adr/0004-tool-effects.md) |
| [SQL graph oracleの決定](docs/adr/0005-relational-graph-jp.md) | [SQL graph oracle decisions](docs/adr/0005-relational-graph.md) |
| [Durable jobの決定](docs/adr/0006-durable-jobs-jp.md) | [Durable-job decisions](docs/adr/0006-durable-jobs.md) |
| [日本語lexical FTSの決定](docs/adr/0007-japanese-fts-jp.md) | [Japanese lexical FTS decisions](docs/adr/0007-japanese-fts.md) |
| [Local MCPの決定](docs/adr/0008-local-mcp-jp.md) | [Local MCP decisions](docs/adr/0008-local-mcp.md) |
| [Implicit recall hookの決定](docs/adr/0009-implicit-recall-hook-jp.md) | [Implicit recall hook decisions](docs/adr/0009-implicit-recall-hook.md) |
| [Atomic structured captureの決定](docs/adr/0010-atomic-capture-jp.md) | [Atomic structured capture decisions](docs/adr/0010-atomic-capture.md) |
| [運用](docs/operations/README-jp.md) | [Operations](docs/operations/README.md) |
| [貢献方法](CONTRIBUTING-jp.md) | [Contributing](CONTRIBUTING.md) |

## 依存ライセンス

project codeは[MIT](LICENSE)ですが、**依存関係すべてがMITではありません**。
[Janome 0.5.0はApache-2.0](https://github.com/mocobeta/janome/blob/0.5.0/LICENSE.txt)です。
同梱mecab-ipadic辞書/統計dataには別の
[IPADIC copyright/license notice（NAIST/ICOT）](https://github.com/mocobeta/janome/blob/0.5.0/NOTICE.txt)
が適用され、[Janomeの辞書追加語](https://github.com/mocobeta/janome/blob/0.5.0/ipadic/Noun.proper.csv.patch)
もこの固定releaseに含まれます。package/image再配布時は上流のlicense/notice fileを保持してください。
同梱辞書はcode依存でありuser memoryではなく、memory projectionの保存先はPostgreSQLだけです。
LLM weightsやbenchmark用会話履歴は同梱していません。
