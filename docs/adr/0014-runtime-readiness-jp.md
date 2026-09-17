# ADR 0014: 上限付きruntime readiness

[English](0014-runtime-readiness.md) | [契約](../STATUS-jp.md#runtime-readiness) | [運用](../operations/README-jp.md#runtime-readiness)

- 日付: 2026-09-17
- 状態: 上限付きv0.0.14/schema 9で採用・検証済み。localと両native architectureで確認
- 拡張対象: [runtime identity境界](0001-initial-slice-jp.md)、[pgvector契約](0011-pgvector-retrieval-jp.md)、[schema 9管理](0013-scope-access-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/本番/性能/品質/HA/DR/完全消去の適格性確認ではない

## 決定

public `GET /healthz`は起動成功後のprocess livenessを維持し、
DBを呼ばず正確な`{"status":"ok"}`を返します。
public・認証不要の`GET /readyz`を`/v1`外に追加し、tenant/principalを選びません。
渡された認証headerは無視します。想定応答は**200**と正確な`{"status":"ready"}`、
または**503**と正確な`{"status":"not_ready"}`です。
両方に`Cache-Control: no-store`と生成UUIDの`X-Request-ID`を含めます。
bodyにreason、DSN、token、memory本文、identity、schema一覧を公開しません。
OpenAPIの両応答はNative `ErrorBody`でなく型付き`ReadinessStatus`であり、
不正なHTTP methodはprobeを実行せず405を返します。

受け付けた要求ごとに設定済みruntime DSNの新接続で既存`validate_runtime`を呼び、
admin資格情報やfallbackは使いません。
既存のAPI起動/worker検証を含むvalidation sessionで
`default_transaction_read_only = on`を明示します。この設定は専用validation接続に限定し、
後続Native mutationの書込み可能性を維持します。
明示SQLは最大4文で、`SET`一つとrole catalog、schema履歴、extension catalog用`SELECT`三つです。
superuser、`BYPASSRLS`、`memory`/`memory_ops`内tableの所有権/owner-role membershipを拒否し、
`NOINHERIT`も対象とします。
厳密な履歴`[1,2,3,4,5,6,7,8,9]`と`public`内の`vector` 0.8.6を要求します。
memory本文読取り、tenant lock、audit/epoch/job/receipt、source、tombstone書込みはありません。
migration、provider呼出し、cache、background検査、自動retryを追加しません。

既存の起動時fail-closedを維持します。
`RuntimeValidationError`は`RuntimeError`を継承し、従来のmessageを維持しつつ
想定driftを固定codeで区別します。任意のprogramming errorをreadiness失敗として扱いません。

## Admissionとdiagnostic境界

API app/processごとにactive検査を一つだけ受け付けます。
並行要求は別DB接続、待機、cache済み成功なしで即503/log reason `probe_busy`となります。
global rate limiterやrequest flood適格性確認ではありません。
固定`asyncio`の**active検査timeout予算5.0秒**と既存の
**connect/statement/lock各5秒予算**を維持します。
cancel/connection cleanupで遅延が増え得るため、厳密なwall-clock SLAではありません。
cancelは伝播しgate/connectionを解放します。

想定内失敗は`readiness_unavailable`、生成`request_id`、固定`reason`をlogへ出します。
reasonは`runtime_role_invalid`、`schema_unavailable`、`schema_version_mismatch`、
`extension_version_mismatch`、`probe_busy`、または例外class名です。
このdiagnosticにraw error文字列、traceback、DSN、token、payloadを含めません。
想定内の`RuntimeValidationError`、`psycopg.Error`、`TimeoutError`は503となります。
通常の`RuntimeError`を含む想定外の例外はnot-ready応答へ変換しません。

## Readyが意味しないこと

ある時点の接続/runtime role/schema/vector契約であり、全principal認可、
table grant/RLS policy完全性audit、write transaction/書込み可能性/primary検査、
継続JWT検証、tokenizer/provider readiness、backlog/load/HA/DR、
性能/品質/本番の適格性確認ではありません。SELECT-only DBも合格し得ます。
resource routeはreadinessを呼ばず、drift後の永続fail-closed gateを新設しません。
既存Native認可を維持し、operatorのtraffic停止を助けるsignalであって認可firewallの代用ではありません。

restart stormを防ぐためlivenessと依存readinessを分離します。
deployment側で失敗/復旧thresholdとbusy 503への対応を決め、
perimeterでpublic probeを制限/rate-limitしてください。
Kubernetes、Compose、Docker `HEALTHCHECK`連携は追加しません。

## 互換性と検証

v0.0.14はschema 9、PostgreSQL 18.6、pgvector 0.8.6、固定imageと依存版を維持し、
新migrationや依存は不要です。
旧API/worker/adapter/hook/SDK caller/管理commandを停止/drainしてから、
対応する**service 0.0.14 / API v1 / schema 9**だけを使い、混在版rolloutは主張しません。
認証付きcapabilitiesはstage `m2-runtime-readiness`と`health_probes` metadataを使います。
`liveness: "/healthz"`、`readiness: "/readyz"`、`readiness_timeout_seconds: 5.0`、
`readiness_max_in_flight_per_process: 1`を追加します。
Native memory resource methodは24のままです。SDK/MCP/hook probe methodは追加せず、
health pathはSDK resource-route coverage対象外です。

**2026-09-17 JSTにv0.0.14最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**495テスト、既存warning 1件**、
既存464 + readiness unit 16 + integration 15テスト（新規31）です。
Ruff、strict mypy（source 19 + SDK consumer 1ファイル）、真のcore/hook/sdk-only導入、
non-root production全smokeも全3環境で合格しました。
合格した使い捨てDB readiness smokeは通常HTTP smokeの後に同じAPI processで
ready 200 → schema ledger rename → health 200のままready 503 →
ledger復元 → ready 200と既存の認証付きsmokeで、source/tombstoneを書き込みません。
live DBでdrift例を実行してはいけません。実装は
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
で公開済みです。[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)は
この完全一致SHAで合格し、native実logで件数、検査、smokeを確認しました。
所要時間は**local 295.67秒 / amd64 385.41秒 / arm64 470.16秒**です。
これは実装の結果であり、その後の最終docs CI runではありません。
[検証証拠](../STATUS-jp.md#v0014--schema-9)を参照してください。所要時間は性能benchmarkではありません。
検証済みv0.0.13実装CI 35196930448と最終docs CI 35198499967は別々の
[過去結果](../STATUS-jp.md#v0013--schema-9)であり、v0.0.14の証拠ではありません。
