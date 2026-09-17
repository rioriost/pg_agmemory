# 貢献方法

[English](CONTRIBUTING.md) | [README](README-jp.md) | [現在の契約](docs/STATUS-jp.md)

`pg_agmemory`への貢献にはプロジェクトの[MIT license](LICENSE)を適用します。
Python package/service名は`pg_agmemory`です。Gitは初期化済みなので、
repositoryを再初期化せずbranchで作業してください。段階的な実装であり、MVP完成版ではありません。
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

scriptはtest imageをbuildし、Ruff、mypy、SDK利用側のstrict型検査、
unitテスト、PostgreSQL integrationテストを実行します。
MCP SDKなしの実core-only/hook-only/SDK-only installを検査した後、
non-root runtime imageをbuild・起動して、日本語tokenizer、API liveness、
worker、MCP両protocol mode、暗黙recall hook全3eventを確認します。
atomic capture smokeでは実workerによる公開、検索、再送、source purgeも検査します。
pgvector smokeではepisode/assertionの合成vectorでexact/hybrid検索とpurgeを確認します。
実embedding modelの意味検索品質を認定するものではありません。
DB imageはPostgreSQL 18.6とpgvector 0.8.6を固定します。pgvectorの
要件は[ADR 0011](docs/adr/0011-pgvector-retrieval-jp.md)を参照してください。
schema 9ではさらに管理者によるACL変更を記録します。
Python SDK smokeではtyped capture/replay、job/input読取り、明示vector検索、削除を検査します。
SDK requestはcaller管理keyとNativeの認可/結果不明の契約を維持する必要があります。
[ADR 0012](docs/adr/0012-python-sdk-jp.md)を参照してください。
scope-access CLI smokeでは使い捨ての管理者資格情報でmembershipを確認・縮小・失効し、
Nativeの読取り/書込み動作を確認します。[ADR 0013](docs/adr/0013-scope-access-jp.md)の
tenant session-lock drain、epoch CAS、原子的audit契約を維持し、
runtimeにRLS bypassを付与してはいけません。
readiness smokeでは`/readyz`の200、使い捨てDBのschema台帳を一時利用不能にした際の
503、その間も`/healthz`が稼働状態を返すこと、台帳復旧後にAPIを再起動せず
readinessが回復することを確認します。読取り専用・時間制限付きprobeを
livenessやresource認可と混同しないでください。
[ADR 0014](docs/adr/0014-runtime-readiness-jp.md)を参照してください。
schema 10はjobの終端状態`cancelled`を追加します。smokeではSDKによるenqueue、
cancel、再送、workerのidle確認、source依存先のpurgeを検査します。
state/attempt CAS、所有者と現在の権限、audit/receiptの原子的更新、
古いworkerの公開防止を維持してください。HTTP 200のcancelもmutationであり、
SDKのkey検証と結果不明の扱いを省略してはいけません。
[ADR 0015](docs/adr/0015-job-cancellation-jp.md)を参照してください。
required-context smokeでは必須の参照を通常のkeyword検索結果より先に格納し、
件数制限、必須内容がbyte予算に収まらない場合の明示エラー、source purgeを確認します。
必須指定でもscope・現在の認可・時間条件を迂回してはいけません。
[ADR 0016](docs/adr/0016-required-context-jp.md)を参照してください。schema 10は変更しません。
structured-recall smokeではkind/subject/predicateの完全一致と必須参照の不一致を検査します。
filterはすべてのrankingとprojection coverage計算より前に適用し、
現在のACL・時間条件を迂回してはいけません。
[ADR 0017](docs/adr/0017-recall-filters-jp.md)を参照してください。hook入力は変更しません。
checkpoint-head smokeではscope内のhead取得、branch更新、過去IDのGET、
source purge後のfail-closed動作を検査します。head読取りでも完全性・照合要否を検査し、
branch作成や過去のancestorへのfallbackをしてはいけません。
[ADR 0018](docs/adr/0018-checkpoint-head-jp.md)を参照してください。読取りはCASの予約ではありません。
job-query smokeではcaller所有jobのkeyset page、現在のstate条件、source purgeを検査します。
現在の可視性・所有者条件を件数制限より前に適用してください。
cursorは位置であり、権限や固定snapshotではありません。job GETと同じ完全性検査を再利用します。
[ADR 0019](docs/adr/0019-job-query-jp.md)を参照してください。
assertion-history smokeでは降順のmetadata page、指定revisionのexplain、
source purgeを検査します。履歴pageにvalue全文や根拠引用を含めず、
revision cursorを権限や固定snapshotとみなさないでください。
[ADR 0020](docs/adr/0020-assertion-history-jp.md)を参照してください。各pageに現在のACLを適用します。
entity-query smokeではscope内のlabel/type完全一致、同名の異なるidentity、
明示的なgraph seed選択、source purgeを検査します。共有scopeの可視性を
所有者限定のjob検索と区別し、同名でidentityを統合してはいけません。
metadata pageに根拠引用は含めません。[ADR 0021](docs/adr/0021-entity-query-jp.md)を参照してください。
graphのpath件数制限は実際のgeneric prepared planでも検査します。
canonical metadataのjoinでRLS保護されたrelation scanが増幅しないよう、
adjacencyのmaterialization境界を維持し、timeoutの延長で退行を隠してはいけません。
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
