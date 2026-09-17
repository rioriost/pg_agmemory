# pg_agmemory

[English](README.md) | [実装プラン](docs/PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**MITライセンスのPostgreSQLベースAgent Memory Serviceです。**
公開リポジトリは[`rioriost/pg_agmemory`](https://github.com/rioriost/pg_agmemory)、
ローカルcheckoutディレクトリ・Pythonパッケージ・サービス名は`pg_agmemory`です。
以下のコマンドはこのローカルcheckoutから実行してください。

**現在の上限付き実装はv0.0.19/schema 10の所有job照会とpaginationです。
v0.0.19実装はlocalと両native architectureで検証済みです。
以下の検証済みv0.0.18以前の結果は過去の証拠であり、v0.0.19の証拠ではありません。
M0/M1/M2/M3全体の完了、MVP完成版、本番リリースではありません。**
認証付き観測保存、同一scopeのepisodeを根拠とする明示的な構造化記憶、
PostgreSQL全文検索、根拠表示、トランザクション内の冪等性、
稼働DBからの同期purgeを実装しています。tenant/scope権限をサービスと
PostgreSQL RLSの両方で強制し、変更はcommit後に応答します。
assertion revisionはサーバー管理のsystem-time履歴とrevision固有の根拠を維持します。
typed checkpointは新branchへのrestore envelopeとdurableなtool-effect台帳を提供します。
明示entityとrevision付きrelation assertionは上限付きの読取り専用SQL graph探索を提供します。
固定principal workerによる明示queue型の構造化publicationと、
opt-inのversion付き日本語lexical search、Native API上のlocal・固定identity MCP adapterも提供します。
任意のvendor-neutral implicit recall hookは読取り専用を維持します。
既存v0.0.10 captureはepisode一つと明示要求された構造化publication job一つを原子的にcommitします。
assertion公開済み、自動capture、自然言語synthesis、検索品質の測定済み改善ではありません。

別assertion間のsupersession/fact調停、provider receipt検証、vendor固有harness adapter、
自動enqueue/抽出、汎用multi-tenant scheduling、自動embedding生成、ANN/HNSW、
AGE/SQL/PGQ、remote MCP HTTP/SSE/OAuth/delegation、同期/TypeScript SDK、
postgresem連携は今後の実装対象です。
性能・記憶品質・災害復旧・完全消去の受入は未測定または未認定です。
利用前に[現在の契約と制限](docs/STATUS-jp.md)を確認してください。

## Owned-job query and pagination

**v0.0.19/schema 10はlocalと両native architectureで検証済みです。**
認証付きread-only `POST /v1/jobs/query`は`Idempotency-Key`不要です。
closedな`QueryJobs`は重複しない`scope_ids`（UUID 1〜32件）、
重複しない`states`（最大5件、`[]`/省略で全状態）、strict整数の`max_items`
（1〜100、既定20）、`before: JobCursor | None`（既定null）を受け付けます。
`JobState`は`pending`、`running`、`succeeded`、`failed`、`cancelled`の5状態を維持します。
closedな`JobCursor`はtimezone付き`created_at` timestampとUUID `job_id`が必須です。
不正field/値は**422 `invalid_request`**です。owner/principal/tenant、kind、
payload query、offset、watch、`after` selectorは受け付けません。

選択は**現在callerが所有するjobだけ**で、tenant、要求scope、
現在のRLS/source/削除可視性、任意state filterを適用します。
既知ID指定GETより意図的に狭く、他principal所有の同一scopeの読取り可能jobは
引き続きGET可能ですが、scope-admin権限があってもこのqueryでは発見できません。
未知/読取り不可scopeは結果へ寄与しません。
一致なしは**200**と`jobs: []`、`next_cursor: null`で、非公開理由やtotalを返しません。

順序は`created_at DESC, id DESC`で、`before`はexclusiveな
`(created_at, id) < (before.created_at, before.job_id)`境界です。
`max_items + 1`件を取得し、overflowがある場合だけ**最後に返すjob**から
`next_cursor`を作ります。lookahead行からは作らず、返すjobは最大`max_items`件です。
署名なしcursorは透明な位置情報であり、権限、receipt、snapshot、cache、retention objectではありません。
そのjobが存在する必要もなく、偽造/古い/削除済みcursorでも現在認可された所有行だけが対象です。

成功は型付き**200 `JobPage`**で、`ListedJob`の`jobs`、`next_cursor`、
現在の`Consistency`（`access_epoch`、`deletion_epoch`）を返します。
SDKもresponse modelの100-job上限を検証します。
`ListedJob`は既存の完全な`JobDetail`へ`scope_id`を加え、GET形式は変えません。
参照、state/時刻、安全なerror、retry parent、元のresult revision 1を返し、
保存payload、evidence quote、lease token、intent digestは返しません。
選択された各page itemは`Jobs.get`の参照件数/result生存検査を通し、
実際に不正な選択jobがあれば既存404/409で**page全体を失敗**させ、黙ったskipや部分成功にしません。

pageごとに現在権限とtenant response-drain barrierを使い、page間snapshotではありません。
state遷移でも元の`created_at`は維持しますが、state/アクセス/削除変更でpage間の構成は変わり得ます。
古いcursorより新しいjobには、`before`なしで明示的に再開します。
SDK `query_jobs(QueryJobs) -> JobPage`はasyncで`mutation=False`を使い、
通常の**request 256 KiB / response 2 MiB**上限とread-only `outcome_unknown: false`を維持します。
自動pagination/retry、claim、取消、worker/provider呼出し、state変更はありません。
Native/SDKは**27 resource method**となり、MCPの4 toolとhookは不変でjob toolはありません。
[契約](docs/STATUS-jp.md#owned-job-query-and-pagination)、
[paging例](docs/operations/README-jp.md#owned-job-query-and-pagination)、
[ADR 0019](docs/adr/0019-job-query-jp.md)を参照してください。

## Checkpoint-head lookup

**既存checkpoint head契約を維持します。v0.0.19はlocalと両native architectureで検証済みです。**
認証付き`POST /v1/checkpoints/head`はread-onlyで、`Idempotency-Key`は不要です。
closedな`CheckpointBranch` bodyは必須UUIDの`scope_id`、`run_id`、`branch_id`だけです。
現在読取り可能な正確なscope/run/branchだけを選び、identity override、
`expected_head`、harness selector、`as_of`、branch横断のlatest選択、履歴一覧は受け付けません。

既存APIのtenant session-lock/drain barrierを使い、SQLは`FOR UPDATE`なしの`SELECT`です。
branch/run作成、state変更、audit event、idempotency receiptはありません。
不在、非公開、異なるscope、別tenant、失効していない空branchは、
ID/内容や空成功でなく汎用**404 `not_found`**です。
sourceの`forget`は対象branchを`invalidated=true`とし、canonical checkpoint/参照payloadを削除しますが、
opaqueな`head_id`と`sequence`、`memory.object` anchor、tombstoneは保持します。
照会は失効を先に確認し、読取り可能な失効branchには**409 `checkpoint_invalidated`**を返し、
保持head IDは返しません。アクセス権の取消後はbranchを404で隠します。
**ancestor、sibling、default branchへのfallbackはありません**。

成功は正確な**200 `CheckpointEnvelope`**で、既存load/envelope検査を再利用します。
保存state/HMAC/参照/epochと現在epoch、`tool_effects`、`requires_reconciliation`、
`untracked_effects`、`resume_allowed`、`automatic_reexecution: false`を返します。
runの`effects_invalidated`、HMAC/state/参照不正、非公開headは既存の拒否動作を維持します。
読んだscope/run/branch/sequenceが選択pointerと一致しなければ409です。

checkpoint IDを失っても安定したbranch IDから現在headを取得できますが、
latestは**照会時点のpointer**であり、watch、予約、後続headの保証ではありません。
`CreateCheckpoint.expected_head`は必須のcaller CASを維持し、読取り後に別writerが進められます。
head照会は**結果不明writeのcommit証明ではありません**。
その変更の元key/bodyを再送して元receiptを取得し、その後headを確認します。
新head/keyによる自動retry、restore、effect遷移、実行、承認をしてはいけません。
checkpoint ID指定GETは既存検査下で生存ancestorも読めますがlatestではなく、
restore先forkのbranch headは独立しています。

SDK `get_checkpoint_head(CheckpointBranch) -> CheckpointEnvelope`はasync/read-only
（`mutation=False`）で、通常の**request 256 KiB / response 2 MiB**上限、
sanitized error、`outcome_unknown: false`、自動retryなしを維持します。
所有job照会による現在のNative/SDK resourceは**27 method**で、MCPの**4 tool**とhookは不変でcheckpoint toolはありません。
v18からのSQL migration、依存/provider/artifact固定値の変更はありません。
[契約](docs/STATUS-jp.md#checkpoint-head-lookup)、
[照会例と更新](docs/operations/README-jp.md#checkpoint-head-lookup)、
[ADR 0018](docs/adr/0018-checkpoint-head-jp.md)を参照してください。

## Exact structured recall filters

**既存recall filter契約を維持します。v0.0.19はlocalと両native architectureで検証済みです。**
既存Native `POST /v1/recall`、型付きSDK `recall`、MCP `memory_recall`は
`Recall.filters: RecallFilters | None = None`を受け付けます。
未知fieldを拒否するnested modelのfieldはnullableな`kind`（`"episode"`または`"assertion"`）、
`subject`（`ShortText`、既存の前後空白除去後1〜256文字）、
`predicate`（`^[a-z][a-z0-9_]{0,63}$`）だけで、既定はnullです。
未知field、不正値、`kind: "episode"`とnon-null subject/predicateの組合せは
**422 `invalid_request`**です。省略、null、`{}`、全field nullなら
全3 retrieval modeの既存結果を維持します。

non-null fieldを**AND**で結合します。subject/predicateはassertionを意味し、
`kind: "assertion"`はrelation assertionを含み、`"episode"`は全assertionを除外します。
subject/predicateは通常のcontract trim後、**大文字小文字を区別する`C` collationの完全一致**です。
部分文字列/FTS一致、Unicode正規化、fuzzy一致、alias、entity解決はありません。
空queryのlexical recallはfilter後の候補をbrowseします。
filterは非空queryの通常lexical一致を迂回しません。

filterは**共有materialized候補の内部で、lexical/vector/hybridのranking、
coverage、required参照の適格性より前**に適用します。
rankingとlexical/vector欠落coverageはfilter後の適格候補集合を使います。
`coverage.jobs_pending`は意図的に要求scope単位のsignalであり、構造化job一致ではありません。
固定`as_of`/`known_at`、要求scope、現在RLS、根拠可視性、削除gateを維持します。
required参照は別契約のlexical専用keyword迂回とrequest順を維持しますが、
filterにも一致が必要です。一つでも不一致ならIDや部分contextを返さず
request全体を**404 `not_found`**にします。既存byte予算/error動作は不変です。

**Native/SDK resource method 27、MCP tool 4**を維持し、safe error codeの追加はありません。
hook入力は`filters`を拒否し、内部既定`None`で信頼する起動時境界を維持します。
v18からのSQL migration、依存/provider/index変更、永続priority、cacheは追加しません。
filterはcallerの選択条件であり、信頼する指示や検証済み真実ではありません。
[契約](docs/STATUS-jp.md#exact-structured-recall-filters)、
[例と更新](docs/operations/README-jp.md#exact-structured-recall-filters)、
[ADR 0017](docs/adr/0017-recall-filters-jp.md)を参照してください。

## Required-context recall

**既存required-context契約を維持し、v0.0.19はlocalと両native architectureで検証済みです。**
既存Native `POST /v1/recall`、SDK `recall`、MCP `memory_recall`は
`Recall.required_memory_refs`を受け付けます。既定は省略または`[]`で、
最大**16**件の`MemoryReference`です。UUIDと正確なrevision **1〜1000**を選び、
revision省略時は**最新でなく1**です。revision違いでも同一IDは重複不可で、
件数は`max_items`以内です。空でない参照には`retrieval_mode: "lexical"`が必須です。
explicit/implicit両recallに対応し、implicitの**2,000-byte**上限など既存制限を維持します。

required itemは通常recallと同じ現在認可済みepisode/assertion候補、
要求scope、固定した`as_of`/`known_at`を使います。
keyword一致とranking cutoffは迂回しますが、**ACL/scope/時間/構造化recall filterは迂回しません**。
不在、読取り不可、purge済み、異なるkind、時間条件外などの正確なrevisionは、
一つでもあればrequest全体を汎用**404 `not_found`**で拒否します。
latest/revision fallbackや欠落参照の開示はありません。

完全なrequired prefixを**request順**で先頭に置き、ID重複を除いた通常のlexical順位の
optional itemを続けます。全itemが`max_items`とcompact `ContextPack`全体のUTF-8 byte予算に含まれます。
required itemが一つでも丸ごと収まらなければ**422 `budget_exhausted`**で部分contextを返しません。
optional itemは従来のgreedyなitem単位除外と`coverage.truncated`を維持します。
参照省略/空配列は選択した構造化filter内で既存の順序、pack、全3 retrieval modeのsemanticsを維持します。

`simple-v1`と`ja-janome-0.5.0-v1`の両方で正確な参照を使え、
日本語projection欠落時もcanonical itemを対象にできますが、
`lexical_incomplete`は欠落coverageを引き続き示します。
index修復や必須制約の自動検出ではありません。caller指定参照は**policy権限や検証済み承認ではなく**、
memoryは根拠であって信頼する指示ではありません。
**Native/SDK resource method 27、MCP tool 4**を維持します。
hookはこの入力fieldを拒否し、参照なしのrecallを構築するためhost pinningは追加しません。
write、idempotency、永続priority、cache、推論、provider呼出し、schema migrationは追加しません。
[契約](docs/STATUS-jp.md#required-context-recall)、
[例と更新](docs/operations/README-jp.md#required-context-recall)、
[ADR 0016](docs/adr/0016-required-context-jp.md)を参照してください。

## Explicit job cancellation

**既存job取消契約を維持し、v0.0.19はlocalと両native architectureで検証済みです。**
`POST /v1/jobs/{job_id}/cancel`はNative認証、callerが保持する`Idempotency-Key`、
`expected_state`（`pending`または`running`）とstrict整数`expected_attempt`
（0〜5、runningは1以上）だけを要求します。
最初にjobを読み、tenant access epochや外部tool版ではなく**state/attempt CAS**を明示選択します。
認証中のownerで、現在のscope read/write権限と有効・可視の入力を持つ場合だけ取消できます。
同一scopeの別readerは`admin` permissionがあっても取消できません。

HTTP **200**でcommit済み`JobReceipt`を返し、GETでterminal `cancelled`を確認します。
期限切れleaseを含むpending/running jobを取消できます。
同じjobのidentity、attempt、source/intent参照、retry parent、作成時刻を保持し、
保存job payload、lease、errorを消去し、resultはありません。
既存tenant session barrier下でstate変更、`job_cancelled` audit、idempotency receiptを
原子的にcommitし、access/deletion epochは進めません。
非同期の取消job、worker kill、provider中断、`forget`ではありません。
source episode、dedup anchor、worker準備memory、WAL、backupは取消で消去されません。

取消が先なら古いworker publicationを拒否し、publicationが先なら
`409 job_cancel_conflict`となり公開済みresultは撤回しません。
応答喪失時は**同じkey/body**か新しいGETで照合し、CAS値を盲目的に更新しません。
failed専用retryはcancelled jobを拒否し、enqueue/captureのdedupは復活させずcancelled jobを返します。
source purgeは引き続きjobを失効させ、取消replayを拒否します。
SDKは`cancel_job`を維持し、所有job照会による現在のsurfaceは**27 method**です。
MCPの4 toolとread-only hookは変更しません。
[完全な契約](docs/STATUS-jp.md#explicit-job-cancellation)、
[運用とschema 10 migration](docs/operations/README-jp.md#explicit-job-cancellation)、
[ADR 0015](docs/adr/0015-job-cancellation-jp.md)を参照してください。

## Runtime readiness

**既存readiness契約を維持し、v0.0.19/schema 10はlocalと両native architectureで検証済みです。**
`GET /healthz`は起動成功後のprocess livenessを維持し、DBを呼ばず
`{"status":"ok"}`を返します。public・認証不要の`GET /readyz`は
HTTP **200**と正確な`{"status":"ready"}`、または想定内の失敗時に
**503**と正確な`{"status":"not_ready"}`を返します。
両readiness応答は`Cache-Control: no-store`と生成UUIDの`X-Request-ID`を持ち、
private詳細を含めません。渡された認証headerは無視し、tenant/principalは選びません。

受け付けた検査ごとに新しい**runtime** DB接続を開き、role/schema/extension契約だけを読みます。
特権runtime roleやアプリtable所有権がないこと、厳密なmigration履歴1〜10、
`public`内の`vector` 0.8.6を確認します。validation sessionは明示read-onlyです。
memory本文、tenant lock、audit/epoch/job/receipt書込み、migration、
retry、cache、background検査はありません。
API app/processごとに同時検査は一つで、並行probeは別接続を開かず即503です。
**active検査予算5.0秒は厳密なwall-clock SLAではなく**、cancel/connection cleanupで遅延が増え得ます。

readinessはある時点のsignalであり、**書込み可能性、認可全体、JWT/tokenizer/provider正常性、
処理能力、本番readinessの証明ではありません**。SELECT-only DBも合格し得ます。
resource routeはprobeを呼ばず、新たな永続readiness gateも取得しません。既存Native認可は維持します。
readinessはtraffic制御に使い、依存障害でrestart stormを起こすlivenessとして使わないでください。
失敗/復旧thresholdとperimeter制限/rate limitを設定します。
Kubernetes、Compose、Docker `HEALTHCHECK`設定は追加しません。
readinessはSDK/MCP/hook probe methodを追加せず、所有job照会による現在のNative/SDK memory surfaceは27です。
[完全な契約](docs/STATUS-jp.md#runtime-readiness)、
[probe運用](docs/operations/README-jp.md#runtime-readiness)、
[ADR 0014](docs/adr/0014-runtime-readiness-jp.md)を参照してください。

## Scope-access administration

**既存scope-access契約を維持し、v0.0.19はlocalと両native architectureで検証済みです。**
特権`pg-agmemory scope-access get|set|revoke` CLIで、既存の同一tenantに属する
scope/principal UUIDのmembershipを管理します。
`PGAG_ADMIN_DATABASE_URL`、RLS bypassと適切なSQL権限を持つ管理者、
明示的に選んだIDが必要です。JWT、runtime資格情報、取得本文由来のidentityは使いません。
**HTTP/MCP/SDK管理methodはありません**。

`get`は現在membershipとtenant全体の`access_epoch`を返します。
`set`はpermission全置換と明示的な未来のaware expiryまたは`--no-expiry`を要求し、
`revoke`はmembershipを削除します。両方`--expected-access-epoch`が必須です。
古いCASは要求状態と既に一致しても失敗します。実変更はmembership更新、
epoch増加、特権専用audit eventを原子的に行い、read/no-opはどちらも行いません。
自然な期限切れはepoch変更でも処理中応答のdrainでもありません。

CLIは共有**tenant session advisory lock**をcommitとJSON stdout flushまで保持します。
同じ版のAPI clientとonlineで協調できますが、migrationは引き続き停止/drainが必要です。
変更結果を失った場合、`get`と特権auditで確認してから新CAS操作を明示承認します。
盲目的retryやidempotency receiptはありません。配信済みcontextは撤回できず、
purge済みdataも復活しません。auditは改ざん耐性の証明やDR solutionではありません。

`009_scope_access.sql`はschema 9でscope-access auditを導入し、schema 10も維持します。
PostgreSQL 18.6/pgvector 0.8.6固定imageと依存版は維持します。
現在stageは`m2-job-query`です。[完全な契約](docs/STATUS-jp.md#scope-access-administration)、
[get → set → revoke例とmigration](docs/operations/README-jp.md#scope-access-administration)、
[ADR 0013](docs/adr/0013-scope-access-jp.md)を参照してください。

## Python SDK

**SDKはread-only所有job照会を追加し27 methodです。v0.0.19はlocalと両native architectureで検証済みです。** 対応checkoutから導入します。

```bash
python -m pip install '.[sdk]'
```

任意の`pg-agmemory[sdk]` extraが追加するのは**httpx==0.28.1だけ**です。
FastAPI、psycopg、Janomeを含む既存core distributionのままであり、
独立した軽量SDK packageでも、PyPI公開済みという主張でもありません。
PEP 561の`py.typed` markerを追加します。`mcp`/`hook`が提供するHTTPXでも
import依存を満たし、HTTPXがなければ固定の導入案内`ImportError`となります。

memory/tool入力でなく、信頼する設定から明示引数を渡してください。
以下の環境変数名は例であり、**SDKは自動で読みません**。
scopeは事前にprovision・認可済みである必要があります。
読取り専用の例で、private memory、token、入力、raw error応答を出力しません。

```python
import asyncio
import os
from uuid import UUID

from pg_agmemory.models import Recall
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError


async def main() -> None:
    request = Recall(
        scope_ids=[UUID(os.environ["PGAG_SDK_SCOPE_ID"])],
        query="synthetic fixture",
        purpose="read-only SDK example",
    )
    try:
        async with AsyncMemoryClient(
            os.environ["PGAG_SDK_API_URL"], os.environ["PGAG_SDK_API_TOKEN"]
        ) as memory:
            result = await memory.recall(request)
            if not result.coverage.retrieval_complete:
                print("Recall coverage is incomplete; do not infer absence.")
    except MemoryClientError as exc:
        if exc.error.outcome_unknown:
            print("Outcome unknown: retain the existing key and body; reconcile.")
        else:
            print("Memory request failed; no automatic retry was attempted.")


asyncio.run(main())
```

`NativeSettings`の設定errorはsanitized `ValueError`であり、`MemoryClientError`ではありません。
request model構築では別にPydantic `ValidationError`が発生し得ます。
そこに含まれるprivate入力詳細をlogに出さないでください。
SDK call時の検証はsanitized SDK errorを返します。

`AsyncMemoryClient`は固定HTTPS originまたはloopback HTTP originとtokenの形を検査し、
実際のtoken認証はserverが行います。context entryで所有HTTP clientを作成し、
認証付きcapabilitiesの**service 0.0.19 / API v1 / schema 10**完全一致を要求します。
一つのcontext内だけで使い、再entryや自動retryはありません。
未完了taskはawaitするかcancel後にawaitして、**contextをexitする前に完了を確認**してください。
client closeはrequestのschedule/cancel管理でもDB rollbackでもありません。
exitで閉じるのは接続であり、**保存memoryは消去しません**。
scopeはserver ACLを狭めるだけです。返されたmemoryは根拠であって、
信頼する指示や現在の事実の保証ではありません。

明示job取消、embedding、job、graph、checkpoint、tool effectを含む全27 public memory resource
methodを対象とし、CLI管理やworker実行は対象外です。
すべての変更にはcallerが保持するkeyword-onlyの`idempotency_key`が必須です。
処理中のcancelを含む変更結果不明時は**同じkeyとbody**で照合し、
新keyへの交換やrollbackの推測をしてはいけません。
[型付きmethod/error契約](docs/STATUS-jp.md#python-sdk)、
[運用と更新](docs/operations/README-jp.md#python-sdk-operations)、
[ADR 0012](docs/adr/0012-python-sdk-jp.md)を参照してください。SDKは管理操作でなくHTTP専用です。

## コンテナ検証

ローカル開発ではDocker Desktopではなく**Apple Container**を使います。
[Apple Container](https://github.com/apple/container)をインストールし、実行します。

```bash
container system start
./scripts/test-containers.sh
```

runnerには`jq`も導入してください。使い捨てsmoke設定を含め、
scriptは**Apple ContainerとDockerの両方**で`jq`を必須とします。

このスクリプトは固定したPython依存関係をビルドし、Ruff、mypy、unitテスト、
PostgreSQL integrationテスト後にproduction APIのHTTP healthを確認し、
**non-root production image**内で実際の`pg-agmemory worker --subject ... --once`を実行します。
worker smokeは使い捨てのprovision済みprincipalとruntime専用資格情報を使い、
`{"outcome":"idle"}`を検査して`Production worker smoke passed`をlogに出します。
non-root runtime imageのtokenizer smokeは`東京都` → `東京` / `都`も検査し、
成功時に`Production Japanese tokenizer smoke passed`を出力します。
end-to-end recallや分割品質の評価ではありません。
v0.0.8 runnerはnon-root production image内で実`pg-agmemory mcp` childも実行します。
固定tokenとprovision済みscopeでloopback Native APIへ接続し、4 toolの列挙とrecallを
modern `2026-07-28`・legacy `2025-11-25`の**両mode**で検査します。
日本語/API/worker smokeも維持しています。
専用の使い捨てPostgreSQLコンテナを利用し、
自分が作ったコンテナ・ネットワークだけを片付けます。
既存のDBやコンテナは変更しません。Python/PostgreSQL/uvのimage版とdigestは固定しています。

GitHub Actionsではnative **linux/amd64**・**linux/arm64** runner上のDockerで
同じスクリプトを実行します。
過去のv0.0.8 step名は`Test containers and smoke-test production API, worker, and MCP`です。

```bash
./scripts/test-containers.sh docker
```

商用モデルのAPI keyや外部memory DBは不要です。
初回はコンテナimageとPython依存packageを取得できる必要があります。
**v0.0.19実装はlocalと両native architectureで検証済みです。**
Apple Containerの`./scripts/test-containers.sh`は**exit 0**で、
**663合格、既存warning 1件、366.14秒（6:06）**でした。実装
[`e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf`](https://github.com/rioriost/pg_agmemory/commit/e4e9d60d090003c77ab9ce4afdc6d6136e9ff7bf)は
完全一致SHAの[CI 35242118110](https://github.com/rioriost/pg_agmemory/actions/runs/35242118110)に合格しました。
native実logで各**663合格、warning 1件**、**amd64 638.06秒 / arm64 586.58秒**を確認しました。
Ruff、strict mypy **source 19 + SDK consumer 1ファイル**、真のcore/hook/sdk-only導入、
所有job照会を含む全non-root production smokeが全3環境で合格しました。
これは実装結果であり、後続最終docs CI結果ではありません。
[検証済み証拠](docs/STATUS-jp.md#v0019--schema-10)を参照してください。

**過去のv0.0.18実装はlocalと両native architectureで検証済みです。** Apple Containerの
`./scripts/test-containers.sh`は**exit 0**で、**630合格、既存warning 1件、
369.39秒（6:09）**でした。Ruff、strict mypy **source 19 + SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、checkpoint head照会を含む全non-root production smokeが全3環境で合格しました。
実装
[`4accd38408a8384b4376f6250d953bb2fa480ec8`](https://github.com/rioriost/pg_agmemory/commit/4accd38408a8384b4376f6250d953bb2fa480ec8)
は[CI 35235315016](https://github.com/rioriost/pg_agmemory/actions/runs/35235315016)で
両native Docker architectureとも合格しました。実logで各**630合格、warning 1件**、
**amd64 663.91秒 / arm64 544.14秒**を確認しました。
別の最終v0.0.18 docs
[`3f01faf56563202c40a00d73cee72830022220b1`](https://github.com/rioriost/pg_agmemory/commit/3f01faf56563202c40a00d73cee72830022220b1)は
[CI 35237907862](https://github.com/rioriost/pg_agmemory/actions/runs/35237907862)に合格しました。
各native architecture **630テスト、warning 1件**、**amd64 644.25秒 / arm64 570.20秒**で、
Ruff、mypy **19 + 1**、optional導入、全production smokeが合格しました。
docs所要時間は実装CI 35235315016とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](docs/STATUS-jp.md#v0018--schema-10)を参照してください。

**過去のv0.0.17実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`22a64461475d4cd5666a842dbbe6afe83ab36894`](https://github.com/rioriost/pg_agmemory/commit/22a64461475d4cd5666a842dbbe6afe83ab36894)は
完全一致SHAの[CI 35230044140](https://github.com/rioriost/pg_agmemory/actions/runs/35230044140)に合格しました。
各環境**604テスト、既存warning 1件**で、local Apple Containerは**321.56秒（5:21）**、
native Docker amd64は**638.66秒**、arm64は**544.33秒**でした。
Ruff、strict mypy **source 19ファイル + strict SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、構造化recall filterを含む全non-root production smokeが全3環境で合格しました。
別の最終v0.0.17 docs
[`5226c81fae7a50a5668109478d5d9f23fc4d7761`](https://github.com/rioriost/pg_agmemory/commit/5226c81fae7a50a5668109478d5d9f23fc4d7761)は
[CI 35232139680](https://github.com/rioriost/pg_agmemory/actions/runs/35232139680)に合格しました。
native amd64は**465.50秒**、arm64は**557.08秒**で、各**604テスト、warning 1件**、
Ruff、strict mypy **source 19 + consumer 1ファイル**、optional導入、全production smokeが合格しました。
docs所要時間は実装CI 35230044140とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](docs/STATUS-jp.md#v0017--schema-10)を参照してください。

**過去のv0.0.16実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57`](https://github.com/rioriost/pg_agmemory/commit/b6f0cf5a4f2525e9064667aa0e33e1b28ac57b57)は
完全一致SHAの[CI 35224189967](https://github.com/rioriost/pg_agmemory/actions/runs/35224189967)に合格しました。
各環境**566テスト、既存warning 1件**で、local Apple Containerは**298.15秒（4:58）**、
native Docker amd64は**601.73秒**、arm64は**565.34秒**でした。
Ruff、strict mypy **source 19ファイル + strict SDK consumer 1ファイル**、
真のcore/hook/sdk-only導入、required-context recallを含む全non-root production smokeが全3環境で合格しました。
別の最終v0.0.16 docs
[`520990d95718b17ae93a7d5259d600e379991df2`](https://github.com/rioriost/pg_agmemory/commit/520990d95718b17ae93a7d5259d600e379991df2)は
[CI 35226313891](https://github.com/rioriost/pg_agmemory/actions/runs/35226313891)に合格しました。
native amd64は**476.15秒**、arm64は**478.08秒**で、各**566テスト、warning 1件**、
全検査、optional導入、production smokeが合格しました。
docs所要時間は実装CI 35224189967とは別です。
両runともv0.0.19の適格性確認ではありません。[過去の証拠](docs/STATUS-jp.md#v0016--schema-10)を参照してください。

**過去のv0.0.15実装の適格性確認はlocalと両native architectureで合格しました。**
実装
[`9cf325f0d7aebe9c8dd6d72c41ba1510840f1460`](https://github.com/rioriost/pg_agmemory/commit/9cf325f0d7aebe9c8dd6d72c41ba1510840f1460)は
完全一致SHAの[CI 35216770999](https://github.com/rioriost/pg_agmemory/actions/runs/35216770999)に合格しました。
各環境**535テスト、既存warning 1件**で、local Apple Containerは**298.29秒（4:58）**、
native Docker amd64は**406.88秒**、arm64は**490.57秒**でした。
native実logでRuff、strict mypy **source 19ファイル + SDK consumer 1ファイル**、
真のoptional導入、明示job取消を含む全production smokeを確認し、localも同じ検査に合格しました。
所要時間は性能benchmarkではありません。別の最終v0.0.15 docs
[`9d34d5329c9db580e7de0459b743511235ad6fb8`](https://github.com/rioriost/pg_agmemory/commit/9d34d5329c9db580e7de0459b743511235ad6fb8)は
[CI 35218254940](https://github.com/rioriost/pg_agmemory/actions/runs/35218254940)に合格しました。
native実logで各**535テスト、warning 1件**、**amd64 605.83秒 / arm64 473.57秒**、
Ruff、strict mypy **source 19 + consumer 1ファイル**、optional導入、全smokeを確認しています。
docs所要時間は実装CI 35216770999とは別です。両v0.0.15 runともv0.0.19の検証ではありません。
[過去の証拠](docs/STATUS-jp.md#v0015--schema-10)を参照してください。

**過去のv0.0.14最終localとnative結果を2026-09-17 JSTに検証しました。**
Apple Containerとnative Docker amd64/arm64で各**495テスト、既存warning 1件**、
Ruff、strict mypy（**source 19ファイル + SDK consumer 1ファイル**）、
真のcore/hook/sdk-only導入、readiness障害/liveness/復旧を含むnon-root production全smokeが合格しました。
内訳は**既存464 + readiness unit 16 + integration 15テスト（新規31）**で、schema 9は不変です。
実装
[`71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277`](https://github.com/rioriost/pg_agmemory/commit/71bd2c59e1fb65e0ae2a6ba45c09d0013fb08277)
は[CI 35201615965](https://github.com/rioriost/pg_agmemory/actions/runs/35201615965)に合格しました。
native実logで完全一致SHA、件数、全検査/smokeを確認しています。
所要時間は**local 295.67秒 / amd64 385.41秒 / arm64 470.16秒**であり、性能benchmarkではありません。
[検証証拠](docs/STATUS-jp.md#v0014--schema-9)を参照してください。
別の最終v0.0.14 docs
[d4b24f6](https://github.com/rioriost/pg_agmemory/commit/d4b24f60a2fbdbba05ebaebd5a8a731b9bf74f68)は
[CI 35202931424](https://github.com/rioriost/pg_agmemory/actions/runs/35202931424)に合格しました。
実logで各native architecture 495テスト/warning 1件と全検査/smokeを確認し、
**amd64 319.51秒 / arm64 503.56秒**でした。両v0.0.14 runともv0.0.15の検証ではありません。

**過去のv0.0.13最終localとnative結果を2026-09-17 JSTに検証しました。**
Apple Containerとnative Docker amd64/arm64で各**464テスト、既存warning 1件**、
Ruff、strict mypy（**source 19ファイル + SDK consumer 1ファイル**）、
真のcore/hook/sdk-only導入検査、scope-accessを含むnon-root production全smokeが合格しました。
内訳は**既存426 + scope-admin unit 22 + integration 16テスト（新規38）**です。
schema 8→9 rollback/retryと以前のmigrationも合格しました。
実装
[`fa5dc8055f0db885879e5a109e15d5fb148b4413`](https://github.com/rioriost/pg_agmemory/commit/fa5dc8055f0db885879e5a109e15d5fb148b4413)
は[CI 35196930448](https://github.com/rioriost/pg_agmemory/actions/runs/35196930448)に合格しました。
native実logで完全一致SHA、件数、全検査/smokeを確認しています。
所要時間は**local 297.52秒 / amd64 539.86秒 / arm64 460.73秒**であり、性能benchmarkではありません。
[検証証拠](docs/STATUS-jp.md#v0013--schema-9)を参照してください。
別の最終v0.0.13 docs
[185f433](https://github.com/rioriost/pg_agmemory/commit/185f433aa49479810b8955f1bec2e856f2715f7b)は
[CI 35198499967](https://github.com/rioriost/pg_agmemory/actions/runs/35198499967)に合格しました。
実logで各native architecture 464テスト/warning 1件と全検査/smokeを確認し、
**amd64 499.25秒 / arm64 454.37秒**でした。両v0.0.13 runともv0.0.14の検証ではありません。

**過去のv0.0.12最終localとnative結果を2026-09-17 JSTに検証しました。**
Apple Containerとnative Docker amd64/arm64で各**426テスト、既存warning 1件**、
Ruff、strict mypy（**source 18ファイル**）、別のstrict型付きconsumer（**1ファイル**）、
真のcore/hook/sdk wheel導入検査、同梱`py.typed`、
新SDK lifecycleを含むnon-root production全smokeが合格しました。
既存345テストに**SDK unit 76 + integration 5テスト（新規81）**を加えた結果です。
実装
[88e1206](https://github.com/rioriost/pg_agmemory/commit/88e1206311e72b94b17d76e0d0a8b8c2e9a3bd6f)は
[CI 35193004945](https://github.com/rioriost/pg_agmemory/actions/runs/35193004945)に合格しました。
native実logで完全一致SHA、件数、全検査を確認しています。
所要時間は**local 292.76秒 / amd64 484.79秒 / arm64 472.49秒**であり、性能benchmarkではありません。
[検証証拠](docs/STATUS-jp.md#v0012--schema-8)を参照してください。
最終v0.0.12 docs [0e00abd](https://github.com/rioriost/pg_agmemory/commit/0e00abdae930dcc1e2d2015fbf5931a74701fe7d)も
[CI 35194141510](https://github.com/rioriost/pg_agmemory/actions/runs/35194141510)に合格しました。
実logで各native architecture 426テストと全検査/smokeを確認し、
**amd64 488.20秒 / arm64 454.88秒**でした。上記実装所要時間やv0.0.13検証とは別のdocs runです。

**過去のv0.0.11/schema 8を2026-09-17 JSTに検証しました。** Apple Containerとnative Docker
amd64/arm64は各**345テスト、既存warning 1件**、Ruff、strict mypy（**source 17ファイル**）、
core-only/hook-only導入検査、non-root productionの全smokeに合格しました。
episode/assertion vectorのexact/hybrid検索とpurgeも対象です。
テスト所要時間は**local 283.44秒、amd64 404.40秒、arm64 433.46秒**であり、性能benchmarkではありません。
実装[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)は
[CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403)に合格しました。
[検証証拠](docs/STATUS-jp.md#v0011--schema-8)を参照してください。
最終v0.0.11 docs
[dccd5cb](https://github.com/rioriost/pg_agmemory/commit/dccd5cb5571873515aace8621ce4adb3de250d3a)も
[CI 35190495385](https://github.com/rioriost/pg_agmemory/actions/runs/35190495385)に合格し、
両native実logで**345テスト、warning 1件**、Ruff、mypy **source 17ファイル**、
導入検査、全smokeを確認しました。**amd64 506.38秒 / arm64 460.18秒**です。
これは上記実装runとは別のdocs run観測値であり、どちらもv0.0.12の検証ではありません。

採用DB profileはprebuilt
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`です。
artifact検査で**amd64/arm64両image**のPostgreSQL **18.6-1.pgdg12+2**、
native ELF、`vector.control` **0.8.6**を確認しました。
PostgreSQL版は18.6のままですが、**base digestが異なる新しい上流DB image/profile**であり、
旧library PostgreSQL imageを変更していないという意味ではありません。
このprofileに新DB Dockerfile、source build、host APT workflowはありません。
artifact検証は上記のapplication/migration/CI合格証拠とは別です。
過去のv0.0.11ではPython依存はproject版metadata以外変更せず、
raw parameter-bound vector castにpgvector Python packageは不要でした。

**過去のv0.0.10/schema 7の最終localとnative結果を2026-09-17 JSTに確認しました。**
Apple Containerとnative Docker amd64/arm64は各**304テスト、既存warning 1件**に合格しました。
**Ruff、strict mypy（source 16ファイル）、真のcore-only/hook-only導入検査、
non-root productionの全smoke**も全3環境で合格しました。
日本語/API/worker、MCP両時代、hook全3 eventに加え、
新しい原子的な**capture → 実worker → recall → replay → purge** workflowが対象です。

| 環境 | テスト所要時間 |
|---|---|
| ローカルApple Container | **275.53秒** |
| Docker、native `linux/amd64` | **467.75秒** |
| Docker、native `linux/arm64` | **434.40秒** |

最終local sourceは公開済み実装
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f)と一致します。
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)は
両native jobとも合格し、実logでjob statusだけでなく完全一致SHA、件数、所要時間、検査を確認しました。
所要時間は性能benchmarkではありません。
[v0.0.10証拠](docs/STATUS-jp.md#v0010--schema-7)を参照してください。
最終v0.0.10 docs
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)も、
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760)で
**各native architecture 304テスト**に合格しました。
このdocs runは上記実装runの所要時間とは別であり、両runともv0.0.11/schema 8を検証していません。

**過去のv0.0.9/schema 7の最終結果を2026-09-17 JSTに確認しました。**
全3環境で**274テスト、既存warning 1件**、Ruff、strict mypy（**source 15ファイル**）、
真のcore-only/hook-only導入検査、non-root productionの日本語/API/worker、
MCP **`2026-07-28`・`2025-11-25`**、
hook **`session_start`・`task_switch`・`after_compaction`**の全smokeに合格しました。

| 環境 | テスト所要時間 |
|---|---|
| Local Apple Container | **248.29秒** |
| Docker、native `linux/amd64` | **482.21秒** |
| Docker、native `linux/arm64` | **374.33秒** |

最終local sourceは公開済み実装
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)と一致します。
[CI run 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)の
両native jobの実logで、job statusだけでなく完全一致SHAと上記全検査を確認しました。
所要時間はテスト観測値であり、性能benchmarkではありません。
範囲は[v0.0.9検証証拠](docs/STATUS-jp.md#v009--schema-7)を参照してください。
これらの検査で元のmilestoneや受入gateが完了したとは扱いません。
最終v0.0.9 docs commit
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)も、
[CI run 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689)で
両native architecture各**274テスト**に合格しました。
どちらのv0.0.9 runもv0.0.10 atomic captureの検証ではありません。

**過去のv0.0.8/schema 7:** 実装
[3b84a22](https://github.com/rioriost/pg_agmemory/commit/3b84a22c4dac56ffdc9a6276f558fb5268774fd2)は、
Apple Containerとnative Docker amd64/arm64で各**214テスト**（既存warning 1件）、
Ruff、strict mypy（source 13ファイル）、全production smokeに合格しました。
上記MCPの両protocol modeも合格しています。
[CI run 35176469004](https://github.com/rioriost/pg_agmemory/actions/runs/35176469004)と
[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。
その後のbilingual docs commit
[0b0f695](https://github.com/rioriost/pg_agmemory/commit/0b0f695d8df63db0f70ddd1a277c497166698ac2)は、
[CI run 35177260509](https://github.com/rioriost/pg_agmemory/actions/runs/35177260509)で
**両native architecture各214テスト**に合格しました。
どちらのv0.0.8 runもv0.0.9 hookや共有client抽出の検証ではありません。

**過去のv0.0.7/schema 7限定の証拠です。** 実装commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)は、
Apple Containerとnative Dockerの**linux/amd64**・**linux/arm64**で、
それぞれ**144テスト**（既存warning 2件）、Ruff、strict mypy（source 12ファイル）、
non-root productionの日本語tokenizer、API HTTP、実CLI worker `--once` idle実行という
全3種のsmokeが合格しました。最終結果は**2026-09-17 JST**に確認しました。
両CI jobは同じ完全一致SHAで実行し、実logで全検査を確認しています。
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)と、
[検証証拠](docs/STATUS-jp.md#検証証拠)を参照してください。
最終bilingual docs commit
[aaea6ef](https://github.com/rioriost/pg_agmemory/commit/aaea6ef7df747e6632b0d132b36fb7cfa85193f2)も
[CI run 35174122899](https://github.com/rioriost/pg_agmemory/actions/runs/35174122899)で
両native jobが合格しました。これらの過去runはv0.0.8/v0.0.9の結果ではありません。

**過去のv0.0.7**最終lockは既存package-feed registryを維持し、全**36 package**のversion、依存metadata、
artifact hashはテスト済みPyPI解決lockとbyte単位で同一です。v6との差分はJanome 0.5.0の
追加とprojectのv0.0.7へのversion更新だけで、無関係なupgradeやregistry移行はありません。
native CIはこのretained-registry lockからbuildしました。v0.0.8 MCP extraには追加依存があり、
旧package件数とlock比較は新lockを説明するものではありません。

## APIの起動

上記の固定上流pgvector DB profile（PostgreSQL 18.6、pgvector 0.8.6）を使用します。
以下のアプリケーションコマンドは
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
migration/provision/rebuildは管理操作であり、public endpointとして公開してはいけません。
起動時にsuperuser、RLS bypass、table ownerのruntime接続を拒否します。

**v0.0.19はschema 10を維持し、migrationを追加しません。** 既存schema 10 DBには
[application-only更新](docs/operations/README-jp.md#schema-10-application-only-upgrade)を使います。
古いschemaにはv0.0.15で導入した`010_job_cancellation.sql`が引き続き必要です。
[既存migration sequence](docs/operations/README-jp.md#schema-10-job-cancellation-upgrade)に従ってください。
古いschemaにはdurable admin audit用の`009_scope_access.sql`を含む既存migration sequenceも必要です。
古いDBには引き続きv0.0.11の`008_pgvector.sql` migrationが必要です。
PostgreSQLは**`public`内の`vector` 0.8.6**を必要とし、
migrationは版/schemaが異なる既存extensionを拒否します。prebuilt profileは対応extensionを提供します。
API、worker、`migrate`はschema 10記録済みでもこれを検査します。
必要なmigrationの前に**旧版・新版の全API、worker、adapter、hook起動、
SDK caller、管理commandを停止/drain**し、backupと現在の削除/ACL記録を保全してoffline migrationを行います。
古いDBにはmigration 007のlexical backfillを含む既存migrationも適用します。
**embedding backfillや自動embedding再構築はありません**。
対応するv0.0.19 processだけを再起動し、API/workerは厳密な履歴
`[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]`とschema `public`内のextension `vector` 0.8.6を要求します。
古いschemaのprocessとschema 10のrolling混在互換性はありません。
旧imageは停止を維持してください。v0.0.1にはschema互換性guardがありません。
rolling共存やdowngradeは非対応です。
[現在のschema 10手順](docs/operations/README-jp.md#schema-10-application-only-upgrade)に従ってください。

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
上限付きruntime検査は[`/readyz`とその制限](docs/STATUS-jp.md#runtime-readiness)を参照してください。

## Pgvector retrieval foundation

**v0.0.11/schema 8で実装・検証済みです。**
実験的なprovider非依存の数学的基盤であり、意味検索の品質認定ではありません。
従来のlexical既定は変えず、vectorは明示的に渡します。

- Native読取り専用`POST /v1/embedding-inputs`は既存Explain
  `{memory_id, revision}`を受け取り（revision既定はlatestでなく**1**）、idempotency keyは不要です。
  認可済みcanonical text、UTF-8 SHA-256の`input_digest`、
  `input_format: "memory-content-v1"`を返します。
  episode textは正規化content、assertion textはrelation display valueも含む正確な
  `subject / predicate: value`で、ID/時刻を含めません。
- Native `POST /v1/embeddings`は`Idempotency-Key`、正確なdigest、
  caller宣言model名/revision、**768個の有限JSON数値**を要求します。
  空間はcosine / `l2-f32-v1`固定で、serverはfloat64で正規化した後pgvector float32へ変換します。
  boolean、数値文字列、zero vector、切詰め、次元変換は認めません。read/write scopeはcanonical parentから導出します。
- canonical revision/model namespace当たり不変vector一つです。
  同じ正規化float32値/digestは別HTTP keyでも重複抑止し、異なるvectorはconflictします。
  置換には新model revisionが必要です。**canonical revision当たりmodel versionは最大8件**で、
  上限時も既存duplicateは許可します。
  保存idempotency resultは`{memory_id, revision}`だけで、平文digest/model名/vectorは含めません。
  現在読取り可能なcanonical inputとprojectionから応答metadataを再構成し、
  HMAC/opaque anchorは保持します。parentが生存していても管理者がprojectionだけを削除した場合、
  replayは再構築せず**409 `embedding_unavailable`**を返します。
- recallに`retrieval_mode: "lexical" | "vector" | "hybrid"`とinline `vector_query`を追加します。
  lexicalはvectorを拒否し、vectorは空text query、hybridは非空textを要求します。
  queryの黙った無視や広いfallbackはありません。vectorは現在の認可/時間条件を満たす
  materialized候補のexact cosine、hybridは決定的lexical/vector rankを**RRF k=60**で統合します。
  省略`as_of`/`known_at`はselection/coverage前に一度だけ確定し、明示時刻は変更しません。
  実際のdistance/scoreの同順位はUUIDで解決しますが、
  任意float結果/rankingの全CPU間bit単位一致を保証しません。
  ANN/HNSWやneighborによるscope拡大はありません。
- 可視・適格projectionの欠落は`vector_incomplete` coverageで明示し、非認可itemをcoverageへ含めません。
  ranking metadataはconfidence/真実ではありません。JSON全体のUTF-8 context-pack予算は維持します。
  parent purgeはvector/digest/model metadataへcascadeし、別memory identityやprovenance vertexを作りません。
  独立model registryもありません。

既定response fieldを追加し、`MemoryItem.retrieval: null`、
`RecallResult.retrieval_mode: "lexical"`、`embedding_model: null`、
`coverage.vector_incomplete: false`となります。
non-nullの`retrieval`は`exact_cosine`/`rrf-60`のranking evidenceとnullableなrank/distance/fusion fieldを持ちます。
既定lexical semanticsは維持しますが、**HTTP JSON形式のbyte単位互換を意味しません**。
capabilitiesに`retrieval_modes: ["lexical", "vector", "hybrid"]`と
`default_retrieval_mode: "lexical"`を追加します。

MCPは**4 tool**を維持し、生成Recall引数へvector/hybrid fieldを追加しますが、
embedding input/upload toolは追加しません。hookは**読取り専用・lexical専用**です。
Native応答の非lexical `retrieval_mode`、non-nullの`embedding_model`/item `retrieval`、
trueの`coverage.vector_incomplete`を拒否します。
capture、Observe、job、workerはembeddingを生成しません。
canonical inputをlogへ出したり、明示承認なく第三者へ送ったりしないでください。
[合成basis vector例](docs/operations/README-jp.md#synthetic-vector-example)は外部modelを呼ばず、
本番embedding modelでもありません。[予定契約](docs/STATUS-jp.md#pgvector-exact-and-hybrid-retrieval)と
[ADR 0011](docs/adr/0011-pgvector-retrieval-jp.md)を参照してください。

## Atomic structured capture

**既存capture契約はv0.0.11でも検証済みです。v0.0.10結果は過去の証拠です。**
Native `POST /v1/captures`は`Idempotency-Key`と
`{episode: <変更しないObserve>, memory: <一つの構造化intent>}`を要求します。
memory intentは`subject`、`predicate`、`value`、一つの`evidence_quote`、
`explicit_intent: true`、任意のtimezone付き/nullの`valid_from`/`valid_to`を持ちます。
**scope、根拠ID、identity fieldは受け付けず**、scopeと一つのepisode根拠IDをtransaction内で導出します。
1〜4,096文字のquoteは正規化済みepisodeに原文として含まれる必要があります。
既存Rememberのfield上限/valid-time規則とreported・未校正semanticsを維持します。

**HTTP 201**は`{memory_id: <episode UUID>, revision: 1,
synthesis_job_id: <job UUID>}`を返します。
episodeと一つの`structured_remember` / `structured-remember-v1` jobのcommitを確認するだけで、
**assertion publicationの完了ではありません**。
既存jobやterminal jobを再利用する場合があり、**201は新規/pending jobを保証しません**。
返却値は過去参照であり、現在のstatusはGETを正とします。
`GET /v1/jobs/{synthesis_job_id}`と既存の固定subject workerで後続publicationを扱います。
scope当たりactive job 100件、job当たり5試行、lease、epoch検査、publication fencingを維持します。
`POST /v1/observe`は変更せず`synthesis_job_id: null`で、自動jobはありません。
明示`/v1/jobs`と同期`/v1/remember`も変更しません。

同じkey/正規化bodyは同じepisode/job組を返し、同じkeyでbodyを変えるとconflictします。
同じepisode/intent/principalなら新HTTP keyでも組を重複抑止します。
別の明示intentは生存episode上に別jobを作れ、別の認可済みprincipalは独立したjob identity/所有権を持ちます。
episode/projection、job data/identity、idempotency、auditを一つのtransactionで扱います。
transaction失敗は新規変更をrollbackし、以前から独立して存在したepisodeは削除しません。

replayは**両IDの現在のACL/削除**を確認します。episode/job/resultの依存purgeで組は
`404`になり得て、新keyでもpurge済みjob identityを再作成できません。
failed jobは既存job-retry操作で明示retryし、capture replayはretry childでなく元jobを返します。
明示retry childを作成した後も変わりません。
生存source上の新しい別intentを永久禁止するものではありません。
IDは過去参照であり、fresh stateはGETで取得します。

captureは**MCP toolではなく**、recall hookも自動captureしません。
capture自体はembedding生成、LLM/provider呼出し、intent抽出、自動synthesis、意味的真実の認定を行いません。
Native HTTP response-drainとhost消去の境界は変更しません。
[全差分契約](docs/STATUS-jp.md#atomic-structured-capture)、
[operator向けcurl例](docs/operations/README-jp.md#atomic-structured-captureの運用)、
[ADR 0010](docs/adr/0010-atomic-capture-jp.md)を参照してください。

## Local stdio MCP

所有job照会はNative/SDK専用であり、MCP job toolは追加しません。

checkpoint head照会はNative/SDK専用で、この4 bindingへcheckpoint toolは追加しません。

`memory_recall`は型付き`filters`も受け付けます。ranking前の完全一致選択と
required参照との共通条件は[filter契約](#exact-structured-recall-filters)に従います。
toolやsafe error codeは追加しません。

`memory_recall`はNative request wrapperで追加の`required_memory_refs`を受け付けます。
required prefixの予算失敗はsafe `budget_exhausted`のtool errorであり、
空の成功結果ではありません。4-tool surfaceは不変です。

通常の`pg-agmemory` package導入では任意の`mcp`/`hook`/`sdk`依存を**導入しません**。
MCPには`pg-agmemory[mcp]`、recall-hookには`pg-agmemory[hook]`、
Python SDKには`pg-agmemory[sdk]`を選択してください。
repositoryのDocker test/runtime imageは意図的に3 extraすべてを含みますが、
**base packageの既定ではありません**。

任意の`pg-agmemory[mcp]` package extraを導入するか、v0.0.19のtest/runtime両stageに
`mcp`・`hook`・`sdk`を含むrepository imageを使用します。
MCP extraは公式**mcp 2.2.0** SDKと**httpx 0.28.1**を固定しています。
checkoutでは`uv sync --frozen --extra mcp`でlock済み環境を準備できます。
信頼するlocal MCP hostから次を起動します。

```bash
pg-agmemory mcp
```

**`PGAG_MCP_API_URL`**と**`PGAG_MCP_API_TOKEN`**は信頼する起動設定から渡し、
tool引数やcommitするhost設定には含めないでください。URLはHTTPS originまたはloopback HTTP
originに限定し、credential/path/query/fragmentは禁止です。tokenは**Native API audience用**で、
Native APIが検査します。MCP caller identityを転送するものではありません。
起動時に認証付きcapabilitiesを照会し、API `v1`、service `0.0.19`、schema `10`の一致を要求します。
設定/認証/versionの失敗はsecretを出さず非zero終了します。
固定tokenの更新には再起動が必要です。`--subject`と`--once`は拒否します。

Native Pydantic model由来のschemaを持つ次の4 toolだけを公開します。

| Tool | 引数 | Native操作 |
|---|---|---|
| `memory_recall` | `{request: <Recall body>}` | `POST /v1/recall` |
| `memory_remember` | `{request: <Remember body>, idempotency_key: "..."}` | `POST /v1/remember` |
| `memory_explain` | `{request: <Explain body>}` | `POST /v1/explain` |
| `memory_forget` | `{request: <Forget body>, idempotency_key: "..."}` | `POST /v1/forget` |

両mutation toolはforget previewも含め、caller管理の**1〜256文字のvisible ASCII key**
（空白不可）を必須とします。keyのtrim/書換えはせず、正確に256文字は許可、257文字は拒否します。
Native forgetは従来どおり**preview/purgeともHTTP 202**です。
結果不明時は**stdio再起動をまたいでも同じkeyとbodyを再利用**
してください。自動retryやkey生成はありません。mutationのtransport障害、5xx、不正応答は
`outcome_unknown`であり、**rollbackではありません**。成功は
`structuredContent: {result: <Native result>, error: null}`、失敗は`isError: true`と
`{result: null, error: {code, retryable, outcome_unknown, native_status, request_id}}`
を返します。短いtextには根拠を重複収録しません。

rememberは明示structured publication専用です。episode単体保存にはNative `/v1/observe`、
明示episode/jobの原子性にはNative `/v1/captures`を使い、どちらもMCP toolではありません。
UTF-8 byte予算（model tokenではない）、日本語recallのopt-in、現在のNative認証/ACL、
削除検査、過去のidempotency参照は維持します。MCP session/request IDはmemory run IDでも
HTTP idempotency keyでもありません。

**信頼identityごとにadapterを一つ使い、共有やnetwork公開はしないでください。**
callごとのheader/identity/URL上書き、remote MCP HTTP/SSE、OAuth、delegationはありません。
adapterは信頼するlocal Native API clientです。Native response-drain barrierはadapterへのHTTP
配信で終了し、**stdio・host UI・LLM contextまでの原子的barrierではありません**。
buffer済み/配信済みcontextは回収できません。forget/ACL変更後はhostがcached contextを破棄する
必要があり、MCP削除通知やadapterのresponse/semantic cacheはありません。
protocol検証の制限を含む[全契約](docs/STATUS-jp.md#local-stdio-mcp)、
[起動と復旧](docs/operations/README-jp.md#local-stdio-mcpの運用)、
[ADR 0008](docs/adr/0008-local-mcp-jp.md)を参照してください。
v0.0.11はmodern `2026-07-28`とlegacy `2025-11-25`の両protocol契約と全MCP semanticsを維持します。
過去のv0.0.9 regressionと両protocol smokeはlocalとnative Docker両architectureで合格しました。
過去v0.0.10/v0.0.11と最終local/native v0.0.12検査も合格しています。

## Implicit recall hook

所有job照会用のhook fieldやjob操作は追加しません。

checkpoint head照会用のhook fieldやcheckpoint操作は追加しません。

hook入力は`filters`を未知fieldとして拒否し、内部`Recall.filters`は既定`None`です。
信頼する起動時設定をeventごとに上書きする機能は追加しません。

hook入力は`required_memory_refs`を拒否し、内部`Recall`は`[]`のためhost pinningはありません。
optional-onlyの成功時の空理由`empty_reason: "budget_exhausted"`は、
required-context Native/SDK/MCPの`422 budget_exhausted` errorとは別です。

**既存の読取り専用・lexical専用hookはv0.0.11でも検証済みです。**
過去のv0.0.9 local/native検査は合格しています。
任意の`pg-agmemory[hook]`を導入します（checkoutでは`uv sync --frozen --extra hook`）。
固定依存は**httpx 0.28.1だけで、MCP SDKではありません**。
Docker test/runtimeには`mcp`・`hook`・`sdk`を含めます。
`pg-agmemory recall-hook`は一回実行のvendor-neutralなlocal Native HTTP clientです。
hostへの自動登録はなく、Copilot・Claude・Codex連携を**主張しません**。
外部model呼出しやDB資格情報は不要です。

stdinへUTF-8 JSON documentを一つ送り、**stdinを閉じます**。

```json
{"event":"session_start","query":""}
```

`event`は`session_start`、`task_switch`、`after_compaction`のいずれかです。
`query`は必須、最大4,096 Unicode文字で、空なら設定scope内をcanonical browseします。
取得意図はJSONの`query`だけから渡し、`event`はquery本文ではなくlifecycle名です。
stdin上限は32,768 byteです。identity、scope、purpose、mode、予算、URL、header、tool、
時刻を含む追加fieldは禁止です。event textはアクセス権を付与しません。
`--subject`/`--once`は拒否します。

prompt/event dataでなく**信頼する起動環境だけ**から必須の`PGAG_HOOK_API_URL`、
必須の固定Native audience用`PGAG_HOOK_API_TOKEN`、
必須の`PGAG_HOOK_SCOPE_IDS`（重複しないUUID 1〜32件のJSON配列）とrecall設定を渡します。
既定はpurpose `implicit_context`、予算**2,000 UTF-8 byte（model tokenではない）**
（64〜2,000）、max items 20（1〜20）、search profile `simple-v1`
（日本語`ja-janome-0.5.0-v1`はopt-in）、timeout 2.0秒（有限の0.1〜20秒）です。
purposeは1〜256文字です。[全環境設定表](docs/STATUS-jp.md#implicit-recall-hook)を参照してください。
HTTPS originまたはloopback HTTPだけを許可し、URL credential/application path/query/fragmentを
禁止しますが、root `/`は許可します。URL/token/scope-ID設定はすべて必須で、
URL未設定は既定宛先でなく`invalid_hook_configuration`になります。
共有`NativeSettings`は`httpx.URL`も使い、transport前に制御文字や不正IDNAを拒否します。
redirect/proxy環境を無効化し、TLSを検証します。

呼出しごとに新しく認証付きcapabilitiesで厳密な
**service `0.0.19` / API `v1` / schema `10`**を検査し、
`mode: "implicit"`とNativeの現在時刻defaultでrecallをPOSTします。
deadlineは**両HTTP処理の合計**に適用し、process起動・stdin入力/待機・出力は含みません。
LLM latency SLOではありません。harness側には別のsubprocess timeoutが必要です。
共有の上限付きNative HTTP transportでMCPの不変条件を維持します。
request/response上限は256 KiB/2 MiBです。`context_pack.byte_count`は**本文だけでなく、
metadata/citationを含むcontext pack全体のcompact JSON serialize結果**を数えます。
UTF-8、`ensure_ascii=False`、`separators=(",", ":")`を使い、
hookがその件数と設定byte予算を検査します。返却item数は`max_items`以下、
返却search profileは設定と一致しなければなりません。不一致は失敗させ、広いfallbackは行いません。
書込み、capture、queue、LLM provider、cache、retry、idempotency keyは追加しません。

検証対象のhook runtime結果はstdoutへJSON結果一つと改行を出し、stderr診断はsanitizedです。
成功は`{status: "ok", event, result: <完全なNative RecallResult>, error: null}`、
失敗は`{status: "error", event: <検証済みeventまたはnull>, result: null,
error: {code, retryable, outcome_unknown: false, native_status, request_id}}`です。
Native status/request UUIDはnullableです。
[確定error code表](docs/STATUS-jp.md#上限結果失敗処理)を参照してください。
終了値**0**はNativeの正当な`not_found`/`budget_exhausted`/`index_incomplete`空結果も含みます。
**2**は不正設定/入力、**1**はNative/network/version/protocol障害です。
**取得失敗を空の成功に変換してはいけません。**
空pack自体にも約192 byteが必要で、有効な指定予算64でも
明示的なNative **422 `budget_too_small` / hook終了値1**になり得ます。
一方、packは収まるが候補が収まらない`budget_exhausted`は**200 / 終了値0**です。
index欠落による`index_incomplete`も不完全coverage付きの200/終了値0になり得ます。
recallは未認可scopeや失効membershipを黙ってfilterし、許可済みsubsetまたは
itemなし/`not_found`を返します。**scopeの存在を示す404ではありません**。
token認証失敗は引き続き明示的な**401 / hook終了値1**です。Native semanticsの変更ではありません。
**起動方法のerrorは例外です。** CLI flag拒否や`hook` extra未導入は、
argparseのstderr診断と**終了値2で、JSON envelopeを返しません**。
hostのtimeout/killでもenvelopeを返せない場合があるため、harnessは非JSON/不正出力も扱います。

hostはerror/coverageを表示し、停止かmemoryなしの継続かを明示判断してください。
取得memoryは**信頼できない根拠であり、指示やpolicyではありません**。
queryをlogへ記録したりerrorへコピーしたりしてはいけません。
Native session advisory barrierは信頼するhookへのHTTP配信で終了し、
buffer/stdout/host contextまで原子的には続きません。
回収、削除通知、host消去証明はありません。
forget/ACL変更後は前のcontextを破棄し、新しくhookを呼び出します。
hookは権限を拡大しません。実行可能な
[vendor-neutral Python harness例](docs/operations/README-jp.md#vendor-neutral-python-harness例)と
[ADR 0009](docs/adr/0009-implicit-recall-hook-jp.md)を参照してください。

## Opt-inの日本語lexical recall

`POST /v1/recall`の既定は`search_profile: "simple-v1"`で、
PostgreSQL `simple`/`plainto_tsquery`/`ts_rank_cd`を維持します。
日本語scriptのsurface/wakati分割には`"ja-janome-0.5.0-v1"`を明示選択し、
応答はそのprofileを返します。Janome **0.5.0**と、Janome追加語を含む同梱
**mecab-ipadic-2.7.0-20070801**を使います。対象の日本語script連続部分だけを分割し、
ASCII識別子/英語はsegmenterをそのまま通過します。Unicode/全半角正規化、原形化/stemming、
同義語処理、分割品質の保証はありません。漢字scriptの処理は中国語文字にも及びますが、
中国語recallの適格性は未確認です。

Janomeは日本語script連続部分がある場合だけlazy importします。
入力prefix cacheは無効（`max_cached_word_len=0`）で、保持するcacheは同梱辞書resourceだけです。
test/runtime container buildは**静的Janome package bytecodeだけ**を逐次事前compileし、
user textやmemory index/cacheは作りません。この事前compileのないcold host installationでは
初期化時のpeakが大幅に増える可能性があり、配置時のresource sizingは適格性未確認です。

`tokenizer_id: "utf8-bytes-v1"`は引き続きcontext byte予算用で、日本語token数ではありません。
scope、時間、根拠、ACL、削除、byte上限を維持します。
認可済み・時間条件内の日本語projectionが欠けると、黙ってfallbackせず
`coverage.lexical_incomplete: true`と`coverage.retrieval_complete: false`を返します。
projection欠落があり候補もなければ`empty_reason: "index_incomplete"`となり、
既存の予算不足とは区別します。lexical modeの空queryはindex不完全flagがあってもcanonical itemをbrowseします。
flagはprojection coverageであり、query関連性やqueue状態ではありません。

書込みはepisodeと全assertion revisionを原子的にindex化し、typed relation/job publicationも
含みます。lexical runtime権限は`SELECT`/`INSERT`だけで、`UPDATE`や直接`DELETE`は
付与しません。canonical parent purgeが同じbarrierでFK cascadeにより派生行を消し、
子tableのDELETE権限は不要です。別memoryとしては扱いません。
offline `pg-agmemory reindex-lexical`は`PGAG_ADMIN_DATABASE_URL`で
**選択DBの全tenant**を再構築します。`--subject`はscope filterではなく拒否し、
`--once`もworker専用です。API/workerを停止/drainし、backup、再構築後に
lexical projectionを再構築して、対応するv0.0.19 processだけを再起動します。
embeddingの投入/再構築は行いません。
自動修復worker、外部model/provider、fileベースのmemory indexはありません。
[契約](docs/STATUS-jp.md#日本語lexical-profile)、
[保守](docs/operations/README-jp.md#lexical-profileとreindexの運用)、
[ADR 0007](docs/adr/0007-japanese-fts-jp.md)を参照してください。

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
ownerはstate/attempt CASで[pending/running作業を取消](#explicit-job-cancellation)できます。
cancelled jobはterminalでretryできず、active-job上限と`jobs_pending`の対象外ですが、
同intentのenqueue/captureは引き続きそのjobへdedupします。
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
`GET /v1/checkpoints/{checkpoint_id}`は現在の認可と検査を通した指定IDのenvelopeを返し、現headとは限りません。
[head照会](#checkpoint-head-lookup)は作成やrestoreをせず、
正確なscope/run/branchから同じ検査済みenvelopeを取得します。
checkpointは`recall`や`explain`には出しません。

`POST /v1/checkpoints/restore`はharness/versionの完全一致と新しいbranchを要求し、
元branchを巻き戻しません。run台帳のdispatched effectはfork作成と原子的にunknownになります。
GET/head/restoreはsnapshot後に追加されたものも含め、runの生存effect全件を統合します。
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
| [日本語lexical FTSの決定](docs/adr/0007-japanese-fts-jp.md) | [Japanese lexical FTS decisions](docs/adr/0007-japanese-fts.md) |
| [Local MCPの決定](docs/adr/0008-local-mcp-jp.md) | [Local MCP decisions](docs/adr/0008-local-mcp.md) |
| [Implicit recall hookの決定](docs/adr/0009-implicit-recall-hook-jp.md) | [Implicit recall hook decisions](docs/adr/0009-implicit-recall-hook.md) |
| [Atomic structured captureの決定](docs/adr/0010-atomic-capture-jp.md) | [Atomic structured capture decisions](docs/adr/0010-atomic-capture.md) |
| [Pgvector retrievalの決定](docs/adr/0011-pgvector-retrieval-jp.md) | [Pgvector retrieval decisions](docs/adr/0011-pgvector-retrieval.md) |
| [Python SDKの決定](docs/adr/0012-python-sdk-jp.md) | [Python SDK decisions](docs/adr/0012-python-sdk.md) |
| [Scope-access管理](docs/adr/0013-scope-access-jp.md) | [Scope-access administration](docs/adr/0013-scope-access.md) |
| [Runtime readiness](docs/adr/0014-runtime-readiness-jp.md) | [Runtime readiness](docs/adr/0014-runtime-readiness.md) |
| [Job取消](docs/adr/0015-job-cancellation-jp.md) | [Job cancellation](docs/adr/0015-job-cancellation.md) |
| [Required-context recall](docs/adr/0016-required-context-jp.md) | [Required-context recall](docs/adr/0016-required-context.md) |
| [構造化recallの完全一致filter](docs/adr/0017-recall-filters-jp.md) | [Exact structured recall filters](docs/adr/0017-recall-filters.md) |
| [Checkpoint headの照会](docs/adr/0018-checkpoint-head-jp.md) | [Checkpoint-head lookup](docs/adr/0018-checkpoint-head.md) |
| [所有jobの照会とpagination](docs/adr/0019-job-query-jp.md) | [Owned-job query and pagination](docs/adr/0019-job-query.md) |
| [運用](docs/operations/README-jp.md) | [Operations](docs/operations/README.md) |
| [貢献方法](CONTRIBUTING-jp.md) | [Contributing](CONTRIBUTING.md) |

## 依存ライセンス

project codeは[MIT](LICENSE)ですが、**依存関係すべてがMITではありません**。
[Janome 0.5.0はApache-2.0](https://github.com/mocobeta/janome/blob/0.5.0/LICENSE.txt)です。
同梱mecab-ipadic辞書/統計dataには別の
[IPADIC copyright/license notice（NAIST/ICOT）](https://github.com/mocobeta/janome/blob/0.5.0/NOTICE.txt)
が適用され、[Janomeの辞書追加語](https://github.com/mocobeta/janome/blob/0.5.0/ipadic/Noun.proper.csv.patch)
もこの固定releaseに含まれます。package/image再配布時は上流のlicense/notice fileを保持してください。
同梱辞書はcode依存でありuser memoryではなく、memory projectionの保存先はPostgreSQLだけです。
LLM weightsやbenchmark用会話履歴は同梱していません。
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6)はprojectのMITでなく
**PostgreSQL License**です。DB image再配布時は上流licenseを保持してください。
stable release日は**2026-07-29**で、検証済み公式tag commitは
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`です
（[固定changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)）。
採用artifactは固定prebuilt上流imageであり、local source buildではありません。
両native最終imageは`/usr/share/doc/pgvector/LICENSE`を保持し、
固定上流licenseとbyte単位一致を検証済みです。SHA-256は
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`です。
