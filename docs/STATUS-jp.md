# 現在の契約と制限

[English](STATUS.md) | [プロジェクトREADME](../README-jp.md) | [実装プラン](PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**現在の上限付き実装はv0.0.19/schema 10の所有job照会とpaginationです。
v0.0.19実装はlocalと両native architectureで検証済みです。
検証済みv0.0.18以前の結果は過去の証拠であり、v0.0.19の結果ではありません。
M0/M1/M2/M3全体の完了、MVP完成、本番適格性の確認を意味しません。**
実装プランは将来の要求を示すもので、現在のAPIそのものではありません。
性能、記憶品質、災害復旧、完全消去の受入目標は未測定または未認定です。
ローカルとCIの検査が合格しても、これらのgateが完了したとは扱いません。

## 実装済みの範囲

durable job queueとlexical projectionを含め、アプリケーションの永続化先はPostgreSQLのみです。
外部memory DB、モデルサービス、外部queue、ファイルベースのmemory indexはありません。
Janome同梱辞書はsoftware依存であり、保存されたapplication memoryではありません。

| Endpoint | 現在の動作 |
|---|---|
| `POST /v1/observe` | caller指定の発生時刻・同意参照とともにepisodeを1件保存。revisionは`1`、`synthesis_job_id`は`null`で、job enqueueは行わない |
| `POST /v1/captures` | episode一つと明示構造化publication job一つを原子的にcommit/再利用。`201`はepisode/job組でありassertion公開済みではない |
| `POST /v1/remember` | 同一scopeの読取り可能なepisodeからの原文引用を根拠とし、明示的に要求された構造化assertionを保存 |
| `POST /v1/jobs` | 構造化記憶publicationを明示queue化。`202`はjob参照であり完了ではない |
| `POST /v1/jobs/query` | 要求した読取り可能scope内の現在caller所有jobをread-onlyで照会。exclusive keyset paginationと全page検証 |
| `GET /v1/jobs/{job_id}` | 現在読取り可能なstate、安全なerror/時刻、正確な入力参照、元の結果revision 1を返す |
| `POST /v1/jobs/{job_id}/retry` | 全intentと現在のアクセス権を検査し、所有するfailed jobのchildを一つ作成/重複抑止 |
| `POST /v1/jobs/{job_id}/cancel` | owner専用state/attempt CAS。HTTP 200でterminal取消をcommitし、worker中断やdata除去は行わない |
| `POST /v1/assertions/{memory_id}/revisions` | expected head、明示的intent、reason、revision固有のepisode根拠を使い、同一assertionへ全置換revisionを追加 |
| `POST /v1/entities` | episode根拠付きの不変・caller申告entity identityをrevision 1で作成 |
| `GET /v1/entities/{memory_id}` | 現在読取り可能なentity metadataとepisode原文根拠を返す |
| `POST /v1/relations` | 同一scopeのentity UUID間のtyped relationを一つのcanonical assertionとして作成 |
| `POST /v1/relations/{memory_id}/revisions` | assertion revision-CASでtarget、根拠、valid interval全体を置換 |
| `POST /v1/graph/expand` | 認証付き読取り専用の上限付きSQL探索。canonical relation revisionを使用し、`Idempotency-Key`は不要 |
| `POST /v1/checkpoints` | branch headのCASでtyped stateを保存し、不変checkpoint参照/checksumを返す |
| `GET /v1/checkpoints/{checkpoint_id}` | 現在のアクセス権と完全性を確認し、state・参照・epoch・照合hintを返す |
| `POST /v1/checkpoints/head` | 正確なscope/run/branchのread-only head照会。全検査済みenvelopeを返し、ancestorへfallbackしない |
| `POST /v1/checkpoints/restore` | 互換checkpointを新target branchへコピー。コード実行や外部副作用の再実行はしない |
| `POST /v1/tool-effects` | 既存checkpoint run内のintentを記録/重複抑止し、初期revision参照を返す |
| `POST /v1/tool-effects/{memory_id}/transitions` | CAS付きledger遷移を追記。toolは呼び出さない |
| `GET /v1/tool-effects/{memory_id}` | 現在の状態、不変event履歴、参照、HMAC識別子、run失効flagを返す |
| `POST /v1/recall` | ranking前の構造化完全一致filterとbyte予算pack付きlexical/vector/hybrid recall。required参照もfilter一致が必須でlexical専用 |
| `POST /v1/embedding-inputs` | HTTP 200。認可済みepisode/assertion revisionの読取り専用canonical embedding input。idempotency key不要 |
| `POST /v1/embeddings` | HTTP 201。canonical parentのscope内の明示的・不変vector upload。caller管理idempotency key必須 |
| `POST /v1/explain` | 指定したassertion revisionと根拠を返す。省略時は最新ではなく引き続き`1`。episodeはrevision `1`のみ。ranking trace APIはない |
| `POST /v1/forget` | 明示IDによる`preview`または`purge`。任意selectorや`suppress` modeは受け付けない |
| `GET /v1/deletions/{receipt_id}` | 許可された削除receiptと、未解決のoperator-managed backup状態を返す |
| `GET /v1/capabilities` | 現在の機能、上限、未対応機能を返す。認証必須 |

型付きrequest/response modelで定義したOpenAPI schemaを
`/docs`と`/openapi.json`で公開します。schemaの生成済みファイルは不要です。
`/healthz`は起動検証後のprocess livenessであり、
PostgreSQLへの継続的なreadiness検査ではありません。
`/readyz`は`/v1`の外で下記の上限付き検査を追加します。

## Owned-job query and pagination

**v0.0.19/schema 10はlocalと両native architectureで検証済みです。**
read-only `POST /v1/jobs/query`にはNative JWT認証が必要で、
`Idempotency-Key`やwrite権限は不要です。closedな`QueryJobs`のfieldは次だけです。

| Field | 契約 |
|---|---|
| `scope_ids` | 必須の重複しないUUID list、1〜32件 |
| `states` | 重複しない`JobState` list、最大5件。既定`[]`は全5状態 |
| `max_items` | strict整数1〜100、既定20。bool/floatは拒否 |
| `before` | `JobCursor`またはnull、既定null。省略/nullで最新から開始 |

`JobState`は既存の`pending`、`running`、`succeeded`、`failed`、`cancelled`を再利用し、
lifecycle意味は変えません。closedな`JobCursor`はtimezone付き`created_at` timestampと
UUID `job_id`の両方が必須です。
不正形式、重複、上限、timestamp、UUID、追加fieldは**422 `invalid_request`**です。
owner/principal/tenant/kind/payload-query selector、offset、watch、`after`、過去ACL入力はありません。

### Caller所有権、可視性、page失敗

現在tenantと認証済みprincipalの**所有jobだけ**を選び、
要求scope、現在のRLS/source/削除可視性、任意stateを交差させます。
scope-admin権限でもcaller所有権をbypassしません。
既知IDの`GET /v1/jobs/{job_id}`より意図的に狭く、
GETは他principalの同一scopeの現在読取り可能jobへのアクセスを維持します。
未知/読取り不可scopeはrecall同様に結果へ寄与しません。
一致なしは**200**と`jobs: []`、`next_cursor: null`で、非公開理由やtotal countは返しません。

選択された各page itemを既存`Jobs.get`で再読取りし、参照件数とresult生存を確認します。
実際に不正な選択jobがあれば既存**404 `not_found` / 409 `job_invalidated`**動作で
**page全体を失敗**させ、黙ったskip、部分成功、部分応答にはしません。
pageごとに現在の権限/削除検査とtenant response-delivery/drain lockを適用します。
queryはstate変更、claim/cancel、worker/provider呼出しを行いません。

### Snapshotではないexclusive keyset位置

SQL順序は`created_at DESC, id DESC`で、timestamp同値はUUID `id`で並べます。
`before`は`(created_at, id) < (before.created_at, before.job_id)`です。
`max_items + 1`行を取得し、最大`max_items`件を返します。
overflowがある場合だけlookahead行ではなく**最後に返すjob**から`next_cursor`を作り、
それ以外は空pageを含めnullです。

cursorは署名なしの透明な位置情報であり、権限、receipt、cache、snapshot、保持server objectではありません。
既存jobを指す必要もありません。偽造/古い/削除済みcursorも、
現在認可された所有候補集合を限定するだけで、権限拡張や他principalのjob発見はできません。
state遷移でも元の`created_at`を維持し、`updated_at`順ではありません。
各pageは照会時点の結果で、state/アクセス/削除変更でpage間の構成は変わり得ます。
古いcursorより上の新しい行はその下のpageへ含まれず、`before`なしで明示再開します。
構成固定の保証、total count、snapshot watermark、global LSN、永続cursor、自動paginationはありません。

### 応答、SDK、導入

正確な**200 `JobPage`**は`jobs: list[ListedJob]`、
`next_cursor: JobCursor | None`、既存`Consistency`
（`access_epoch`、`deletion_epoch`）を返し、snapshot権限ではありません。
response modelは`JobPage.jobs`を100件に制限し、SDKもこの上限を検証します。
既存checkpointのepoch readerを`MemoryService.epochs`としてcheckpoint、recall、job queryで共有します。
この抽出で永続化、schema、mutation hashは変えません。
`ListedJob`は既存の完全な`JobDetail`へUUID `scope_id`を加え、
既知ID指定GETの`JobDetail`形式は変えません。
entryは参照、state/時刻、安全なerror、`retry_of`、元のresult revision 1を含みますが、
保存payload、evidence quote、`lease_token`、intent digestは含みません。

async `query_jobs(QueryJobs) -> JobPage`は内部必須の`mutation=False`、
call時model検証、通常の**request 256 KiB / response 2 MiB**上限、
sanitizedなread-only errorの`outcome_unknown: false`を使い、自動retry/paginationはありません。
既存SDK safe `job_invalidated`を再利用し、新error codeは追加しません。
**Native/SDK resource methodは27**で、MCPの4 toolは不変、MCP job toolやhook fieldはありません。
管理/worker CLI機能はSDK resourceではありません。
全adapterで厳密な**service 0.0.19 / API v1 / schema 10**を要求します。
stage `m2-job-query`は`job_query` metadataを追加します。
`endpoint: "/v1/jobs/query"`、`ownership: "caller"`、
`order: ["created_at_desc", "job_id_desc"]`、`pagination: "exclusive_keyset"`、
`max_items: 100`です。
v18→v19にはSQL migration、依存/provider/artifact固定値変更はありません。
旧componentを停止/drainして対応版を使い、履歴1〜10は不変です。
性能、MVP、本番、DR適格性確認は主張しません。
[運用](operations/README-jp.md#owned-job-query-and-pagination)、
[ADR 0019](adr/0019-job-query-jp.md)、[検証済み証拠](#v0019--schema-10)を参照してください。

## Checkpoint-head lookup

**既存checkpoint head契約を維持します。v0.0.19はlocalと両native architectureで検証済みです。**
`POST /v1/checkpoints/head`はNative JWT認証とscopeの現在のread権限を要求し、
write権限は不要です。`Idempotency-Key`も不要です。
closedな型付き`CheckpointBranch` bodyは必須UUIDの`scope_id`、`run_id`、`branch_id`だけです。
不正形式/UUIDや余分なfieldは**422 `invalid_request`**です。
identity override、`expected_head`、harness selector、`as_of`、
branch横断のlatest selector、履歴一覧入力は受け付けません。

### 正確なpointerと現在の認可

正確なtenant/scope/run/branch identityに一致する既存branchだけを選択します。
既存APIのtenant session-lock/drain barrier下で、`FOR UPDATE`なしのSQL `SELECT`を使います。
run/branch作成、state変更、audit、idempotency receiptはありません。
不在/非公開/異なるscope/別tenantのbranchと、失効していない空branchは
要求ID/内容や空成功でなく汎用**404 `not_found`**です。
現在読取り可能な失効branchは、保持されたhead pointerを使う前に
**409 `checkpoint_invalidated`**です。earlier ancestor、sibling、default branchへのfallbackはありません。

既存envelope経路で選択headを読み、現在scope権限、HMAC、typed state、
memory参照、live effect検査を維持します。
runの`effects_invalidated`や既存の完全性不正は既存の拒否を維持し、非公開headは404です。
読んだ`scope_id`、`run_id`、`branch_id`、`sequence`を選択branchと比較し、
不一致なら**409 `checkpoint_invalidated`**にします。
sourceの`forget`は対象branchを`invalidated=true`とし、canonical checkpoint/参照payloadを削除します。
opaqueなbranchの`head_id`と`sequence`、`memory.object` anchor、tombstoneは保持します。
head照会は失効を先に確認し、まだ読取り可能な失効branchには409を返して保持head IDは返しません。
scopeアクセス権が取り消されていれば404でbranchを隠し、どちらも削除stateを復活させません。

### 予約やcommit receiptではない完全なenvelope

成功は正確な**200 `CheckpointEnvelope`**で、最小IDや新形式ではありません。
state、保存HMAC checksumと参照、`saved_access_epoch`、`saved_deletion_epoch`、
`current_access_epoch`、`current_deletion_epoch`、現在のrun全体の`tool_effects` ledger view、
`requires_reconciliation`、`untracked_effects`、`resume_allowed`、
`automatic_reexecution: false`を含みます。同じenvelope helperを再利用し、
保存epochで古い権限を許可しません。`resume_allowed: true`は承認やprovider実行の証明ではありません。
read時のrestore、effect遷移、harness実行、自動承認はありません。

checkpoint IDを失っても安定したscope/run/branch IDからheadを見つけられます。
latestはそのbranchの照会時点の現在pointerであり、watch、予約、後続保証、branch横断のlatest照会ではありません。
`CreateCheckpoint.expected_head`は必須UUID-or-nullのcaller CASを維持します。
read後にwriteが進む場合があり、**409 `checkpoint_head_conflict`**にはcallerの再検討が必要で、
更新headや新keyを使った自動retryはしません。
別writerがheadを進められるため、head照会はcallerの結果不明mutationがcommitした**証明ではありません**。
既存replay guard下で元mutationのkey/bodyを再送して元receiptを取得し、その後headを確認します。
checkpoint ID指定GETは既存検査下で生存ancestorも読めますがlatestを保証しません。
restore先forkのtarget headは独立しています。

### SDK、互換性、制限

async `get_checkpoint_head(CheckpointBranch) -> CheckpointEnvelope`はSDKの
`_post`を`mutation=False`で使い、通常の**request 256 KiB / response 2 MiB**上限とcall時model検証を維持します。
read-only transport失敗は`outcome_unknown: false`で、自動retryはありません。
`checkpoint_invalidated`は既存SDK safe codeであり、新error codeは追加しません。
**Native/SDK resource methodは27**となりますが、**MCP toolは4**のままです。
MCP/hook surfaceは不変で、checkpoint toolやhook入力fieldはありません。
全adapterで厳密な**service 0.0.19 / API v1 / schema 10**を要求します。
API/worker/readinessは厳密な履歴1〜10を維持します。
v18→v19にはSQL migration、依存/provider/artifact固定値変更がなく、旧componentを停止/drainして対応版を使います。
stageは`m2-job-query`で、capabilitiesの`checkpoint_head`を維持します。
`endpoint: "/v1/checkpoints/head"`、`read_only: true`、
`branch_identity: ["scope_id", "run_id", "branch_id"]`、`fallback_to_ancestor: false`です。
harness連携、compaction、MVP、汎用復旧/本番/DRの適格性確認ではありません。
[運用](operations/README-jp.md#checkpoint-head-lookup)、
[ADR 0018](adr/0018-checkpoint-head-jp.md)、[検証済み証拠](#v0018--schema-10)を参照してください。

## Exact structured recall filters

**既存recall filter契約を維持します。v0.0.19はlocalと両native architectureで検証済みです。**
既存requestへ`Recall.filters: RecallFilters | None = None`を追加します。
`RecallFilters`は共有の型付きclosed nested契約で、未知fieldを拒否します。

| Field | 契約 |
|---|---|
| `kind` | `"episode"`、`"assertion"`、nullのいずれか。既定null |
| `subject` | `ShortText`またはnull。既定null。既存の前後空白除去後1〜256文字 |
| `predicate` | `^[a-z][a-z0-9_]{0,63}$`に一致するstringまたはnull。既定null |

`kind: "episode"`とnon-null subject/predicateは組み合わせられません。
不正field、形式、値、組合せは**422 `invalid_request`**です。
field alias、任意SQL、model推論filter、range、array形式selectorはありません。
省略、null、`{}`、全field nullはlexical/vector/hybrid全modeの既存結果を同一に維持します。

### Query拡張ではない完全一致選択

non-null fieldを**AND**で結合します。subjectまたはpredicateはrelationを含む
assertion候補を意味します。`kind: "assertion"`だけでもrelation assertionを含み、
`kind: "episode"`は全assertionを除外します。
subject/predicateは通常の`Contract` string trim後、
**`C` collationによる大文字小文字を区別した完全一致**で比較します。
部分文字列/FTS、Unicode正規化、fuzzy一致、entity解決は行いません。
subjectは保存assertionのsubjectであり、解決済みentity aliasではありません。
空queryのlexical recallは完全一致filter後の候補をbrowseし、
通常の非空lexical queryには引き続きlexical一致が必要です。filterはqueryを迂回しません。
別のrequired参照契約だけがkeyword迂回を維持します。

### 候補、ranking、coverageの境界

filterはtop-K選択後でなく、**共有materialized候補relationの内部で、
lexical/vector/hybridのranking、coverage、required参照の適格性より前**に適用します。
rank、cosine候補、RRF寄与はfilter後の適格候補集合を使って計算します。
同じ固定`as_of`/`known_at`、要求scope、現在RLS、根拠可視性/完全性、
削除gateを維持し、filterは選択を狭めるだけです。
lexical/vector欠落coverageは除外objectでなく、このfilter後の適格候補集合を反映し、
引き続きquery関連性やitem上限とは独立です。
`coverage.jobs_pending`は意図的に要求scope単位のsignalを維持し、
job payloadの構造化一致を**意味しません**。

空でない`required_memory_refs`はlexical専用で、既定revisionは**最新でなく1**、
既存時刻選択とrequest順prefixを維持します。
全required参照にもfilter一致が必要です。不一致なら欠落IDや部分packを返さず、
**request全体を汎用404 `not_found`**にします。required参照はfilterを迂回しません。
compact `ContextPack`全体のUTF-8 byte予算、optional除外/truncation、
requiredの**422 `budget_exhausted`**、既存`budget_too_small`/成功時の空理由は不変です。

### Surface、導入、制限

既存の型付きSDK `recall(Recall)`とMCP `memory_recall`が追加fieldを公開します。
**Native/SDK resource method 27、MCP tool 4**で、filter自体はrouteやsafe error codeを追加しません。
SDK recallはread-only、`outcome_unknown: false`、sanitized call時error、自動retryなしを維持します。
hook入力は`filters`に**非対応**で、未知fieldを拒否します。
内部`Recall.filters`は既定`None`で、信頼する起動時境界を維持します。
filterは権限付与、内容検証、意図推論を行いません。
v18からのSQL migration、依存/provider/index変更、永続priority、cacheは追加しません。
schema 10と厳密な履歴1〜10を維持し、停止/drain後に対応する
**service 0.0.19 / API v1 / schema 10** componentを使います。
stageは`m2-job-query`で、capabilitiesの`recall_filters`を維持します。
`fields: ["kind", "subject", "predicate"]`、`match: "exact"`、`combination: "and"`、
`retrieval_modes: ["lexical", "vector", "hybrid"]`です。
MVP、意味品質、性能、本番、DRの適格性確認は主張しません。
[運用](operations/README-jp.md#exact-structured-recall-filters)、
[ADR 0017](adr/0017-recall-filters-jp.md)、[検証済み証拠](#v0017--schema-10)を参照してください。

## Required-context recall

**既存required-context契約を維持し、v0.0.19はlocalと両native architectureで検証済みです。**
既存`Recall`への追加fieldであり、新routeやSDK methodではありません。

| Request規則 | 契約 |
|---|---|
| `required_memory_refs` | 既定は省略または`[]`。最大16件の`MemoryReference` |
| 参照 | `memory_id` UUIDとrevision 1〜1000。省略revisionは**最新でなく1** |
| Identity/件数 | revision違いでもmemory IDは一意。件数は`max_items`以下 |
| Retrieval | 空でない参照は`retrieval_mode: "lexical"`を要求。vector/hybridは拒否 |
| Recall mode | explicit/implicit両方。既存implicit 2,000-byte上限を維持 |

不正なfield組合せ、重複ID、件数超過、参照形式/範囲の不正は**422 `invalid_request`**です。
field省略または`[]`なら選択した構造化filter内で既存lexical/vector/hybridの順序、結果、pack semanticsを維持します。
新たなreadの書込み、idempotency要求、priority metadata、永続cache、
model推論、provider呼出しは追加しません。

### 候補の適格性と順序

required参照は通常recallと**同じ現在認可済み・materializedなepisode/assertion候補relation**を使い、
要求`scope_ids`と固定した`as_of`/`known_at`を適用します。
scope拡張、ownership/ACL override、時間/[構造化filter](#exact-structured-recall-filters)迂回、
latest選択、別revisionへのfallbackはありません。
既存bitemporal規則により、ある`known_at`ではobject当たり一つのrevisionを選択します。
relation assertionは同じfilter下で対象となりますが、entity objectは異なるkindの参照であり、
recall候補ではありません。
不在/読取り不可/purge済み/異なるkind/要求scope外/時間条件外/異なるrevisionの参照が
一つでもあれば、**request全体を汎用404 `not_found`で拒否**します。
部分contextや欠落参照名は開示しません。

required参照が迂回するのは**query keyword一致とranking cutoffだけ**です。
先頭に**request順**で置き、通常のlexical順位のoptional itemを続けます。
required IDをoptional候補から除外して重複を防ぎます。
両方が`max_items`以内に含まれ、追加のoptional overflow候補で引き続き
`coverage.truncated`を示します。
`simple-v1`と`ja-janome-0.5.0-v1`に対応します。
日本語projection欠落時も正確なcanonical参照を含められますが、
`coverage.lexical_incomplete`など既存の不完全coverage metadataを維持し、index修復はしません。

### Required prefix全体またはerror

予算は引用、quote、warning/`refresh_required` markerを含む
**compact `ContextPack` JSON全体のUTF-8 byte数**です。
relationのentity UUIDとcitation overheadも同じbyte予算に含めます。
`token_budget`は正確なmodel token計数の保証ではありません。
required itemが一つでも丸ごと収まらなければ、
Nativeは**422**と`ErrorBody.code`の`budget_exhausted`を返します。
空の成功、required省略、部分required contextにはしません。
最小予算64では、空envelope自体が収まらない場合でもこのerrorになり得ます。
optional itemはgreedyなitem単位除外と`coverage.truncated`を維持します。
required参照なしでは既存の空envelope **422 `budget_too_small`**と、
成功時の空理由`empty_reason: "budget_exhausted"`を維持します。
新error codeと既存の成功時の空理由は別物です。

Native SDKとMCPはraw本文を含めずsafe `budget_exhausted`を伝播します。
SDK recallはread-onlyで、errorは`outcome_unknown: false`、自動retryはありません。
caller指定参照/予算を明示的に再検討し、required prefixを黙って弱めたりaccessを広げたりしないでください。
選択した参照は**policy権限、検証済み承認、信頼する指示、現在事実の保証ではありません**。
必須制約の自動検出、意味品質/性能の適格性確認、MVP完成の主張はありません。

### Surfaceと互換性

既存の型付きSDK `recall(Recall)`とMCP `memory_recall`でfieldを使い、
**Native/SDK resource method 27、MCP tool 4**を維持します。
hook入力は`required_memory_refs`に**非対応**で、追加fieldとして拒否します。
内部で構築する`Recall`は既定`[]`のままで、host pinningは追加しません。
共有の制限付きsafe-code catalogに`budget_exhausted`を追加し、
SDKはより広いNative error catalogを維持します。
adapterは厳密な**service 0.0.19 / API v1 / schema 10**を要求します。
API/worker/readinessは厳密なschema履歴1〜10を維持します。
v18→v19に**migration、依存upgrade、固定image変更はなく**、古いschemaは停止/drain後にmigrationします。
stageは`m2-job-query`で、capabilitiesに`required_context`を維持します。
`retrieval_modes: ["lexical"]`、`max_refs: 16`、`order: "request_order"`、
`budget_policy: "all_required_or_error"`です。
[運用](operations/README-jp.md#required-context-recall)、
[ADR 0016](adr/0016-required-context-jp.md)、[過去の証拠](#v0016--schema-10)を参照してください。

## Explicit job cancellation

**既存job取消契約を維持し、v0.0.19はlocalと両native architectureで検証済みです。**
`POST /v1/jobs/{job_id}/cancel`はNative JWT認証とcallerの`Idempotency-Key`を要求します。
`CancelJob`が受け付けるのは次のfieldだけです。

```json
{"expected_state":"pending","expected_attempt":0}
```

`expected_state`は`pending`または`running`です。
`expected_attempt`は**strict整数0〜5**で、runningは**1以上**を要求します。
boolean、整数への暗黙変換、追加fieldは不正です。
reason、provider、lease token、任意state強制入力はありません。
job GETでstate/attemptを取得し、そのCASを明示選択します。
**tenant `access_epoch`や外部tool state/versionではありません**。

### 権限と原子的な結果

jobの`principal_id`は認証中のcurrent principalと一致し、現在のscope
**readとwrite**権限、既存のsource可視性/完全性検査も満たす必要があります。
同じscopeの別readerは`admin` permissionがあっても取消できず、
runtime `job_update` RLSは変更しません。
不在/private/cross-tenant/non-job/purge済みtargetは`404`、
既存の参照失効は`409 job_invalidated`です。
原子的SQL UPDATEは**expected stateとattemptの両方**を照合します。

HTTP **200**（202ではない）でcommit済み`JobReceipt`を返します。

```text
{job_id, kind: "structured_remember", recipe_version: "structured-remember-v1"}
```

GETで`state: "cancelled"`を確認します。leaseが有効**または期限切れ**でも
pending/runningからterminal cancelledへ遷移できます。
保存job payload、`lease_token`、`lease_until`、`error_code`を消去し、resultはありません。
同じjob identity、attempt、source/intent/input参照、`retry_of`、`created_at`を保持し、
DB clockの`updated_at`で完了を記録します。取消reason/private自由文は保存しません。
state変更、`job_cancelled` audit、idempotency receiptは原子的です。
保存idempotency結果は`{job_id}`だけで、request/keyはHMACで保護します。
transaction失敗時は3者をまとめてrollbackし、access/deletion epochは進めません。

既存tenantの**session advisory lockをHTTP配信完了まで保持**し、
worker claim/publicationも同じbarrierを使います。
取消jobのqueue化、worker kill、provider中断/compensationではありません。
古い準備処理が続いてもpublish/heartbeat/failはnot-runningを検出して
`job_lease_conflict`となり、workerは`lease_lost`を報告します。
取消が先ならそのjobからresultは公開されません。
publicationのcommitが先なら取消は競合し、succeeded resultを維持します。**公開撤回はしません**。

### 競合、replay、保持

state/attempt変更やterminal（`succeeded`、`failed`、`cancelled`）は
`409 job_cancel_conflict`です。既にcancelledのjobを新keyで取消する場合も同じです。
成功した同じkey/bodyは、現在のowner/write/access/liveness検査が通る間だけ元receiptをreplayします。
同じkeyでbody**またはjob ID**を変えると`409 idempotency_conflict`です。
HTTP配信結果不明時は**同じkey/bodyまたは現在job GET**で照合し、
expected値やkeyを盲目的に新しくしません。自動retryはありません。

取消は**`forget`ではありません**。canonical source episode/evidence、
opaque intent参照、dedup anchorを保持します。
worker準備memory、WAL、backupの消去は保証しません。
source forgetは依存jobをpurgeして取消replayを拒否し、再grantでもpurge済みpayloadは復活しません。
retryは**failed専用**のためcancelledは`409 job_retry_conflict`です。
通常の同intent enqueue/captureはcancelled jobへdedupし、
capture replayは元のepisode/job pairを維持して復活させません。
recipe版や架空intent keyでdedupを迂回しないでください。
cancelledは100 active-job上限と`jobs_pending`の両方から除外します。

### SchemaとSDK境界

`010_job_cancellation.sql`はjob state/payload制約とguard triggerを変更し、tableは追加しません。
cancelledはDB上で不変となり、復活/書換えを拒否します。
payload/result/lease/errorのnullを制約し、新規jobは引き続きpending、attempt 0が必須です。
既存pending/running/succeeded/failed semanticsを維持します。
schema 9→10 migrationは旧processの停止/drainを要求します。
PostgreSQL 18.6、pgvector 0.8.6、依存、provider、固定artifactは変更しません。

SDKの非同期`cancel_job(UUID, CancelJob, *, idempotency_key) -> JobReceipt`は
modelを再検証し、正確なHTTP 200を要求してsafe Native error catalogへ`job_cancel_conflict`を追加します。
所有job照会による現在のNative resource/SDK surfaceは**27 method**です。MCPの4 toolとread-only hookは変更しません。
Python taskのcancelはこのjob取消endpointを呼びません。
全adapterは**service 0.0.19 / API v1 / schema 10**を要求し、readinessは厳密な履歴1〜10を検査します。
過去v0.0.15のstageは`m2-job-cancellation`、現在は`m2-job-query`です。
capabilitiesは`job_cancellation` metadataを維持します。
`endpoint: "/v1/jobs/{job_id}/cancel"`、`compare_and_swap: ["state", "attempt"]`、
`terminal_state: "cancelled"`、`provider_interruption: false`を追加します。
[運用](operations/README-jp.md#explicit-job-cancellation)と
[ADR 0015](adr/0015-job-cancellation-jp.md)を参照してください。

## Runtime readiness

**既存readiness契約を維持し、v0.0.19/schema 10はlocalと両native architectureで検証済みです。**
health probeはpublic・認証不要のpathであり、Native memory resource routeではありません。
`GET /healthz`は起動成功後にDBを呼ばず正確な`{"status":"ok"}`を返す動作を維持します。
v0.0.14で導入した`GET /readyz`は渡された認証headerを無視し、tenant/principalを選びません。
想定するreadiness応答は次のとおりです。

| HTTP status | 正確なbody | Header |
|---|---|---|
| `200` | `{"status":"ready"}` | `Cache-Control: no-store`、生成UUIDの`X-Request-ID` |
| `503` | `{"status":"not_ready"}` | `Cache-Control: no-store`、生成UUIDの`X-Request-ID` |

OpenAPIの両応答はNative `ErrorBody`でなく型付き`ReadinessStatus`です。
不正なHTTP methodはreadiness検査を行わず405を返します。

public bodyにreason、DSN、token、payload、identity、schema一覧を含めません。
受け付けたprobeごとに**同じruntime DSN**の新しい接続で既存`validate_runtime`を呼び、
admin資格情報やfallbackは使いません。API起動とworker検証を含むvalidation sessionは
`default_transaction_read_only = on`を明示します。
明示SQLは最大4文で、`SET`一つとrole catalog、schema履歴、extension catalog用の
`SELECT`三つです。read-onlyは専用validation接続だけに適用し、
後続Native mutationの書込み可能性は維持します。

- superuser、`BYPASSRLS`、`memory`または`memory_ops`のtable所有権/owner-role membershipを
  拒否し、`NOINHERIT` membershipも対象です。
- 厳密なmigration履歴`[1,2,3,4,5,6,7,8,9,10]`を要求します。
- **`public`内の`vector` 0.8.6**を要求します。

memory本文読取り、tenant lock、audit/epoch/job/receipt書込み、migration、
provider呼出し、成功結果cache、background検査、retryはありません。
既存の起動時fail-closed動作を維持します。
`RuntimeValidationError`は`RuntimeError`を継承し、従来の起動messageを維持しつつ、
想定する設定driftを固定codeで区別します。

### Admission、cancel、diagnostic

API app/processごとにactive検査は一つだけ受け付けます。並行要求はlog reason
`probe_busy`で即503となり、**別接続、待機、cache済み成功応答はありません**。
process単位のadmissionであり、global rate limitやrequest flood適格性確認ではありません。
固定`asyncio` active検査timeout予算は**5.0秒**、DB connect/statement/lock予算は
引き続き**5秒**です。cancel/connection cleanupで遅延が増え得るため、
**厳密なwall-clock SLAではありません**。cancelは伝播しgate/connectionを解放し、retryしません。

想定内の失敗は生成`request_id`と固定`reason`を持つ`readiness_unavailable`をlogへ出します。
reasonは`runtime_role_invalid`、`schema_unavailable`、`schema_version_mismatch`、
`extension_version_mismatch`、`probe_busy`、または例外class名です。
readiness diagnosticにraw error文字列、traceback、DSN、資格情報、payloadは含めません。
`psycopg.Error`と`TimeoutError`は503になります。
通常の`RuntimeError`を含む想定外のprogramming exceptionは
**catchしてnot-ready応答に見せかけません**。

### 解釈と互換性

これは**ある時点の接続/runtime role/schema/vector契約**であり、
全principal認可、table grant/RLS policyの完全性audit、write transaction、
書込み可能性/primary検査、継続JWT検証、tokenizer/provider readiness、
backlog/load/HA/DR/性能/品質/本番の適格性確認ではありません。
SELECT-only DBもこの上限付き検査に合格し得ます。
resource routeはprobeを呼ばず、drift後の永続fail-closed gateを新設しません。
readinessはoperatorのtraffic停止を助けるもので、**認可境界の代用ではありません**。
既存Native認可は引き続き強制します。

livenessには`/healthz`を使い、restart stormを起こす依存readinessで代用しないでください。
busy 503も考慮したorchestrator失敗/復旧thresholdを設定し、
deployment perimeterでpublic probeを制限/rate-limitします。
Kubernetes、Compose、Docker `HEALTHCHECK`設定は追加しません。

過去v0.0.14のstageは`m2-runtime-readiness`で、現在は`m2-job-query`です。認証付きcapabilitiesに
`health_probes` metadataとして`liveness: "/healthz"`、`readiness: "/readyz"`、
`readiness_timeout_seconds: 5.0`、`readiness_max_in_flight_per_process: 1`を追加します。
所有job照会によりpublic memory surfaceは**27 resource method**となり、health pathはSDK resource-route coverage対象外です。
SDK/MCP/hook probe methodは追加しません。
対応adapterはすべて**service 0.0.19 / API v1 / schema 10**を要求します。
readiness動作は維持しますが、job取消のschema 10にはmigration 010が必要です。
依存や固定imageは変更しません。
[運用](operations/README-jp.md#runtime-readiness)と
[ADR 0014](adr/0014-runtime-readiness-jp.md)を参照してください。

## Scope-access administration

**既存scope-access契約を維持し、v0.0.19はlocalと両native architectureで検証済みです。**
`pg-agmemory scope-access get|set|revoke --tenant-id UUID --scope-id UUID --principal-id UUID`
は特権管理CLIであり、agent toolやruntime APIではありません。
**既存の同一tenant**のtenant/scope/principalを対象とし、identityやscopeは作成しません。
IDはUUIDとしてparseし、lock keyを正規化します。取得memory本文ではなく、
信頼する管理情報から承認済みopaque IDを選んでください。
public Native memory surfaceは27 resource methodとなり、
**HTTP、MCP、Python SDK管理methodは追加しません**。

### 権限とcompare-and-swap

`PGAG_ADMIN_DATABASE_URL`のみを使います。接続DB roleは
`rolsuper`または`rolbypassrls`**と適切なSQL table権限**を必要とします。
`get`でもruntime資格情報を拒否し、RLS bypassだけではtable権限を与えません。
JWT、`--subject`、`--once`、runtime URLへのfallbackはありません。
`get`は`FOR UPDATE`を使わず、非ownerの`BYPASSRLS` roleも、
`memory`の`USAGE`とschema履歴/tenant/scope/principal/`scope_member`の`SELECT`で照会できます。
変更には追加でtenantの`UPDATE`、該当する`scope_member`の
`SELECT`/`UPDATE`/`INSERT`/`DELETE`、`memory_ops`の`USAGE`と
`scope_access_event`の`INSERT`が必要です。
readもsession advisory barrierを取得し、table権限がread-onlyでもRLS bypass roleは必須です。
操作前にrole、厳密なschema履歴**1〜10**、**`public`内の`vector` 0.8.6**を検査します。

| 操作 | 必須intent | 意味 |
|---|---|---|
| `get` | 3 IDのみ。変更optionなし | response-drain barrier内で現在membershipとtenant epochを取得 |
| `set` | `--expected-access-epoch`、`--permissions`、expiryの明示選択 | permissionとexpiryの全置換。mergeしない |
| `revoke` | `--expected-access-epoch`。permission/expiry optionなし | membership行を削除。現在epochで既に不在ならno-op |

すべての変更で**1〜9223372036854775807**のexpected epochを明示します。
要求状態と既に一致するかを確認する前に、lock下で**tenant全体**の`access_epoch`と比較します。
古い期待値は常に`access_epoch_conflict`となり、無関係なscope変更とも競合します。
自動retry、HTTP `Idempotency-Key`、変更receipt、receipt queueはありません。

`set`は`read`、`write`、`delete`の重複しないflag、または`admin`単独を受け付けます。
重複や`admin`との混在を拒否します。write-only/delete-onlyもDB flagとして許可しますが、
必要なread権限を含むNative操作の要件を上書きしません。
permissionの返却順は**read、write、delete、admin**で、
順序変更だけでは同等と扱い変更を作りません。
**timezone-aware ISO timestampの`--expires-at`または`--no-expiry`**を必ず選びます。
naive timestampは不正で、lock取得後のDB clock以下の期限も拒否します。
無期限accessは明示必須で、expiry省略によって期限を消しません。
expiryだけの変更もepochを進めます。

### 結果、期限、audit

成功は**result wrapperのないJSON 1行**です。

```text
{
  operation, tenant_id, scope_id, principal_id, access_epoch, changed,
  membership_exists, permissions, expires_at, effective_permissions, evaluated_at
}
```

membership不在時は`membership_exists: false`、`permissions: []`、
`expires_at: null`です。legacyの空permission行は`membership_exists: true`のままです。
`effective_permissions`はDB `evaluated_at`時点で期限切れなら空、
有効な`admin`なら全4 flag、それ以外は設定flagです。
**ある時点のmembership解釈であり、Native action全体の認可判定ではありません**。
結果直後に期限切れになることもあります。
自然な期限切れは`access_epoch`を進めず、payload/auditを消さず、処理中HTTPもdrainしません。
強いdrainが必要な場合は明示revoke/barrierを使います。

既存**`009_scope_access.sql`**は特権専用`memory_ops.scope_access_event`を作り、
forced RLSと**runtime policy/grantなし**を適用します。
主keyは`(tenant_id, access_epoch)`で、同一tenant FKが対象scope/principalを結びます。
eventは`set`/`revoke`、変更前後のpermission/expiry、DB `recorded_at`、
`current_user`由来の`database_role`を記録し、
実行DB roleを示します。end-user actorを偽装するものではありません。
`recorded_at`はDB clockを使い、`evaluated_at`はresponse専用でaudit fieldではありません。
平文memory本文、external subject、DSNを含めません。
既存ACL行を維持し、**過去の手動変更へaudit backfillは行いません**。
暗黙のownership変更やpurge動作も追加しません。
**実変更だけ**がmembership更新、tenant epoch増加、audit追記を原子的に行います。
read、no-op、conflict、rollbackした変更はeventやepoch増加を作りません。
epoch上限では変更を拒否します。
特権管理者はDBを変更できるため、**改ざん耐性の証明、独立したrevocation復旧台帳、
DR solutionではありません**。

### Response-drain barrierと失敗

専用同期admin connectionはautocommitと**connect/statement/lock各5秒timeout**を使います。
API/workerと同じ**session** advisory lock
`pg_advisory_lock(hashtextextended(canonical_tenant_uuid, 0))`を取得します。
membership transactionのcommit**とCLI JSON stdout flush完了まで**保持し、
connection closeで解放します。`get`と変更no-opもbarrierを取得します。
このconnectionをpoolしたり、transactionだけのlockへ置き換えたりしてはいけません。
先行する遅い応答は管理操作を止め得ます。lock timeoutは変更なしで失敗します。
変更途中の失敗はmembership、epoch、auditをまとめてrollbackし、
失敗cleanupでsessionを閉じてlockを解放します。
協調する同じ版のAPI clientはこれらの管理操作中もonlineを維持できますが、
**migrationは引き続き旧processの停止/drainが必要**です。

syntax/model/config errorは固定sanitized stderr、exit **2**、JSONなしです。
不正なCLI構文は`invalid_scope_access_arguments`を報告します。
`PGAG_ADMIN_DATABASE_URL`未設定も固定stderr diagnostic、exit 2、stdoutなしです。
DB/domain操作errorはstdout、exit **1**です。

```json
{"error":{"code":"access_epoch_conflict","outcome_unknown":false}}
```

error catalogは`admin_role_required`、`admin_privilege_required`、
`schema_unavailable`、`schema_version_mismatch`、`extension_version_mismatch`、
`not_found`、`access_epoch_conflict`、`access_epoch_exhausted`、`invalid_expiration`、
`admin_database_unavailable`、`admin_database_error`です。
raw DB error、DSN、資格情報、private dataは出力しません。
commit通信失敗は保守的に`outcome_unknown: true`とし、commit試行前の失敗はfalseです。
cancel、process kill、stdout喪失でも変更結果は不明になり得ます。
**古いCASを盲目的に再送しないでください**。`get`と特権auditで確認し、
新しく観測したepochを使う新操作を明示承認します。自動retryはありません。

barrierは配信済みcontextを撤回できません。最新ACL/削除記録のrestoreは手動であり、
grantによってpurge済みdataは復活しません。
過去のv0.0.13 stageは`m2-scope-access`で、現在stageは`m2-job-query`です。
capabilitiesは`scope_access_administration`
metadataとして`transport: "admin-cli"`、`command: "scope-access"`、
`compare_and_swap: "tenant_access_epoch"`、`audit: "database_role"`を追加します。
PostgreSQL 18.6/pgvector 0.8.6の固定imageとPython依存版は変更しません。
[運用と例](operations/README-jp.md#scope-access-administration)と
[ADR 0013](adr/0013-scope-access-jp.md)を参照してください。

## Python SDK

**SDKはread-only所有job照会を追加し27 methodです。v0.0.19はlocalと両native architectureで検証済みです。**
`from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError`で既存public Native
memory resource用のasync専用clientを公開します。
request/response型は`pg_agmemory.models`からimportします。
mutableなmodel instanceもcall時に再検証し、応答も同じNative型付きmodelを使います。
packageはPEP 561の`py.typed` markerを含みます。

### 導入、lifecycle、権限

対応checkoutから`python -m pip install '.[sdk]'`で導入します。
`pg-agmemory[sdk]`が追加するのは`httpx==0.28.1`だけですが、
**core distributionには引き続きFastAPI、psycopg、Janomeが含まれます**。
独立公開の軽量SDKでもPyPI公開済みという主張でもありません。
HTTPXがない場合のSDK importは固定の明確な`ImportError`となり、
`mcp`/`hook`が提供するHTTPXでも動作します。extraの選択名でなく依存の存在を検出します。
Docker test/runtimeは`mcp`・`hook`とともに`sdk`を含みます。
core-only/hook-only/sdk-only検査と、hook-only/sdk-only導入にMCP SDKがないことの検査を実装しています。
真のnoneditable wheel導入3検査と同梱`py.typed`検査はv0.0.19のlocalと両native architectureで合格しました。
Python依存のupgradeはありません。

`AsyncMemoryClient(api_url, api_token)`へ明示引数を渡し、信頼できないcall入力から
設定しないでください。`NativeSettings`は固定HTTPS originまたはloopback HTTP originを
要求し、application path、userinfo、query、fragmentを禁止します。
constructorはbearer tokenの**形だけ**を検査し、設定errorは`MemoryClientError`でなく
既存のsanitized `ValueError`です。serverが実際の認証・認可を行います。
request scopeは現在のserver ACLを狭めるだけで、identity変更や権限付与はできません。

client instanceごとに`async with ... as memory:`を一度だけ使います。
entryで所有HTTP clientを作り、resource利用前に必須の認証付きcapabilities照会で
厳密な**service `0.0.19` / API `v1` / schema `10`**一致を要求します。
entry失敗時も`finally`で所有resourceを閉じます。
entry前/exit後のcallは`client_not_open`、再entryは`client_already_used`で拒否します。
exitは接続を解放するだけで、**dataは消去しません**。
callerは未完了taskをawaitするかcancel後にawaitして、**context exit前に完了を確認**します。
client closeはrequestのschedule/cancel管理でもDB rollbackでもありません。
処理中の変更cancelには引き続き照合が必要です。
環境変数の自動読込み、retry、cache、DB資格情報、provider呼出し、
host登録、delegation、自動jobは追加しません。

### 型付きresource method

すべて非同期です。下記body名はNative request model、ID引数はUUIDであり任意pathではありません。
すべての変更にはcaller管理のkeyword-only `idempotency_key`が必須です。
`forget`のpreview/purgeも対象で、両方HTTP 202です。
その他の読取り専用methodはkey不要でHTTP 200、他の書込みはNative既存の201/202を維持します。
`cancel_job`は正確なHTTP 200を要求し、取消receiptはpublicationでなくcommit済み取消を示します。
SDK内部の各POSTは期待成功statusから変更を推測せず、readとmutationを明示区別します。
HTTP 200の取消にもkey検証と保守的な変更結果不明の扱いを適用し、
`forget` previewも同じ保守的な扱いを維持します。

| Method / request | 型付き結果 |
|---|---|
| `observe(Observe)` | `ObserveResult` |
| `capture(Capture)` | `CaptureResult` |
| `remember(Remember)` | `RememberResult` |
| `revise_assertion(UUID, ReviseAssertion)` | `RevisionResult` |
| `recall(Recall)` | `RecallResult` |
| `explain(Explain)` | `EpisodeExplanation \| AssertionExplanation` |
| `forget(Forget)` | request modeで選ぶ`DeletionPreview \| DeletionResult` |
| `get_deletion(UUID)` | `DeletionProgress` |
| `embedding_input(Explain)` | `EmbeddingInput` |
| `put_embedding(PutEmbedding)` | `EmbeddingReceipt` |
| `create_entity(CreateEntity)` | `EntityReceipt` |
| `get_entity(UUID)` | `EntityDetail` |
| `create_relation(CreateRelation)` | `RememberResult` |
| `revise_relation(UUID, ReviseRelation)` | `RevisionResult` |
| `expand_graph(ExpandGraph)` | `GraphResult` |
| `enqueue_job(EnqueueJob)` | `JobReceipt` |
| `get_job(UUID)` | `JobDetail` |
| `query_jobs(QueryJobs)` | `JobPage` |
| `retry_job(UUID, EnqueueJob)` | `JobReceipt` |
| `cancel_job(UUID, CancelJob)` | `JobReceipt` |
| `create_checkpoint(CreateCheckpoint)` | `CheckpointReceipt` |
| `get_checkpoint(UUID)` | `CheckpointEnvelope` |
| `get_checkpoint_head(CheckpointBranch)` | `CheckpointEnvelope` |
| `restore_checkpoint(RestoreCheckpoint)` | `CheckpointEnvelope` |
| `plan_tool_effect(PlanToolEffect)` | `ToolEffectReceipt` |
| `get_tool_effect(UUID)` | `ToolEffectDetail` |
| `transition_tool_effect(UUID, TransitionToolEffect)` | `ToolEffectReceipt` |

対象はpublic memory resource routeであり、**CLI管理/worker関数は対象外**です。
capabilities照会は内部処理のみで、public health、OpenAPI download、
raw任意request methodは追加しません。同期client、TypeScript SDK、token refreshもありません。

### 上限、error、復旧

共有Native HTTP transportはMCP/hookの上限を維持します。
**exchange全体20秒、I/O 10秒 / connect 5秒、最大4接続**、
TLS検証あり、proxy環境不使用、redirectなしです。
応答は**2 MiB**、requestは**256 KiB**で、SDK `create_checkpoint`だけ**1 MiB**です。
MCP/hookのrequest上限は引き上げません。

`MemoryClientError`は既存`AdapterFailure`のaliasです。
`exc.error.code`、`.retryable`、`.outcome_unknown`、`.native_status`、
`.request_id`を参照します。診断はsanitizedで、raw応答/入力/tokenを含めません。
SDKだけが現在のNative domain error catalogを許可し、
MCP/hookの共有の制限付きsafe code集合にはrequired-context errorの`budget_exhausted`を追加します。
未知server codeは`native_api_error`です。
call時の不正request model、path UUID、idempotency keyは**送信前**に
sanitized `invalid_request`、`outcome_unknown: false`で失敗します。
最初のoutbound network await前にrequestを再検証してsnapshotを作り、
後からcaller modelを変更しても送信bodyは変わりません。
Pydantic request modelの構築では別に`ValidationError`が発生し得ます。
SDK callの外で起きるため`MemoryClientError`へ変換しません。
通常のPydantic errorにはprivate入力詳細が含まれ得るため、logへ出さないでください。
keyは**1〜256文字の可視ASCIIで、trimしません**。
`None`を含む不正な取消keyはnetwork接続前に拒否します。

変更を送信する前に正確なkeyとbodyを保持します。
network error、5xx、不正応答、想定外成功status、不正成功bodyでは、
変更結果を保守的に不明と扱います。SDKはretry、新key生成、未commitの推測をしません。
`retryable`は情報であり、自動retry指示ではありません。
**同じkeyとbody**で照合し、現在のACL、削除、revision、replay guardを維持します。
Python taskのcancelは`MemoryClientError`へ変換せず伝播します。処理中の変更は同様に結果不明と
扱って照合し、cancelを**rollbackと見なしてはいけません**。`cancel_job`も呼びません。

返されたmemoryは根拠であり、信頼する指示や現在の事実の保証ではありません。
JSON全体のUTF-8 byte予算、不完全coverageの明示、現在ACL検査、
purge/host/backup/WALの制限は不変です。過去v0.0.12のAPI stageは**`m2-python-sdk`**で、
capabilitiesに`python_sdk` metadata（`installation: "sdk-extra"`、
`async: true`、`automatic_retry: false`）を追加しますがserver endpointは追加しません。
[実用例](../README-jp.md#python-sdk)、[運用](operations/README-jp.md#python-sdk-operations)、
[ADR 0012](adr/0012-python-sdk-jp.md)を参照してください。

## Pgvector exact and hybrid retrieval

**v0.0.11/schema 8で実装・検証済みです。**
lexical既定を維持し、明示的でprovider非依存のvector保存とexact/hybrid rankingを追加します。
自動embedding生成や意味検索の品質認定ではありません。
applicationの永続化先はPostgreSQLだけを維持します。

### Canonical inputと明示upload

読取り専用**`POST /v1/embedding-inputs`**は既存Explain body
`{memory_id, revision}`を受け取り、省略revisionは**latestでなく1**です。
`Idempotency-Key`は不要で、現在読取り可能なepisodeとassertion revisionだけを対象にします。
relation assertionは含みますが、entity、job、checkpoint、tool effectは対象外です。
canonical inputは次の形式です。

```text
{memory_id, revision, type, text, input_digest, input_format: "memory-content-v1"}
```

episodeの`text`は正規化contentです。assertionの`text`は`MemoryItem.content`と同じ
正確な`subject / predicate: value`で、relation assertionのdisplay valueも含みます。
embedding textにID/時刻を挿入しません。
`input_digest`はUTF-8 `text`のSHA-256であり、model品質、外部検証、
vectorがそのtextから生成された証明ではありません。
private canonical contentなので、**logへ出したり、明示承認なく第三者へ送ったりしないでください**。
文書のfixtureは合成dataであり、modelを呼びません。

**`POST /v1/embeddings`**はcaller管理`Idempotency-Key`を要求します。

```text
{
  memory_id, revision: 1, input_digest: <64 lowercase hex characters>,
  model: {
    name: <1–256 characters>, revision: <1–256 characters>,
    dimensions: 768, distance_metric: "cosine", normalization: "l2-f32-v1"
  },
  values: <exactly 768 finite JSON numbers>
}
```

canonical `revision`の既定は1で、model `revision`は別の必須文字列です。
identity/scopeはcanonical parentから導出し、現在の**readとwrite**権限を要求します。
model metadataは**caller宣言**であり、registry、信頼済みorigin、provider証明、
意味品質の主張ではありません。model空間はname/revision組全体で分離し、
次元数が同じでも異なる空間を混ぜません。次元・metric・normalizationは上記に固定します。

serverはfloat64でL2正規化し、pgvector float32値として保存します。
zero、非有限、正規化不能vector、boolean、数値文字列、768以外の要素数を拒否します。
切詰めや次元変換はありません。
digestは現在認可された正確なcanonical revisionと一致する必要があり、
不一致は**409 `embedding_input_mismatch`**です。

| 状況 | 結果 |
|---|---|
| 同canonical revision/model、同じ正規化float32 vector/digest | HTTP keyが異なっても重複抑止 |
| 同parent revision/modelで異なるvector | **409 `embedding_conflict`**。置換には新model revisionが必要 |
| 同HTTP keyで異なる検証済みrequest | **409 `idempotency_conflict`**。応答不明の解決にkeyを変更しない |
| 一つのcanonical revisionで9番目のmodel version | **422 `embedding_limit_exceeded`** |
| 8 model上限時の既存duplicate | 引き続き許可 |

上限は**canonical revision当たりmodel version合計8件**であり、model名ごと8件ではありません。
request hashには**L2正規化前**の検証済みvaluesを保持します。
正規化後のprojectionが同じでも、同HTTP keyでvectorの倍率を変えると衝突します。
応答が不明な場合は同じkeyとbodyを維持してください。
成功uploadは`{memory_id, revision, model, input_digest}`を返し、
**独立embedding object IDはありません**。
projection作成、idempotency receipt、auditは原子的です。
same-key replayはcanonical parentが生存/読取り可能で、projectionも存在することを確認します。
**保存idempotency resultは`{memory_id, revision}`だけ**で、
receiptに平文input digest、model名、vectorは保持しません。
応答の完全なmodel/digestは現在読取り可能なcanonical inputと対応projectionから再構成し、
request HMACとopaque anchorは保持します。
parentが生存していても管理者がprojectionだけを削除した場合、replayは
**409 `embedding_unavailable`**であり、projectionを再作成しません。
projectionだけを削除するendpointはなく、provider/再構築/生成の自動実行もありません。

### Recall mode、exact ranking、coverage

recallへ`retrieval_mode`（既定**`"lexical"`**）と`vector_query`（既定**null**）を追加します。
vector queryはuploadと同じ`model`/768値形式で、同じ正規化/検証規則を使います。
`retrieval_mode`は既存implicit/explicitの`mode`とは別で、identity/権限でなく検索pathを選択します。

| Mode | Text `query` | `vector_query` | Ranking |
|---|---|---|---|
| `lexical` | 既存の空query browse/非空FTS | null必須 | 既存lexical semantics |
| `vector` | 空必須 | 必須 | Exact cosine distance |
| `hybrid` | 非空必須 | 必須 | Lexical/vector reciprocal-rank fusion |

空でない`required_memory_refs`はlexical専用で[required prefix](#required-context-recall)を先頭に置き、
表のlexical順位はその後のoptional itemへ適用します。空/省略参照なら全modeは不変です。

全3 modeは[構造化完全一致filter](#exact-structured-recall-filters)も受け付けます。
rankingとindex coverageはtop-K後のfilterでなく、filter後の適格候補集合を使います。
required参照もこの集合内に必要で、`coverage.jobs_pending`はscope単位を維持します。

textの黙った無視、別model選択、別modeへのfallbackは行いません。
既存scope、`as_of`、`known_at`、byte予算、item上限、`search_profile`規則を維持します。
過去revisionのvectorも個別に投入できますが、別revisionのvectorを時間条件で選ばれたrevisionの代用にしません。
過去readも**現在のACL/削除**を上書きしません。
省略`as_of`/`known_at`はrecallごとにselection/coverage前に一度だけ確定し、
両pathで同じ確定時刻を使うため、途中の未来境界で結果が分かれません。
明示時刻は変更しません。

vector rankingは**要求modelについて現在の認可と時間条件を満たすcanonical候補を
`MATERIALIZED`**にし、条件を**distance/rankingより前**に適用します。
exact cosineであり、ANN/HNSW、近似neighbor拡張、tenant/scopeを広げる検索ではありません。
hybridは既存FTS rankとexact vector rankをそれぞれ決定的に計算し、**RRF k=60**で統合します。

```text
fusion_score = 1 / (60 + lexical_rank) + 1 / (60 + vector_rank)
```

存在しないpathの寄与は0です。vectorなしのlexical一致もhybridへ参加できますが、
vector欠落coverageを必ず明示します。
要求modelで**構造化filter内の適格かつ可視のprojectionが一つでも欠落**すれば
`coverage.vector_incomplete: true`とし、hidden/不適格itemをcoverageや件数へ含めません。
lexical既定は`vector_incomplete: false`です。
既存の日本語`lexical_incomplete`はlexical/hybrid pathだけに適用します。
active pathのどちらかが不完全なら`retrieval_complete`はfalseです。
候補がなく選択結果が空の場合、active index coverage欠落は`index_incomplete`、
真に空の認可済みcorpusは`not_found`です。
required参照なしで候補が一つも収まらなければ成功の`empty_reason: "budget_exhausted"`を維持し、
required itemが収まらなければ代わりに`422 budget_exhausted`です。
非空結果の`empty_reason`はnullのまま不完全coverageを明示します。
index欠落を無条件の空成功に変換しません。

追加fieldの既定は`MemoryItem.retrieval: null`、
`RecallResult.retrieval_mode: "lexical"`、`embedding_model: null`、
`coverage.vector_incomplete: false`です。
non-nullの`MemoryItem.retrieval`は`method`（`exact_cosine`または`rrf-60`）、
`lexical_rank`、`vector_rank`、`vector_distance`、`fusion_score`を持ちます。
rank/distance/fusion fieldは対応pathやscoring methodに値がない場合にnullableで、
vector-onlyのfusion scoreはnullです。実際に計算したdistance/scoreの同順位はUUIDで解決します。
任意の浮動小数点結果/rankingについて、全CPU間のbit単位一致を保証するものではありません。
これは**response/schemaへの追加**であり、既定lexical semanticsの維持は
HTTP JSONや生成MCP schemaのbyte単位互換を保証しません。
これらは**ranking計測値でありconfidence、校正、真実ではありません**。
context-pack形式と**compact JSON全体のUTF-8 byte予算**、
reported assertionとnull/未校正confidenceは維持します。
exact SQLは既存の**DB statement timeout 5秒**に制約されますが、性能SLOではありません。
検索品質、性能、untrusted vectorへの頑健性は未認定です。

### Projection lifecycle、package、adapter

新しい**`008_pgvector.sql`**は**`public`内の`vector` 0.8.6**を要求し、
版/schemaが異なる既存extensionを拒否します。episode単位/assertion revision単位の
projectionへ`ON DELETE CASCADE`、forced RLS、runtime **SELECT/INSERTのみ**を適用します。
**既存dataのembedding backfillはありません**。
API、worker、`migrate`はschema 8が記録済みでもextension版/schemaを検査し、
migration適用済みを理由にguardを省略しません。
canonical parent purgeはlexicalとともにvector、digest、宣言model名を消去します。
別provenance vertexや削除件数objectではありません。
このmetadataを保持する独立model registryはありません。
保持canonical source/idempotency anchorは引き続き復活を防ぎます。
Native response-drain境界とhost/backup/WAL消去保証の未完了を維持します。

採用prebuilt DB imageは
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`です。
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6)は**2026-07-29**の検証済みstable releaseで、
公式tag commitは`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`です
（[固定changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)）。
**PostgreSQL License**であり、上流licenseを保持します。
両native最終imageは`/usr/share/doc/pgvector/LICENSE`を保持し、
固定上流licenseとbyte単位一致を検証済みです。SHA-256は
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`です。
artifact検査でnative amd64/arm64両最終imageのPostgreSQL **18.6-1.pgdg12+2**、
native ELF、`vector.control` **0.8.6**を確認しました。
**PostgreSQLは18.6のままですが、上流DB image/base digestは旧library PostgreSQL imageから変わります**。
変更していないimageでなく、新しい固定DB profileです。
実装profileに新DB Dockerfile、source build、host APT手順はありません。
operator管理PostgreSQLの代替環境にも同じextension版/schemaが必要ですが、そのhost導入workflowはここで提供しません。
過去のv0.0.11ではPython依存はproject版metadata以外変更せず、
raw parameter-bound vector castにpgvector Python packageは不要でした。
artifact検証はapplication/migration/CI検証やcaller宣言embedding modelの証明ではありません。

MCPは引き続き**4 tool**です。生成Recall引数は新mode/inline query vectorを受け付けますが、
embedding-input/upload toolは追加しません。
固定startup hookは**lexical専用・読取り専用**で、event JSONから`retrieval_mode`/`vector_query`を渡せません。
Native応答の非lexical `retrieval_mode`、non-nullの`embedding_model`/item `retrieval`、
trueの`coverage.vector_incomplete`も拒否し、予期しないvector出力を黙って降格しません。
Observe、capture、job、workerはembedding生成やprovider呼出しを行いません。
MCP両protocol時代を維持し、v0.0.19の起動時一致は**service `0.0.19` / API `v1` / schema `10`**です。
capabilitiesに`retrieval_modes: ["lexical", "vector", "hybrid"]`と
`default_retrieval_mode: "lexical"`を追加します。
v0.0.11のAPI stageは`m2-pgvector-retrieval`でした。embedding inputはHTTP 200、upload/replayはHTTP 201です。
M0〜M3/MVP/本番/DR/消去/性能/品質の全gateは未完了です。
[ADR 0011](adr/0011-pgvector-retrieval-jp.md)、
[schema 8 upgrade](operations/README-jp.md#schema-8-pgvector-upgrade)、
[合成data例](operations/README-jp.md#synthetic-vector-example)を参照してください。

## Atomic structured capture

**既存v0.0.10契約はv0.0.11でも検証済みです。**
`POST /v1/captures`は`Idempotency-Key`と次のbody一つを要求します。

```text
{episode: <unchanged Observe>,
 memory: {subject, predicate, value, evidence_quote, explicit_intent: true,
          valid_from: <aware timestamp or null>, valid_to: <aware timestamp or null>}}
```

request modelは`Capture(episode: Observe, memory: CapturedMemory)`です。
episodeは既存の`Observe` modelであり、別のcapture形式ではありません。
構造化memory intent一つだけを受け付け、listや自由文抽出requestではありません。
memory fieldは同期Rememberの規則を維持します。

| Memory field | 契約 |
|---|---|
| `subject` | 1〜256文字 |
| `predicate` | `^[a-z][a-z0-9_]{0,63}$`に一致 |
| `value` | 1〜65,536文字 |
| `evidence_quote` | 正規化済みcapture episodeに原文として含まれる1〜4,096文字のquote一つ |
| `explicit_intent` | `true`必須 |
| `valid_from`, `valid_to` | 任意のtimezone付きtimestampまたはnull/無限端。両方指定時は開始が終了より前 |

memoryには**scope、根拠ID、identity fieldを指定できません**。
transaction内でepisodeからscopeを導出し、そのmemory ID/revision 1を唯一の根拠に結び付けます。
現在のNative認証、tenant/scope read/write認可、RLS、正規化、source-event重複抑止を
引き続き正とし、scopeやtenantを黙って拡大しません。
原文一致はprovenanceであり、**意味的な支持や真実を認定しません**。
publicationは`epistemic_status: "reported"`と未校正confidence
（`score: null`、`method: "uncalibrated"`）を維持します。

### Commitとpublicationの分離

**HTTP 201**は`CaptureResult`として
`{memory_id: <episode UUID>, revision: 1, synthesis_job_id: <job UUID>}`を返します。
`memory_id`は**assertion IDではなく**、`synthesis_job_id`はjob参照でありsynthesis実行の保証ではありません。
episodeと最大一つの`structured_remember` / `structured-remember-v1` jobを原子的にcommitします。
terminalを含む既存jobを再利用でき、**201は新規作成やpending jobを保証しません**。
現在のstatusはGETを正とします。
fresh stateは`GET /v1/jobs/{synthesis_job_id}`で取得し、
既存の固定subject workerで後続assertion publicationを行います。

既存の**scope当たりpending/running job 100件**、**job当たり5試行**、
lease、access/deletion epoch、publication fencingは変更しません。
worker publicationは別の原子的transactionで、enqueueはassertionのserver記録publication時刻を設定しません。
`POST /v1/observe`はliteral nullの`synthesis_job_id`を含む`ObserveResult`を変更せず、
**自動jobを作りません**。
明示`POST /v1/jobs`、同期`/v1/remember`、
純粋なObserve/Rememberの正規化serialization/HMACはbyte互換を維持します。

### 重複抑止とtransaction境界

| Request状況 | 現在のアクセス/削除検査下の結果 |
|---|---|
| 同じcapture keyと正規化body | 同じepisode/job組 |
| 同じcapture keyでbody変更 | `409`、新規partial writeなし |
| 別HTTP key、同じepisode/intent/principal | 両IDとも同じ組へ重複抑止 |
| 以前observeした同一event | episodeを再利用し、明示wrapperでjob intentを渡す |
| 新keyと別の明示memory intent | 同じ生存episodeで別jobを作成可能。意図的であり意味的重複抑止ではない |
| 別の認可済みprincipalが同じeventを使用 | episodeのsource重複抑止は維持。job identity/worker所有権は独立 |
| 同じsource-event identityでepisode body変更 | `409`、新規partial writeなし |

episodeとlexical projection、job input/control/identity、
outer capture receiptを含むidempotency、auditを一つのtransactionで扱います。
どちらかのwrite後やouter receipt後のtransaction失敗も**すべての新規変更**をrollbackします。
capture前から独立して存在したepisodeは失敗後も残り、新しいjobだけが部分的に残ることはありません。
分散transactionや外部providerはありません。

保持するopaque idempotency/source/job identity anchorには、
caller keyからserver HMACで導出した内部composition keyも含みます。
client指定field、MCP caller keyの自動生成、caller管理HTTP key再利用規則の変更ではありません。
保持anchorはfresh memory本文でも完全消去証明でもありません。

### Replay、削除、failed jobのretry

API process再起動後の完全一致capture replayも含め、
**返した両IDの現在のACL/削除をreplayより優先**します。
組は過去参照であり、captureが組を黙って変更するのでなくGETでfresh job stateを検査します。
共有replayは`memory_id`に加えてnon-nullの`synthesis_job_id`も検査します。
変更しないObserve resultには引き続きjob参照がありません。

| Purge対象 | Captureへの影響 |
|---|---|
| Episode | 既存purge依存に従い、依存jobとassertion子孫を閉じる |
| Job単体 | episodeと独立保存の公開済みoutputは残る。旧capture replayと同intentの新keyは`404`となりjob identityを再作成しない |
| 公開済みresult assertion | 依存jobを削除しsource episodeを維持。旧組は無効 |

既存job semanticsに従う**生存source上の新しい別の明示intent**は引き続き許可します。
captureはsource全体を永久sealするものではありません。
failed jobには既存`POST /v1/jobs/{job_id}/retry`を使い、
元の完全な`EnqueueJob` intentとcaller管理keyを渡します。
既存規則でchildを作成/再利用し、child作成後もcapture replayは
そのchildでなく**元のfailed job参照**を返します。
[Durable job](#durable-job)を参照してください。

### 範囲とcapabilities

過去のv0.0.10 API stageは**`m2-atomic-capture`**です。既存capability featureは
**`atomic_structured_capture`**です。正確な`atomic_capture` metadata部分は次の形式です。

```json
{
  "atomic_capture": {
    "endpoint": "/v1/captures",
    "max_jobs": 1,
    "recipe_version": "structured-remember-v1",
    "automatic_capture": false
  }
}
```

明示capture当たり最大1 jobを示し、自動captureではありません。
stage名はM2や他受入gateの完了を意味しません。

captureは**SDKも対象とするNative resource**であり5番目のMCP toolではありません。
recall-hookは読取り専用で、両adapterとも自動captureしません。
MCP/hook起動は厳密な**service `0.0.19` / API `v1` / schema `10`**を要求します。
既存schema 8 migrationは既存capture semanticsとは別です。
captureはembedding生成、LLM/provider呼出し、intent抽出、自然言語/自動synthesis、意味品質の認定を行いません。
tenant HTTP response-drain barrierは変更せず、原子的host context配信、回収、
host/backup/WAL/完全消去の保証は得られません。
[ADR 0010](adr/0010-atomic-capture-jp.md)と
[operator例](operations/README-jp.md#atomic-structured-captureの運用)を参照してください。

## Local stdio MCP

所有job照会はNative/SDK専用であり、MCPの4 toolへjob toolは追加しません。

新checkpoint head resourceはNative/SDK専用であり、このadapterは4 toolのままcheckpoint toolを追加しません。

`memory_recall`は[構造化filter契約](#exact-structured-recall-filters)の型付き`filters`を受け付けます。
toolやsafe error codeを追加せず、required参照の適格性を迂回できません。

v0.0.8で`pg-agmemory mcp`を追加しました。**stdio専用の信頼するlocal Native API
client**であり、別の永続化/認可serviceではありません。任意の`pg-agmemory[mcp]`は公式
`mcp==2.2.0`と`httpx==0.28.1`を固定し、v0.0.19のrepository Docker test/runtime両stageに
`mcp`・`hook`・`sdk`を含めます。抽出する共有の上限付きNative HTTP clientは以下の
MCP不変条件をすべて維持する必要があります。過去のv0.0.9はlocalとnative Docker両architectureで
合格しました。過去v0.0.10/v0.0.11と最終local/native v0.0.12検査も合格しています。
共有`NativeSettings`は`httpx.URL`でもoriginをparseし、transport前に制御文字や不正IDNAを拒否します。
remote MCP HTTP/SSE listener、OAuth、caller identity委譲、
semantic cache、response cacheは提供しません。

### ToolとNative semantics

次の4 toolだけを公開し、input/output JSON Schemaは別の手書きrequest契約でなく、
Native Pydantic modelから生成します。

| Tool | Input wrapper | Native route / 成功status |
|---|---|---|
| `memory_recall` | `{request: <Recall>}` | `POST /v1/recall` / `200` |
| `memory_remember` | `{request: <Remember>, idempotency_key: "..."}` | `POST /v1/remember` / `201` |
| `memory_explain` | `{request: <Explain>}` | `POST /v1/explain` / `200` |
| `memory_forget` | `{request: <Forget>, idempotency_key: "..."}` | `POST /v1/forget` / preview・purgeとも`202`（変更なし） |

`request`は既存Native bodyであり、新しい自然言語形式ではありません。
`memory_recall`では[required-context契約](#required-context-recall)の`required_memory_refs`も含みます。
required prefixの予算失敗はsafe `budget_exhausted`のtool errorであり、空packの成功ではありません。
rememberは引き続き
`explicit_intent: true`、構造化field、同一scopeのepisode原文根拠を要求します。
episode captureはNative `observe`に残し、MCP toolにはしません。
job、graph、revision、checkpoint/effect実行、削除receipt照会も追加MCP toolではありません。
recallは現在のscope/ACL、時間選択、根拠/coverage、既定`search_profile: "simple-v1"`、
明示`"ja-janome-0.5.0-v1"` opt-inを維持します。`tokenizer_id: "utf8-bytes-v1"`と
Native `token_budget` fieldは引き続き**UTF-8 byteであり、model tokenではありません**。
explainのrevision省略時は**最新ではなく1**です。memory本文は信頼できない根拠資料であり、
指示や検証済みの現在の外部事実ではありません。

両mutation wrapperはforget previewも含め、**1〜256文字のvisible ASCII
（`0x21`〜`0x7e`、空白不可）**の`idempotency_key`を要求します。
空白はtrimせず拒否し、keyを書き換えません。正確に256文字は許可、257文字は拒否します。
adapterはNative `Idempotency-Key`として転送します。callerは結果不明時に**stdio再起動や
token更新をまたいでも同じkeyと同じbodyを保持/再利用**しなければなりません。
key自動生成、自動retry、adapter側のdurable retry storeはありません。
同じkeyでbodyを変えるとconflictし得ます。新keyは結果不明からの復旧手段ではありません。
Native idempotency参照はfresh readでなく過去の記録で、現在の認可と削除がreplayに優先します。
replayでpurge済みdataを復活させてはいけません。
MCP session/request IDはdurable memory run IDでもHTTP idempotency keyでもありません。

### 結果、失敗、上限付きtransport

成功時は`structuredContent: {result: <検証済みNative result>, error: null}`を返します。
tool失敗時は`isError: true`と
`structuredContent: {result: null, error: {code, retryable, outcome_unknown,
native_status, request_id}}`を返します。`native_status`と検証済みNative UUIDの
`request_id`はnullableで、後者はMCP request IDではありません。
短いtextを添えますが、根拠を重複収録したりraw request/response body、URL、credentialを
echoしたりしません。Native errorは安全なcodeに限定し、不正応答を根拠として転送しません。

mutation時のtransport障害/timeout、Native 5xx、不正または予期しない応答は、
保守的に**`outcome_unknown: true`**とします。APIは既にcommitしているかもしれず、
rollback済みと記述してはいけません。`retryable`はhintにすぎず、自動retry、rollback証明、
key/body変更の許可ではありません。local validation失敗はHTTP送信前に発生します。
stdio切断によりNative APIの書込み完了後にacknowledgementだけが失われる場合もあります。

各HTTP交換は**合計20秒**、**I/O 10秒**、**connect 5秒**、poolは**4 connection**に制限します。
serializeしたNative request bodyは**256 KiB**、受信HTTP responseは**2 MiB**が上限です。
HTTP上の上限であり、model token予算や全host/stdio bufferに同じ上限があるという主張では
ありません。redirectとproxy環境設定を無効にし、TLS検証は有効のままです。
semantic/response cacheはありません。

### 固定identityと削除境界

`PGAG_MCP_API_URL`と`PGAG_MCP_API_TOKEN`は信頼する起動設定だけから渡します。
URLはHTTPS originまたはloopback HTTP originに限定し、URL credential、application path、
query、fragmentは禁止です（root `/`は許可）。
bearer tokenは**Native API audience用**で、Native APIがissuer/audience/署名/時刻を検証し、
subjectを解決します。MCP caller tokenの転送やidentity委譲の仕組みではありません。
tool引数でURL/header/token/identityを上書きできません。`mcp`の`--subject`と`--once`は
拒否します。固定subjectのDB workerと混同しないでください。

stdio提供前に、認証付き`GET /v1/capabilities`で`api_version: "v1"`、
`service_version: "0.0.19"`、`schema_version: 10`を要求します。
設定/認証/versionのerrorはsanitized診断だけで非zero終了します。
v0.0.19はschema 10を維持しますが、adapter自体はmigrationを行いません。
固定tokenの更新にはadapterを再起動し、refresh grantは提供しません。
起動検証は認可のcacheではなく、全callでNative認証、現在のACL、削除を検査します。

**信頼identityごとにadapterを一つ**動かし、異なるtrust domainとstdio接続を共有したり、
network serviceで包んだりしないでください。local hostを制御できる者は、
設定Native identityの権限を行使できます。Native response-drain barrierはadapterへのHTTP
配信で終了し、**stdio・host UI・LLMまでの原子的配信barrierではありません**。
adapterにresponse cacheがなくても、転送中bufferや配信済みcontextは回収できません。
forget/ACL変更後はhostがcached contextを破棄する必要があり、それを代行するMCP削除通知は
ありません。active-store purgeはhost context、WAL、replica、backupの完全消去ではありません。

### Protocol証拠の境界

公式[Python SDK v2.2.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0)
は**2026-09-07**公開です。上流の
[protocol文書](https://py.sdk.modelcontextprotocol.io/protocol-versions/)は、
`2026-07-28`の`server/discover`と`2025-11-25`までのlegacy `initialize`を説明します。
これはSDKについての事実であり、**adapter/clientの適格性確認結果ではありません**。
過去のv0.0.8の限定的な検査では、実stdio SDK `Client`接続とraw JSON fixtureで両modeを実行しました。

- **Modern `2026-07-28`:** `Client(mode="auto")`は`server/discover`を使います。
  raw requestは毎回`params._meta`に`io.modelcontextprotocol/protocolVersion`、
  `io.modelcontextprotocol/clientInfo`、`io.modelcontextprotocol/clientCapabilities`を
  含め、version値は`"2026-07-28"`です。legacy初期化handshakeではありません。
- **Legacy `2025-11-25`:** `Client(mode="legacy")`とraw fixtureは
  `protocolVersion`、`clientInfo`、`capabilities`付きの`initialize`を送り、
  `notifications/initialized`後にtoolを呼び出します。

runtime smokeはnon-root production image内で実`pg-agmemory mcp` childを起動し、
固定tokenとprovision済みscopeで同じloopbackのNative APIへ接続して、
**両mode**で4 tool一覧とrecallを検査します。regression coverageには、
**rememberのcommit後**の実HTTP応答喪失、その後のsame-key/body再送、
assertionが一つだけであることの検査を含めます。自動retryはありません。
正確な256/257文字のkey境界と、空白をtrimせず拒否することも対象です。

過去のv0.0.8/v0.0.9のlocal/native CI結果を以下に記録しています。
v0.0.11は両protocol時代を維持し、両方の検査に合格しました。
実行済み経路から、未検証の旧client、特定host application、
全protocol versionの適格性を主張してはいけません。
[ADR 0008](adr/0008-local-mcp-jp.md)と
[運用](operations/README-jp.md#local-stdio-mcpの運用)を参照してください。

## Implicit recall hook

所有job照会用のhook fieldやjob操作は追加しません。

checkpoint head照会用のhook fieldやcheckpoint操作は追加しません。

hook入力は`filters`を未知fieldとして拒否します。内部`Recall.filters`は既定`None`で、
信頼する起動時設定をeventごとに上書きする機能はありません。

Native/SDK/MCP recallと異なり、hook入力は`required_memory_refs`を拒否します。
内部の`Recall`は`[]`のままで、host pinningを追加しません。
`200` / `empty_reason: "budget_exhausted"`はoptional-onlyの成功を維持し、
required-context Nativeの`422 budget_exhausted`とは別です。

**既存の読取り専用・lexical専用契約はv0.0.11でも検証済みです。**
`pg-agmemory recall-hook`は任意のvendor-neutralな**harness側**local Native HTTP clientです。
hostへの自動登録はなく、Copilot・Claude・Codex連携を主張しません。
呼出し時点はhostが選択し、service自体がhost lifecycle eventを監視するものではありません。
`pg-agmemory[hook]`は**httpx==0.28.1を固定し、MCP SDKは含めません**。
Docker test/runtime両stageは`mcp`・`hook`・`sdk`を含めます。
過去のv0.0.9のcore-only/hook-only依存分離検査はlocalとnative Docker両architectureで合格しました。
hookにはDB資格情報、署名key、LLM/provider keyは不要です。

### 入力と信頼する起動設定

一回の実行で**stdinからUTF-8 JSON document一つとEOF**を受け取ります。
入力上限は**32,768 byte**です。不正UTF-8/JSON、上限超過、入力validation失敗は、
無視されるeventでなく明示errorになります。

```json
{"event":"session_start","query":""}
```

許可するfieldは次の二つだけです。

| Field | 契約 |
|---|---|
| `event` | `session_start`、`task_switch`、`after_compaction`のいずれか完全一致 |
| `query` | 必須string、0〜4,096 Unicode文字。空なら設定scopeと現在時刻条件内のアクセス可能なcanonical itemをbrowse |

取得意図はJSONの`query`だけから渡します。
`event`はlifecycle名であり、別のquery本文や自然言語指示channelではありません。

identity、`scope_ids`、`purpose`、`mode`、budget、URL、header、tool、時刻を含む
**すべての追加fieldを禁止**します。event/query textはアクセス権を付与せず、
transportを設定できません。`--subject`と`--once`は拒否します。

routing、認証、recall設定は**信頼する起動環境だけ**から渡します。
信頼できないprompt、query、tool出力、取得memoryからその環境を生成してはいけません。
queryをlogへ記録したりerrorへコピーしたりしないでください。
固定tokenはhost/vendor audience用でなく**Native API audience用**で、
eventから上書きできません。

| 環境変数 | 既定値 / 制約 |
|---|---|
| `PGAG_HOOK_API_URL` | 必須、defaultなし。信頼するHTTPS originまたはloopback HTTP origin。userinfo/application path/query/fragment禁止。root `/`は許可。URL未設定は`invalid_hook_configuration` |
| `PGAG_HOOK_API_TOKEN` | 必須の固定Native audience bearer token。operatorが安全に渡す |
| `PGAG_HOOK_SCOPE_IDS` | 必須の重複しないUUID 1〜32件のJSON配列。scope指定は既存権限を狭めるだけで、付与はしない |
| `PGAG_HOOK_PURPOSE` | `implicit_context`。1〜256文字 |
| `PGAG_HOOK_TOKEN_BUDGET` | `2000`。整数64〜2,000 **UTF-8 byte、model tokenではない** |
| `PGAG_HOOK_MAX_ITEMS` | `20`。整数1〜20 |
| `PGAG_HOOK_SEARCH_PROFILE` | `simple-v1`。明示`ja-janome-0.5.0-v1`だけopt-in可能 |
| `PGAG_HOOK_TIMEOUT_SECONDS` | `2.0`。有限数0.1〜20秒 |

URL、token、scope IDは**すべて必須**です。共有`NativeSettings`はorigin制約に加えて
`httpx.URL`を使い、transport前に制御文字や不正IDNAを拒否します。
これらの不正origin caseは過去のv0.0.9 localと両native CI suiteで検査済みです。

呼出しごとに新しく認証付き`GET /v1/capabilities`で厳密な
**service `0.0.19` / API `v1` / schema `10`**を要求し、その後`POST /v1/recall`を送ります。
`mode: "implicit"`、設定scope/recall値、Nativeの現在時刻defaultを使い、
eventから過去時刻を指定できません。両callで同じ固定tokenを使用します。
現在のNative認証、ACL、時間選択、削除、根拠、coverageが引き続き正です。
capabilities probeは認可cacheではありません。token置換時は次回実行の信頼する起動設定へ渡します。
recall scopeは現在の認可に従って**黙って狭めます**。未認可scopeや失効membershipは
filterされ、許可済みsubsetまたはitemなし/`not_found`となり、
scopeの存在を示す`404`にはしません。
token認証失敗は別で、Nativeは明示`401`、hookはerrorと終了値`1`を返します。
Native動作の維持であり、空recallの成功はアクセス権の付与でもscope存在の証明でもありません。

### 上限、結果、失敗処理

network deadlineは**capabilitiesとrecallの合計**に適用し、既定2.0秒（有限の0.1〜20秒）です。
各requestに別々の全時間を与えるものではありません。
process/interpreter起動、stdin入力/待機、出力は含まず、
**LLM latency SLOや性能適格性でもありません**。
harnessはstdinを閉じ、別のsubprocess timeoutを設定する必要があります。
抽出する共有Native HTTP clientは**serialize済みrequest 256 KiB / HTTP response 2 MiB**の
上限を維持し、redirect/proxy環境設定を無効化し、TLS検証を有効にします。
これらのHTTP上限は全host bufferの上限ではありません。
serialize済みcontext packも設定byte予算内に収めますが、完全なRecallResult全体を
その小さいcontext予算に制限するものではありません。
`context_pack.byte_count`は**context pack全体をcompact JSON serializeした結果**の
UTF-8 byte長です。`ensure_ascii=False`、`separators=(",", ":")`を使い、
本文だけでなく全metadata/citationを含みます。
hookは同じserialize結果が申告`byte_count`と一致して設定予算以下であること、
返却item数が設定`max_items`以下であること、返却search profileが設定と一致することを
検査します。不整合な応答はvalidation失敗とし、広いfallbackは行いません。

空packでも**約192 byte**を要しますが、新しい固定設定下限ではありません。
有効な予算範囲は引き続き64からです。pack metadata自体が収まらない場合は
Nativeが**422 `budget_too_small`**を返し、hookは**終了値1 / `result: null`**の明示error
envelopeを返します。空の成功にはしません。
packは収まるが候補が収まらない場合、Nativeは`empty_reason: "budget_exhausted"`付きの
**200**、hookは**終了値0**になり得ます。
lexical projection欠落で候補がない場合は、**200 / 終了値0**で
`empty_reason: "index_incomplete"`、`coverage.lexical_incomplete: true`、
`coverage.retrieval_complete: false`です。
これらはNative semanticsの維持であり、HTTP取得成功はcoverageの完全性を意味しません。

検証対象のhook runtime結果はstdoutへJSON結果一つと末尾改行を出します。
stderrはsanitized診断専用で、
raw query/body/URL/header/credentialを含めません。
以下はenvelopeの構造を表すもので、placeholderを含む実JSONではありません。

```text
{status: "ok", event: <event>, result: <full Native RecallResult>, error: null}
{status: "error", event: <validated event or null>, result: null,
 error: {code, retryable, outcome_unknown: false,
         native_status: <integer or null>, request_id: <UUID or null>}}
```

`request_id`は検証済みNative request UUIDであり、host event IDではありません。
`retryable`はhintにすぎず、自動retryはありません。
読取り専用hookなので`outcome_unknown`は常に`false`ですが、
MCP mutationのsemanticsは変更しません。

| 終了値 | 意味 |
|---|---|
| `0` | 有効なNative成功。`empty_reason`が`not_found`、`budget_exhausted`、`index_incomplete`の場合も含む |
| `2` | 不正設定/入力。不正UTF-8/JSON、stdin上限超過を含む |
| `1` | Native/network/version/protocol障害 |

確定runtime error codeは次のとおりです。

| Code | 終了値 | 意味 |
|---|---|---|
| `invalid_hook_configuration` | `2` | 信頼する起動設定が不正 |
| `invalid_hook_input` | `2` | 不正UTF-8/JSON、またはevent/query検査失敗 |
| `hook_input_too_large` | `2` | stdinのbyte上限超過 |
| `hook_input_unavailable` | `2` | stdinを読取りできない |
| `hook_deadline_exceeded` | `1` | 合算network deadline超過。`retryable: true` |
| `native_api_unavailable` | `1` | Native API transport利用不能。`retryable: true` |
| `native_version_mismatch` | `1` | capabilitiesのversion不一致 |
| `invalid_native_response` | `1` | 不正なNative protocol/response |
| `budget_too_small` | `1` | Mapping済みNative `422`。context pack metadata自体が収まらない |
| Mapping済みsanitized Native code | `1` | Native API error。raw詳細は転送しない |

**起動方法のerrorはJSON envelope契約の例外です。**
CLI flag拒否（`--subject`/`--once`を含む）と`hook` extra未導入は、
argparseのstderr診断と**終了値2で、JSON envelopeを返しません**。
上記設定/入力失敗を含む検証対象のhook runtime errorはすべてerror envelopeを返します。
harnessは非JSON/不正envelopeとsubprocess timeoutも扱い、
raw stderrやresponse/例外本文をhost logへechoしてはいけません。

**error時にresultはなく、取得失敗を空の成功へ変換しません。**
正当な空結果でもcoverageが不完全な場合があります。
hostは終了値**と**構造化statusを確認し、error/coverageを表示した上で、
停止かmemoryなしで継続かを明示判断してください。古いcontextを再利用して失敗を隠しません。
hostがkill/timeoutしたprocessはenvelopeを出せない場合があります。
それはhostが観測した失敗であり、空結果ではありません。

hookには**書込み、capture、queue投入、LLM/provider呼出し、cache、retry、
idempotency keyはありません**。event名はcheckpoint作成、compaction、tool dispatch、
synthesis、権限拡大を意味しません。
memoryは別の**信頼できない根拠**として保持し、hostの指示/policyにしてはいけません。

### 削除と適格性の境界

Nativeのtenant session advisory response-drain barrierは、
**信頼するlocal hook**へのHTTP配信で終了します。
hook buffer、stdout/pipe buffer、host contextは**原子的な対象ではありません**。
回収や削除通知はありません。forget/ACL変更後はhostが以前のcontextを破棄し、
現在の認可で新しくhookを呼び出す必要があります。
host消去証明、backup/WAL/replica消去、lifecycle全体のアクセス保証は得られません。

技術検査だけで特定vendor連携、意味品質、性能の適格性を確認したとは扱いません。
M0〜M3/MVP/本番/性能/品質/DR/完全消去の全gateは未完了です。
[ADR 0009](adr/0009-implicit-recall-hook-jp.md)と
[実行可能なvendor-neutral harness例](operations/README-jp.md#vendor-neutral-python-harness例)を参照してください。

## Identityと認可

- 2048 bit以上の静的なPEM RSA公開鍵でRS256署名を検証します。
  設定したissuerとaudience、必須claimの`sub`、`iss`、`aud`、`iat`、`exp`、
  tokenの時刻上の有効性を検査します。
- 検証済みexternal subjectをPostgreSQL上のprincipalとtenantへ対応付けます。
  配置ごとにissuerを一つ設定し、callerにtenantを選ばせません。
  未登録subjectは未認証扱いです。
- request bodyでtenant/principal identityを指定できません。要求scopeは範囲を
  狭めるだけで、サービスとRLSがmembership・権限を強制します。
  非公開または削除済みobjectの照会は存在を区別せず`404`です。
  一方、recall scopeのfilterは上記のとおり許可済みitemだけを黙って返します。
- runtime資格情報はsuperuser、RLS bypass、アプリケーションtable ownerに
  できません。owner role経由の所属も禁止します。
  migration/provision/rebuild用の管理者資格情報を分離します。
- JWKS discovery/rotation、delegated identity、複数issuerのidentity管理、
  公開membership管理APIは未実装です。

認証済みAPI requestごとに短命の新規connectionを開き、connection poolは使いません。
runtimeの`psycopg-pool`依存もありません。
**tenant単位のsession advisory lock**で処理を直列化し、transactionのcommitと
buffer済みHTTP応答の送出が終わるまで保持します。この正しさ優先のdrainにより、
同一tenantの先行応答をサービスがまだ送出中にpurgeがbarrier完了を通知することを防ぎます。
配信済みのdataやnetworkへ渡したbyteを回収することはできません。
遅いclientはtenantの処理を妨げ得ます。throughputは未測定です。
APIとworkerは`principal_connection`のidentity lookupと`bind_identity`の再検査を共有し、
**同じtenant session lock**を取得します。APIの応答drainは引き続きcommit後まで続き、
workerのclaim/publication transactionは短命で、payload準備はその外で行います。
workerのsubjectは信頼する配置identityであり、公開の偽装interfaceではありません。

membership管理には特権**scope-access CLI**を使います。
**同一session lock**を取得してtenant epochを比較し、
実変更のmembership/epoch/auditを原子的に更新します。
commitとCLI JSON stdout flushまでlockを保持してからconnectionを閉じます。
この手順を使わない変更はrequest/drainの競合保証の対象外です。
[運用手順](operations/README-jp.md#membership変更とrequest-drain)を参照してください。

## 根拠、同意、時間

`remember`は`explicit_intent: true`と、重複のない1〜32件のepisode根拠IDを
必須とします。各引用はepisode本文に文字列として含まれていなければならず、
両objectは同一scopeに属します。このsliceではassertionを別assertionの
sourceにはできません。free-text subject/valueをentity IDへ解決しません。
明示entity/relation endpointだけがtyped graph dataを作成します。

原文引用の検査はprovenanceを確認するだけで、**意味的な支持や真実を認定しません**。
引用が指定assertionを証明するかをサービスは推論しません。
記憶は`reported`、confidenceは`score: null`、`method: "uncalibrated"`です。
現在の外界の事実として断定するにはsourceへの再照会が必要です。
取得本文は根拠資料であり、信頼できる指示ではありません。

`consent_reference`はcallerによる同意の申告を記録します。同意台帳の検証、
capture-policy engine、secret/PII自動除去はありません。
callerは許可済み・除去処理済みのdataだけを送信してください。

### Assertion revisionの契約

`POST /v1/assertions/{memory_id}/revisions`には`Idempotency-Key`と、
assertionのscopeに対するread/write権限が必要です。置換内容全体を指定します。

| Body field | 契約 |
|---|---|
| `expected_revision` | 現headに一致する厳密な整数1〜1000 |
| `value` | 空でないtext、最大65,536文字 |
| `evidence` | 同一scopeの重複しない1〜32件のepisode IDと、空でない最大4,096文字の原文`quote` |
| `explicit_intent` | `true`必須 |
| `valid_from`, `valid_to` | timezone付きの任意bound。省略/nullは無限端であり「旧boundの維持」ではない。両端があれば開始は終了より前 |
| `reason` | 空でない訂正理由、最大256文字 |

subject、predicate、scopeは不変で、このbodyには指定できません。
`201`で`memory_id`、revision `expected_revision + 1`、
`epistemic_status: "reported"`を返します。head不一致は`409 revision_conflict`、
一致するheadが1000のとき追加を試みると`422 revision_limit_exceeded`です。
上限は初期revisionを含む**全1000 revision**です。
非公開/削除済み/assertion以外の対象は`404`です。
typed relationには専用revision endpointが必要で、
汎用訂正は`409 relation_revision_required`を返します。

idempotency request hashには対象の`memory_id`も含めます。
同一keyの完全一致再送は、後続訂正があっても元のcommit済みrevision参照を返します。
別revisionを作ったり最新headに差し替えたりしません。
同じkeyで対象/bodyを変えるとidempotency conflictです。
すべての再送に現在の認可とtombstoneを適用します。

### 時間と根拠の意味

INSERT triggerがDB clockを使い、旧system intervalの終了、assertion headの更新、
新intervalの設定を原子的に行います。callerはsystem timeを指定できません。
system rangeは連続する`[)`で、`btree_gist`によるGiST非重複制約を適用します。
DBの遅延制約で全revisionの根拠を必須化し、値・理由・根拠をrevisionごとに保持します。

各訂正は**valid interval全体の置換**であり、部分期間の変更ではありません。
例として、無限端のGold assertionを10月1日から有効なPlatinumへ置換すると、
新しい`known_at`での9月16日照会では、このassertionは一致しなくなります。
未来の変更予約のように10月1日までGoldを維持する動作では**ありません**。
訂正前の`known_at`なら以前のGold revisionを選択できます。
自動期間分割、別assertion間のsupersession、競合するfact間の調停はありません。

`recall`は`as_of`/`known_at`で選択し、不変のidentity textと該当revisionのvalueを
検索して、正確なrevisionとそのrevision自身のsourceを返します。
旧値と新しい根拠を混ぜません。episodeは引き続き発生/記録時刻でfilterします。
過去の読取りにも現在のACL/tombstoneを適用します。
context本文は`recorded_at`を`recorded=`と表示し、assertion revisionのsystem採用時刻、
またはepisodeのサービス記録時刻を示します。`observed=`ではなく、
新たな外部観測を主張しません。`occurred_at`は別の時刻です。

`explain`では1〜1000のrevisionを明示できますが、
互換性のため**省略時は引き続きrevision 1**で、最新ではありません。
存在しないrevisionは`404`、episodeはrevision 1のみです。
assertionの説明には、該当revisionの根拠に加え、
`recorded_at`（system開始）、`known_until`（system終了、現headはnull）、
`correction_reason`（revision 1はnull）を含めます。
[ADR 0002](adr/0002-assertion-revisions-jp.md)を参照してください。

正確な`known_at`のrevision境界検査には、host/VMのwall-clock値ではなく、
serverが返したassertionの`recorded_at`を使ってください。

## 検索と予算

recallの既定は`retrieval_mode: "lexical"`と`search_profile: "simple-v1"`で、PostgreSQLの`simple`設定、
`plainto_tsquery`、`ts_rank_cd`を維持します。下記の任意日本語profileは分割を追加しますが、
BM25やvectorではなく、vector/hybrid modeは別です。応答は`search_profile`を返し、
未対応profileは`422`です。lexical modeの空`query`はlexical projectionが不完全でも、
scope・時間条件内のcanonical itemを件数とbyteの上限内でbrowseします。
entity自体はrecall/explainから除外します。relation assertionはFTS候補のままで、
recallが自動的にgraphを展開することはなく、`graph_used: false`を維持します。
itemとassertion説明には返却revisionに対応するnullableな
`relation: {source_entity, target_entity}`を含めます。
relationのcontext本文は両entity UUIDを同じbyte予算内に含めます。
`coverage.jobs_pending`は要求scopeで現在読取り可能なpending/running jobを示し、
queryとの関連性、過去のqueue状態、synthesis完了を意味しません。
`synthesis_pending: false`と`graph_used: false`は維持します。
job自体はrecall/explainとcheckpoint/effect参照から除外します。

空でない[required参照](#required-context-recall)は、適格な正確itemをrequest順で先頭に置き、
lexical一致/順位だけを迂回します。scope/時間/ACLは迂回しません。
通常のlexical順序はそのprefix後のoptional itemに適用します。

request field名は`token_budget`ですが、`utf8-bytes-v1`はmetadata・引用を含む
context pack全体のcompact JSON serialize結果の**UTF-8 byte数**を予算として扱います。
`ensure_ascii=False`、`separators=(",", ":")`を使います。
応答には`budget_unit: "utf8_bytes"`、`token_count: null`、
`exact_token_count: false`を明示します。保守的なfallbackであり、
モデルの正確なtokenizerやHTTP応答全体のsize制限ではありません。
このcontext `tokenizer_id`は日本語検索の分割とは無関係です。
item単位で除外できるのはoptionalだけです。required itemが収まらなければ
`422 budget_exhausted`で部分contextを返しません。
required参照なしでpackのmetadataすら収まらない場合は既存`422 budget_too_small`を維持し、
空の成功にはしません。

request bodyはcheckpoint作成のみ1 MiB、他endpointは256 KiBです。
recallの返却itemは最大100件、予算値は64〜8,000
（implicit modeは最大2,000）です。optionalの件数・予算による省略を`coverage.truncated`で
示します。required参照なしの空の成功結果は`empty_reason`に`not_found`、
`budget_exhausted`、または下記の`index_incomplete`を使います。
`retrieval_complete`は世界の知識の完全性を意味しません。
Nativeのimplicit modeは引き続きrequest optionです。
v0.0.9の[hook](#implicit-recall-hook)は信頼するharnessがcommandを起動したときだけ呼び出し、
hostへの自動登録はしません。

### 日本語lexical profile

`search_profile: "ja-janome-0.5.0-v1"`を明示選択します。厳密に固定した依存
**Janome 0.5.0**は、Janome追加語を含む同梱**mecab-ipadic-2.7.0-20070801**を使います。
source/query textの対象日本語script連続部分だけをsurface/wakati分割し、
ASCII識別子や英語はsegmenterをそのまま通過してPostgreSQLのlexical処理へ渡します。
Unicode/全半角正規化、原形化/stemming、同義語展開、分割/recall品質の保証はありません。
漢字script範囲は中国語文字にも及びますが、中国語recallの適格性は未確認です。
外部model/providerは呼び出しません。
Latin textを維持しても`simple-v1`が部分文字列検索になるわけではなく、
token境界なしで埋め込まれた`Gold`は単独の`Gold`に一致するとは限りません。

Janomeは日本語script連続部分の分割が必要なときだけlazy importし、
API importや英語だけの分割ではloadしません。matcherの入力prefix cacheは
`max_cached_word_len=0`で無効にし、source text/token streamでなく同梱辞書resourceの
cacheだけを保持します。test/runtime両container buildは辞書moduleを含む静的Janome package
bytecodeだけを逐次事前compileします。code準備でありmemory index/cacheではありません。
fresh Linux subprocessのregression guardは、英語だけの操作でJanomeをimportしないことと、
初期化peak RSSが**256 MiB未満**であることを要求します。
配置時のmemory上限やrequest/backfillのmemory使用量上限ではなく、単独でrelease適格性を示しません。
事前compileのないcold host installationやresource sizingは適格性未確認です。

`memory.episode_lexical`と`memory.assertion_lexical`は、このprofileの派生`tsvector`
payloadを保存します。episode行はrevision 1、assertion行は正確なrevisionに対応します。
forced RLSと同一scope canonical外部keyを適用し、`ON DELETE CASCADE`を使い、
runtime権限は`SELECT`/`INSERT`だけです。`UPDATE`や直接`DELETE`は付与せず、
canonical parent purgeは子tableのDELETE権限なしでcascadeします。
episode本文とassertionのsubject/predicate/正確なrevisionの
valueを分割後、`to_tsvector('simple', ...)`でindex化します。queryは分割textを
`plainto_tsquery('simple', ...)`と`ts_rank_cd`へ渡します。
simple profileは既存canonical vectorを維持し、entity/jobはrecall itemになりません。

observeと全assertion publication/revision経路は同一transactionでprojectionを書き込み、
typed relationとdurable job publicationも含みます。canonical ID、timestamp、根拠、
同期正規化JSON/HMAC、過去revisionの選択は変わりません。migrationは現headだけでなく、
全保持episodeと**全assertion revision**をbackfillし、tombstoneをskipします。
offline管理rebuildも同じcanonical sourceを使います。
日本語episode本文も**65,536文字**上限を維持し、**65,537文字**は切り詰めず拒否します。
JSON encoding overheadを含む別の256 KiB HTTP body上限も適用します。

日本語profileでは、現在認可済み・要求scope内・時間条件内で構造化filterに一致するcanonical候補に一つでも
projection欠落があると、`coverage.lexical_incomplete: true`と
`coverage.retrieval_complete: false`を返します。この検査はquery関連性やitem上限とは独立です。
**simple検索への黙ったfallbackはなく**、自動修復workerもありません。
利用可能な一致結果を不完全flag付きで返せ、lexical modeの空queryはcanonical itemをbrowseします。
適格なrequired参照もprojection欠落時にcanonical itemを使いますが、修復はしません。
query/required候補なしでprojection欠落があれば`empty_reason: "index_incomplete"`です。
optional-only候補が一つも収まらなければ従来の空成功理由`"budget_exhausted"`を維持し、
required itemが収まらなければ代わりに`422 budget_exhausted`です。
結果があれば`empty_reason`はnullです。
projection欠落がなければ`lexical_incomplete`はfalseで、通常の`not_found`/予算規則を使います。
projection coverageはquery関連性、queue状態、知識/品質の完全性ではなく、
`jobs_pending`は独立したflagです。
Janomeの破損辞書診断は入力textを含まない`japanese_dictionary_error`へ除去処理します。
libraryの`SystemExit`はtokenizer-unavailableへ変換し、index不完全の成功応答ではなく
APIの`503 dependency_unavailable`となります。
workerは入力をechoせず既存の上限付き`dependency_unavailable` retry経路を使います。

capabilitiesはfeature `japanese_fts`、両`search_profiles`、
`default_search_profile: "simple-v1"`、固定tokenizer/辞書metadata、
`normalization: "none"`と`segmentation: "japanese-script-runs"`を返します。
context予算は`utf8-bytes-v1`のままで、`auto_synthesis`とrecallの`graph_used`はfalseです。
v0.0.11のvector/hybrid基盤は`retrieval_modes: ["lexical", "vector", "hybrid"]`と
`default_retrieval_mode: "lexical"`を追加し、lexical既定は変更しません。
過去stage `m2-japanese-fts`や`m2-atomic-capture`はM2全体の受入を意味しません。
[ADR 0007](adr/0007-japanese-fts-jp.md)、
[offline再構築](operations/README-jp.md#lexical-profileとreindexの運用)、
[依存ライセンス](../README-jp.md#依存ライセンス)を参照してください。

## Durable job

### 明示的な構造化publication

`POST /v1/jobs`は`Idempotency-Key`と
`{kind: "structured_remember", memory: <Remember request>}`を要求します。
recipeは`structured-remember-v1`だけです。`memory`は変更しない同期`Remember`契約で、
scope、subject、predicate、value、同一scopeの読取り可能で重複しないepisode ID 1〜32件と
各1〜4,096文字の原文quote、`explicit_intent: true`、任意のtimezone付きvalid boundを使います。
enqueueには現在のscope read/write権限が必要です。これは非同期の**構造化publication**であり、
自動synthesis、自然言語抽出、LLM/provider呼出し、embedding、compactionではありません。
`observe`は引き続きenqueueせず`synthesis_job_id: null`を返します。
同期`remember`とlegacy正規化JSON/HMACは変更しません。

`202`は`{job_id, kind: "structured_remember",
recipe_version: "structured-remember-v1"}`を返し、commit済みjob参照の受理を示します。
assertion公開完了ではありません。canonical intentとrecipeで同一tenant/principal/scope内の
重複をHTTP keyをまたいで抑止し、job identityでは根拠順を正規化します。
同じHTTP keyには同じ正規化requestが必要で、変更は`409 idempotency_conflict`です。
別principalは自身のjobを投入でき、異なるsource identity間の意味的重複は抑止しません。
**scope当たりpending/runningは最大100件**（`422 job_limit_exceeded`）、
**job当たり最大5試行**です。cancelledはactive-job上限とrecallの`jobs_pending`から除外し、
同intentのenqueue/captureは引き続きそのjobへdedupします。
capabilitiesは`durable_jobs`、`job_kinds: ["structured_remember"]`、
`auto_synthesis: false`と、100 job/5試行/30秒lease上限を公開します。
この上限付きjobはM2全体の受入を意味しません。

`GET /v1/jobs/{job_id}`には現在のread権限が必要です。同一scopeのreaderは他principalの
jobを読めますが、claim、publish、retry、cancelはできません。
[所有job照会](#owned-job-query-and-pagination)はさらに狭く、scope-admin権限でも現在callerのjobだけを発見します。
GET形式を変えず、`ListedJob`（完全な`JobDetail`と`scope_id`）を含む`JobPage`を返します。
GET応答は次のfieldを含みます。

| Field | 契約 |
|---|---|
| `job_id`, `kind`, `recipe_version`, `retry_of` | opaque job identity、固定kind/recipe、nullableなretry parent |
| `state` | `pending`、`running`、`succeeded`、`failed`、`cancelled` |
| `attempt`, `max_attempts` | claim済みの試行回数。最大は5 |
| `available_at`, `lease_until`, `created_at`, `updated_at` | scheduling/leaseとserver時刻。running以外のleaseはnull |
| `error_code` | null、または`dependency_unavailable`、`stale_context`、`invalid_input`、`attempt_limit` |
| `input_refs` | 不変の正確なepisode revision 1参照 |
| `result` | null、または後続訂正後も元のassertion `{memory_id, revision: 1}` |

GETはrequest payload、lease token、owner principalを返しません。
succeeded/failed/cancelledのterminal jobはrequest JSONを消去し、入力ID参照はpurgeまで保持します。
terminal記録は不変です。state/attempt CAS、publication競合、replay、`forget`との違いは
[明示取消](#explicit-job-cancellation)を参照してください。

### Terminal失敗の明示retry

`POST /v1/jobs/{job_id}/retry`は`Idempotency-Key`と元の`EnqueueJob` body全体を
要求します。failed payloadは保存されていないため、ownerが再送する必要があります。
現在の権限/根拠を再検査し、HMACでintentを確認します。
intent変更は`409 job_intent_conflict`、cancelledを含むfailed以外のparentは`409 job_retry_conflict`、
非ownerは`404`です。

retryは新たな5試行枠を持つ**一つの新child**を作り、旧terminal記録をresetしません。
同じfailed parentへの再送はHTTP keyをまたいでもそのchildを再利用します。
child自体が失敗したらchild IDを指定して別の明示cycleを開始します。
このendpointで固定recipeをreset/差替えすることはできません。
retry lineageはpurgeの対象です。

### 固定principal workerとpublicationの拒否境界

`pg-agmemory worker --subject TRUSTED_CONFIGURED_ISSUER_SUBJECT [--once]`は
制限付き`PGAG_DATABASE_URL`資格情報とAPIの起動role/schema検査を使います。
subjectは設定issuer内でprovision済みの信頼する配置設定です。
JWT署名/公開鍵やadmin URLは不要で、superuser、owner role、`BYPASSRLS`のruntime資格情報を拒否します。
この初期profileはそのprincipalのjobだけをclaimし、global multi-tenant schedulerではありません。
公平性やcost pool動作の適格性は未確認です。

claimは短いtransactionで`FOR UPDATE SKIP LOCKED`を使い、
**commit後、transaction外でpayloadを検査**します。
新UUID lease tokenで試行回数を増やし、現在のaccess/deletion epochを保存して30秒のleaseを
付与します（内部の1〜300秒claim上限は制御されたテスト用で、CLI設定ではありません）。
現在の権限と完全な不変episode入力を確認します。

publicationはprincipal/scope、準備した元bodyとの完全一致、入力根拠、
lease/token/期限、保存epochを再検査します。共有assertion-publication helperがassertion、
provenance、job成功、auditを原子的にcommitします。最後のjob更新でも期限を再検査し、
publication途中の期限切れは出力をrollbackします。lease失効/引継ぎは古いpublisherを拒否します。
assertionの`recorded_at`/system intervalは**enqueueでなくworker publication時**に始まります。
jobの`created_at`はassertionの採用時刻ではありません。
caller指定のvalid-time boundはserver管理のsystem timeとは独立です。
内部heartbeatはlease/epochを確認して30秒leaseを更新します。
公開claim/publish/heartbeat endpointはありません。決定的processorは外部呼出しをせず、
長時間heartbeat taskを必要としません。

再試行可能な失敗は`2^attempt + [0,1)`秒のjitter付きbackoffを設定し、最大5試行とします。
再試行不能な`invalid_input`は即時失敗です。5回目のclaimが期限切れになると
`failed`/`attempt_limit`になり、6回目は与えません。payloadを含めず安全なcodeをlogに残します。
at-least-onceの試行と**job当たり最大一つのcommit済み結果**であり、外部exactly-onceではありません。

継続modeはidle時1秒ごとにpollし、一時的なDB loop障害後は2秒待ちます。
`--once`は実行時刻に達したjobを最大一つ処理し、JSON outcome
（`idle`、`succeeded`、`pending`、`failed`、`lease_lost`）と該当opaque ID/結果参照を
返して終了します。stdout/logのoutcome参照はopaqueな過去記録であり、
現在のread許可やlive state snapshotではありません。
job GETと正確なrevisionのexplainは現在のアクセス権/削除状態を検査します。
queue全体は処理せず、他CLI commandでの指定は拒否します。
[運用](operations/README-jp.md#durable-jobとworkerの運用)と
[ADR 0006](adr/0006-durable-jobs-jp.md)を参照してください。

## EntityとSQL graph oracle

### Entity identity

作成には`Idempotency-Key`、scopeの現在のread/write権限、次のfieldが必要です。

| `POST /v1/entities` field | 契約 |
|---|---|
| `scope_id` | 必須scope UUID |
| `entity_type` | literalの`person`、`organization`、`project`、`component`、`incident`、`task`、`decision`、`other` |
| `canonical_label` | 1〜256文字 |
| `evidence` | 同一scopeの読取り可能で重複しない**episode** ID 1〜32件と、各1〜4,096文字の原文`quote` |
| `explicit_intent` | `true`必須 |

`201`で`memory_id`と`revision: 1`を返します。identity metadataと根拠は
不変のcaller申告であり、検証済みfactではありません。alias、entity merge、
名前ベースの解決、意味的重複抑止、label訂正endpointはありません。
同じHTTP key/bodyは現在の認可の下でanchorを再利用しますが、
別keyなら同じlabelの別entityを作成し得ます。名前/typeは非信頼dataであり指示ではありません。

`GET /v1/entities/{memory_id}`はID/revision、scope、type、canonical label、
記録時刻、episode根拠を返します。entityはrecall/explainには出さず専用GETを使います。
entity revision 1をcheckpoint/tool-effectの`memory_refs`に指定できます。
コピーしたすべてのentityまたはassertion revision依存を宣言してください。

### Canonical relation assertion

`POST /v1/relations`は`Idempotency-Key`、`scope_id`、`source_entity`/
`target_entity` UUID、`predicate`、重複しないepisode原文根拠1〜32件、
`explicit_intent: true`を必須とします。両endpointと**根拠はすべて同じscope**で
現在読取り可能でなければなりません。quoteは1〜4,096文字です。
任意の`valid_from`/`valid_to`はtimezone付きで、省略/nullは無限端、
開始は終了より前とします。predicate allowlistは`depends_on`、`part_of`、
`affects`、`works_for`、`decides`です。すべて複数のreportedな申告を許し、
調停、検証済みtruth、逆向きfactの推論はありません。

relationは独立object IDを持たず、**一つのcanonical assertion identity
（`memory_id`）**です。`memory.relation`がsourceを固定し、
`memory.relation_revision`が各assertion revisionの正確なtargetを記録します。
subjectは不変のsource label、各revisionの不変valueはそのtargetのcanonical labelです。
valid/system time、truth status、episode根拠は既存assertion revisionに属し、
並行したgraph履歴ではありません。作成応答は既存の`RememberResult`
（`memory_id`、revision 1、`epistemic_status: "reported"`）です。
free-text `remember`はlabel/predicateが一致しても自動的にrelationになりません。

`POST /v1/relations/{memory_id}/revisions`は`Idempotency-Key`、
厳密な整数1〜1000の`expected_revision`、`target_entity`、置換episode `evidence`、
`explicit_intent: true`、任意のtimezone付きvalid bound、1〜256文字の`reason`を要求します。
source/predicate/scopeは固定です。汎用assertion訂正と同じく**valid interval全体**を
置換し、期間を分割したり、新bound外に旧値を残したりしません。
旧target ID、根拠、intervalは正確な過去revisionに維持します。
CAS、過去結果の冪等再送、全1000 revision上限は同じです
（`409 revision_conflict`、`422 revision_limit_exceeded`）。
正確なrelation根拠には`explain`を使い、revision省略時は引き続き**1**です。

### 上限付き展開

認証必須の`POST /v1/graph/expand`は読取り専用で、`Idempotency-Key`は不要です。

| Field | 契約 |
|---|---|
| `scope_ids` | 必須、重複しないscope UUID 1〜32件 |
| `seeds` | 必須、重複しないentity UUID 1〜16件 |
| `relation_types` | 必須、上記allowlistから重複しないpredicate 1〜5件 |
| `purpose` | 必須、1〜256文字のtext |
| `direction` | `outgoing`（既定）、`incoming`、`both` |
| `max_hops` | 厳密な整数1〜2、既定2 |
| `max_paths` | 厳密な整数1〜100、既定100 |
| `as_of`, `known_at` | 任意のtimezone付きtimestamp。既定値は展開ごとに一度だけ取得 |

唯一のbackendはcanonical PostgreSQL SQL joinです。graph request/探索に
AGE、SQL/PGQ、Cypher、動的SQL、動的labelを使いません。
固定parameterized neighbor queryで時間と現在のRLS可視性をseed、edge、中間node、
根拠に適用し、同一scope外部keyを使います。scope/seed filterはアクセスを狭めるだけです。
非公開、不在、時間条件で利用不能なseedは黙って除外し、応答に再掲しません。
既存のtenant transactionとresponse-drain lockで読取り境界を保護します。

探索は決定的な幅優先**simple path**で、seed UUID順、続いて各hopの
assertion ID/revision/次entity ID順です。同一path内でentityを繰り返さず、
意味的なcycle edgeもnodeを反復するpathを作りません。
全prefixをglobal path予算に数え、limit-plus-one probeで追加可能なpathを検出します。
返却は最大`max_paths`件です。incoming/bothは探索方向だけを変え、
edgeのsource/targetやreported factを反転しません。

応答は`backend: "sql"`、`projection_watermark: null`（graph projection/lag/watermark保証は不要）、
実効`as_of`/`known_at`と次のfieldを含みます。
- `nodes`: canonical entity summary。完全な根拠quoteは含めない。
- `edges`: canonical assertion ID/revision、source/target UUID、predicate、
  valid interval、記録時刻、`epistemic_status: "reported"`。
- `paths`: `{nodes: [UUIDs], assertions: [{memory_id, revision}]}`。
- `coverage`: `max_hops`、`truncated`、`complete_within_bounds`。
- `consistency`: 現在のaccess/deletion epoch。
- `empty_reason`: pathがなければ`not_found`、あればnull。

可視の孤立seedはpathがなくても`nodes`に現れ得ます。pathなしや上限内の完全性は
**factが存在しない証明ではありません**。根拠はentity GET/relation explainで取得し、
展開summaryには含めません。最終再検査でnodeが欠ければ`409 graph_invalidated`で
fail-closedとなり、DB障害は空の成功応答でなく`503`です。
capabilitiesは`graph_backend: "sql"`、entity/relation type allowlist、graph上限を公開します。
将来backendの適合性を比較する正しさの基準であり、graph有用性の測定や
M1/M3全体の受入ではありません。[ADR 0005](adr/0005-relational-graph-jp.md)を参照してください。

## Checkpointの契約

checkpoint作成とrestoreには`Idempotency-Key`とscopeの現在のread/write権限が必要です。
GETと[head照会](#checkpoint-head-lookup)には現在のread権限が必要で、idempotency keyは不要です。
run/branch UUIDはtenant/scope内でcallerが指定する
identityであり、global sessionではありません。

| 作成field | 契約 |
|---|---|
| `scope_id`, `run_id`, `branch_id` | scope内のrunとbranchを識別するUUID |
| `expected_head` | 必須UUIDまたは`null`。空branchだけnull、それ以外は正確な現checkpoint ID |
| `harness_id`, `harness_version` | 必須の空でないtext、各最大256文字。run内で固定 |
| `state_schema_version` | `1`のみ。既定も1 |
| `event_watermark` | 必須の非負64-bit整数。parentから減少できない |
| `state` | typedな`goal`、`constraints`、`completed_actions`、`decisions`、`unresolved_questions`、`next_actions`、`pending_effects`。任意object/pickleは不可 |
| `memory_refs` | 同一scopeの重複しない`(memory_id, revision)`を最大100件。episode/entityはrevision 1、assertion（relationを含む）は存在するrevision。list省略は空、revision省略は最新ではなく1 |

goalとstateのtext要素は空でなく最大4,096文字です。
constraints、decisions、unresolved questions、next actionsは各64件、
completed actionsとpending effectsは各100件までです。pending effectは
一意のUUID `operation_id`、1〜256文字の`description`、
`planned / dispatched / unknown`のstatusを持ちます。
snapshot hintであり、外部actionの実行・確認の証拠ではありません。

checkpoint UUIDとbranch内のsequence（1から）はサーバーが付与します。
古いheadはCASで`409 checkpoint_head_conflict`、
watermark減少は`409 checkpoint_watermark_conflict`です。
失効branchは再開できません（`409 checkpoint_invalidated`）。
既存checkpoint payloadは不変です。HMAC checksumは`hmac-sha256-v1`で、
参照・保存時epochを含むenvelopeを対象にします。
GETはchecksum、typed state、可視参照、現在の認可を確認します。
不正envelopeはfail-closed、非公開/削除済みcheckpointは`404`です。

envelopeは`saved_access_epoch`、`saved_deletion_epoch`、
`current_access_epoch`、`current_deletion_epoch`を含みます。
保存時epochはmetadataであって旧権限の利用許可ではありません。
checkpointはrecall/explainの対象外で、既知IDにはcheckpoint GET、正確なbranchにはhead照会を使います。
GETは生存ancestorも読めますが最新headを保証しません。
head照会は同じ検査を再利用し、失効branchからfallbackしません。

### Restoreと依存境界

restoreには`checkpoint_id`、未使用の`target_branch_id`、
完全一致する`harness_id`、`harness_version`、`state_schema_version`を指定します。
同一scope/runでsequence 1の新checkpointを作り、別branch上でも元checkpointを
parentにします。元branch/checkpointは変更せず、headを巻き戻しません。
既存target branchは`409 checkpoint_branch_conflict`、
harness/schema不一致は`422 checkpoint_incompatible`です。

stateと参照をコピーしますが、dispatched snapshot hintはunknownに変更します。
同じtransaction内でrunの全生存dispatched effectへ
`origin: "checkpoint_restore"`の`unknown` eventを追記してからforkを作成します。
新revisionのCASで古いledger writerを拒否しますが、進行中の外部呼出しは取り消せません。
完全一致restore再送ではforkもjournal eventも重複作成しません。
保存済みassertion参照は正確な過去revisionを維持します。
restoreで最新assertion revisionを選び直したり、現在の外部事実を自動更新したりはしません。
必要な場合は別途、新しい観測を取得してください。
GET/head/restoreは別branchや保存後に追加したeffectも含め、**runの全生存effect**を統合します。
`tool_effects`は現在のsummaryであり、このlive viewで保存state/checksumは変更しません。

| 現ledger状態 | Snapshot hint | このoperationの照合 |
|---|---|---|
| `dispatched` / `unknown` | 任意またはなし | 必須 |
| `confirmed` / `failed` | 任意またはなし | caller申告のterminal記録で解決 |
| `planned` | なし、または`planned` | 不要。ただし実行許可ではない |
| `planned` | `dispatched` / `unknown` | 必須。不確実性を記録してreceiptを照合 |
| 未追跡 | **`planned`を含む**全hint | 必須。`untracked_effects`にも列挙 |

`requires_reconciliation`に全blocking operation IDを列挙し、
一つでもあれば`resume_allowed: false`です。v0.0.3のsnapshot-only動作から
意図的に厳格化しています。`automatic_reexecution`は常にfalseです。
権限検査・承認・provider照合はhostの責任です。
provider照会サービス、自動実行、checkpoint実行用harness adapterは未実装です。

同一keyの完全一致再送は新headでなく元のcheckpoint参照を維持します。
再送記録にstateは保存せず、読取り/restore再送は現在の認可でenvelopeを再構成するため、
現在のepoch metadataは変わり得ます。purge済み参照の再送は`404`です。

**callerは全memory依存を`memory_refs`へ宣言しなければなりません。**
依存DAGは宣言済み参照、parent lineage全体、下記のrun全体のeffect-to-checkpoint依存を対象にし、
未宣言のコピー本文をsemantic scannerが発見することはありません。
同意とsecret/PII除去もcallerの責任です。
working snapshot、compaction、checkpoint実行用harness連携は別の将来課題です。
[ADR 0003](adr/0003-checkpoints-jp.md)と[ADR 0004](adr/0004-tool-effects-jp.md)を参照してください。

## Tool-effect ledger

planと遷移には`Idempotency-Key`とscopeの現在のread/write権限、
GETにはread権限が必要です。**先にbootstrap checkpointを作成してください。**
planはrunを作らず、存在しないrunは`404`です。

| Plan field | 契約 |
|---|---|
| `scope_id`, `run_id`, `operation_id` | caller UUID。operation identityはtenant/scope/run/operation内でありglobalではない |
| `tool_name` | 空でないtext、最大256文字 |
| `action_hash` | callerの正規化actionの小文字64桁hex digestが必須。serverは外部呼出しとの一致を検証できない |
| `memory_refs` | 同一scopeの重複しない正確な参照を最大100件。episode/entityはrevision 1、assertion（relationを含む）は存在するrevision 1〜1000。既定は空、revision省略は最新でなく1 |

**actionが利用した全memory依存を宣言してください。**
checkpoint/effect/jobは参照kindにできず、未宣言コピーは発見しません。
tenant-HMACの`action_fingerprint`と安定した64桁hex `external_idempotency_key`のみを
永続化し、生のaction hashや引数は保存しません。
GETはこれらの識別子、参照ID、最新revision/status、`run_invalidated`、
最大4 eventの不変履歴を返します。tool名、reason、receipt参照の機密情報除去はcallerの責任です。
effectはrecall/explainの対象外であり、専用GET endpointを使ってください。

planは`201`で`memory_id`、`revision: 1`、`status: "planned"`を返します。
異なる冪等性keyでもoperation identityと正規化bodyが同じなら、後の遷移後も
元のrevision 1参照へ重複抑止します。intent変更は`409 operation_conflict`、
同一冪等性keyでのbody変更は`409 idempotency_conflict`です。
非公開/purge済みidentityは再送で復元できません（完全一致再送は`404`）。
runの存続期間中の上限は**terminal記録を含め100 effect**で、
超過は`422 effect_limit_exceeded`です。purgeはrunを封鎖するため容量を再利用できません。
新run/operation IDは意味的な重複抑止ではありません。

遷移は厳密な整数1〜4の`expected_revision`、`status`、
空でない最大256文字の`reason`を要求します。成功は次revision/statusを含む`201`、
古いCASは`409 revision_conflict`、禁止遷移は`409 effect_transition_conflict`です。

| 現状態 | 許可する次状態 |
|---|---|
| `planned` | `dispatched`, `unknown` |
| `dispatched` | `unknown`, `confirmed`, `failed` |
| `unknown` | `confirmed`, `failed` |
| `confirmed`, `failed` | なし。terminal状態は不変 |

`planned → unknown`はprotocol外/legacyの実行試行に関する不確実性を記録するもので、
実行許可ではありません。`unknown → dispatched`はありません。
terminal遷移は空でない最大256文字の`receipt_reference`と、
`receipt_source: "provider_receipt"`または`"operator_review"`を必須とし、
他状態では両receipt fieldを省略またはnullにします。**caller申告の参照であり、server検証済み結果ではありません。**
GET履歴は記録時刻、reason、receipt field、`origin`を含みます。
DBが時刻とactorを付与し、FSM、連続revision、head更新、参照制約を強制します。
RLSと複合tenant/scope外部keyを維持し、特権helperは使いません。

冪等性記録は結果参照/revision/statusとkeyed request digestのみで、
receiptやbodyのコピーは含みません。dispatch応答を含むplan/遷移の応答は
**過去revisionの参照であり、現在状態のsnapshotや実行許可ではありません**。
現在の認可の下で、同一intentのplan再送や完全一致する遷移再送は、
run封鎖後でも生存effectの元の参照を返し得ます。runの封鎖を解除するものではなく、
新たなdispatchは拒否し、purge済みeffectの完全一致再送は引き続き`404`です。
harnessは外部呼出し前にdispatchを永続記録し、
providerが対応する場合は安定した外部keyを使ってください。
外部exactly-once、承認、自動再試行/実行、provider receipt照会の保証はありません。

## 冪等性と削除

mutationには`Idempotency-Key`が必要で、変更と冪等性結果を同じtransactionで
commitしてから応答します。keyの範囲はtenant・principal・operationです。
正規化後のrequestが一致すれば現在のアクセス権を再検査して結果を再利用し、
payloadが変われば`409`です。削除済みmemoryへの同一request再送は、
以前の本文ではなく`404`を返します。

source eventの重複抑止は**namespace + event ID**の組をtenant-keyed HMACにし、
PostgreSQL上でtenant/scopeごとに管理します。event ID単独のhashではありません。
同一eventは新しいidempotency keyでも生存episodeを再利用できますが、
payloadが衝突すれば`409`です。purge済みeventの完全一致再送は`404`であり、
元のsource identityを再利用してepisodeを復活させることはできません。

`preview`は現在の対象件数を示すだけで、状態変更やselector予約token発行はしません。
`purge`は1〜100件のroot IDを受け付け、
**episode → assertion（全revision）→ checkpoint参照 → 子孫/fork checkpoint**
を辿ります。episodeからcheckpointへの直接参照も対象です。
entity根拠により**episode → entity → sourceまたは過去のどのtargetとしてでも
そのentityを使うrelation → assertion全履歴**も辿ります。entityの直接purgeも同じ
relation closureを持ち、checkpoint/effectへの直接entity参照も対象です。
relationが消えただけで他の生存entity identityは削除しません。
entityはepisodeだけに依存するため、意味的graph cycleがprovenance cycleを作ることはありません。
宣言済みepisode/entity/assertion-to-effect参照により、
**source → tool effect → 同一scope/runの全checkpoint**も辿ります。
参照が空の旧checkpointやeffect作成前のsnapshotも対象です。
jobは不変入力による**episode → job**、**result assertion → job**、
**parent job → retry子孫**を追加し、同じclosure上限に数えます。
result assertionの後続revisionだけで使われたsourceを含め、どのsourceでも削除時は
assertion全履歴と依存jobをpurgeします。
**job/control記録やfailed parentのretry chainを消しても、公開済みの独立assertion出力や
source episodeは消しません。** 出力には直接episode provenanceがあるため、
factを消すにはoutput/sourceを明示purgeしてください。job → resultの依存cycleはありません。
上限は要求rootに加えて依存物全体で10,000件であり、層ごとの上限ではありません。
超過時は一部削除せず`422`です。過去のどのsourceでも、その削除は保守的に
assertion全履歴と全依存checkpoint payloadを削除し、後のsnapshotがそのsourceを
省略していても対象です。すべての子はparent lineage全体を引き継ぎ、forkでも逃れられません。

**どのeffectでも**purgeするとrunの`effects_invalidated`を永続設定します。
新effect plan/dispatchは`409 effect_run_invalidated`、
新checkpointは`409 checkpoint_invalidated`で拒否し、そのrunのcheckpointは再開できません。
独立した他effectは自動purgeせず、GETで`run_invalidated: true`と履歴を返します。
`unknown → confirmed/failed`を含む許可済み照合遷移は可能ですが、dispatchはできません。

影響するbranch headは永続的に失効し、同じIDで再開できません。
checkpoint削除は祖先やsource episodeを削除しません。再生成も行いません。
既存tenant session lockでclosure、payload purge、run/branch失効、
read barrierを原子的に扱います。

purgeはassertion/episode payloadとtombstoneより先にjob入力/request行を削除し、
同じtenant barrierで実行中publisherを拒否します。purge済みjobのGET/完全一致HTTP再送は
`404`となり、保持job identityが同じexact jobの復活を防ぎます。
purgeは対象episode/entity/assertion/checkpoint/effect/job payload、entity根拠、
typed relation link、reason/receipt参照を含むeffect event、依存する引用/参照を同期SQL削除した後、
**同じtransaction内で**scopeに束縛されたopaqueな削除markerと時刻を
`memory_ops.object_tombstone`へ挿入します。`memory.object`に`deleted_at`列はなく、
SELECT RLSがtombstoneのあるobjectを除外します。
同じtransactionで`deletion_epoch`を進めてreceiptをcommitします。
`active_store_purged`を含むHTTP `202`は、**queue上のpurge jobや完全消去証明ではありません**。
opaque operation registry/run flag、run/branch metadata、object記録、tombstone、audit/receipt metadata、
job identity、tenant-keyed HMACのsource/idempotency tombstoneはtenantの存続期間中保持します。
自動期限切れやtenant完全消去workflowはありません。
過去参照や再送からpurge済みlabel、value、receiptを復活させることはできません。
canonical削除は同じtenant barrierで対象episode/assertionの全lexical revisionへもcascadeし、
tombstone commitより先に消去します。これらは派生payloadであり、
別memory identityやprovenance vertexではありません。
rebuildはtombstoneをskipし、purge済み本文を再生成しません。

receiptは`backup_status: "operator_managed"`、
`backup_retention_deadline: null`を返します。旧DB page、WAL、replica、backup、
配信済みcontextの消去は証明しません。完全消去保証や本番complianceへの適格性は未確認です。
backupから復元したDBは最新の削除台帳とACL失効を再適用するまで隔離してください。
自動backup recovery/台帳replayとDR適格性確認は未実装です。

## Schema互換性

**v0.0.19はschema 10を維持し、migrationを追加しません**。
既存schema 10 DBには[application-only更新](operations/README-jp.md#schema-10-application-only-upgrade)を使います。
古いschemaにはv0.0.15でjob state/payload制約とterminal guard用に導入した
`010_job_cancellation.sql`を、[既存migration sequence](operations/README-jp.md#schema-10-job-cancellation-upgrade)で適用します。
過去のv0.0.13がdurable admin audit用の`009_scope_access.sql`を導入しました。
既存の特権専用`memory_ops.scope_access_event`はforced RLS、runtime policy/grantなし、
原子的membership/epoch/audit変更を使います。
固定PostgreSQL 18.6/pgvector 0.8.6 imageを維持します。
旧API、worker、adapter、hook、SDK callerを停止/drainしてから対応v0.0.19 componentだけを
導入します。混在版/rolling互換性は主張しません。
すべての古いschemaには010までの既存migrationが必要です。
古いDBには引き続きv0.0.11の`008_pgvector.sql`が必要です。
migrationは**`public`内の`vector` 0.8.6**を要求し、別schema/版の既存extensionを拒否します。
上記の固定prebuilt上流DB profileを使い、旧PostgreSQL imageが不変と想定したり、
未固定extensionを使ったりしないでください。schema 10で古いschemaのprocessを起動してはいけません。
既存episode/assertion revision projectionにはforced RLS、canonical `ON DELETE CASCADE`、
runtime SELECT/INSERTのみを適用します。
既存dataの**embedding backfillはなく**、生成/再構築/provider呼出しは明示的な外部操作のままです。
MCP adapter、hook、SDKはHTTPのみでDDLを行わず、対応するservice `0.0.19`、API `v1`、schema `10`を要求します。
以下の既存migration履歴はschema 7より古いDBに引き続き適用します。

変更しないmigration 001〜006に続き、追加的な`007_japanese_fts.sql`を適用します。
二つのlexical projection tableを作成し、migration runnerがschema 7記録前の
**同一transaction**内でPython backfillを行います。全保持episode/assertion revisionを
対象にし、canonical ID/system time、receipt、tombstoneは変えません。
backfill完了後の失敗でもprojection DDL/dataとschema ledgerをまとめてrollbackし、
schema 6からのupgradeは6のままです。一方、明示reindexの失敗は既存schema 7の
projectionを維持します。
typed graph/job/effect/checkpoint履歴とguard、legacy `Remember` JSON/HMAC順、
source identity、checkpoint checksumは維持します。projectionはcheckpoint/effect参照kindを
追加しません。v0.0.19のAPI**とworker**は厳密な履歴`[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]`と
schema `public`内のextension `vector` 0.8.6を要求し、不一致と安全でないruntime roleを拒否します。

migration/rebuildにはforced RLSをbypassできる適切な権限の管理者が必要で、
migrationにはDDL権限、`btree_gist`、PostgreSQL serverへ導入した対応pgvector extensionも必要です。
`row_security = off`はbackfillがRLSで
filterされる場合にfail-closedにする設定であり、bypass権限を与えません。
`pg-agmemory reindex-lexical`は選択DBの**全tenantを対象とするoffline管理操作**です。
`PGAG_ADMIN_DATABASE_URL`と対応するv0.0.19/schema 10 toolingを使い、migration lock下でlexical projectionだけを
原子的に置換します。source本文ではなく`profile`と`episodes`/`assertion_revisions`件数を
出力します。`--subject`はprincipal/scope filterではなく明示拒否し、
`--once`もworker専用として拒否します。
旧版・新版の全API**とworker**を停止/drainし、backup、原子的migration/rebuildの後に、
対応するv0.0.19 processだけを起動してください。保守中はadapter、hook起動、SDK caller、管理commandも停止します。
lexical reindexはvectorを生成/投入/再構築しません。
**すべての旧imageを停止してください。v0.0.1にはschema起動guardがありません。**
rolling共存やdowngradeは非対応です。
[現在のschema 10運用](operations/README-jp.md#schema-10-application-only-upgrade)に従ってください。

## 検証証拠

公開repository: [rioriost/pg_agmemory](https://github.com/rioriost/pg_agmemory)。

<a id="v0019--schema-10"></a>

### v0.0.19 / schema 10 — 検証済み

**2026-09-18 JSTに最終localとnativeの実装結果を検証しました。**
Apple Containerの`./scripts/test-containers.sh`は**exit 0**で、
**663合格、既存warning 1件、366.14秒（6:06）**でした。
内訳は**既存630 + 新規33テスト**で、**`tests/test_contract.py`の16 case、
`tests/test_jobs.py`の10 case、`tests/test_sdk.py`の7 case**です。
commit・push済み実装:
[`e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf`](https://github.com/rioriost/pg_agmemory/commit/e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf)
（`feat: query caller-owned jobs with bounded keyset pages`）。
[CI 35242118110](https://github.com/rioriost/pg_agmemory/actions/runs/35242118110)は
この完全一致SHAでnative Docker amd64/arm64とも合格しました。
実logで各**663合格、warning 1件**、**amd64 638.06秒 / arm64 586.58秒**を確認しました。
Ruff、strict mypy（**source 19 + strict SDK consumer 1ファイル**）、
真のcore-only/hook-only/sdk-only導入検査が全3環境で合格しました。

全non-root production smokeが全3環境で合格しました。日本語tokenizer、HTTP、
readiness、worker、MCP両protocol mode、hook全3 event、capture、pgvector、
SDK lifecycle、required context、recall filter、checkpoint head、job取消、
**所有job照会**、scope accessが対象です。
新production SDK sequenceはsource + job三つ → pendingの最初の1件page →
middle jobを明示取消 → 現在の次pageは最古jobだけ →
cancelled jobを照会 → source purge `object_count: 4` → pending照会が空、
の順で合格しました。query自体が取消や自動pagingを行うわけではありません。

full suiteでは実100-item pageと101行overflow、timestamp同値のexclusive UUID境界、
全5状態、現在state変更、削除/偽造cursor、現在ACLとscope-adminの所有権境界、
全page GET検証、read-only SQL、明示的なtyped SDKの3 page、
payload非開示、自動paginationなしが合格しました。
SDKはresponse modelの100-job上限も検証し、既存GET形式は不変です。
共有`MemoryService.epochs`は永続化/schema/mutation hashを変えず、
checkpoint/recall/job-queryのconsistencyを維持します。
schema 10にはSQL migrationや依存/provider/artifact固定値変更はありません。
所要時間は性能benchmarkではありません。これは実装結果であり、後続最終docs CI結果ではありません。
性能/MVP/本番/DR適格性確認は主張しません。

<a id="v0018--schema-10"></a>

### 過去のv0.0.18 / schema 10 — 検証済み

**2026-09-17 JSTに最終localとnativeの実装結果を検証しました。** Apple Containerの
`./scripts/test-containers.sh`は**exit 0**で、**630合格、既存warning 1件、
369.39秒（6:09）**でした。内訳は**既存604 + 新規26テスト**で、
**`tests/test_contract.py`の8 case、`tests/test_checkpoints.py`の11 case、
`tests/test_sdk.py`の7 case**です。新test fileではありません。
既存historical/CAS/fork/ACL/purge/large-SDK/effect/OpenAPI/26-route coverageも拡張しました。
Ruff、strict mypy（**source 19 + strict SDK consumer 1ファイル**）、
真のcore-only/hook-only/sdk-only導入検査は全3環境で合格しました。

全non-root production smokeが全3環境で合格しました。日本語tokenizer、HTTP、readiness、
worker、MCP両protocol mode、hook全3 event、capture、pgvector、SDK lifecycle、
required context、recall filter、**checkpoint head照会**、job取消、scope accessが対象です。
新しい実SDK smokeはsource + checkpoint二つ → head sequence 2 /
過去GET sequence 1 → source purge `object_count: 3` →
head `409 checkpoint_invalidated`で全3環境とも合格しました。

全環境のfull runには修正済みの完全な`ErrorBody` SDK fixtureと、実schemaに沿うtombstone化head fixtureを含みます。
生存ancestor GETが200でもhead照会は404です。この意図的にbranchを失効させないfixtureは
通常の`forget`動作ではなく、**object TTL機能の追加はありません**。
実read-only SQL head transactionとscopeのread-only権限での照会が合格しました。
実際にcommitされたPOST create 201のresponse lossは`outcome_unknown: true`となり、
同じkeyのreceipt/head回復が合格しました。POST head 200のresponse lossは
`outcome_unknown: false`となり、明示的な再照会が合格しました。
large response、現在effect-ledger/head/forkの同等性、**全26 resource route**も合格しました。
source purgeはopaqueな`head_id`/`sequence`を保持して失効flagを設定し、
照会は保持IDを返さず409となります。

commit・push済み実装:
[`4accd38408a8384b4376f6250d953bb2fa480ec8`](https://github.com/rioriost/pg_agmemory/commit/4accd38408a8384b4376f6250d953bb2fa480ec8)
（`feat: discover current checkpoint branch heads`）。
[CI 35235315016](https://github.com/rioriost/pg_agmemory/actions/runs/35235315016)は
この完全一致の実装SHAでnative Docker amd64/arm64とも合格しました。
実logで各architectureの**630合格、warning 1件**、
**amd64 663.91秒 / arm64 544.14秒**、Ruff、mypy **19 + 1**、
全optional導入検査と上記全production smokeを確認しました。
別の最終v0.0.18 docs
[`3f01faf56563202c40a00d73cee72830022220b1`](https://github.com/rioriost/pg_agmemory/commit/3f01faf56563202c40a00d73cee72830022220b1)は
[CI 35237907862](https://github.com/rioriost/pg_agmemory/actions/runs/35237907862)に合格しました。
native amd64 **644.25秒**、arm64 **570.20秒**で、各**630テスト、warning 1件**でした。
Ruff、mypy **19 + 1**、全optional導入、全production smokeも両architectureで合格しました。
docs結果は実装CI 35235315016とは別で、両runともv0.0.19の適格性確認ではありません。
schema 10にはSQL migrationがなく、artifact固定値とproviderは不変です。
所要時間はbenchmarkではありません。
harness/compaction/MVPや汎用復旧/本番/DRの適格性確認は主張しません。

<a id="v0017--schema-10"></a>

### 過去のv0.0.17 / schema 10 — 検証済み

**2026-09-17 JSTに実装の最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**604テスト、既存warning 1件**が合格しました。
localの`./scripts/test-containers.sh`は**exit 0**でした。
内訳は**既存566 + 新規38テスト**で、**`tests/test_recall_filters.py`の37 case**と
**実hook CLIのfilter拒否1 case**です。
Ruff、strict mypy（**source 19ファイル + strict SDK consumer 1ファイル**）、
真のcore-only/hook-only/sdk-only導入検査は全3環境で合格しました。
consumerは`RecallFilters`を明示構築します。
公開済み実装:
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894)
（`feat: add exact structured recall filters`）。
[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)は
この完全一致SHAで両native Linux architectureとも合格しました。
job statusだけでなく**実log**で各architectureの件数、検査、smokeを確認しています。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **321.56秒（5:21）** |
| Docker、native `linux/amd64` | **638.66秒** |
| Docker、native `linux/arm64` | **544.33秒** |

所要時間は性能benchmarkではありません。別の最終v0.0.17 docs
[`5226c81fae7a50a5668109478d5d9f23fc4d7761`](https://github.com/rioriost/pg_agmemory/commit/5226c81fae7a50a5668109478d5d9f23fc4d7761)は
[CI 35232139680](https://github.com/rioriost/pg_agmemory/actions/runs/35232139680)に合格しました。
native実logで各**604テスト、warning 1件**、
**amd64 465.50秒 / arm64 557.08秒**、Ruff、strict mypy **source 19 + consumer 1ファイル**、
全optional導入、全production smokeを確認しました。
docs結果は実装CI 35230044140とは別で、両runともv0.0.18の適格性確認ではありません。

全3 modeのranking前/coverage filter、required参照不一致、
不正nested list/string filter、全3 modeのkind-only overflow、
Unicodeの合成済み/分解済み表現を正規化しない完全一致、OpenAPI schema assertionが合格しました。
後から追加した5 caseは最終件数に含まれ、別加算ではありません。
共有`Predicate` aliasは既存`Remember`/`CapturedMemory`のfield順とsemanticsを維持します。

全3環境で全non-root production smokeが合格しました。日本語tokenizer、HTTP、
readiness障害/回復、worker、MCP両mode、hook全3 event、capture、pgvector、
Python SDK、required context、構造化recall filter、job取消、scope accessを対象にします。
新SDK smokeはobserve + remember → `max_items: 1`でtruncationなしの3 field完全一致 →
required source参照とassertion filterの衝突
（`404 not_found`、read-only `outcome_unknown: false`）→
`object_count: 2`を返すsource purge → filter付き空readで合格しました。
project版は0.0.17へ進め、schema 10、依存版、artifact固定値は不変です。
MVP/意味品質/性能/本番/DRの適格性確認は主張しません。

<a id="v0016--schema-10"></a>

### 過去のv0.0.16 / schema 10 — 検証済み

**2026-09-17 JSTに実装の最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**566テスト、既存warning 1件**が合格しました。
localの`./scripts/test-containers.sh`は**exit 0**でした。
内訳は**既存535 + 新規31テスト**で、
**`test_required_context` 29 case + 実hook拒否1 case + SDK safe-code parameter 1 case**です。
Ruff、strict mypy（**source 19ファイル + strict SDK consumer 1ファイル**）、
真のcore-only/hook-only/sdk-only導入検査は全3環境で合格しました。
公開済み実装:
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57)
（`feat: preserve required recall references within context budgets`）。
[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)は
この完全一致SHAで両native Linux architectureとも合格しました。
job statusだけでなく**実log**で各architectureの件数、検査、smokeを確認しています。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **298.15秒（4:58）** |
| Docker、native `linux/amd64` | **601.73秒** |
| Docker、native `linux/arm64` | **565.34秒** |

所要時間は性能benchmarkではありません。別の最終v0.0.16 docs
[`520990d95718b17ae93a7d5259d600e379991df2`](https://github.com/rioriost/pg_agmemory/commit/520990d95718b17ae93a7d5259d600e379991df2)は
[CI 35226313891](https://github.com/rioriost/pg_agmemory/actions/runs/35226313891)に合格しました。
native実logで各**566テスト、warning 1件**、
**amd64 476.15秒 / arm64 478.08秒**、Ruff、strict mypy **source 19 + consumer 1ファイル**、
全optional導入、全production smokeを確認しました。docs結果は実装CI 35224189967とは別で、
両runともv0.0.17の適格性確認ではありません。

実SQLの正確に16参照、正確なbyte境界と1 byte不足、scope/ACL filter、
過去時刻での既定revision 1、latest fallbackなしのrevision 2、
source purge、日本語projection欠落時の`lexical_incomplete`が合格しました。
Native/SDKとMCP auto/legacyの完全一致とerror伝播も合格しました。
required relationはentity UUID/citation byte overheadを含めて保持し、
entityの異なるkindによる`404`と、正しい形式の`required_memory_refs`を
未知fieldとして実hook CLIが拒否する検査も合格しました。

全3環境で全non-root production smokeが合格しました。日本語tokenizer、HTTP、
readiness、worker、MCP両mode、hook全3 event、capture、pgvector、
Python SDK、required-context recall、job取消、scope accessを対象にします。
required-context smokeはSDK後・取消前に、専用sourceとoptionalな`Gold`一致を使って実行しました。
`max_items: 1`とtruncationでのrequired query迂回、explicit予算64による
`budget_exhausted`とread-only `outcome_unknown: false`、
続くsource purgeの`object_count: 2`が合格しました。
project版だけを0.0.16へ進め、schema 10、依存、固定artifactは不変です。
MVP/意味品質/性能/本番/DRの制限を維持します。

<a id="v0015--schema-10"></a>

### 過去のv0.0.15 / schema 10 — 検証済み

**2026-09-17 JSTに実装の最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**535テスト、既存warning 1件**が合格しました。
localの`./scripts/test-containers.sh`は**exit 0**でした。
内訳は**既存495 + 新規40テスト**で、unit/integration別の件数は主張しません。
Ruff、strict mypy（**source 19ファイル + strict SDK consumer 1ファイル**）、
真のcore-only/hook-only/sdk-only導入検査は全3環境で合格しました。
公開済み実装:
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)
（`feat: add fenced durable job cancellation`）。
[CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)は
この完全一致SHAで両native Linux architectureとも合格しました。
job statusだけでなく**実log**で各architectureの件数、検査、smokeを確認しています。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **298.29秒（4:58）** |
| Docker、native `linux/amd64` | **406.88秒** |
| Docker、native `linux/arm64` | **490.57秒** |

所要時間は性能benchmarkではありません。別の最終v0.0.15 docs
[`9d34d5329c9db580e7de0459b743511235ad6fb8`](https://github.com/rioriost/pg_agmemory/commit/9d34d5329c9db580e7de0459b743511235ad6fb8)は
[CI 35218254940](https://github.com/rioriost/pg_agmemory/actions/runs/35218254940)に合格しました。
native実logで各architecture **535テスト、warning 1件**、
**amd64 605.83秒 / arm64 473.57秒**、Ruff、strict mypy **source 19 + consumer 1ファイル**、
真のoptional導入、全production smokeを確認しています。
docs結果は上記実装CI 35216770999とは別です。両v0.0.15 runともv0.0.16の検証ではありません。
全3環境で全production smokeが合格しました。日本語tokenizer、HTTP、readiness、
worker、modern/legacy MCP、hook全3 event、capture、pgvector、Python SDK、
明示job取消、scope accessを対象にします。
取消smokeは実SDKのenqueue → cancel → 同key replay → GET cancelled →
worker `--once`のidle報告 → source purgeを実行し、
そのfixtureのpurgeは`object_count: 2`を返しました。

job取消のDB/CAS/競合/ownership/purge/rollback/worker `lease_lost`検査と、
SDKの**全25 resource route** coverageが合格しました。
実際にcommit済みのHTTP 200取消応答を失うと`outcome_unknown: true`となり、
同じkey/bodyのreplayでreceiptを回復しました。
別keyは`409 job_cancel_conflict`、`outcome_unknown: false`でした。
不正応答/想定外statusの取消は結果不明のままとし、
`None`を含む不正keyはnetwork接続前に拒否しました。
SDK内部POSTは成功statusと独立してmutationを明示分類します。

DDL後のschema 9→10 ledger失敗は以前のguard function、制約、schema 9履歴を復元し、
その後のretryは成功しました。既存v6 jobのstate、attempt、payloadは不変でした。
既存MVP/本番/品質/DRの制限を維持します。

<a id="v0014--schema-9"></a>

### 過去のv0.0.14 / schema 9 — 検証済み

**2026-09-17 JSTに最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**495テスト、既存warning 1件**、
Ruff、strict mypy（**source 19ファイル + 別のstrict SDK consumer 1ファイル**）、
真のcore-only/hook-only/sdk-only導入検査が合格しました。
localの`./scripts/test-containers.sh`は**exit 0**でした。
内訳は**既存464 + readiness unit 16 + integration 15テスト（新規31）**です。
schema 9は不変で、migrationは追加しません。
公開済み実装:
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
（`feat: add bounded runtime readiness probe`）。
[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)は
この完全一致SHAで合格しました。job statusだけでなく**実log**で両native Linux architectureの
件数、検査、smokeを確認しています。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **295.67秒（4:55）** |
| Docker、native `linux/amd64` | **385.41秒** |
| Docker、native `linux/arm64` | **470.16秒** |

所要時間は性能benchmarkではありません。
別の最終v0.0.14 docs
[`d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68`](https://github.com/rioriost/pg_agmemory/commit/d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68)は
[CI 35202931424](https://github.com/rioriost/pg_agmemory/actions/runs/35202931424)に合格しました。
native実logで各architecture **495テスト、warning 1件**、Ruff、
strict mypy **source 19 + SDK consumer 1ファイル**、optional導入、全production smokeを確認し、
**amd64 319.51秒 / arm64 503.56秒**でした。
docs所要時間は上記実装CI 35201615965とは別です。両v0.0.14 runともv0.0.15/schema 10の検証ではありません。

実5秒locked-schema timeout検査はassertion範囲4.5〜10秒内で合格しましたが、
wall-clock SLAではありません。cancel後のruntime backend leakはなく、
connection refusalからの復旧と既存livenessも合格しました。
`NOINHERIT` owner-role membership、schema ledger SELECTの剥奪/復元、
extension namespace移動/復元のDB variantも合格しました。

non-root productionの日本語tokenizer、HTTP/liveness、readiness schema障害/復旧、
worker、MCP **2026-07-28/2025-11-25**、hook全3 event、capture、pgvector、
Python SDK、scope-accessの全smokeが全3環境で合格しました。
合格した使い捨てDB readiness smokeは**同じAPI process**で
**ready 200 → schema ledger rename → health 200のままready 503 →
ledger restore → ready 200**を検査します。通常HTTP smokeの後に実行し、
後続Native/SDK smokeは変更せず、source/tombstoneも書き込みません。
このlifecycleは全3環境で合格しました。live DBでdriftを再現してはいけません。
MVP/本番/性能/品質/DR/完全消去の適格性確認は主張しません。

<a id="v0013--schema-9"></a>

### 過去のv0.0.13 / schema 9 — 検証済み

**2026-09-17 JSTに最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**464テスト、既存warning 1件**、
Ruff、strict mypy（**source 19ファイル**と別の**SDK consumer 1ファイル**）、
真のcore-only/hook-only/sdk-only導入検査が合格しました。
内訳は**既存426 + scope-admin unit 22 + integration 16テスト（新規38）**です。
localの`./scripts/test-containers.sh`は**exit 0**でした。公開済み実装:
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413)
は[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)に
完全一致SHAで合格しました。job statusだけでなく**実log**で両native Linux architectureの
件数、検査、smokeを確認しています。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **297.52秒（4:57）** |
| Docker、native `linux/amd64` | **539.86秒** |
| Docker、native `linux/arm64` | **460.73秒** |

所要時間は性能benchmarkではありません。
別の最終v0.0.13 docs
[`185f433aa49479810b8955f1bec2e856f2715f7b`](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)は
[CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967)に合格しました。
native実logで各architecture **464テスト、warning 1件**、Ruff、
strict mypy **source 19 + consumer 1ファイル**、optional導入検査、全production smokeを確認し、
**amd64 499.25秒 / arm64 454.37秒**でした。
docs所要時間は上記実装CI 35196930448とは別です。両runともv0.0.14の検証ではありません。

合格integrationは非owner `BYPASSRLS` roleによる`FOR UPDATE`なしの照会、
epoch最大値での変更拒否/no-op動作、schema 8→9のDDL後のledger記録失敗、
transactional rollback/retry、以前のmigrationを含みます。
実commit後も出力lockを保持すること、consumer失敗時のconnection cleanup、
実commit後に注入した`OperationalError`を対象にします。保存ACL/auditはcommit済みのまま、
`outcome_unknown`はtrueで、古いexpected epochの再送は競合しました。
実5秒lock timeoutでの変更なし、別scope間のtenant-global CASとlegacy空permission行、
schema 9での既存ACL維持/audit backfillなし/forced RLS/capabilitiesを対象にします。
これらも合格しました。

non-root productionの日本語tokenizer、HTTP API、worker `--once` idle、
MCP **2026-07-28/2025-11-25**、hook全3 event、capture、pgvector、Python SDK、scope-accessの
全smokeが全3環境で合格しました。
scope-access CLI subprocess smokeは専用admin資格情報を使い、
**get → CAS read-only set → SDK read成功 / write replay 404 →
CAS revoke → SDK empty recall → get inactive**を確認します。
admin資格情報は管理subprocessだけに渡し、SDK/APIには渡しません。
この検証済みlifecycleは外部ACL provider連携ではありません。
本番/品質/性能/DR/完全消去のgateは未完了です。

<a id="v0012--schema-8"></a>

### 過去のv0.0.12 / schema 8 — 検証済み

**2026-09-17 JSTに最終localとnative結果を検証しました。**
Apple Containerとnative Docker amd64/arm64で各**426テスト、既存warning 1件**、
Ruff、strict mypy **source 18ファイル**、全24 method annotationを対象にした
別のstrict型付きconsumer **1ファイル**も合格しました。
真の**core-only/hook-only/sdk-only noneditable wheel導入3検査**、
同梱`py.typed`、hook/sdk-onlyにMCPがないことの検査も合格しました。
これらは全3環境で合格しました。
件数の内訳は**既存345 + SDK unit 76 + SDK integration 5テスト（新規81）**です。
先のfixture key失敗runに代わる最終結果です。
公開済み実装:
[`88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f`](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)
（`feat: add typed asynchronous Native Python SDK`）。
[CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945)は
この完全一致SHAで合格しました。job statusだけでなく**実log**で各native architectureの
SHA、件数、検査、production smokeを確認しています。

| 環境 | テスト所要時間 |
|---|---|
| ローカルApple Container | **292.76秒** |
| Docker、native `linux/amd64` | **484.79秒** |
| Docker、native `linux/arm64` | **472.49秒** |

所要時間はテスト観測値であり、性能benchmarkではありません。

最終v0.0.12 docs
[0e00abd](https://github.com/rioriost/pg_agmemory/commit/0e00abdae930dcc1e2d2015fbf5931a74701fe7d)は
[CI 35194141510](https://github.com/rioriost/pg_agmemory/actions/runs/35194141510)に合格しました。
native実logで**各architecture 426テスト**、全検査/smokeを確認しました。
docs run所要時間は**amd64 488.20秒 / arm64 454.88秒**で、
実装CI 35193004945と上記所要時間とは別です。両v0.0.12 runともv0.0.13/schema 9の検証ではありません。

全3環境の使い捨てPostgreSQL DB上の実HTTPでSDK integration全5テストが合格しました。
全24 resourceを対象にし、
graph/relation revision、実workerを伴うfailed job retry、
256 KiB超checkpoint create/readとtool-effect restore照合、
認可失効、実post-commit応答喪失と同一key復旧を含みます。
合格したunit検査はentry cancel時のcleanup、entry失敗後のsingle-use、
caller body例外後のexit、256文字key、GET成功status完全一致、
forget両modeの不正応答shapeを対象にしています。
non-root production全smokeも全3環境で合格しました。
日本語tokenizer、HTTP API、実worker `--once` idle、
MCP **2026-07-28/2025-11-25**、hook全3 event、実worker付きatomic capture、
pgvector exact/hybrid検索、新Python SDK lifecycleが対象です。
SDK smokeは**capture/replay →
pending `get_job` → embedding input/upload → exact vector recall → preview/purge →
削除進捗 → capture replay 404**を実行します。
**このsmoke自体はworkerを呼びません**。従来の実capture-worker smokeは別に維持します。
SDK smokeはlocalと両native architectureで合格しました。
上記実装/最終docs runは別々の過去証拠であり、v0.0.13の適格性確認ではありません。
M0〜M3/MVP/本番/性能/品質/DR/完全消去のgateは未完了です。
以下の過去v0.0.11証拠でSDK変更の適格性は認定しません。

<a id="v0011--schema-8"></a>

### 過去のv0.0.11 / schema 8 — 検証済み

**最終localとnative結果を2026-09-17 JSTに確認しました。** 実装
[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)は、
完全一致SHAの[CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403)に合格しました。
Apple Containerとnative Docker両jobで各**345テスト、既存warning 1件**、
Ruff、strict mypy（**source 17ファイル**）、core-only/hook-only導入検査、
non-root productionの全smokeに合格しました。日本語tokenizer、HTTP API、worker、
MCP **2026-07-28/2025-11-25**、hook全3 event、atomic capture lifecycle、
pgvector exact/hybrid検索とpurgeが対象です。

| 環境 | テスト所要時間 |
|---|---|
| ローカルApple Container | **283.44秒** |
| Docker、native `linux/amd64` | **404.40秒** |
| Docker、native `linux/arm64` | **433.46秒** |

最終v0.0.11 docs
[dccd5cb](https://github.com/rioriost/pg_agmemory/commit/dccd5cb5571873515aace8621ce4adb3de250d3a)は
[CI 35190495385](https://github.com/rioriost/pg_agmemory/actions/runs/35190495385)に合格しました。
実logで各native architecture **345テスト、warning 1件**、Ruff、
strict mypy（**source 17ファイル**）、導入検査、全smokeを確認しました。
docs run所要時間は**amd64 506.38秒 / arm64 460.18秒**です。
実装CI 35189448403と上記local/native所要時間とは別であり、
どちらのrunもv0.0.12の検証ではありません。

job statusだけでなく実logで確認した結果です。所要時間は性能benchmarkではありません。
artifact検査は固定上流pgvector 0.8.6 profileの両architectureを別途確認しました。
合格した検査は新DB profileでのschema 8 migration/role/extension版/schema guard、canonical digest結合、
float正規化/不変性/model分離/8 model上限、決定的exact/RRF数学、
ranking前のACL/時間filter、coverage、purge/replay、既存lexical/MCP/hook/capture動作です。
実装fixtureにはDB norm/次元/composite FK/8 model guard、直接RLS可視性とUPDATE拒否、
ACL失効、実際のschema 7→8 migrationでledger失敗時のDDL/extension rollback後のretryも含み、
embedding backfillは行いません。production vector smokeは**episode/assertion両projection**をuploadし、
basis vector distance **[0, 1]**とRRF、source purge後のupload replay `404`を検査します。
これらの検査は全3環境で合格しました。
testや合成basis vector例は意味品質、本番性能、provider由来証明、
untrusted vectorへの頑健性を認定しません。
採用profileにsource-build workflowは含めません。
元のM0〜M3/MVP/本番/DR/完全消去/性能/品質の全gateは未完了です。

<a id="v0010--schema-7"></a>

### 過去のv0.0.10 / schema 7 — 検証済み

**最終local Apple Containerとnative CI結果を2026-09-17 JSTに確認しました。**
最終local sourceは公開済み実装
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f)と一致します。
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)は
両native jobとも合格しました。実logでjob statusだけでなく、
完全一致SHAと以下のテスト件数、所要時間、検査を確認しました。

| 環境 | Command | テスト | テスト所要時間 |
|---|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | **304合格、既存warning 1件** | **275.53秒** |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | **304合格、既存warning 1件** | **467.75秒** |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | **304合格、既存warning 1件** | **434.40秒** |

全3環境の最終runは**Ruff、strict mypy（source 16ファイル）、真のcore-only/hook-only導入検査、
non-root productionの全smoke**にも合格しました。
日本語tokenizer、API HTTP、worker CLI、MCPの**`2026-07-28`・`2025-11-25`両mode**、
hook全3 event（**`session_start`・`task_switch`・`after_compaction`**）、
atomic captureが対象です。所要時間は観測値であり、性能benchmarkではありません。

各write/outer receipt後のrollback fault、source/key重複抑止の競合、
quota、RLS/削除、API process再起動と実workerを検査します。
全3環境で合格した新規fixtureのproduction smokeはMCP/hook検査後に、Native capture → pending job →
実worker CLI `--once` → episode/assertion組のrecall → 同capture replay →
source purge → job GET `404`とcapture replay `404`を確認します。
最終suiteは、commit後に実HTTP 201応答を喪失してもsame-key retryで
正確に同じepisode/job組を返し、publicationが一つだけとなる検査も含めます。
明示retry child作成後も元のfailed capture jobがreplay対象のままであることも検査します。
これらのregressionはlocalとnative Docker両architectureで合格しました。
新依存はなく、project v0.0.10のlock metadataだけを変更します。
M0〜M3/MVP/本番/性能/品質/DR/完全消去の全gateは未完了です。
最終v0.0.10 docs
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)も、
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760)で
**各native architecture 304テスト**に合格しました。
このdocs runは上記実装runの所要時間とは別であり、両runともv0.0.11/schema 8を検証していません。

<a id="v009--schema-7"></a>

### 過去のv0.0.9 / schema 7

**2026-09-17 JSTに最終localとnative CI結果を確認しました。**
検査した最終local sourceは公開済み実装
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)と一致します。
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)の
両native jobは合格し、実logでjob statusだけでなく完全一致SHAと以下の件数・検査を確認しました。
v0.0.7/v0.0.8の結果をv0.0.9証拠に流用しません。

| 環境 | Command | テスト | テスト所要時間 |
|---|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | **274合格、既存warning 1件** | **248.29秒** |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | **274合格、既存warning 1件** | **482.21秒** |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | **274合格、既存warning 1件** | **374.33秒** |

全3環境の最終runで**Ruff、strict mypy（source 15ファイル）、真のcore-only/hook-only導入検査、
non-root productionの全smoke**にも合格しました。
日本語tokenizer、API HTTP、worker CLI、MCPの**`2026-07-28`・`2025-11-25`両mode**、
recall-hookの**`session_start`・`task_switch`・`after_compaction`**が対象です。
suiteは抽出済み共有`native_client.py`、hook入力検証/失敗処理、pack全体のbyte計算、
index欠落時の`index_incomplete`/不完全coverage、予算の各結果、
scope/失効の黙ったfilter、明示token認証失敗を検査し、Native/MCP semanticsを維持します。
所要時間は観測値であり、性能benchmarkではありません。

共有`NativeSettings`の`httpx.URL`によるorigin検証もsuiteの対象で、
transport前に制御文字や不正IDNAを拒否します。

自動Docker **`adapter-extras-check`** targetは真のcore-only導入/extra未導入、
続いて**MCPなし**のhook-only導入を検査し、HTTP失敗時の明示JSONも対象とします。
container scriptはlocal Apple Containerとnative Docker両architectureでこのtargetをbuildします。
真の導入/HTTP失敗の検査は、上記production smokeとともに**全3環境で合格**しました。
M0〜M3/MVP/本番/性能/品質/DR/完全消去の全gateは未完了です。

最終v0.0.9 docs commit
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)も、
[CI run 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689)で
各native architecture **274テスト**に合格しました。
最終docs runは上記実装runの所要時間とは別です。
どちらのv0.0.9 runもv0.0.10 atomic captureを検証していません。

<a id="v008--schema-7"></a>

### 過去のv0.0.8 / schema 7

実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)を
**2026-09-17 JST**に検証しました。

| 環境 | Command | テスト | テスト所要時間 |
|---|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | 214合格、既存warning 1件 | 240.83秒 |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | 214合格、既存warning 1件 | 415.46秒 |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | 214合格、既存warning 1件 | 389.47秒 |

3環境で**Ruff、strict mypy（source 13ファイル）、non-root productionの
日本語tokenizer、API HTTP、worker CLI、MCP stdio smoke**も合格しました。
MCPはSDK 2.2.0と独立したraw wire fixtureで`2026-07-28`・`2025-11-25`の両方を検査しています。
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)の
両jobは上記SHAと完全一致し、実logで件数とsmokeを確認しました。
所要時間はテストの観測値であり、性能benchmarkではありません。
残るwarningは既存のanyio BlockingPortal aliasに関するものです。

別途Apple Containerでfreshなcore-only install
（`uv sync --frozen --no-dev --no-editable`）も検証しました。
`mcp`・`httpx`なしでNative APIをimportでき、`pg-agmemory mcp`は
extra未導入の明示的な診断付きで終了しました。この追加検査はローカルだけであり、
別のDocker CI検査の主張ではありません。
M0〜M3/MVP/本番/性能/品質/DR/完全消去の全受入gateは未完了です。
以下の過去のv0.0.7結果をMCP検証として扱ってはいけません。

その後のbilingual docs commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)は、
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509)で
**各native architecture（`linux/amd64`・`linux/arm64`）214テスト**に合格しました。
v0.0.8のdocs runであり、上記実装runの所要時間とは別です。
どちらのrunもv0.0.9やrecall hookを検証していません。

### 過去のv0.0.7 / schema 7

**v0.0.7/schema 7だけ**の実装commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)について、
**2026-09-17 JST**に最終結果を確認しました。

| 環境 | Command | テスト | テスト所要時間 |
|---|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | 144合格、既存warning 2件 | 206.54秒 |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | 144合格、既存warning 2件 | 386.32秒 |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | 144合格、既存warning 2件 | 331.59秒 |

3環境の最終実行で**Ruff、strict mypy（source 12ファイル）、
non-root productionの日本語tokenizer、API HTTP、実CLI workerという全3種のsmoke**も合格しました。
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)
の両native Docker jobは上記SHAと完全一致し、job状態だけでなく実logで件数と各検査を確認しました。
所要時間はテスト実行の観測値であり、性能benchmarkではありません。
最終bilingual docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)も、
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899)で
両native jobが合格しました。これは過去の最終docs CIであり、上記実装runの所要時間とは別です。
どちらのrunもv0.0.8/v0.0.9を検証していません。

既定/opt-in lexical動作、日本語/ASCII処理、正確な65,536文字のindex化と65,537文字の拒否、
lazy load/fresh Linux初期化guard、時間/RLSとindex不完全/予算動作、
子table DELETE権限なしのcanonical purgeを検査しています。
実際の初回backfill後のschema 6へのrollback、一部reindex失敗時の旧projection維持、
reindexのscope/worker flag拒否、job/graph/effect/checkpointとlegacy冪等性の維持も対象です。
正確な過去照会検査にはVMのwall-clock値でなくserver記録のassertion時刻を使います。

tokenizer smokeは`東京都` → `東京` / `都`を検査し、
`Production Japanese tokenizer smoke passed`をlogに出します。API smokeはHTTP healthを検査します。
worker smokeでは使い捨てprincipalとruntime専用資格情報で、
実際の`pg-agmemory worker --subject ... --once`をnon-root production image内で実行し、
`{"outcome":"idle"}`を確認して`Production worker smoke passed`をlogに記録しました。
CI step名は`Test containers and smoke-test production API and worker`です。
これらのsmokeは同梱tokenizer動作、API liveness、worker起動/idle実行を確認するもので、
end-to-end recall品質やqueue済みpublicationの正しさを認定しません。
publication動作は別途test suiteの検査対象です。

v0.0.7最終lockは従来のpackage-feed registryを維持しました。全**36 package**のversion、
依存metadata、artifact hashはテスト済みPyPI解決lockとbyte単位で同一と確認しました。
v6との差分はJanome 0.5.0の追加とprojectのv0.0.7へのversion更新だけで、
無関係なupgradeやregistry移行はありません。
native CIは最終retained-registry lockからbuildしました。
このpackage件数/比較は過去のもので、v0.0.8 MCP lockについての主張ではありません。

それ以前のv5証拠は[ADR 0005](adr/0005-relational-graph-jp.md)に過去のものとして残し、
v6の決定/証拠は[ADR 0006](adr/0006-durable-jobs-jp.md)に保持します。
検査はM0/M1/M2/M3全体の完了、性能/品質の測定、外部exactly-once、MVP、本番readiness、
backup/DR、完全消去の適格性を示すものではありません。

## 今後の実装対象

自動enqueue/自然言語抽出/synthesis、LLM/provider処理、global multi-tenant scheduling/
公平性/cost pool、別のworking snapshot/compaction、
自動embedding/provider連携、ANN/HNSW、vector/hybrid retrievalの品質/性能認定、AGE、SQL/PGQ、
provider receipt検証、vendor固有harness連携と実行/recovery、
別assertion間のsupersession/fact調停、remote MCP HTTP/SSE/OAuth/delegation、
同期/TypeScript SDK、postgresem連携はありません。
v0.0.12非同期Python SDKはlocalと両native architectureで検証済みです。
vendor-neutral hookはどのhostも登録せず、適格性を認定しません。
local stdio MCP、opt-in lexical分割、明示structured job、上限付きSQL graph oracle、
typed checkpoint envelopeだけで、
計画上の二時点・graph・provenance・削除architectureが完了したとは扱いません。

選択理由は[ADR 0001](adr/0001-initial-slice-jp.md)、
安全な管理は[運用](operations/README-jp.md)、
container検証workflowは[貢献方法](../CONTRIBUTING-jp.md)を参照してください。
