# investment-agent リファクタ仕様書（目標組織 v6）

本書は既存コードを「目標組織図 v6」へ抜本的に入れ替えるための仕様。
Claude Code（Superpowers のレビュー/計画スキル）に渡して、現状との差分と
最小・安全な分割によるリファクタ計画を作る土台として使う。

## 0. 目的とスコープ
- 現行の煩雑さ（Critic の軸バラバラ、orphan、疑似パネル、CXO 肥大化）を解消する。
- 売買を (市場 × 戦略) の 4 実行エージェントに整理し、各々に専属クリティークを 1:1 で付ける。
- 全体リスクと資金配分を CIO に集約する。
- ペーパートレードを安全網として維持したまま段階的に移行する。

## 1. エージェント構成（CIO を除いて 12 体）

### 実行（4）＋専属クリティーク（4）
| 実行エージェント | 担当 | 時間軸 | 専属クリティーク |
|---|---|---|---|
| ScalpDay_JP | 日本株・超短期スキャル | 当日決済（秒〜時間） | ScalpDay_JP_Critic |
| ScalpDay_US | 米国株・超短期スキャル | 当日決済（秒〜時間） | ScalpDay_US_Critic |
| MomentSwing_JP | 日本株・モメンタム×スイング | 数日〜数週 | MomentSwing_JP_Critic |
| MomentSwing_US | 米国株・モメンタム×スイング | 数日〜数週 | MomentSwing_US_Critic |

- 戦略ロジックは `ScalpDayBase` / `MomentSwingBase` の 2 基底に置く。
- `_JP` / `_US` は薄いサブクラス。差し替えるのは broker・取引時間・呼値/単元のみ。
- 「バリュー」概念は廃止（選定基準はモメンタム/値動きベース）。

### 資金リバランス（1）＋専属クリティーク（1）
- FXRebalance：円⇄ドル配分を調整し、為替トレードを実行。配分提言を CIO に上げる。
- FXRebalance_Critic：1:1 のレビュー（旧 CriticFX の「審査経由」意図をここで実現）。

### 情報収集（1）＋専属クリティーク（1）
- IntelScout：テック情報を収集して KB へ、マクロ（金利/中銀/雇用統計等）をマクロ DB へ蓄積。
  さらに MarketContext の **観測層**（`macro_notes`・セクター活動度）を生成し CIO へ供給。
- IntelCritic：収集情報の正誤を多角的に審査（1:1）。

### 司令塔（CIO・上記 12 体に含めない）
- CIOAgent：MarketContext の **判断層**（`risk_level` / `rotation_signal`）を決定。
  各実行の提案＋FX 提言を見て「資金枠＋活性セクター」を各ポッドに配分する元締め。

## 2. データフローと責任分担
- MarketContext は 2 層。観測層＝IntelScout、判断層＝CIO。
  実装はデータクラスを分けてもよいし、1 個のまま「観測フィールドは IntelScout、
  判断フィールドは CIO が埋める/上書き」でもよい。
- テック情報 KB は株式 4 体すべてが参照・共有。マクロ DB は FXRebalance が参照。
- 上申：IntelScout（観測層）と FXRebalance（配分提言）が CIO に上げる。
- 配分：CIO → 各ポッドへ「資金枠 ＋ 活性セクター」を配る。
- ストア（テック情報 KB / マクロ DB）はエージェントではなくインフラ（将来 RAG/ベクタ DB）。

## 3. 銘柄ユニバースと選定ルール（4 体一律）
- 各ポッドのユニバース＝CIO の活性セクター内の銘柄群を基本とする。
- セクター外でも出来高・値動きが異常な銘柄を拾う「カタリスト例外枠」を 1〜2 銘柄だけ許可。
  （活性セクターは絶対の門番ではなく「主に見る範囲」）
- watchlist をセクタータグ付きに統一し、活性セクターで動的フィルタ。JP/US の固定リスト分裂を解消。

## 4. リスク/資金ゲート
- 横断の総量リスク・資金配分は CIO に集約（1:1 クリティークは自分のペアしか見ない）。
- `risk_level == "high"` の抑制は、CIO が該当ポッドの資金枠をゼロ/縮小で表現して一貫させる
  （セッションごとに勝手に止める現行挙動を、配分ゲートに寄せる）。

## 5. 相互議論
- 当面は軽い往復：提案 → 承認/否決＋修正指示 → 最大 1〜2 回修正で確定（現行 `_refine_and_review` 踏襲）。
- 自前 `BaseAgent` を継続。多ラウンドの本格対話・LangGraph は挙動安定後の後付け。

## 6. 旧 → 新 マッピング（差分の起点）
| 現行 | 変更 |
|---|---|
| DaytradeAgent（JP/US 兼用） | ScalpDay_JP / ScalpDay_US に分割（＋ ScalpDayBase） |
| USEquityAgent（screen_value・バリュー） | MomentSwing_US に改称＋選定ロジックをモメンタム化（＋ MomentSwingBase） |
| （JP の中長期実行は不在。EquityAgent は意見出しのみ） | MomentSwing_JP を新設（実行能力を追加） |
| EquityAgent（evaluate のみ・議論用） | 廃止（議論役は DiscussionOrchestrator ごと消滅） |
| CriticDayAgent | ScalpDay_JP_Critic へ |
| CriticUSAgent（US デイ＋バリュー両方を審査） | ScalpDay_US_Critic と MomentSwing_US_Critic に分割 |
| CriticEquityAgent（orphan） | 廃止（1:1 クリティークが役割を担う） |
| CriticFXAgent（未配線） | FXRebalance_Critic として実体化 |
| FXStrategyAgent | FXRebalance に改称（配分提言を CIO へ） |
| IntelligenceAgent | IntelScout に改称＋観測層生成を追加 |
| CriticIntelligenceAgent | IntelCritic へ |
| RiskManagerAgent（議論で拒否権） | CIO の配分ゲートに吸収（standalone 廃止） |
| DiscussionOrchestratorAgent（中央裁定） | 廃止し CIO へ吸収。suggest_reorganization は別管理 or 削除 |
| USEquityAgent.panel_review（疑似パネル） | 廃止（多視点は実エージェントの上申に一本化） |
| CIOAgent（生ニュース要約まで担当） | 判断層＋配分ゲートに専念。生情報の要約は IntelScout の観測層へ移管 |
| CXOAgent（内部で CIO/FX/Daytrade/Broker を new） | レポート/通知に専念。必要データは外から注入（依存注入） |
| 各 `_BASE_SYMBOLS` 固定リスト | セクタータグ付き watchlist に統一し動的フィルタ |

## 7. 着手順（局所 → 広域、各段階でテスト緑を維持）
1. 局所掃除：orphan（CriticEquity / CriticFX）削除、panel_review 除去、DiscussionOrchestrator 解体。
2. 実行 4 体の分割：ScalpDayBase / MomentSwingBase ＋ _JP/_US サブクラス化。MomentSwing_JP 新設。
3. 1:1 クリティーク整備：4 ペア＋FX＋Intel のペアを軽い往復で接続。
4. CIO ゲート＋MarketContext 2 層化：観測層=IntelScout、判断層=CIO、資金枠＋活性セクター配分。
5. データ層：watchlist のセクタータグ統一＋動的フィルタ、カタリスト例外枠。
6. CXO 整理：依存注入化、レポート/通知に専念。

## 8. スコープ外（今回はやらない・後で）
- 多ラウンドの本格対話、LangGraph 等のフレームワーク導入。
- RAG/ベクタ DB、Obsidian、NotebookLM、OpenAI セカンドオピニオン（Phase 3）。
- 実弾移行（ペーパートレードで挙動が安定してから別途判断）。

## 9. 安全策
- 作業は専用ブランチで。各段階の後にペーパートレードで挙動確認。
- 既存テスト（例: tests/test_org_reorg.py）は移行に合わせて更新し、常に緑を維持。
- 1 段階＝1 PR を目安に小さく刻む。レビューは「変更前に指摘 → 承認 → 着手」の順を守る。
