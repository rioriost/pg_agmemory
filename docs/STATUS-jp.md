# 現在の契約と制限

[English](STATUS.md) | [プロジェクトREADME](../README-jp.md) | [実装プラン](PG_AGMEMORY_IMPLEMENTATION_PLAN-jp.md)

**v0.0.4/schema 4のtool-effect ledgerを実装済みで、ローカルとnative Dockerの検査は合格しています。
M0/M1全体の完了、MVP完成、本番適格性の確認を意味しません。**
実装プランは将来の要求を示すもので、現在のAPIそのものではありません。
性能、記憶品質、災害復旧、完全消去の受入目標は未測定または未認定です。
ローカルとCIの検査が合格しても、これらのgateが完了したとは扱いません。

## 実装済みの範囲

アプリケーションの永続化先はPostgreSQLのみです。外部memory DB、
モデルサービス、durable queue、ファイルベースのmemory indexはありません。

| Endpoint | 現在の動作 |
|---|---|
| `POST /v1/observe` | caller指定の発生時刻・同意参照とともにepisodeを1件保存。revisionは`1`、`synthesis_job_id`は`null`で、job enqueueは行わない |
| `POST /v1/remember` | 同一scopeの読取り可能なepisodeからの原文引用を根拠とし、明示的に要求された構造化assertionを保存 |
| `POST /v1/assertions/{memory_id}/revisions` | expected head、明示的intent、reason、revision固有のepisode根拠を使い、同一assertionへ全置換revisionを追加 |
| `POST /v1/checkpoints` | branch headのCASでtyped stateを保存し、不変checkpoint参照/checksumを返す |
| `GET /v1/checkpoints/{checkpoint_id}` | 現在のアクセス権と完全性を確認し、state・参照・epoch・照合hintを返す |
| `POST /v1/checkpoints/restore` | 互換checkpointを新target branchへコピー。コード実行や外部副作用の再実行はしない |
| `POST /v1/tool-effects` | 既存checkpoint run内のintentを記録/重複抑止し、初期revision参照を返す |
| `POST /v1/tool-effects/{memory_id}/transitions` | CAS付きledger遷移を追記。toolは呼び出さない |
| `GET /v1/tool-effects/{memory_id}` | 現在の状態、不変event履歴、参照、HMAC識別子、run失効flagを返す |
| `POST /v1/recall` | PostgreSQL全文検索で許可済みepisode/assertionを検索し、byte予算内の決定的context packを生成 |
| `POST /v1/explain` | 指定したassertion revisionと根拠を返す。省略時は最新ではなく引き続き`1`。episodeはrevision `1`のみ。ranking trace APIはない |
| `POST /v1/forget` | 明示IDによる`preview`または`purge`。任意selectorや`suppress` modeは受け付けない |
| `GET /v1/deletions/{receipt_id}` | 許可された削除receiptと、未解決のoperator-managed backup状態を返す |
| `GET /v1/capabilities` | 現在の機能、上限、未対応機能を返す。認証必須 |

型付きrequest/response modelで定義したOpenAPI schemaを
`/docs`と`/openapi.json`で公開します。schemaの生成済みファイルは不要です。
`/healthz`は起動検証後のprocess livenessであり、
PostgreSQLへの継続的なreadiness検査ではありません。

## Identityと認可

- 2048 bit以上の静的なPEM RSA公開鍵でRS256署名を検証します。
  設定したissuerとaudience、必須claimの`sub`、`iss`、`aud`、`iat`、`exp`、
  tokenの時刻上の有効性を検査します。
- 検証済みexternal subjectをPostgreSQL上のprincipalとtenantへ対応付けます。
  配置ごとにissuerを一つ設定し、callerにtenantを選ばせません。
  未登録subjectは未認証扱いです。
- request bodyでtenant/principal identityを指定できません。要求scopeは範囲を
  狭めるだけで、サービスとRLSがmembership・権限を強制します。
  非公開または削除済みobjectへのアクセスは、存在を区別せず`404`です。
- runtime資格情報はsuperuser、RLS bypass、アプリケーションtable ownerに
  できません。owner role経由の所属も禁止します。
  migration/provision用の管理者資格情報を分離します。
- JWKS discovery/rotation、delegated identity、複数issuerのidentity管理、
  公開membership管理APIは未実装です。

認証済みAPI requestごとに短命の新規connectionを開き、connection poolは使いません。
runtimeの`psycopg-pool`依存もありません。
**tenant単位のsession advisory lock**で処理を直列化し、transactionのcommitと
buffer済みHTTP応答の送出が終わるまで保持します。この正しさ優先のdrainにより、
同一tenantの先行応答をサービスがまだ送出中にpurgeがbarrier完了を通知することを防ぎます。
配信済みのdataやnetworkへ渡したbyteを回収することはできません。
遅いclientはtenantの処理を妨げ得ます。throughputは未測定です。

管理者によるmembership変更も**同一session lock**を取得し、
transaction内で権限と`access_epoch`を更新してcommitした後に、
lockを解放するかconnectionを閉じる必要があります。
この手順を使わない変更はrequest/drainの競合保証の対象外です。
[運用手順](operations/README-jp.md#membership変更とrequest-drain)を参照してください。

## 根拠、同意、時間

`remember`は`explicit_intent: true`と、重複のない1〜32件のepisode根拠IDを
必須とします。各引用はepisode本文に文字列として含まれていなければならず、
両objectは同一scopeに属します。このsliceではassertionを別assertionの
sourceにはできません。subjectとvalueはtextであり、解決済みentity graphではありません。

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

## 検索と予算

全文検索はPostgreSQLの`simple`設定、`plainto_tsquery`、`ts_rank_cd`を使います。
BM25、日本語の分かち書き、vector検索、hybrid retrievalではありません。
空の`query`を許可し、scope・時間条件で参照可能なitemを、
件数とbyteの上限内で取得します。

request field名は`token_budget`ですが、`utf8-bytes-v1`はmetadata・引用を含む
serialized context packの**UTF-8 byte数**を予算として扱います。
応答には`budget_unit: "utf8_bytes"`、`token_count: null`、
`exact_token_count: false`を明示します。保守的なfallbackであり、
モデルの正確なtokenizerやHTTP応答全体のsize制限ではありません。
item単位で除外し、packのmetadataすら収まらない場合は`422`を返します。

request bodyはcheckpoint作成のみ1 MiB、他endpointは256 KiBです。
recallの返却itemは最大100件、予算値は64〜8,000
（implicit modeは最大2,000）です。件数・予算による省略を`coverage.truncated`で
示します。空の選択結果は`not_found`または`budget_exhausted`です。
`retrieval_complete`は世界の知識の完全性を意味しません。
implicit modeはrequest optionであり、自動harness hookの実装ではありません。

## Checkpointの契約

checkpoint作成とrestoreには`Idempotency-Key`とscopeの現在のread/write権限が必要です。
GETには現在のread権限が必要です。run/branch UUIDはtenant/scope内でcallerが指定する
identityであり、global sessionではありません。

| 作成field | 契約 |
|---|---|
| `scope_id`, `run_id`, `branch_id` | scope内のrunとbranchを識別するUUID |
| `expected_head` | 必須UUIDまたは`null`。空branchだけnull、それ以外は正確な現checkpoint ID |
| `harness_id`, `harness_version` | 必須の空でないtext、各最大256文字。run内で固定 |
| `state_schema_version` | `1`のみ。既定も1 |
| `event_watermark` | 必須の非負64-bit整数。parentから減少できない |
| `state` | typedな`goal`、`constraints`、`completed_actions`、`decisions`、`unresolved_questions`、`next_actions`、`pending_effects`。任意object/pickleは不可 |
| `memory_refs` | 同一scopeの重複しない`(memory_id, revision)`を最大100件。episodeはrevision 1、assertionは存在するrevision。list省略は空、revision省略は最新ではなく1 |

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
checkpointはrecall/explainの対象外で、stateにはcheckpoint GETを使います。

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
GET/restoreは別branchや保存後に追加したeffectも含め、**runの全生存effect**を統合します。
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
provider照会サービス、自動実行、harness adapterは未実装です。

同一keyの完全一致再送は新headでなく元のcheckpoint参照を維持します。
再送記録にstateは保存せず、読取り/restore再送は現在の認可でenvelopeを再構成するため、
現在のepoch metadataは変わり得ます。purge済み参照の再送は`404`です。

**callerは全memory依存を`memory_refs`へ宣言しなければなりません。**
依存DAGは宣言済み参照、parent lineage全体、下記のrun全体のeffect-to-checkpoint依存を対象にし、
未宣言のコピー本文をsemantic scannerが発見することはありません。
同意とsecret/PII除去もcallerの責任です。
working snapshot、compaction、harness連携は別の将来課題です。
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
| `memory_refs` | 同一scopeの重複しない正確な参照を最大100件。episodeはrevision 1、assertionは存在するrevision 1〜1000。既定は空、revision省略は最新でなく1 |

**actionが利用した全memory依存を宣言してください。**
checkpoint/effectは参照kindにできず、未宣言コピーは発見しません。
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
宣言済みepisode/assertion-to-effect参照により、
**source → tool effect → 同一scope/runの全checkpoint**も辿ります。
参照が空の旧checkpointやeffect作成前のsnapshotも対象です。
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

purgeは対象episode/assertion/checkpoint/effect payload、
reason/receipt参照を含むeffect event、依存する引用/参照を同期SQL削除した後、
**同じtransaction内で**scopeに束縛されたopaqueな削除markerと時刻を
`memory_ops.object_tombstone`へ挿入します。`memory.object`に`deleted_at`列はなく、
SELECT RLSがtombstoneのあるobjectを除外します。
同じtransactionで`deletion_epoch`を進めてreceiptをcommitします。
`active_store_purged`を含むHTTP `202`は、**queue上のpurge jobや完全消去証明ではありません**。
opaque operation registry/run flag、run/branch metadata、object記録、tombstone、audit/receipt metadata、
tenant-keyed HMACのsource/idempotency tombstoneはtenantの存続期間中保持します。
自動期限切れやtenant完全消去workflowはありません。

receiptは`backup_status: "operator_managed"`、
`backup_retention_deadline: null`を返します。旧DB page、WAL、replica、backup、
配信済みcontextの消去は証明しません。完全消去保証や本番complianceへの適格性は未確認です。
backupから復元したDBは最新の削除台帳とACL失効を再適用するまで隔離してください。
自動backup recovery/台帳replayとDR適格性確認は未実装です。

## Schema互換性

変更しないmigration 001〜003に続き、追加的なschema `004_tool_effects.sql`を適用します。
保存checkpoint checksumやassertion履歴を書き換えず、
effect ledger/operation registryとrun失効flagを追加します。
v0.0.4 runtimeはledgerが厳密に`[1, 2, 3, 4]`であることを要求し、
旧版・将来版・不完全な履歴を拒否します。

migrationにはforced RLSをbypassできるDDL権限付き管理者と`btree_gist`が必要です。
migration 002の`row_security = off`はbackfillがRLSでfilterされる場合にfail-closedにする設定で、
bypass権限を付与するものではありません。旧版・新版すべてのAPI trafficを停止し、
backup、原子的migrationの後に、対応する新版APIだけを起動してください。
**すべての旧imageを停止してください。v0.0.1にはschema起動guardがありません。**
旧APIとのrolling共存やdowngradeは非対応です。
[運用](operations/README-jp.md#v004の保守migration)に従ってください。

## 検証証拠

公開repository: [rioriost/pgag_memory](https://github.com/rioriost/pgag_memory)。
**v0.0.4/schema 4**の実装commit
[4a7d3f8](https://github.com/rioriost/pgag_memory/commit/4a7d3f8)について、
2026-09-16に次の成功結果を確認しました。

| 環境 | Command | 結果 |
|---|---|---|
| ローカルApple Container | `./scripts/test-containers.sh` | 73テスト、Ruff、strict mypy（source 8ファイル）、production HTTP health smokeが合格 |
| Docker、native `linux/amd64` | `./scripts/test-containers.sh docker` | 73テスト、Ruff、strict mypy（source 8ファイル）、production HTTP health smokeが合格 |
| Docker、native `linux/arm64` | `./scripts/test-containers.sh docker` | 73テスト、Ruff、strict mypy（source 8ファイル）、production HTTP health smokeが合格 |

Dockerの両jobは
[GitHub Actions run 35098507356](https://github.com/rioriost/pgag_memory/actions/runs/35098507356)
で成功しました。job状態だけでなく、各jobの実際のlogでテスト数、
lint/型検査、production HTTP health smokeまで確認しています。
3環境とも既存warningは2件でした。

schema更新とv0.0.3 checkpoint checksumの維持、effect FSM/CASとreceipt要件、
現在の認可、外部成功を模擬した後の実API process crash、restore/確認の競合、
restore-fence transactionのrollback、run永久封鎖後の過去dispatch再送を検査しています。
run当たり100 effect・effect当たり100参照の正確な上限、過去assertionへの依存、
run全体のeffect/checkpoint purgeも対象です。
M1全体の完了、性能/品質の測定、外部exactly-once、backup/DR、
完全消去の適格性を示すものではありません。

## 今後の実装対象

worker、job enqueue/status API、自動synthesis、別のworking snapshot/compaction、
embedding/pgvector、日本語tokenizer、AGE、SQL/PGQ、SQL/graph oracle、
provider receipt検証、実際のharness連携/実行/recovery、
別assertion間のsupersession/fact調停、MCP、SDK、postgresem連携はありません。
このtyped checkpoint envelopeだけで、
計画上の二時点・graph・provenance・削除architectureが完了したとは扱いません。

選択理由は[ADR 0001](adr/0001-initial-slice-jp.md)、
安全な管理は[運用](operations/README-jp.md)、
container検証workflowは[貢献方法](../CONTRIBUTING-jp.md)を参照してください。
