# ADR 0005: Canonical relational graphと上限付きSQL oracle

[English](0005-relational-graph.md) | [現在の契約](../STATUS-jp.md#entityとsql-graph-oracle) | [運用](../operations/README-jp.md#entityとgraphの運用)

- 日付: 2026-09-16
- 状態: v0.0.5/schema 5を実装済み。ローカルとnative Dockerの検査は合格。M0/M1/M3全体は未完了
- 拡張対象: [ADR 0002](0002-assertion-revisions-jp.md)、
  [ADR 0003](0003-checkpoints-jp.md)、[ADR 0004](0004-tool-effects-jp.md)の
  assertion revision、宣言済みcheckpoint/effect依存、purge
- 命名: ローカルdirectory/package/serviceは`pg_agmemory`、公開repositoryは`rioriost/pgag_memory`

過去の範囲: 以下のschema 5要件と証拠はv5時点のものです。
[ADR 0006](0006-durable-jobs-jp.md)がdurable jobとschema 6を追加します。
API/worker更新には本ADRの過去schema要件ではなく、現在の運用手順を使ってください。

## 決定と範囲

canonical PostgreSQL tableと固定parameterized SQL joinをgraphの正しさの比較基準にします。
AGE、SQL/PGQ、Cypher、request由来の動的SQL/label、projectionはなく、
recallへgraph自動利用を追加しません。将来backendの適合性を比較する上限付きoracleであり、
graph有用性の測定、M1/M3全体の受入、MVP、本番/性能/品質/DRの適格性を意味しません。

## Entity解決でなく明示identity

`POST /v1/entities`は不変のrevision 1 memory anchorを作成します。
scope UUID、allowlist内のentity type（`person`、`organization`、`project`、
`component`、`incident`、`task`、`decision`、`other`）、1〜256文字のcanonical label、
同一scopeの読取り可能で重複しないepisode ID 1〜32件と各1〜4,096文字の原文quote、
`explicit_intent: true`を要求します。原文一致はprovenanceであり意味的支持ではありません。
identity metadataはcaller申告で、検証済みfactではなく、
名前/typeを信頼できる指示として扱ってはいけません。

alias、entity merge、名前ベースの解決、意味的重複抑止、label訂正endpointはありません。
同じHTTP冪等性key/bodyはanchorを再利用しますが、別keyなら同名の別identityを作成し得ます。
`GET /v1/entities/{memory_id}`がmetadataと根拠を返し、entityはrecall/explainから除外します。
checkpoint/effectの`memory_refs`はepisodeと正確なassertion revisionに加え、
entity revision 1を許可します。

## 一つのassertion identityと時間履歴

`POST /v1/relations`はscope、source/target entity UUID、allowlist内predicate、
同一scopeのepisode原文根拠、明示intentを要求します。
**両endpointと根拠はすべて同一scope**でなければなりません。
predicateは`depends_on`、`part_of`、`affects`、`works_for`、`decides`で、
すべて複数値を許すreportedな申告です。truth調停や逆向きfactの推論はありません。

relationはcanonical assertionの`memory_id`であり、第二のmemory objectではありません。
`memory.relation`がsourceを固定し、`memory.relation_revision`が正確な各assertion
revisionへtargetを対応付けます。subjectは不変のsource canonical label、
各revisionの不変valueはそのtarget canonical labelです。根拠、reported truth status、
valid interval、server管理のsystem履歴は既存assertion revisionを使い、
並行したgraph modelを作りません。作成応答は既存の`RememberResult`のID/revision/
reported statusです。label/predicateが一致するfree-text `remember`もuntypedのままです。

`POST /v1/relations/{memory_id}/revisions`は厳密な整数1〜1000の`expected_revision`、
target UUID、完全なepisode根拠、明示intent、任意のtimezone付き`valid_from`/`valid_to`、
1〜256文字のreasonを要求します。source/predicateは固定です。
valid interval全体を置換し、boundの省略/nullは維持でなく無限端です。
旧target参照、根拠、時間をrevisionごとに維持します。
CAS、過去結果の冪等再送、全1000 revision上限は変わりません。
汎用assertion訂正は`409 relation_revision_required`を返します。

relationは通常のFTS assertion候補で、`graph_used: false`を維持します。
recall itemとassertion explainへ正確なrevisionのnullableな`relation` endpointを追加します。
context本文は両UUIDを既存UTF-8 byte予算内に含めます。
explainのrevision省略は引き続き最新でなく1です。

## 上限付き認可graph読取り

認証必須の`POST /v1/graph/expand`は読取り専用で`Idempotency-Key`は不要です。
重複しないscope 1〜32件、entity seed 1〜16件、許可済みrelation type 1〜5件、purposeが
必須です。directionはoutgoing（既定）、incoming、bothです。
厳密な上限は1〜2 hop（既定2）、1〜100 path（既定100）です。
任意のtimezone付き`as_of`/`known_at`の既定値は展開ごとに一度だけ取得します。

scope/seed filterはアクセスを狭めるだけです。現在のRLS、同一scope外部key、
時間filter、既存のtenant transaction/response-drain lockをseed、中間node、edge、根拠に
適用します。非公開、不在、時間条件で利用不能なseedは黙って除外し、応答に再掲しません。

固定neighbor SQLで決定的な幅優先simple pathを生成し、seed UUID順、続いて各hopの
assertion ID/revision/次UUID順とします。同一path内でentityを反復しません。
全prefixをglobal path上限に数え、limit-plus-one probeで打切りを判定します。
意味的cycleでもnodeを反復するpathは作りません。
incoming/bothは探索方向であり、新しい逆向きassertionではありません。

応答は`backend: "sql"`、`projection_watermark: null`、実効時間、access/deletion epoch、
quoteなしのcanonical node summary、正確なassertion参照/endpoint/predicate/valid interval/
記録時刻/reported status付きedge、node UUIDとassertion参照のpathを返します。
coverageは`max_hops`、`truncated`、`complete_within_bounds`を明示します。
canonical joinなのでprojection/lag/watermark保証は不要です。

pathがなければ`empty_reason: "not_found"`、あればnullです。
可視の孤立seedはpathなしでも現れ得ます。pathなしや上限内の完全性はfact不在の証明ではありません。
根拠quoteはentity GET/relation explainで取得します。
最終再検査でnodeが欠ければ`409 graph_invalidated`、
DB障害は`503`であり、空の成功応答へfallbackしません。
capabilitiesは`graph_backend: "sql"`、entity/relation type、graph上限を公開します。

## 依存closureと保存guard

entityはepisodeだけに依存するため、意味的relation cycleはprovenance cycleを追加しません。
削除はepisode根拠 → entity → そのentityをsourceまたは**過去のどのtargetとしてでも**
使うrelation → assertion全履歴 → 宣言済みcheckpoint/effect参照と子孫/fork checkpointへ
伝播します。直接entity purgeとcheckpoint/effectへの直接entity参照も対象です。
relationが消えただけで他の生存entityは削除しません。

どのeffectでもpurgeすると、引き続きrunの全checkpoint payloadを消してrunを永久封鎖し、
独立した生存effectの照合は可能です。payload、entity根拠、typed link、quote、影響するreceiptを
opaque tombstone挿入より先に原子的にpurgeします。過去参照/再送からlabel、value、receiptを
復活させることはできません。opaque anchor/idempotencyは残り、backup/WAL/配信済みdata/
restore隔離の制限は変わりません。callerはコピーしたすべてのentityまたはassertion revision
依存を`memory_refs`へ宣言しなければなりません。

追加的な`005_relational_graph.sql`は001〜004を変更しません。
`entity`、`entity_evidence`、`relation`、`relation_revision`、RLS、同一scope外部key、
checkpoint/effectのentity参照kindを追加し、runtimeへpayload UPDATE権限を与えません。
遅延検査で完全なentity根拠と各relation revisionの正確なtyped target/valueを要求します。
marker/linkの除去やgeneric value変更でtyped契約を回避できません。
`assertion.is_relation DEFAULT false`で旧free-text dataを保護し、
`Remember` JSON field/hash順、checkpoint checksum、旧履歴は変更しません。

全API trafficを停止し、backup、`pg-agmemory migrate`の後、
正確なschema履歴`[1, 2, 3, 4, 5]`を確認してv5だけを再起動します。
rolling共存やdowngradeは非対応です。旧v0.0.1には起動schema guardがなく、停止を維持します。

## 証拠の境界

Apple Containerとnative Docker amd64/arm64の各環境で91テスト（既存warning 2件）、
Ruff、strict mypy（source 9ファイル）、production HTTP health smokeが合格しました。
2 tenantのgraph golden/時間/非公開/予算/削除ケース、v4 effect/履歴と
v3 checkpoint checksum/idempotency互換性、free-text/typed relation assertion両方の
正確な1000 revision境界を検査しています。最終commit/CI linkとローカルでの
強化ケースの追加確認は[STATUS](../STATUS-jp.md#検証証拠)を参照してください。
M0/M1/M3全体の完了や、性能、記憶品質、MVP/本番、完全消去、backup/DRの適格性を
示すものではありません。
