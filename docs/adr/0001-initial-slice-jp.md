# ADR 0001: 正しさを優先する初期memory slice

[English](0001-initial-slice.md) | [現在の契約](../STATUS-jp.md)

- 日付: 2026-09-16
- 状態: M1の初期sliceに採用。M0/M1 gate全体の完了証拠ではない
- プロジェクト/repository: `pgag_memory`、Python package/service: `pg_agmemory`
- License: MIT。依存ライブラリのlicenseは別途適用

## 背景

[実装プラン](../PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)は、はるかに広いサービスを
要求しています。非同期の派生処理やgraph backendを導入する前に、認証付き保存、
認可済み検索、原文根拠、削除を動かす小さなsliceが必要です。
本ADRは現在の契約を限定するもので、MVP全体を暗黙に完了扱いへ変更しません。

## 決定

| 項目 | 初期の選択 | 帰結 |
|---|---|---|
| 永続化 | PostgreSQLのみ。通常table、運用結果のJSONB、tenant/scope複合参照、RLS | queue、外部memory store、モデル依存なし |
| Schema配布 | `src/pg_agmemory/storage/001_initial.sql`をアプリに同梱し、migrationをruntimeから分離して管理 | インストール済みruntime imageにもschemaを含む。runtimeのtable所有・RLS bypassは禁止 |
| Identity | 静的RS256検証鍵、固定issuer/audience、署名・時刻検査、DBのexternal-subject対応 | JWKS rotation、delegation、複数issuer管理なし |
| 根拠 | 同一scopeのepisode → assertionのみ。sourceの原文引用を必須化 | provenanceの確認であり真実の認定ではない。assertionは`reported`、confidenceはnull |
| 同意 | callerの`consent_reference`を記録 | 同意台帳の検証・secret/PII自動除去なし |
| 時間 | 初期valid/system interval、revisionは`1`のみ | 時点filterはあるが過去訂正・supersessionはない |
| 検索 | PostgreSQLの`simple` FTS。空queryを許可 | BM25、日本語分かち書き、vector/hybrid検索、graph expansionなし |
| Context | 決定的templateとserialized UTF-8 byte予算 | fallbackをmetadataで明示し、正確なモデルtoken数は保証しない |
| 並行処理 | requestごとの新規connectionと、commit・buffer済み応答送出まで保持するtenant session advisory lock | throughputより削除の正しさを優先してtenant全体を直列化。poolも性能測定済みという主張もない |
| 再送 | transaction内の冪等性とtenant-keyed HMAC source-event記録 | namespace + event IDをtenant/scopeで区切る。削除済みmemoryの完全一致再送は`404` |
| 削除 | `preview`または同期`purge`。派生assertionは最大10,000件 | `suppress`、一般的な依存DAG、再生成、非同期purge workerなし |
| 削除後の可視性 | `memory.object`の`deleted_at`更新ではなく、`memory_ops.object_tombstone`へscopeに束縛されたopaque markerと時刻を記録 | `SECURITY DEFINER`などの特権helperなしで、SELECT RLSがmarkerのあるobjectを非公開にする |

request lockは`pg_advisory_lock(hashtextextended(tenant_uuid::text, 0))`という
session lockで、tenantは正規化されたUUID textで表します。
commit後もbuffer済み応答の送出完了またはconnection closeまで存続します。
transaction lockだけでは応答送出をdrainする前に解放されてしまいます。
管理者のmembership変更も同一session lockを使い、権限と`access_epoch`の両方を
commitしてから解放しなければなりません。手順を無視した任意の管理者writeは
保護対象外であり、clientへ配信済みのdataを回収することもできません。

`memory.object`へのsoft-delete `UPDATE`を削除markerに置き換えます。
PostgreSQLのRLS UPDATE後の行（post-image）検査では、
更新によってその行自身が不可視になると拒否される場合があります。
代わりに、object anchorが可視な間にepisode/assertion/根拠payloadを削除し、
**同じtransaction内で**`memory_ops.object_tombstone`へscopeに束縛された
tombstoneを挿入します。objectのSELECT RLSはtombstoneがあるanchorを除外し、
`memory.object`には`deleted_at`を持たせません。epochとreceiptも同時にcommitします。
`SECURITY DEFINER`、RLS bypass、runtimeの権限昇格を使わずpost-image問題を回避します。

source-eventとidempotency記録には、低entropyな本文のplain hashではなく
tenant-keyed HMAC digestを使います。purgeは稼働DBのepisode/assertion/根拠本文を
削除しますが、opaque object anchor、object tombstone、運用metadata、HMAC再送tombstoneは
tenantの存続期間中保持します。意図しない再出現を抑えるためで、完全消去ではありません。
backup receiptは保持期限を持たずoperator管理を示します。restoreの隔離と
最新削除台帳/ACLの再適用は必須ですが、自動replay/DRは未実装です。

## 延期する代替案

- pool、細粒度の並行処理、streaming応答、background jobの導入には、
  commit-before-sendと削除drainの明示的な代替設計が必要です。
- worker、synthesis、job enqueue、compaction、pgvector、AGE/SQL/PGQ、
  checkpoint、訂正、MCP、SDK、postgresem adapterは未実装です。
- 一般的なprovenance、cross-scope根拠、entity resolution、confidence校正、
  consent-policy自動化、完全な保持・消去workflowには別途設計と受入証拠が必要です。

## 検証と再検討条件

ローカル検証には`scripts/test-containers.sh`経由でApple Containerを使います。
GitHub Actionsはnative `linux/amd64`・`linux/arm64` runnerのDockerを使います。
既存テストscriptは検証手段であり、全architectureやroadmap gateの合格証拠ではありません。
本書で測定済みとして報告する性能目標はありません。

sliceを広げる前に、変更する認可・根拠・再送・削除境界のテストを追加してください。
tenant直列化は測定と代替drain protocolが揃ってから再検討します。
本番利用可能性や完全消去を主張する前に、復旧・保持の明示的な証拠を要求します。
契約変更ごとに[状態](../STATUS-jp.md)、[運用](../operations/README-jp.md)、
および英語版を同期してください。
