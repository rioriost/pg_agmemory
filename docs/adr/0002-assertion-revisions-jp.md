# ADR 0002: 同一assertionのrevision

[English](0002-assertion-revisions.md) | [現在の契約](../STATUS-jp.md) | [運用](../operations/README-jp.md)

- 日付: 2026-09-16
- 状態: 限定したv0.0.2 milestoneを実装済み。ローカル/native Docker検査は合格、M1全体は未完了
- 置換対象: [ADR 0001](0001-initial-slice-jp.md)のrevision 1のみのモデルと
  同一assertion訂正の延期。初期architecture全体を置き換えるものではない

**v0.0.2当時の記録です。** [ADR 0003](0003-checkpoints-jp.md)で
checkpoint/削除境界を拡張し、v0.0.3の要求schemaを3へ進めます。
以下の決定と検証結果は引き続きv0.0.2の範囲に限定します。

## 範囲と決定

tenant、scope、subject、predicateを不変とする安定したassertion identityを維持します。
value、valid/system interval、reported status、explicit intent、訂正理由、根拠を
revision固有の記録へ分離します。`remember`はrevision 1を作成し、
訂正は総数1000までrevisionを追加します。episodeは引き続きrevision 1のみです。

これは**valid interval全体の置換**であり、部分期間の分割、
別assertion間のsupersession、fact keyの調停ではありません。例:

| 状態 | 照会 | このassertionの結果 |
|---|---|---|
| Revision 1は無限端のvalid timeでGoldを報告 | system timeで訂正前に9月16日を照会 | Gold、revision 1 |
| Revision 2で10月1日から有効なPlatinumへ置換 | system timeで訂正後に9月16日を照会 | 一致するassertion revisionなし |
| 同じrevision 2 | system timeで訂正後に10月2日を照会 | Platinum、revision 2 |

10月1日までGoldを維持する未来の変更予約ではありません。
現在の知識では旧valid coverageを置換しますが、以前の`known_at`なら
当時の認識を照会できます。ただし現在のACLと削除状態を適用します。
valid boundの省略は旧boundの継承ではなく無限端へのresetです。

## APIと並行処理

`POST /v1/assertions/{memory_id}/revisions`には`Idempotency-Key`と
[状態](../STATUS-jp.md#assertion-revisionの契約)で定義するbody全体が必要です。
`expected_revision`（整数1〜1000）、`value`、
同一scopeの原文引用を持つ重複しない1〜32件のepisode `evidence`、
`explicit_intent: true`、任意の`valid_from`/`valid_to`、
1〜256文字の`reason`を指定します。subject/predicate/scopeはこのendpointで変更できません。

既存のtenant session lock、短いtransaction、buffer済み応答の
commit-before-send境界を維持します。サービスはread/write権限とexpected headを確認します。
`201`で`memory_id`、revision `expected_revision + 1`、
`epistemic_status: "reported"`を返します。不一致は`409 revision_conflict`、
head 1000からrevision 1001を作ろうとすると`422 revision_limit_exceeded`です。
非公開、削除済み、assertion以外の対象は`404`です。

正規化したrequest hashには対象の`memory_id`を含めます。
commit済み同一keyの完全一致再送は、新headとの比較より前に確認し、
後続訂正があっても元のrevision参照を返します。同じkeyで対象やpayloadを変えれば
conflictです。再利用前に現在のアクセス権/tombstoneを確認します。
idempotencyを過去の認可で現在の制限を回避する手段にはしません。

## DBの不変条件

`memory.assertion`はidentityと`current_revision`を、
`memory.assertion_revision`は版ごとの内容を保持します。
`memory.provenance_edge.child_revision`で各根拠引用を正確なrevisionに束縛します。
tenant/scope複合外部キーとRLSを引き続き必須とします。
原文引用の検査はsource spanの確認であり、意味的な真実の認定ではありません。
statusは`reported`、confidenceはnullのままです。

BEFORE INSERT triggerの`memory.adopt_revision`がassertion headをlockし、
`clock_timestamp()`で旧system rangeを閉じ、headを原子的に進めます。
新system rangeもtriggerが設定し、callerによるsystem timeの指定や遡及はできません。
閉じたrevisionは元の開始時刻を維持します。system intervalは連続する`[)`で、
headだけが終了未定です。`btree_gist`を使うGiST排他制約で重複を拒否し、
遅延history/evidence制約で期間gap、revision欠落、根拠のないrevisionを拒否します。
trigger functionは`SECURITY DEFINER`を使いません。

`recall`は`as_of`と`known_at`の両方を評価し、identity textと
選ばれたrevisionのvalueを検索します。返すrevisionとsourceは同じ版に属し、
旧本文と現在の根拠を混在させません。`explain`は1〜1000のrevisionを明示できますが、
互換性のため省略時は**最新ではなくrevision 1**です。
revision固有の根拠とともに`recorded_at`、`known_until`、
`correction_reason`を返します。episodeは1のみで、存在しない版は`404`です。

## 削除とmigration

どのrevisionでも利用したsourceをpurgeすると、
保守的に**assertion履歴全体**を削除します。現revisionや対象sourceのedgeだけでなく、
全revisionの値・理由・根拠を削除します。
episode → assertion closureの上限は引き続き10,000件の派生assertionです。
同じtransactionでpayloadを先に削除してから`memory_ops.object_tombstone`へ挿入します。
過去のexplain/recall/replayでも現在のtombstoneや権限を回避しません。
opaque/HMAC記録とoperator-managed backupが残るため、完全消去とは主張できません。

`001_initial.sql`を変更せず、同梱の`002_assertion_revisions.sql`で
既存の値、valid/system interval、status、根拠をrevision 1へbackfillします。
実際の時刻をresetせず、adoption triggerはbackfillの後に導入します。
source-event/idempotency記録を維持し、完全一致再送のために
旧requestのserialization順序との互換性も維持する必要があります。

migration loopは連続履歴を検証し、未適用scriptとledger記録を原子的に適用します。
再実行可能で、lock timeoutは5秒です。
migration 002には`btree_gist`と、必要なDDL権限を持つsuperuserまたは
適格な`BYPASSRLS`管理者が必要です。`row_security = off`は、
可視tenantだけを黙ってbackfillするのではなくRLS filter時にfail-closedにします。
bypass権限を付与する設定ではなく、runtimeは非特権のままです。

旧版・新版の全API traffic/processを停止し、backupとmigration後に
対応する新APIだけを起動します。新版の起動検査はledgerが厳密に`[1, 2]`であることを
要求します。**旧APIには同等のguardがなく、停止を維持しなければなりません。**
旧APIとのrolling共存やdowngrade経路はありません。
[現在の保守protocol](../operations/README-jp.md#v003の保守migration)に従い、
backup restoreでは引き続き隔離と最新削除/ACLのreplayを必要とします。

## 検証と残る範囲

v0.0.2のcommit 5458402について、Apple Containerとnative Dockerの
linux/amd64・linux/arm64で、それぞれ32テスト、Ruff、mypy、
production HTTP health smokeが合格しました。
確認済みCI log、migrationと一括投入する境界fixture、公開HTTPの境界検査は
[状態](../STATUS-jp.md#検証証拠)を参照してください。
本ADRはM1完了、性能/品質の測定、DRを主張しません。
checkpoint、worker、synthesis、vector、graph、MCP、SDK、
別assertion間のsupersessionはこのmilestoneの対象外です。
