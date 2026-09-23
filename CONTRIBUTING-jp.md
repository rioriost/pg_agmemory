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
COMMIT cancel検査では、存在しない同期standbyと`local`既定値で起動した
別の所有PostgreSQL primaryを使い、対象transactionだけ`remote_apply`にします。
共有clusterの設定は変更しません。`tests/test_commit_outcomes.py`は実際の`SyncRep`を観測し、
そのtest backendだけをcancelして、local永続化と成功receipt/worker retryの抑止を確認します。
warningを隠すsession既定値とCOMMIT前rollbackも対象です。通常DBでは
missing-standby caseをskipしますが、main container runnerはCI両architectureで別途実行します。
[結果不明の契約](docs/operations/README-jp.md#unconfirmed-commit-outcomes)を参照してください。
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
graphのpath件数制限は自動選択と実際のgeneric prepared planで検査し、
nested-loop-onlyの計画も含め、outgoing・incoming・双方向を対象にします。
100path境界では実行計画のassertionで
保護されたmetadataとendpoint根拠の再scan回数を制限します。
adjacency・assertion・revision・重複除去したendpointのmaterialization境界と
事前計算したID配列を維持し、timeoutの延長で退行を隠してはいけません。
batch-capture smokeでは1episodeから独立に公開する複数job、順序を維持した再送、
source purgeを検査します。受付は原子的ですがworkerの公開は原子的ではありません。
途中失敗時のrollbackと再送時の全jobの現在の可視性検査を維持してください。
[ADR 0022](docs/adr/0022-batch-capture-jp.md)を参照してください。
episode-query smokeではmetadata page、ExplainとRememberによる明示的な
source選択、source purgeを検査します。現在読取り可能なscopeと発生日時の半開区間を
page件数制限より前に適用し、発生日時ではなく受付日時で並べます。
metadata pageに本文や同意参照を含めず、cursorを権限・snapshot・
compaction watermarkとみなさないでください。
[ADR 0023](docs/adr/0023-episode-query-jp.md)を参照してください。
selectable-inference smokeでは合成HTTPモデル、operator CLI、
明示的なNative vector登録・purgeを検査します。Azure SQLの契約検査は合成fixtureであり、
managed extensionのbinaryや実Azure環境の検証ではありません。
TLS、catalog・権限検査、parameter binding、入出力上限、
自動retry/fallbackを行わない境界を維持してください。
通常suiteのために実会話データを使ったり、有料resourceやモデルを利用したりしません。
実モデルの品質・managed serviceの適格性は別検証です。
[ADR 0024](docs/adr/0024-selectable-inference-jp.md)を参照してください。
実providerの検査はopt-inです。pytestの`live` markerは
`PGAG_LIVE_PROVIDER_CONFIG`が未設定ならskipし、承認されたcontainer内の
合成data検証にのみ設定します。両モデルを含むprofileでは5ケース・6回のモデル呼出しを
選択し、既存のNative embedding登録・再送・検索・purgeシナリオも再利用します。
このシナリオには使い捨ての`PGAG_TEST_DATABASE_URL`も必要です。
契約とlifecycleの検査であり、M2の品質gateではありません。
通常のローカル/CI実行はlive profileやkeyを注入しません。
モデル選定、OpenAIの安全な設定、host/containerのloopbackの違いは
[推論profile](docs/INFERENCE_PROFILES-jp.md)を参照してください。
検証のためにloopback制約を緩めたり、Ollamaを全interfaceへ公開したりしてはいけません。
imageは`SSL_CERT_FILE`でDebianの信頼済みCA bundleを明示します。
Azure SQLの`verify-full`とoperatorによるCAの明示指定を維持してください。
ローカルTLS transportの退行検査は一時的なtest CAを使い、cloud資格情報や
hostのtrust store変更を必要としません。production smokeでも設定したsystem bundleから
信頼済みCAを読み込めることを確認します。managed serviceの全versionを認定する検査ではなく、
実環境の結果は検証した正確なprofileごとに記録してください。
schema 11はscope単位のcapture受付policyを追加します。
`scope-capture` smokeでは完全なpolicyをtenant epoch CASで設定し、SDK経由の
observe/capture/batch、無効化後の新key・同一key再送の拒否、policy復元、
合成sourceのpurgeを検査します。管理者の共通response-drain barrier、
audit/epochの原子的更新、idempotency/source-event重複検査より前のpolicy検査を
維持してください。scope権限の検査はpolicy拒否より先に行います。
override未設定時は意図的に従来の受付を維持し、secret/PII検出・同意の真正性確認・
providerへの外部送信許可を提供する機能ではありません。
[ADR 0026](docs/adr/0026-scope-capture-policy-jp.md)を参照してください。
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
