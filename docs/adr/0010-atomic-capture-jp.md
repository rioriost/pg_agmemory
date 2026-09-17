# ADR 0010: Atomic structured capture

[English](0010-atomic-capture.md) | [差分契約](../STATUS-jp.md#atomic-structured-capture) | [Operator例](../operations/README-jp.md#atomic-structured-captureの運用)

- 日付: 2026-09-17
- 状態: v0.0.10/schema 7実装済み。最終localとnative amd64/arm64検査に合格
- 拡張対象: [ADR 0006](0006-durable-jobs-jp.md)と[ADR 0009](0009-implicit-recall-hook-jp.md)。Native認可、job、読取り専用hook semanticsを維持
- 命名/license: `rioriost/pg_agmemory`、package/service `pg_agmemory`。MITを変更せず、二言語文書を維持
- 受入: M0〜M3全体、MVP、本番、性能、記憶品質、DR、完全消去のgateは未完了

**過去版の範囲:** このADRはv0.0.10/schema 7と検証済み証拠を記録します。
[ADR 0011](0011-pgvector-retrieval-jp.md)はv0.0.11/schema 8 vector retrievalを記録し、
migration/起動要件は別です。captureはembeddingを自動生成しません。

## 決定と範囲

Native **`POST /v1/captures`**を追加し、caller管理`Idempotency-Key`を必須にします。
一つの明示requestがepisode一つと構造化publication job一つを原子的にcommitまたは再利用します。
別々のObserveとenqueue呼出し間のpartial writeをなくすもので、
任意の会話からintentを推測したり、全observeをjob化したりしません。

requestは次の形式です。

```text
{
  episode: <unchanged Observe>,
  memory: {
    subject, predicate, value, evidence_quote, explicit_intent: true,
    valid_from: <aware timestamp or null>,
    valid_to: <aware timestamp or null>
  }
}
```

`memory`は構造化intent一つであり、listではありません。
**scope、根拠ID、identity fieldはありません**。
serverはtransaction内でepisodeからscopeを導出し、
一つのepisode ID/revision 1を根拠に結び付けます。
quoteは正規化capture episode内に原文として存在する1〜4,096文字である必要があります。
Rememberのsubject 1〜256文字、predicate `^[a-z][a-z0-9_]{0,63}$`、
value 1〜65,536文字、explicit intent true、timezone付き/null valid bound、
両端指定時は開始が終了より前という規則を維持します。
正規化とNative tenant/scope認可を引き続き正とし、captureはアクセスを拡大しません。

**LLM、抽出provider、自動synthesis、自然言語synthesis、pgvector、意味的真実の認定はありません**。
原文根拠はprovenanceを示すだけで、quoteが命題を支持するかは認定しません。
publicationは`epistemic_status: "reported"`と未校正confidence
（`score: null`、`method: "uncalibrated"`）を維持します。

## Commitはpublicationではない

HTTP **201**は次を返します。

```text
{memory_id: <episode UUID>, revision: 1, synthesis_job_id: <job UUID>}
```

commit済みepisode/job組であり、**公開済みassertionではありません**。
既存jobやterminal jobの場合もあり、**201は新規/pending jobのいずれも保証しません**。
返却IDは過去参照であり、現在のstatusはGETを正とします。
capture操作当たり`structured_remember` / `structured-remember-v1` jobは最大一つです。
`GET /v1/jobs/{synthesis_job_id}`でfresh stateを取得し、
既存固定subject workerで後続assertion publicationを行います。
worker publicationは別の原子的transactionであり、assertionのserver記録publication時刻は
capture enqueue時でなくpublication時に設定します。
既存のscope当たりpending/running job 100件、5試行、lease、
access/deletion epoch、publication fencingを維持します。
外部exactly-once保証ではありません。

純粋な`POST /v1/observe`は`synthesis_job_id: null`を含む正確なschema/resultを維持し、
**自動enqueueしません**。明示`POST /v1/jobs`と同期`/v1/remember`も変更しません。
純粋なObserve/Rememberの正規化serializationとHMACはbyte互換を維持します。

## 原子性とidentity

episode保存とlexical projection、job input/control/identity、
outer capture receiptを含むidempotency、auditを一つのPostgreSQL transactionで扱います。
どちらかのwrite後やouter receipt後のtransaction faultも**全新規変更**をrollbackします。
以前から独立して存在したepisodeは残り、新しいjobだけが部分的に残ることはありません。
commit後のHTTP timeoutはrollback証明ではなく、元のkey/bodyを保持し、
現在のアクセス下でreplayにより復旧します。

現在のACL/削除検査下で次を適用します。

| Request | 結果 |
|---|---|
| 同じcapture keyと正規化body | 同一episode/job組 |
| 同じkeyでbody変更 | `409`、新規partial writeなし |
| 別HTTP key、同じepisode/intent/principal | 両IDを重複抑止 |
| 以前observeした同一event | episodeを再利用し、wrapperがjob intentを明示 |
| 新keyと別の明示intent | 生存episodeを使う別jobを作成可能 |
| 別の認可済みprincipal、同じevent | episodeのsource重複抑止は共有し、job identity/worker所有権は独立 |
| 同じsource-event identityでepisode body変更 | `409`、新規partial writeなし |

既存source identityはtenant/scopeとsource namespace/event ID組のHMACを使い、
event IDをglobal uniqueとは扱いません。
異なるintentのjobは意図的であり、意味的重複抑止の証拠ではありません。
保持するopaque source/job/idempotency anchorには、
caller keyからserver HMACで導出した内部composition keyも含みます。
client指定fieldでもMCP caller keyの自動生成でもなく、callerのkey再利用責務を変更しません。

## 現在のアクセス、削除、retry

capture IDは過去参照でありfresh stateではありません。
API process再起動後のreplayを含め、**両返却IDの現在のACL/削除検査を優先**します。
どちらかが読取り不能なら部分的な組を返しません。

| Purge対象 | 結果 |
|---|---|
| Source episode | 依存jobとassertion子孫を閉じる |
| Job単体 | episodeと独立保存の公開済みoutputを残す。旧capture replayや同intentの新keyは`404`で、purge済みjob identityを再作成しない |
| 公開済みresult assertion | 依存jobを削除しsourceを保持。旧組は無効になる |

生存source上の新しい明示的な**別intent**は既存job semanticsで許可します。
source全体の永久sealではありません。
failed jobは既存`POST /v1/jobs/{job_id}/retry`に完全な元の`EnqueueJob` intentと
caller keyを渡して明示retryできます。retry childは既存identity/所有権規則に従います。
明示retry child作成後も旧capture replayは元のfailed job参照を返し、
childへの置換やjobの自動再queue化をしません。

Native tenant HTTP response-drain advisory barrierは変更しません。
信頼するlocal clientへの配信はhost buffer、stdout、host context、外部copyを原子的に覆いません。
回収/通知機構、host消去証明、新しいbackup/WAL/完全消去保証はありません。
保持opaque anchorは消去済みmemory本文でも完全消去の主張でもありません。

## Versionとadapter互換性

**v0.0.10 / API v1 / 厳密なschema 7**を使い、
正確な履歴`[1, 2, 3, 4, 5, 6, 7]`を維持します。
既存v0.0.7/v0.0.8/v0.0.9のschema 7 DBに
**migration 008/009/010、DDL、新backfillは不要**です。
新依存はなく、dependency lock変更はproject版metadataだけです。
対応版を起動する前に旧API、worker、adapter、hook起動を停止/drainし、
同じschemaをversion混在/downgrade対応の根拠にしません。

MCP/hook起動検査は厳密なservice **`0.0.10`**、API **`v1`**、schema **`7`**を要求します。
captureは**5番目のMCP toolではありません**。
MCPの両時代`2026-07-28`と`2025-11-25`は既存契約を維持します。
recall hookは読取り専用で、自動captureしません。
任意`[mcp]`/`[hook]`依存、真の依存分離、上限付きNative transport、
root origin URL許可、固定token認証、sanitized error semanticsを変更しません。

実装済みAPI stageは**`m2-atomic-capture`**、featureは**`atomic_structured_capture`**です。
正確なmetadataは`atomic_capture: {endpoint: "/v1/captures", max_jobs: 1,
recipe_version: "structured-remember-v1", automatic_capture: false}`です。
stage名やengineering testはM2を完了させず、
特定vendor連携、記憶品質、性能の適格性を認定しません。

## 検証状態

**v0.0.10の最終localとnative結果を2026-09-17 JSTに確認しました。**
Apple Containerとnative Docker amd64/arm64は各**304テスト、既存warning 1件**に合格しました。
**Ruff、strict mypy（source 16ファイル）、真のcore-only/hook-only導入検査、
non-root productionの全smoke**も全3環境で合格しました。
日本語/API/worker、MCP両時代、hook全3 event、atomic captureが対象です。
テスト所要時間はApple Container **275.53秒**、native amd64 **467.75秒**、
native arm64 **434.40秒**です。
最終local sourceは公開済み実装
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f)と一致します。
[CI run 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)は
両native jobとも合格し、実logでjob statusだけでなく完全一致SHA、件数、所要時間、全検査を確認しました。
所要時間は性能benchmarkではありません。

検査はrollback fault、source/key重複抑止の競合、quota、RLS/削除、
API process再起動と実workerを対象にします。
全3環境で合格した新規fixtureのproduction smokeはMCP/hook後に、Native capture → pending job →
実worker CLI `--once` → episode/assertion recall → 同capture replay →
source purge → job GET `404`とcapture replay `404`を確認します。
全3環境の最終runには、commit後の実HTTP 201応答喪失、same-keyによる正確な同一組の復旧と一つのpublication、
明示retry child作成後も元のfailed capture jobをreplayする検査を含めます。
[v0.0.10証拠](../STATUS-jp.md#v0010--schema-7)を参照してください。全受入gateは未完了です。

過去のv0.0.9実装
[3d52a8f](https://github.com/rioriost/pg_agmemory/commit/3d52a8fdf950e28fbbd30181850629021bd00050)は
[CI 35181334488](https://github.com/rioriost/pg_agmemory/actions/runs/35181334488)に合格しました。
最終v0.0.9 docs
[de1bcc1](https://github.com/rioriost/pg_agmemory/commit/de1bcc13da74bb6e26475269a7acd283f43625db)は
[CI 35182291689](https://github.com/rioriost/pg_agmemory/actions/runs/35182291689)に合格しました。
両runともnative両architectureで各274テストに合格しましたが、
**過去のv0.0.9**結果でありatomic captureの証拠ではありません。
正確な範囲は[保持した証拠](../STATUS-jp.md#v009--schema-7)を参照してください。
