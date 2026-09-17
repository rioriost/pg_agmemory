# ADR 0011: Pgvector exact/hybrid retrieval基盤

[English](0011-pgvector-retrieval.md) | [Draft契約](../STATUS-jp.md#pgvector-exact-and-hybrid-retrieval) | [運用](../operations/README-jp.md#schema-8-pgvector-upgrade)

- 日付: 2026-09-17
- 状態: v0.0.11/schema 8で採用・検証済み
- 拡張対象: [ADR 0007](0007-japanese-fts-jp.md)と[ADR 0010](0010-atomic-capture-jp.md)
- Repository/license: `rioriost/pg_agmemory`。project MITを変更せず、二言語文書を維持
- 受入: M0〜M3全体、MVP、本番、性能、記憶品質、DR、完全消去のgateは未完了

## 決定と権限境界

明示的でprovider非依存のvector projectionとexact/hybrid retrievalを追加します。
**lexicalを既定に維持**します。実験的な数学的基盤であり、embedding provider、
model registry、自動生成/backfill、意味検索品質の認定ではありません。
application永続化先はPostgreSQLだけを維持します。

読取り専用Native `POST /v1/embedding-inputs`は既存Explain
`{memory_id, revision}`（既定は**latestでなく1**）を使い、idempotency keyは不要です。
現在読取り可能なepisode/assertion revisionだけを受け付けます。
`{memory_id, revision, type, text, input_digest, input_format: "memory-content-v1"}`を返します。
episode textは正規化content、assertion textはrelation assertionのdisplay valueも含む
`MemoryItem.content`と同じ正確な`subject / predicate: value`です。
embedding textへID/時刻を追加しません。

digestはUTF-8 textのSHA-256であり、**意味的な支持、model品質、外部検証、
vectorがこのtextから生成された証明ではありません**。
canonical inputはprivate contentで、log出力や明示承認のない第三者への送信は禁止です。
例は合成dataだけを使い、外部modelを呼びません。

## 明示的・不変upload

Native `POST /v1/embeddings`はcaller管理`Idempotency-Key`、`memory_id`、
canonical `revision`（既定1）、小文字64桁hex `input_digest`、`model`、`values`を要求します。
model名/revisionは必須1〜256文字stringで、次元**768**、metric **`cosine`**、
normalization **`l2-f32-v1`**に固定します。
正確に768個の有限JSON数値を必要とし、float64で正規化した後pgvector float32で保存します。
zero/非有限/正規化不能vector、boolean、数値文字列、異なる次元、切詰めは拒否します。

scope/identityはcanonical parentから導出し、現在のread/write認可を要求します。
model metadataは**caller宣言**であり信頼済みprovenanceではありません。
同次元でもmodel空間はname/revision組全体で分離します。
正確な認可済みcanonical revisionのdigest不一致は**409 `embedding_input_mismatch`**です。

canonical revision/model namespace当たりvector一つで不変です。
同じ正規化float32 vector/digestは別HTTP keyでも重複抑止し、
異なる値は**409 `embedding_conflict`**で、置換には新model revisionが必要です。
同keyで異なる正規化requestもconflictします。
**canonical revision当たりmodel version合計8件**までで、
9件目は**422 `embedding_limit_exceeded`**ですが、既存duplicateは許可します。
projection作成、receipt、auditは原子的です。
返却`{memory_id, revision, model, input_digest}`に独立embedding IDはありません。
replayは過去receiptだけでなく生存parentとprojectionを確認します。
**idempotency resultに保存するのは`{memory_id, revision}`だけ**であり、
平文input digest、model名、vectorは保存しません。現在読取り可能なcanonical inputと
対応projectionから応答の完全なmodel/digestを再構成し、request HMACとopaque anchorは保持します。
parentが生存中に管理者がprojectionだけを削除した場合、
replayは再構築せず**409 `embedding_unavailable`**を返します。

## Exact rankingと正直なcoverage

recallへ`retrieval_mode`（既定`lexical`、`vector`、`hybrid`）と
`vector_query`（既定null、指定時は宣言modelと768値）を追加します。
lexicalはvector queryを拒否し、vector-onlyは空text、hybridは非空textを要求します。
両非lexical modeにはvectorが必須で、text無視や別model/modeへのfallbackはありません。

scope、`as_of`/`known_at`、予算、search-profile規則を維持します。
省略`as_of`/`known_at`はselection/coverage前に一度だけ確定し、
途中の未来境界にかかわらず全pathで同じ確定時刻を使います。明示時刻は変更しません。
**現在の認可/時間条件を満たすcanonical候補を`MATERIALIZED`にし、
cosine distance/rankingより前にfilter**します。
ANN/HNSW、近似neighbor拡張、scope/tenant拡大は追加しません。
過去revisionのvectorも別途投入できますが、過去readも現在のACL/削除検査を迂回しません。
hybridは決定的な既存FTS rankとexact vector rankを**RRF k=60**
`1/(60+lexical_rank) + 1/(60+vector_rank)`で統合し、不在pathの寄与は0です。
vectorなしlexical一致も参加できますが、不完全coverageを明示します。

適格かつ可視のmodel projectionが一つでも欠落すれば`vector_incomplete: true`で、
private/不適格itemはcoverage/件数に含めません。lexical既定はfalseを維持します。
日本語`lexical_incomplete`はlexical/hybridだけで、active pathのどちらかが不完全なら
`retrieval_complete`はfalseです。候補がなく選択結果が空なら、
active index coverage欠落は`index_incomplete`、空の認可済みcorpusは`not_found`です。
候補が収まらない場合は`budget_exhausted`を維持し、非空結果の`empty_reason`はnullのまま
不完全coverageを明示します。
失敗やindex欠落を完全な空recallとして隠しません。

既定fieldとして`MemoryItem.retrieval: null`、`RecallResult.retrieval_mode: "lexical"`、
`embedding_model: null`、`coverage.vector_incomplete: false`を追加します。
non-nullの`MemoryItem.retrieval`は`method`（`exact_cosine`/`rrf-60`）、
`lexical_rank`、`vector_rank`、`vector_distance`、`fusion_score`を持ち、
path/methodが提供しないrank/distance/fusion fieldはnullableです。
実際に計算したdistance/scoreの同順位はUUIDで解決します。
任意の浮動小数点結果/rankingの全CPU間bit単位一致を保証しません。追加fieldであり、
**lexical semanticsの維持はHTTP response/schemaのbyte単位互換を意味しません**。
rankingは**confidenceや真実ではありません**。
compact JSON全体のUTF-8 context-pack予算、reported assertion、null/未校正confidenceを維持します。
既存DB statement timeout 5秒は性能SLOではなく、
品質/性能/untrusted vectorへの頑健性は未認定です。

## Schema、削除、package

**`008_pgvector.sql`はschema 8へ進め、`public`内の`vector` 0.8.6を要求**し、
版/schemaが異なる既存extensionを拒否します。
episode単位/assertion revision単位projectionへforced RLS、
runtime SELECT/INSERTのみ、canonical `ON DELETE CASCADE`を適用します。
**embedding backfillはありません**。
parent purgeはlexical payloadとともにvector/digest/宣言model名へcascadeし、
別memory identity、provenance vertex、削除件数にはしません。
このmetadataを保持する独立model registryはありません。
projection-only削除endpointや自動embedding再構築/providerはありません。
保持canonical source/idempotency anchorは復活を防ぎます。
Native response-drainとhost/backup/WAL/完全消去の制限は維持します。

採用prebuilt上流DB profileは
`docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a`です。
[pgvector 0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6)は検証済みの
**2026-07-29** stable releaseで、公式tag commitは
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`です
（[固定changelog](https://github.com/pgvector/pgvector/blob/8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c/CHANGELOG.md)）。
**PostgreSQL License**であり、上流licenseを保持します。
両native最終imageは`/usr/share/doc/pgvector/LICENSE`を保持し、
固定上流licenseとbyte単位一致を検証済みです。SHA-256は
`6bba9ebeb73e27477463b05e5ef1bf303bccbddb3db9bbc95905d351604d6a87`です。
両最終amd64/arm64 imageの検査でPostgreSQL **18.6-1.pgdg12+2**、
native ELF、`vector.control` **0.8.6**を確認しました。
**PostgreSQLは18.6のままですが、上流DB image/base digestは変わります**。
旧library PostgreSQL imageを変更していないという意味ではありません。
実装profileに新DB Dockerfile、source build、host APT workflowは含めません。
operator管理の代替環境にも同じextension版/schemaが必要ですが、host導入手順はここで提供しません。
**旧版・新版の全API、worker、adapter、hook起動を停止/drain**してください。
v0.0.11 API/worker起動は厳密なschema履歴`[1, 2, 3, 4, 5, 6, 7, 8]`と
schema `public`内のextension `vector` 0.8.6を要求します。
旧schema 7 processのrolling混在互換性やdowngrade対応はありません。
API、worker、`migrate`はschema 8記録済みでもextension版/schemaを検査し、
migration適用済みを理由にguardを省略しません。
新Python依存は不要です。raw parameter-bound vector castを使うためpgvector Python packageは不要で、
Python依存の変更はproject版metadataだけです。

MCPは4 toolと両protocol時代を維持し、生成Recall引数へinline query vectorを追加しますが、
embedding input/upload toolは公開しません。
hookは**lexical専用・読取り専用**で、event入力からvector fieldを指定できません。
Native応答の非lexical `retrieval_mode`、non-nullの`embedding_model`/item `retrieval`、
trueの`coverage.vector_incomplete`も拒否し、黙って降格しません。
Observe/capture/job/workerはembeddingを生成しません。
adapter起動はservice **0.0.11**、API **v1**、schema **8**の一致を要求します。
capabilitiesに`retrieval_modes: ["lexical", "vector", "hybrid"]`と
`default_retrieval_mode: "lexical"`を追加します。
API stageは`m2-pgvector-retrieval`、embedding inputはHTTP 200、upload/replayはHTTP 201です。

## 証拠と帰結

**v0.0.11はlocal Apple Containerとnative Docker amd64/arm64の検査に合格しました**。
各**345テスト、既存warning 1件**、Ruff、strict mypy（**source 17ファイル**）、
core-only/hook-only導入検査、non-root productionの全smokeに合格しました。
テスト所要時間は**local 283.44秒、amd64 404.40秒、arm64 433.46秒**です。
実装[f185572](https://github.com/rioriost/pg_agmemory/commit/f185572e0b5d3c9a2d79e3ad9b7b390de8464fc1)は
[CI 35189448403](https://github.com/rioriost/pg_agmemory/actions/runs/35189448403)に合格しました。
[証拠](../STATUS-jp.md#v0011--schema-8)を参照してください。
検証済みartifact検査は別であり、source-build workflowは含めません。
正規化/digest/不変性/model上限、決定的exact/RRF数学、ACL/時間の事前filter、
coverage、削除/replay、migration/version/role guard、既存動作を
local Apple Containerとnative Docker amd64/arm64で検証しました。
実装fixtureにはDB norm/次元/composite FK/8 model guard、直接RLS可視性/UPDATE拒否、
ACL失効、実際のschema 7→8でledger失敗時のDDL/extension rollback後のretryも含み、
backfillは行いません。production vector smokeはepisode**と**assertionのprojectionをuploadし、
basis distance **[0, 1]**とRRF、source purge後のupload replay `404`を検査します。
これらの検査は全3環境で合格しました。所要時間は性能benchmarkではありません。
[合成basis vector例](../operations/README-jp.md#synthetic-vector-example)は
本番model、品質benchmark、private dataを外部へ送信する許可ではありません。

過去のv0.0.10/schema 7実装
[ac42c35](https://github.com/rioriost/pg_agmemory/commit/ac42c354b9310e877c9d248cf9c8cc8f4293128f)、
[CI 35185176814](https://github.com/rioriost/pg_agmemory/actions/runs/35185176814)は
各環境304テストに合格し、local **275.53秒**、native amd64 **467.75秒**、
native arm64 **434.40秒**でした。最終docs
[bd530a8](https://github.com/rioriost/pg_agmemory/commit/bd530a89c0832a45fac005b3c10566ccf90c6cb6)も
[CI 35186202760](https://github.com/rioriost/pg_agmemory/actions/runs/35186202760)で
各native architecture 304テストに合格しましたが、そのdocs runと実装所要時間は別です。
両runともschema 8/vector retrievalを検証していません。[過去の証拠](../STATUS-jp.md#v0010--schema-7)を参照してください。
