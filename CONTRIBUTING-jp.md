# 貢献方法

[English](CONTRIBUTING.md) | [README](README-jp.md) | [現在の契約](docs/STATUS-jp.md)

`pg_agmemory`への貢献にはプロジェクトの[MIT license](LICENSE)を適用します。
Python package/service名は`pg_agmemory`です。Gitは初期化済みなので、
repositoryを再初期化せずbranchで作業してください。MVP完成版ではなくM1の初期sliceです。
認可・根拠・transaction・削除を変更する前に
[ADR 0001](docs/adr/0001-initial-slice-jp.md)を確認してください。

## Containerを使った検証

ローカル検証は**Apple Container**を使い、Docker Desktopや無断のhost fallbackへ
切り替えません。Apple Containerを導入・起動し、`jq`を利用可能にしたうえで、
repository rootから実行してください。

```bash
container system start
./scripts/test-containers.sh
```

scriptはtest imageをbuildし、Ruff、mypy、unitテスト、PostgreSQL integrationテストを
実行します。MCP SDKなしの実core-only/hook-only installを検査した後、
non-root runtime imageをbuild・起動して、日本語tokenizer、API liveness、
worker、MCP両protocol mode、暗黙recall hook全3eventを確認します。
atomic capture smokeでは実workerによる公開、検索、再送、source purgeも検査します。
テストと起動smoke確認で別の使い捨てPostgreSQL containerを使い、
自分が作成したresourceを片付けます。テスト、schema reset、purge訓練、
restore実験を永続/共有DBへ向けないでください。
`PGAG_TEST_DATABASE_URL`は使い捨てtest data専用です。個人のDBを指定せず、
scriptによる作成を優先してください。

GitHub Actionsでは**native `linux/amd64`・`linux/arm64` runnerのDocker**で
同じscriptを実行します。buildだけ・emulationだけの検証ではありません。

```bash
./scripts/test-containers.sh docker
```

これはCI用の経路であり、ローカルの既定ではありません。ローカル1回の成功は、
CIの両architectureでの合格証拠にはなりません。実際のengine、architecture、
command、結果を報告し、実行できない検査は合格とせず未実行と明示してください。
モデルのAPI keyは不要ですが、初回は固定imageとPython packageを取得できる必要があります。
必要な依存変更はmanifestとlock dataを同時に更新し、
失敗した検査を回避するためにpinを緩めたり無関係なtoolを追加したりしないでください。

## 変更範囲とドキュメント

- patchの目的を絞り、変更した動作のregression testを追加してください。
  tenant/scope分離、再送時の最新認可、同一scope episodeの原文根拠、
  commit-before-send、削除drain protocolを維持します。
- worker、job enqueue、vector/graph検索、checkpoint、訂正、SDKなどのroadmap
  機能を実装・検証前に提供済みと宣伝しないでください。
  null confidenceやbyte予算の注意点を、根拠のない真実/token保証に置換してはいけません。
- 英語・日本語ドキュメントを**同じ変更で更新**し、
  例、上限、状態、運用上の注意を一致させてください。
  相互linkと既存の`-jp.md`命名を維持し、
  設計目標・実装済み動作・測定結果を区別します。
- OpenAPIはruntimeの`/openapi.json`で公開するため、
  生成済みドキュメントfileは不要です。
  source modelを更新し、契約変更を両言語の文書で説明してください。
- schema変更はpackage同梱のPostgreSQL storage resourceで、
  明示的なmigration方針とともに行います。
  適用済みschema版を黙って編集し、再実行だけで既存DBが更新されると想定してはいけません。

## 安全な提出

fixtureは合成dataのみを使います。token、password、署名鍵、非公開接続URL、
個人の会話、顧客data、本番backupのコピーをcommitしないでください。
private dataを第三者サービスへ送信してはいけません。
issue/PRに含めるlogやscreenshotは機密情報を除去してください。

PRには変更点と制限、実際の検証結果、関連issue/ADRへのlinkを記載します。
残る検査を正直に記録してください。完全消去、DR、本番利用可能性、性能、
記憶品質の主張には別途証拠が必要で、health probeやcontainer buildの成功だけでは
その証拠になりません。依存/datasetのlicenseをプロジェクトのMIT licenseと区別し、
無許諾または機密のfixtureを追加しないでください。
