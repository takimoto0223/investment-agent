# v6 リファクタ 設計記録

> **ステータス**: v6 組織リファクタ①〜⑥、命名一気通貫、市場ガード、IntelScout 収集スケジュール、
> データクラス改名（2026-06-19 完了）。**残タスクは [docs/backlog.md](backlog.md) を参照。**

---

## report/template.py データクラス改名（完了）

v6 命名一気通貫（2026-06-19）では `report/template.py` 内のデータクラス名は
HTML レポート生成コードへの連鎖が大きいためスコープ外とし、後続ステップで実施。
2026-06-19 のステップ3で完了。

### 改名結果

| 旧名 | 新名（実装済み） | 変更ファイル |
|---|---|---|
| `DaytradeCandidate` | `ScalpDayCandidate` | template.py, cxo.py, test_cxo_report.py, main.py, verify_cxo_reports.py |
| `ValueDecision` | `SwingDecision` | template.py, cxo.py, test_cxo_report.py |
| `MorningReportData.daytrade_candidates` | `scalpday_candidates` | template.py, cxo.py |
| `MorningReportData.value_decisions` | `swing_decisions` | template.py, cxo.py |
| `_daytrade_table()` | `_scalpday_candidate_table()` | template.py |

### 改名しなかったフィールド（意図的）

`daytrade_records` / `daytrade_gross_pl` / `daytrade_fees` / `daytrade_net_pl` は
`DaytradeRecord` / `calc_daytrade_pl()` と対になるドメイン用語（Alpaca の制度的概念）のため
改名対象から外した。

HTML テンプレート内の「デイトレ」「本日デイトレ候補」等の日本語ラベルも、
Python クラス名とは独立した UI テキストとして維持（変更すると表示が変わる）。

### 命名の設計意図

- **ScalpDayCandidate**（候補）: ScalpDay スクリーニングを通過した段階。Critic 審査前。
  `signal=buy|sell` はシグナル方向のみ持ち、qty・SL/TP は未確定。
- **SwingDecision**（決定）: MomentSwing Critic 審査後の buy/reject 結論。
  `qty` と `consensus` を持ち、発注または見送りが確定済み。
  両者は「候補（審査前）」vs「決定（審査後）」という意味的非対称を型名に反映している。

---

## 参考: v6 組織リファクタの設計判断サマリー

### CIO への戦略配分の集約

MarketContext の生成と資金配分（`allocate_budgets`）を CIOAgent 1 か所に集約した。
旧来の RiskManagerAgent が持っていた「risk=high 時に全発注を止める」拒否権は
CIO の `budget=0` ゲートとして吸収。エージェント数を減らしつつ同等の安全弁を維持している。

### 1:1 クリティーク（Critic ← Agent ペア）

各トレーダーエージェント（ScalpDay_JP/US、MomentSwing_JP/US 等）に専用 Critic を 1:1 対応させる。
パネル議論（旧 Group B）は廃止し、Critic → revise 1往復に統一。
実験コストと複雑性を下げ、否決理由のトレーサビリティを上げることが目的。

### MarketContext 二層分離

CIO が生成する `MarketContext`（マクロ・セクタースコア・リスク水準）と、
`CXOReportContext`（ブローカー残高・ポジション等の実口座データ）を別クラスに分離した。
CIO は市場判断だけを担い、CXO がレポート生成時に両者を合成する設計。

### v6 命名規則

エージェント名は `{戦略}_{市場}` 形式（例: `ScalpDay_JP`、`MomentSwing_US`）。
データクラスも `{戦略}{役割}` 形式に揃える（例: `ScalpDayCandidate`、`SwingDecision`）。
