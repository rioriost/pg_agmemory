# ADR 0018: Checkpoint headの照会

[English](0018-checkpoint-head.md) | [契約](../STATUS-jp.md#checkpoint-head-lookup) | [運用](../operations/README-jp.md#checkpoint-head-lookup)

- 日付: 2026-09-17
- 状態: 上限付きv0.0.18/schema 10で採用・検証済み。localと両native architectureで確認
- 拡張対象: [checkpoint](0003-checkpoints-jp.md)、[tool-effect ledger](0004-tool-effects-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/harness/compaction/汎用復旧/本番/DRの適格性確認ではない

## 決定とrequest境界

認証付きread-only `POST /v1/checkpoints/head`を追加し、scopeの現在のread権限を要求しますが、
`Idempotency-Key`は不要です。closedな型付き`CheckpointBranch` bodyは
必須UUIDの`scope_id`、`run_id`、`branch_id`だけです。
UUID欠落/不正や追加fieldは**422 `invalid_request`**です。
identity override、`expected_head`、harness selector、`as_of`、
branch横断のlatest選択、履歴一覧は受け付けません。

現在読取り可能な正確なtenant/scope/run/branchだけを選びます。
既存APIのtenant session-lock/drain barrier下で、`FOR UPDATE`なしのSQL `SELECT`を使います。
run/branch作成、state変更、audit、idempotency receipt書込みは行いません。
不在/非公開/異なるscope/別tenantまたは失効していない空branchは
要求ID/内容や空成功でなく汎用**404 `not_found`**です。

## 失効と完全なenvelope

sourceの`forget`は対象branchを`invalidated=true`とし、canonical checkpoint/参照payloadを削除しますが、
opaqueな`head_id`と`sequence`、`memory.object` anchor、tombstoneは保持します。
照会は失効を先に確認し、読取り可能な失効branchには**409 `checkpoint_invalidated`**を返して
保持head IDは返しません。アクセス権取消後は404でbranchを隠します。
earlier ancestor、sibling、default branchへfallbackしません。
HMAC、typed state、参照、現在の認可、run全体のlive effectについて既存load/envelope検査を再利用します。
runの`effects_invalidated`や既存の完全性不正は既存の拒否動作を維持し、非公開headは404です。
読んだ`scope_id`、`run_id`、`branch_id`、`sequence`は選択branchと一致が必要で、
不一致なら409です。

成功は正確な**200 `CheckpointEnvelope`**で、保存state/HMAC/参照、
`saved_access_epoch`、`saved_deletion_epoch`、`current_access_epoch`、
`current_deletion_epoch`、現在の`tool_effects`、`requires_reconciliation`、
`untracked_effects`、`resume_allowed`、`automatic_reexecution: false`を含みます。
旧権限やeffect検査のbypassは追加しません。
保存epochや`resume_allowed: true`はhost承認やprovider receiptではありません。
read時のrestore、effect遷移、コード実行、承認付与はありません。

## 照会時点のlookupとmutationの不確実性

checkpoint IDを失っても、安定したscope/run/branch IDから現在headを見つけられます。
latestは照会時点のpointerで、watch、予約、後続保証、branch横断検索ではありません。
`CreateCheckpoint.expected_head`は必須のcaller UUID-or-null CASを維持します。
read後にwriteが進む場合があり、**409 `checkpoint_head_conflict`**にはcallerの再検討が必要で、
更新headや新keyを使った自動retryはしません。
別writerがpointerを進められるため、head照会はcallerの結果不明mutationのcommit証明ではありません。
現在のreplay guard下で元mutationのkey/bodyを再送して元receiptを取得し、その後headを確認します。
ID指定GETは既存検査下で生存ancestorも読めますがlatestではありません。
restore先forkのtarget headは独立しており、readはrestoreしません。

## SDKと導入

async `get_checkpoint_head(CheckpointBranch) -> CheckpointEnvelope`を追加し、
SDK `_post`を`mutation=False`で使います。call時model検証、通常の
**request 256 KiB / response 2 MiB**上限、sanitized error、
read-only transport失敗時の`outcome_unknown: false`を維持し、自動retryはありません。
既存safe `checkpoint_invalidated`を再利用し、新error codeではありません。
Native/SDK resource methodは26、MCPは既存4 toolのままで、
checkpoint toolやhook fieldは追加しません。

全adapterで厳密なservice 0.0.18 / API v1 / schema 10を要求します。
v17→v18はapplication-onlyで、SQL migration、依存/provider/artifact固定値変更はありません。
API/worker/readinessは厳密な履歴1〜10と既存role/pgvector検査を維持します。
旧componentを停止/drainして対応版を使い、古いschemaには既存offline migrationを適用します。
stageは`m2-checkpoint-head`です。capabilitiesへ`checkpoint_head`を追加し、
`endpoint: "/v1/checkpoints/head"`、`read_only: true`、
`branch_identity: ["scope_id", "run_id", "branch_id"]`、`fallback_to_ancestor: false`を設定します。

## 検証境界

**最終localとnativeの実装検証は合格しました。** Apple Containerの`./scripts/test-containers.sh`は
**exit 0**で、**630合格、既存warning 1件、369.39秒（6:09）**でした。
**既存604 + 新規26テスト**（`tests/test_contract.py`が8、
`tests/test_checkpoints.py`が11、`tests/test_sdk.py`が7）と既存coverageの拡張です。
Ruff、strict mypy **source 19 + SDK consumer 1ファイル**、真のcore/hook/sdk-only導入、
全non-root production smokeが全3環境で合格しました。
実read-only SQL/scope read-only権限でのhead照会、完全なSDK error fixture、
生存ancestor GET 200でもtombstone化headは404となるcase、
create/headのresponse-loss区別、large response、現在effect-ledger/head/forkの同等性、
26-route coverageを含みます。object TTL機能は追加しません。
新しい実SDK smokeはsource + checkpoint二つ → head sequence 2 /
過去GET sequence 1 → source purge `object_count: 3` →
head `409 checkpoint_invalidated`で全3環境とも合格し、opaque pointerを保持してIDは返しません。
実装
[`4accd38408a8384b4376f6250d953bb2fa480ec8`](https://github.com/rioriost/pg_agmemory/commit/4accd38408a8384b4376f6250d953bb2fa480ec8)
は[CI 35235315016](https://github.com/rioriost/pg_agmemory/actions/runs/35235315016)で
両native Docker architectureとも合格しました。実logで各**630合格、warning 1件**、
**amd64 663.91秒 / arm64 544.14秒**、全検査/導入、全production smokeを確認しました。
これは実装結果であり、後続最終docs CI結果ではありません。
所要時間はbenchmarkではありません。[検証済み証拠](../STATUS-jp.md#v0018--schema-10)を参照してください。
検証済みv17実装と別の最終docs CIは[過去の証拠](../STATUS-jp.md#v0017--schema-10)であり、
v18の適格性確認ではありません。この照会はharness adapter、compaction機構、汎用復旧保証ではありません。
