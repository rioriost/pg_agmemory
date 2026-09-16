# pgag_memory

[English](README.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**MITライセンスのPostgreSQLベースAgent Memory Serviceです。**
リポジトリ名は`pgag_memory`、Pythonパッケージ名とサービス名は`pg_agmemory`です。

**現在はM1の初期実装であり、MVP完成版・本番リリースではありません。**
認証付き観測保存、同一scopeのepisodeを根拠とする明示的な構造化記憶、
PostgreSQL全文検索、根拠表示、トランザクション内の冪等性、
稼働DBからの同期purgeを実装しています。tenant/scope権限をサービスと
PostgreSQL RLSの両方で強制し、変更はcommit後に応答します。

訂正・revision履歴、checkpoint、worker、自動抽出、pgvector、日本語tokenizer、
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

## APIの起動

PostgreSQL 18を使用します。以下のアプリケーションコマンドは
`Dockerfile`で構築したimage内で実行します。最終stageがruntime imageです。

1. 対象の空Memory DBを`PGAG_ADMIN_DATABASE_URL`に指定し、
   `pg-agmemory migrate`を実行します。migrationはtransactionalで再実行可能です。
   管理者にはrole/schemaを作成する権限が必要です。
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

## ドキュメント

| 日本語 | English |
|---|---|
| [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md) | [Implementation plan](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN.md) |
| [現在の契約と制限](docs/STATUS-jp.md) | [Current contract and limitations](docs/STATUS.md) |
| [初期アーキテクチャ決定](docs/adr/0001-initial-slice-jp.md) | [Initial architecture decisions](docs/adr/0001-initial-slice.md) |
| [運用](docs/operations/README-jp.md) | [Operations](docs/operations/README.md) |
| [貢献方法](CONTRIBUTING-jp.md) | [Contributing](CONTRIBUTING.md) |

[LICENSE](LICENSE)を参照してください。依存ライブラリには各自のlicenseが適用されます。
model weights、第三者dataset、benchmark用会話履歴は同梱していません。
