# ADR 0023: Episode照会とpagination

[English](0023-episode-query.md) | [契約](../STATUS-jp.md#episode-query-and-pagination) | [運用](../operations/README-jp.md#episode-query-and-pagination)

- 日付: 2026-09-18
- 状態: 上限付きv0.0.23/schema 10として実装済み・適格性確認済み
- 拡張対象: [初期slice](0001-initial-slice-jp.md)、[Python SDK](0012-python-sdk-jp.md)
- Repository/license: `rioriost/pg_agmemory`。MIT不変、二言語文書
- 受入: M0〜M3/MVP/性能/記憶品質/本番/DRの適格性確認ではない

## 決定とrequest境界

認証付きread-only `POST /v1/episodes/query`を追加し、現在のread権限を要求します。
write権限や`Idempotency-Key`は不要です。
closedな`QueryEpisodes`は重複しないUUID `scope_ids`（1〜32件）、
`occurred_from`と`occurred_to`（timezone付きtimestampまたはnull、既定null）、
strict整数`max_items`（1〜100、既定20）、`before: EpisodeCursor | None`
（既定null）だけを受け付けます。
closedな`EpisodeCursor`はtimezone付き`recorded_at`とUUID `memory_id`が必須です。
未知field、scope重複、不正UUID/naive timestamp、範囲違反は**422 `invalid_request`**で、
strictな`max_items`はboolやfloatを拒否します。

Occurred-time境界は**半開区間`[from, to)`**で、
`occurred_from <= occurred_at < occurred_to`です。省略/nullの境界側は無制限ですが、
同一または逆転した境界は不正です。scopeとtime filterはLIMIT前に組み合わせます。
event-time filterであり、`as_of`、`known_at`、過去ACL、二時点再構成ではありません。
source identityはHMAC anchorとして保持するため、平文の`source_namespace` filterや
存在しないsource fieldは追加しません。

## 現在のmetadataでありcontentやowner専用発見ではない

SQLは現在tenant/scope RLSの下で`episode`と`object`をjoinし、metadataだけを選びます。
読取り可能な共有scopeも含め、**所有権filterはありません**。
未知/非公開/別tenantのscopeは行を寄与せず、一致なしは**200**、
`episodes: []`、`next_cursor: null`で、非公開理由や件数は返しません。
各pageで現在権限、purge可視性、tenant response-delivery/drain barrierを適用します。
このreadはaudit、receipt、job、その他application書込みを作りません。

正確な**200 `EpisodePage`**は`episodes: list[EpisodeSummary]`（最大100件）、
`next_cursor: EpisodeCursor | None`、`consistency`（`access_epoch`、`deletion_epoch`）を返します。
各summaryは`memory_id`、`revision: 1`、`scope_id`、`occurred_at`、`recorded_at`だけです。
body/content、consent reference、source URI、`source_namespace`、event ID、job payloadは返しません。
callerがepisodeを明示選択し、`Explain(memory_id, revision=1)`で現在認可されたcontentを取得し、
必要ならliteral根拠付きの`Remember`を明示します。
自動ingestion、synthesis、抽出、compactionではありません。
contentは根拠であり、信頼する指示や検証済み/現在の事実ではありません。

## Recorded-time keysetでありevent順やwatermarkではない

`object.created_at DESC, episode.id DESC`順を
`recorded_at DESC, memory_id DESC`として公開し、exclusiveな
`(recorded_at, memory_id) < (before.recorded_at, before.memory_id)`を使います。
`max_items + 1`件を取得して最大`max_items`件を返し、overflowの場合だけ
lookaheadでなく**最後に返すitem**からcursorを作ります。
省略/nullの`before`は最新から開始し、nullの`next_cursor`は継続の終端です。
**occurred time順ではなく**、遅れて受付した過去eventが一致行の先頭に現れ得ます。

署名なしcursorは位置であり、権限、snapshot、receipt、event sequence、
compaction watermark、retention objectではありません。既存episodeを指す必要はなく、
削除済み/偽造位置も現在認可された行を限定するだけです。
各pageは照会時点の結果で、ACL/purge変更により構成が変わり得ます。
境界より新しいrecorded行には`before`なしの明示再開が必要です。

## SDKと導入

async `query_episodes(QueryEpisodes) -> EpisodePage`は`mutation=False`、
call時request再検証、通常の**request 256 KiB / response 2 MiB**上限を使います。
SDK response parsingも100-item上限を検証します。
read応答消失はsanitizedな`outcome_unknown: false`で、mutation結果不確実とはしません。
明示的な再照会は異なる現在dataを見る場合があります。
自動pagination/retry、mutation、provider呼出し、SDK管理/worker methodはありません。
Native/SDKは31 resource method、MCPの4 toolとclosed hookは不変です。
厳密なservice 0.0.23 / API v1 / schema 10を要求し、
stage `m2-episode-query`はfeature `episode_query`とmetadataを追加します。
`endpoint: "/v1/episodes/query"`、`order: ["recorded_at_desc", "memory_id_desc"]`、
`pagination: "exclusive_keyset"`、`occurred_time_bounds: "half_open"`、
`max_items: 100`、`includes_content: false`です。
v22→v23はapplication-onlyで、schema 10/履歴1〜10と固定artifactを維持し、
SQL migrationや依存/provider変更はありません。
旧componentを停止/drainして対応版を使い、混在versionは保証しません。

## 検証境界

v23実装
[`bf53a30625ffcfb0f23f986abcec5e2d608dcb68`](https://github.com/rioriost/pg_agmemory/commit/bf53a30625ffcfb0f23f986abcec5e2d608dcb68)は
Apple Containerのfull `./scripts/test-containers.sh`で**821合格、warning 1件、499.94秒**でした。
完全一致SHAの[CI 35280254044](https://github.com/rioriost/pg_agmemory/actions/runs/35280254044)は、
amd64 **821合格、warning 1件、821.33秒**、arm64 **821合格、warning 1件、823.87秒**でした。
全3環境でRuff、mypy **source 19 + strict SDK consumer 1ファイル**、
全optional導入検査、episode照会を含む全production smokeも合格しました。
**821 = 既存780 + 新規41 case**で、contract 27、Native episode 7、
SDK 7（mock 6 + 実workflow 1）です。
検証済みcoverageはclosed model、timezone/range境界、同時刻100/101行、
遅い過去eventの順序、共有scopeのACL/purge/epoch、
audit書込みのないmetadata専用read-only SQL、typed SDK page、明示select/Explain/Rememberです。
[適格性確認の証拠](../STATUS-jp.md#v0023--schema-10)を参照してください。
これは実装の結果で、この更新の最終docs CIはまだ実行していません。
v22の初期実装、docs run失敗、検証済みgraph修正、別の最終docs CIは
[過去の証拠](../STATUS-jp.md#v0022--schema-10)であり、v23の適格性確認ではありません。
