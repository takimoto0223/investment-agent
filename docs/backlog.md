# Backlog

## 来週(kabu API到着後)
- JP実弾配線: 疎通確認(トークン+1銘柄の残高/板) → KabuBroker実API化でモック前提とのズレ潰し
  → 発注せず実データで提案〜枠計算のドライラン → 少額で実発注、の順
- API確認: eスマートで円⇄ドル両替がAPIで可能か / 外貨決済(ドルで米株)の仕組みがあるか、
  Alpacaの日本からの入金・通貨扱い。FXRebalanceの両替設計に直結

## マクロデータの実勢取得（積み残し）
- USD/JPY: `data/fx_rate.py` (Frankfurter API) で実勢取得済み（2026-06-19〜）
- VIX・米10Y: `main.py` の `_MACRO_DATA` 文字列で固定値（VIX=18.5, 米10Y=4.35%）のまま、実勢取得は未対応

## FXRebalance(両替役に徹する・確定方針)
- レートAPI(Exchange Rate API / CurrencyFreaks 等、無料〜月数ドル)で高値掴み回避の見送りロジック
  (161円等の不利なレートでは両替を実行せず待つ。建玉の逆指値ではなく条件付き両替)
- 口座間の国際送金は自動ループに入れない(必要時のみ手動)。JPはJP内・USはUS内で資金完結が基本
- 投機/建玉/FX取引APIは不要(確定)

## 筋肉づけ(各トレーダーの腕を鍛える)
- データ供給(先) → 判断ノウハウのスキル化(後)の二段
- 武器候補: 板読み、複数時間軸ローソク足、チャートパターンと勝率、信用残、逆指値
- MomentSwing_USへの5/20日リターン追加は完了済み(一段目の実例)

## MomentSwing_US バックテスト深掘り(保留)
- SL/TP感度チェック(最良値探しでなく頑健性確認・過剰最適化回避)
- 複数ポジション許可は第二段

## 掃除の積み残し(今回スコープ外)
- verify_cxo_reports.py のキーワードチェックが any() で形骸化 → 意味あるチェックに直すか外す
- レポート内容の非対称: ScalpDayは候補、MomentSwingは決定済み結果を表示している件の整理

## ツール導入の合図(覚えるため)
- Obsidian: IntelScoutのダイジェスト(logs/digests/)が溜まって見返したくなったら
- MCP: Obsidianやcode以外の外部(Langfuse等)をClaudeから直接触りたくなったら

## 完了済み(参考)
- v6組織リファクタ①〜⑥、市場ガード、IntelScout収集スケジュール、命名一気通貫、デッドコード掃除
- 命名積み残し(template.py等): DaytradeCandidate→ScalpDayCandidate / ValueDecision→SwingDecision 完了
- MomentSwing_US: build_us_universe() に ret_5d_pct / ret_20d_pct 追加(プロンプト要求と実入力の整合修正) 完了
