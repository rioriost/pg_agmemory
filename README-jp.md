# pgag_memory

[English](README.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**MITライセンスのPostgreSQLベースAgent Memory Serviceです。**
公開リポジトリは引き続き`rioriost/pgag_memory`、
ローカルcheckoutディレクトリ・Pythonパッケージ・サービス名は`pg_agmemory`です。
以下のコマンドはこのローカルcheckoutから実行してください。

**v0.0.3/schema 3のtyped checkpointを実装済みで、ローカルとnative Dockerの検査は合格しています。
M1全体の完了、MVP完成版、本番リリースではありません。**
認証付き観測保存、同一scopeのepisodeを根拠とする明示的な構造化記憶、
PostgreSQL全文検索、根拠表示、トランザクション内の冪等性、
稼働DBからの同期purgeを実装しています。tenant/scope権限をサービスと
PostgreSQL RLSの両方で強制し、変更はcommit後に応答します。
assertion revisionはサーバー管理のsystem-time履歴とrevision固有の根拠を維持します。
新milestoneではtyped checkpointの保存と新branchへのrestore envelopeを追加します。
harnessや外部副作用を実行する機能ではありません。

別assertion間のsupersession/fact調停、外部副作用ledger、harness adapter、
worker、自動抽出、pgvector、日本語tokenizer、
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
PostgreSQL integrationテストを実行した後、production imageを起動してHTTP
healthを確認します。専用の使い捨てPostgreSQLコンテナを利用し、
自分が作ったコンテナ・ネットワークだけを片付けます。
既存のDBやコンテナは変更しません。Python/PostgreSQL/uvのimage版とdigestは固定しています。

GitHub Actionsではnative **linux/amd64**・**linux/arm64** runner上のDockerで
同じスクリプトを実行し、runtime imageの起動も確認します。

```bash
./scripts/test-containers.sh docker
```

商用モデルのAPI keyや外部memory DBは不要です。
初回はコンテナimageとPython依存packageを取得できる必要があります。
v0.0.3の実装commit
[8adb40a](https://github.com/rioriost/pgag_memory/commit/8adb40a)は、
Apple Containerとnative Dockerの**linux/amd64**・**linux/arm64**で、
それぞれ**54テスト**、Ruff、strict mypy（source 7ファイル）、
production HTTP health smokeが合格しました。
[CI run 35088907082](https://github.com/rioriost/pgag_memory/actions/runs/35088907082)と、
報告された[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。

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

**v0.0.3への更新には保守停止とbackupが必要です。**
旧版・新版すべてのAPI trafficとimageを停止し、`003_checkpoints.sql`までの
未適用migrationを適用してから新版APIだけを起動します。
新版runtimeはschema履歴が厳密に`[1, 2, 3]`であることを要求します。
旧imageは停止を維持してください。v0.0.1にはschema互換性guardがありません。
旧APIとのrolling共存やdowngradeは非対応です。
[migration手順](docs/operations/README-jp.md#v003の保守migration)に従ってください。

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

## Typed checkpoint

`POST /v1/checkpoints`はscope内のrun/branchにschema 1のtyped stateを保存します。
`expected_head`は必須（最初は`null`）で、非減少のevent watermark、
正確なmemory参照、HMAC checksumを使います。
`GET /v1/checkpoints/{checkpoint_id}`は現在の認可と検査を通したenvelopeを返します。
checkpointは`recall`や`explain`には出しません。

`POST /v1/checkpoints/restore`はharness/versionの完全一致と新しいbranchを要求し、
元branchを巻き戻しません。dispatched effectはunknownになり、callerの照合が必要です。
`automatic_reexecution`は常にfalseであり、
durable effect ledger、receipt照会、実行engineではありません。
保存済みassertion参照は正確な過去revisionを維持し、
restoreで最新revisionを選び直したり、現在の外部事実を更新したりはしません。
callerは全memory依存を宣言し、stateから機密情報を除去してください。
未宣言のコピー本文は自動発見しません。

source削除はassertion履歴、checkpoint参照、全子孫/fork lineageへ伝播します。
影響するbranch headは再開できません。
[checkpoint契約](docs/STATUS-jp.md#checkpointの契約)と
[ADR 0003](docs/adr/0003-checkpoints-jp.md)を参照してください。
M1全体や災害復旧の完成を意味しません。

## ドキュメント

| 日本語 | English |
|---|---|
| [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) |
| [現在の契約と制限](docs/STATUS-jp.md) | [Current contract and limitations](docs/STATUS.md) |
| [初期アーキテクチャ決定](docs/adr/0001-initial-slice-jp.md) | [Initial architecture decisions](docs/adr/0001-initial-slice.md) |
| [Assertion revisionの決定](docs/adr/0002-assertion-revisions-jp.md) | [Assertion revision decisions](docs/adr/0002-assertion-revisions.md) |
| [Checkpointの決定](docs/adr/0003-checkpoints-jp.md) | [Checkpoint decisions](docs/adr/0003-checkpoints.md) |
| [運用](docs/operations/README-jp.md) | [Operations](docs/operations/README.md) |
| [貢献方法](CONTRIBUTING-jp.md) | [Contributing](CONTRIBUTING.md) |

[LICENSE](LICENSE)を参照してください。依存ライブラリには各自のlicenseが適用されます。
model weights、第三者dataset、benchmark用会話履歴は同梱していません。
