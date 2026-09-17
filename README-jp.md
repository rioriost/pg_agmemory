# pgag_memory

[English](README.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**MITライセンスのPostgreSQLベースAgent Memory Serviceです。**
公開リポジトリは引き続き`rioriost/pgag_memory`、
ローカルcheckoutディレクトリ・Pythonパッケージ・サービス名は`pg_agmemory`です。
以下のコマンドはこのローカルcheckoutから実行してください。

**v0.0.6/schema 6のdurable jobを実装済みで、ローカルとnative Dockerの検査は合格しています。
M0/M1/M2/M3全体の完了、MVP完成版、本番リリースではありません。**
認証付き観測保存、同一scopeのepisodeを根拠とする明示的な構造化記憶、
PostgreSQL全文検索、根拠表示、トランザクション内の冪等性、
稼働DBからの同期purgeを実装しています。tenant/scope権限をサービスと
PostgreSQL RLSの両方で強制し、変更はcommit後に応答します。
assertion revisionはサーバー管理のsystem-time履歴とrevision固有の根拠を維持します。
typed checkpointは新branchへのrestore envelopeとdurableなtool-effect台帳を提供します。
明示entityとrevision付きrelation assertionは上限付きの読取り専用SQL graph探索を提供します。
新milestoneは固定principal workerによる明示queue型の構造化記憶publicationを追加し、
自動synthesis、自然言語抽出、tool/provider実行は行いません。

別assertion間のsupersession/fact調停、provider receipt検証、harness adapter、
自動enqueue/抽出、汎用multi-tenant scheduling、pgvector、日本語tokenizer、
AGE/SQL/PGQ、MCP、SDK、postgresem連携は今後の実装対象です。
性能・記憶品質の受入目標は未測定です。
利用前に[現在の契約と制限](docs/STATUS-jp.md)を確認してください。

## コンテナ検証

ローカル開発ではDocker Desktopではなく**Apple Container**を使います。
[Apple Container](https://github.com/apple/container)をインストールし、実行します。

```bash
container system start
./scripts/test-containers.sh
```

このスクリプトは固定したPython依存関係をビルドし、Ruff、mypy、unitテスト、
PostgreSQL integrationテスト後にproduction APIのHTTP healthを確認し、
**non-root production image**内で実際の`pg-agmemory worker --subject ... --once`を実行します。
worker smokeは使い捨てのprovision済みprincipalとruntime専用資格情報を使い、
`{"outcome":"idle"}`を検査して`Production worker smoke passed`をlogに出します。
専用の使い捨てPostgreSQLコンテナを利用し、
自分が作ったコンテナ・ネットワークだけを片付けます。
既存のDBやコンテナは変更しません。Python/PostgreSQL/uvのimage版とdigestは固定しています。

GitHub Actionsではnative **linux/amd64**・**linux/arm64** runner上のDockerで
同じスクリプトを実行します。
step名は`Test containers and smoke-test production API and worker`です。

```bash
./scripts/test-containers.sh docker
```

商用モデルのAPI keyや外部memory DBは不要です。
初回はコンテナimageとPython依存packageを取得できる必要があります。
**v0.0.6/schema 6**の実装commit
[a4aa7f6](https://github.com/rioriost/pgag_memory/commit/a4aa7f6c8a9ccc52f906619c64e70a8d00eae0d8)は、
Apple Containerとnative Dockerの**linux/amd64**・**linux/arm64**で、
それぞれ**114テスト**（既存warning 2件）、Ruff、strict mypy（source 11ファイル）、
non-root production API HTTPと実CLI worker `--once` idle smokeの両方が合格しました。
両CI jobは同じ完全一致SHAで実行し、実logで全検査を確認しています。
[CI run 35168437396](https://github.com/rioriost/pgag_memory/actions/runs/35168437396)と、
所要時間・過去のv5結果を区別した[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。

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
migration/provisionは管理操作であり、public endpointとして公開してはいけません。
起動時にsuperuser、RLS bypass、table ownerのruntime接続を拒否します。

**v0.0.6への更新には保守停止とbackupが必要です。**
旧版・新版すべてのAPI**とworker**を停止/drainし、`006_durable_jobs.sql`までの
未適用migrationを適用してから対応するv6 API/workerだけを起動します。
両者ともschema履歴が厳密に`[1, 2, 3, 4, 5, 6]`であることを要求します。
旧imageは停止を維持してください。v0.0.1にはschema互換性guardがありません。
rolling共存やdowngradeは非対応です。
[migration手順](docs/operations/README-jp.md#v006の保守migration)に従ってください。

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
| [運用](docs/operations/README-jp.md) | [Operations](docs/operations/README.md) |
| [貢献方法](CONTRIBUTING-jp.md) | [Contributing](CONTRIBUTING.md) |

[LICENSE](LICENSE)を参照してください。依存ライブラリには各自のlicenseが適用されます。
model weights、第三者dataset、benchmark用会話履歴は同梱していません。
