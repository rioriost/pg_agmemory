# ADR 0007: 上限付きopt-in日本語lexical FTS

[English](0007-japanese-fts.md) | [現在の契約](../STATUS-jp.md#日本語lexical-profile) | [運用](../operations/README-jp.md#lexical-profileとreindexの運用)

- 日付: 2026-09-17
- 状態: v0.0.7/schema 7を実装済み。ローカルとnative Dockerの検査は合格。M0/M1/M2/M3全体は未完了
- 拡張対象: lexical recallと派生index。graph/job/checkpoint/effect契約を維持し、
  [ADR 0006](0006-durable-jobs-jp.md)も継承
- 命名: ローカルdirectory/package/serviceは`pg_agmemory`、公開repositoryは`rioriost/pg_agmemory`

**過去の範囲:** このADRはv0.0.7/schema 7とその検証/lock証拠の記録であり、
現在の依存一式やMCP結果ではありません。v0.0.8 local stdio MCP milestoneもschema 7を維持します。
[ADR 0008](0008-local-mcp-jp.md)を参照してください。
そのlocal/native CI結果は[STATUS](../STATUS-jp.md#検証証拠)に記録しています。

## 決定と範囲

既定recall algorithmを変えず、明示的なversion付き日本語lexical profileを追加します。
PostgreSQLをcanonicalとし、全memory projectionも保存します。
上限付きlexical milestoneであり、vector/hybrid retrieval、測定済みの分割/recall品質、
M2全体の受入ではありません。

`POST /v1/recall`の既定は`search_profile: "simple-v1"`で、PostgreSQLの
`simple`、`plainto_tsquery`、`ts_rank_cd`を維持します。
callerはrequestごとに`"ja-janome-0.5.0-v1"`をopt-inし、応答は`search_profile`を返します。
未対応profileは`422`です。scope、現在のACL/削除検査、時間選択、根拠、item上限、
context byte上限は変わりません。context `tokenizer_id: "utf8-bytes-v1"`は検索分割とは独立し、
日本語/model tokenではなく引き続きUTF-8 byteを測ります。

## Semantic retrievalではなく固定した分割

**Janome 0.5.0**と、Janome追加語を含む同梱**mecab-ipadic-2.7.0-20070801**を固定します。
source/query textの対象日本語script連続部分だけをsurface/wakati分割します。
ASCII識別子や英語はsegmenterをそのまま通過し、その後PostgreSQLの通常のlexical処理を
行います。Unicode/全半角正規化、原形化/stemming、同義語照合、分割品質の保証はありません。
漢字script範囲は中国語文字にも及びますが、中国語recallの適格性は未確認です。
変更しない`simple-v1`はtokenベースであり部分文字列検索ではありません。
token境界なしで埋め込まれた`Gold`は単独の`Gold`に一致するとは限りません。

外部model/providerは呼び出しません。同梱辞書/統計resourceはsoftware依存であり、
fileベースのmemory indexやuser memory保存先ではありません。
profileはlexical動作を固定するもので、関連性や意味的同等性を約束しません。
capabilitiesは両`search_profiles`、`default_search_profile: "simple-v1"`、
feature `japanese_fts`、固定tokenizer/辞書、`normalization: "none"`と
`segmentation: "japanese-script-runs"`を返します。
stage `m2-japanese-fts`は受入gateの結果ではありません。

## Runtime初期化の境界

Janomeは日本語script連続部分があるときだけlazy importし、API importや英語だけの分割では
loadしません。`max_cached_word_len=0`でmatcherの入力prefix cacheを無効化し、
source text/token streamでなく同梱辞書resourceのcacheだけを保持します。
test/runtime両container buildは、辞書moduleを含む**静的Janome package bytecodeだけ**を
逐次事前compileします。software依存の準備でありmemory index/cacheやuser入力ではありません。

技術診断であり、**release/性能の適格性確認ではありません**:
未compileのcold importはpeak RSS約**995,496 KiB**でexit 137となりました。
lazy load/静的事前compile後の観測process peakは、API importで**64,012 KiB**、
tokenizer初期化後で**136,980 KiB**でした。限定的な診断観測値であり、
配置時のsizingやworkload memory上限ではありません。
事前compileのないcold host installationでは、この初期化riskへの注意が引き続き必要です。

fresh Linux subprocessのregression guardは、初期化peak RSS **256 MiB未満**と、
英語だけの操作でJanomeをimportしないことを要求します。
閾値はruntime memory capや配置時のsizing保証ではありません。
non-root production imageのsmokeは既存API HTTP/実worker CLIに加え、
`東京都` → `東京` / `都`を検査し、成功時に`Production Japanese tokenizer smoke passed`を
出力します。いずれもrecall品質や配置時のresource sizingを認定しません。

## Canonical revisionとprojection lifecycle

`memory.episode_lexical`はepisode（revision 1）に対応するprofileの派生`tsvector`を保存し、
`memory.assertion_lexical`は正確なassertion revisionとprofileをkeyにします。
両方にforced RLS、`ON DELETE CASCADE`付き同一scope canonical外部key、GIN indexを適用し、
runtime権限は`SELECT`/`INSERT`だけで、`UPDATE`や直接`DELETE`は付与しません。
canonical parent purgeが子tableのDELETE権限なしでFK cascadeによりprojection行を消します。
episode本文とassertionのsubject/predicate/正確なrevisionの
valueを分割後に`to_tsvector('simple', ...)`へ渡し、日本語queryは分割textを
`plainto_tsquery('simple', ...)`と`ts_rank_cd`へ渡します。
simple検索は既存canonical vectorを維持します。
日本語episode入力も65,536文字上限を維持し、65,537文字は切り詰めず拒否します。
別の256 KiB HTTP body上限も適用します。

observeと全assertion publication/revisionはcanonical dataとprojectionを原子的に
書き込みます。typed relationとdurable job publicationも含み、publication失敗時は
projectionもrollbackします。legacy同期正規化JSON/HMAC、canonical ID/system time、
HTTP receiptは変えません。job結果assertionのrecorded/system timeはenqueueでなく
publication時に始まります。過去の`known_at`は現在値ではなく正確な過去revisionと
そのprojection/根拠を選択します。explainの既定は引き続きrevision 1です。
正確な`known_at`境界の検査にはhost/VMのwall-clock値ではなく、
serverが返したassertionの`recorded_at`を使います。

projection行は派生payloadであり独立memoryやprovenance vertexではありません。
canonical purgeは同じtenant barrierで対象の全lexical revisionへcascadeし、
tombstone commit前に消去します。既存episode/entity/relation、job retry、checkpoint、
effectの削除規則は維持し、job削除は独立した公開済みoutputやsource episodeを消しません。
rebuildはtombstoneをskipし、purge済み本文を復活させません。
opaque anchor/receiptには既存の保持方針を適用し、backup/WAL/配信済みcontextの消去は
認定しません。

## Projection欠落を明示し、fallback成功にしない

日本語profileでは、認可済み・要求scope内・時間条件内のcanonical候補にprojection欠落が
一つでもあると、`coverage.lexical_incomplete: true`と
`coverage.retrieval_complete: false`を返します。この検査はquery関連性やitem上限とは
独立し、`jobs_pending`やsynthesis coverageではありません。
simple profileへの黙ったfallback、遅延修復、自動修復workerはありません。

利用可能な一致結果は不完全flag付きで返せます。空queryはprojection欠落があっても
canonical候補をbrowseします。query候補なしでprojection欠落があれば
`empty_reason: "index_incomplete"`、候補があってcontextに収まらなければ
`budget_exhausted`を優先し、結果があれば`empty_reason`はnullです。
それ以外は通常の`not_found`/予算規則を使います。
完全なprojection coverageも、関連性、fact不在、世界知識の完全性を証明しません。
Janome破損辞書logは入力textを含まない`japanese_dictionary_error`へ除去処理します。
libraryの`SystemExit`をtokenizer-unavailableへ変換し、入力をechoせずAPIの
`503 dependency_unavailable`と既存の上限付きworker依存障害retryにします。
空/不完全retrievalの成功ではありません。

## Offline migrationと修復

migration 001〜006を変更せず、`007_japanese_fts.sql`を追加します。
migration runnerはprojectionを作り、schema 7記録前の同一transaction内で、
**全保持episodeと全assertion revision**をPythonでbackfillします。
tombstoneをskipし、canonical ID、system time、receiptを維持します。
実際のbackfill完了後の失敗でもprojection DDL/dataとschema ledgerをまとめてrollbackし、
schema 6からのupgradeは6のままです。

`pg-agmemory reindex-lexical`は選択DBの**全tenantを対象とするoffline管理操作**であり、
principal/scopeをfilterするworker commandではありません。
`--subject`は明示拒否し、`--once`もworker専用として拒否します。
**`PGAG_ADMIN_DATABASE_URL`**を使い、厳密なschema履歴
`[1, 2, 3, 4, 5, 6, 7]`を要求し、migration advisory lock（lock timeout 5秒）下で
canonical sourceから両projectionを原子的に置換します。
JSON出力は`profile: "ja-janome-0.5.0-v1"`と整数の
`episodes`/`assertion_revisions`件数だけで、本文/tokenは含みません。
一部置換後の失敗でも旧schema 7のprojectionを維持します。
管理権限にはforced RLS bypassが必要で、
`row_security = off`はbypassを付与しません。
runtime資格情報はrebuild用ではなく、rebuild HTTP endpointもありません。

migration**とrebuild**では、自動再起動を含む**全API/workerを停止/drain**し、
先にbackupを取得し、offline操作後に対応するv7 processだけを起動します。
API/workerとも厳密なschema/role検査を行います。
rolling共存やdowngradeは非対応です。v0.0.1にはschema起動guardがないため停止を維持します。
advisory lockはruntime trafficをdrainしません。
保守所要時間/resource使用量、restore/DR安全性の適格性は未確認です。

## 依存ライセンス

project codeは引き続き[MIT](../../LICENSE)ですが、依存関係の条件は別です。
Janome 0.5.0は[Apache-2.0](https://github.com/mocobeta/janome/blob/0.5.0/LICENSE.txt)です。
同梱IPADIC辞書/統計dataには
[NAIST/ICOTのcopyright、配布条件、無保証notice](https://github.com/mocobeta/janome/blob/0.5.0/NOTICE.txt)
があり、この固定releaseには
[Janome辞書追加語](https://github.com/mocobeta/janome/blob/0.5.0/ipadic/Noun.proper.csv.patch)
も含みます。package/image再配布時は上流のlicense/notice fileを保持してください。
全依存関係をMITとしたり、第三者辞書dataを同梱しないと主張したりしてはいけません。

最終lockは従来のpackage-feed registryを維持し、全**36 package**のversion、依存metadata、
artifact hashはテスト済みPyPI解決lockとbyte単位で同一と確認しました。
v6との差分はJanome 0.5.0の追加とprojectのv0.0.7へのversion更新だけで、
無関係なupgradeやregistry移行はありません。
native CIはこの最終retained-registry lockからbuildしました。

## 証拠と対象外

実装commit
[678ba24](https://github.com/rioriost/pg_agmemory/commit/678ba2410fcc6adf73102bb44b3b36681cf47473)について、
**2026-09-17 JST**にv7最終結果を確認しました。

| 環境 | テスト | テスト所要時間 |
|---|---|---|
| Apple Container | 144合格、既存warning 2件 | 206.54秒 |
| Native Docker `linux/amd64` | 144合格、既存warning 2件 | 386.32秒 |
| Native Docker `linux/arm64` | 144合格、既存warning 2件 | 331.59秒 |

3環境すべてで**Ruff、strict mypy（source 12ファイル）、non-root productionの
日本語tokenizer、API HTTP、実CLI worker `--once` idle実行という全3種のsmoke**が合格しました。
[CI run 35173023029](https://github.com/rioriost/pg_agmemory/actions/runs/35173023029)の
両native Docker jobの実logで完全一致SHA、件数、各検査を確認しています。
所要時間はテスト実行の観測値であり、性能benchmarkではありません。
suiteはprofile/時間/RLS動作、projection欠落と予算、正確な入力上限、初期化guard、
子table DELETE権限なしのcascade purge、migration/reindex rollback、CLI制限、
従来graph/job/checkpoint/effect互換性を対象とします。
[詳細な証拠](../STATUS-jp.md#検証証拠)を参照してください。
schema 6の決定と結果は[ADR 0006](0006-durable-jobs-jp.md)に過去のものとして保持します。

自動synthesis/enqueue/抽出、LLM/provider処理、embedding/pgvector、vector/hybrid retrieval、
暗黙graph展開、AGE/SQL/PGQ、動的SQL/Cypher/labelは追加しません。
entityは不変のcaller申告identityのままrecall/explainから除外し、
typed relation assertionは`graph_used: false`のlexical候補を維持します。
graph探索は引き続き明示canonical SQLでありgraph projectionではありません。
M0/M1/M2/M3、MVP/本番、記憶/分割品質、性能、harness連携、完全消去、backup/DRの受入は
未完了です。
