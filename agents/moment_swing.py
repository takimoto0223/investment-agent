"""
agents/moment_swing.py
モメンタム×スイング戦略エージェント群。

MomentSwingBase  ─ スクリーニング・提案生成・修正の戦略ロジック（市場非依存）
  MomentSwing_US ─ 米国株モメンタム×スイング（Alpaca・USD建て）
  MomentSwing_JP ─ 日本株モメンタム×スイング（kabu API・JPY建て）

市場依存部分（サブクラスに実装）:
  _market           "JP" | "US"
  _currency         "JPY" | "USD"
  _currency_symbol  "¥"  | "$"
  _position_limit_text()  ブローカー・単元・枠上限を文字列で返す
"""
import json
import logging
from agents.base import BaseAgent, MarketContext, TradeProposal

logger = logging.getLogger(__name__)


class MomentSwingBase(BaseAgent):
    """モメンタム×スイング戦略ロジックの基底。市場差分はサブクラスに委譲する。"""

    _market: str = ""
    _currency: str = ""
    _currency_symbol: str = ""

    def _position_limit_text(self) -> str:
        raise NotImplementedError

    def screen_value(
        self,
        universe: list[dict],
        ctx: MarketContext,
        existing_symbols: list[str],
        max_position: float,
        cash: float,
    ) -> list[TradeProposal]:
        """
        ユニバースから保有候補を絞り込みTradeProposalリストを返す。
        max_position / cash の通貨単位はサブクラスの _currency に従う。
        existing_symbols に含まれる銘柄はスキップ。
        """
        available = [u for u in universe if u["symbol"] not in existing_symbols]
        if not available:
            self.logger.info(f"{self.name}: スクリーニング対象なし（全銘柄保有済み）")
            return []

        sym = self._currency_symbol
        cur = self._currency
        prompt = f"""
## 候補銘柄ユニバース
{json.dumps(available, ensure_ascii=False, indent=2)}

## 市場コンテキスト（CIO判断）
- リスク水準: {ctx.risk_level}
- セクターローテーション: {ctx.rotation_signal}
- セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}
- マクロノート: {ctx.macro_notes}

## 制約
- 利用可能キャッシュ: {sym}{cash:,.0f} {cur}
- 1銘柄最大投資額: {sym}{max_position:,.0f} {cur}
- リスク水準が "high" の場合は提案数を1銘柄に絞ること

## フィールド定義（ユニバース内の数値の読み方）
- ret_5d_pct  : 直近5営業日の価格リターン（%）。短期モメンタムの強さ
- ret_20d_pct : 直近20営業日の価格リターン（%）。中期トレンド方向の確認
- price_change_pct : 前日比変化率（当日のみ）
- volume_ratio : 本日出来高 ÷ 直近20日平均出来高（1.0 = 平均並み）
- atr_pct : ATR（ボラティリティ）÷ 株価 × 100

## 指示
モメンタム×スイング戦略の視点で銘柄を選定してください（保有期間の目安：数日〜数週間）。

選定の優先基準（順に重視）:
1. 価格モメンタム：ret_5d_pct と ret_20d_pct の両方がプラス・かつ継続中であること。
   片方だけ強い場合は優先度を下げる（例：5日は強いが20日はマイナス＝短期過熱の疑い）
2. 出来高増加：volume_ratio が 1.5 以上かつ増加トレンド
3. 相対強度：セクター内・市場全体に対して強い（ret_5d_pct・ret_20d_pct での比較を活用）
4. セクター一致：CIO の活性セクター（スコア上位）に属している（カタリスト例外は1〜2枠）
5. ブレイクアウト/プルバック：明確なテクニカルシグナルがある

除外条件:
- 出来高が薄い（流動性リスク）
- 直近で急騰しすぎた（ret_5d_pct が極端に高い、過熱・リターンリバーサルリスク）
- リスク水準 "high" 時は最も確度が高い1銘柄のみ

以下のJSON配列で返してください。候補なければ空配列[]：

[
  {{
    "symbol": "TICKER",
    "name": "銘柄名",
    "qty": 株数(整数、max_position÷概算株価で算出),
    "price": 0,
    "rationale": "選定理由（モメンタム・テクニカル・セクターとの整合性、100文字以内）",
    "stop_loss_pct": 損切り割合(例: 0.06 = 6%下落で損切り),
    "target_return_pct": 目標リターン(例: 0.12 = 12%)
  }}
]
"""
        data = self._ask_llm_json(prompt)
        proposals: list[TradeProposal] = []
        if not isinstance(data, list):
            return proposals

        for item in data:
            try:
                proposals.append(TradeProposal(
                    agent=self.name,
                    symbol=item["symbol"],
                    market=self._market,
                    side="buy",
                    qty=max(1, int(item.get("qty", 1))),
                    price=0.0,
                    strategy="momentum_swing",
                    rationale=item.get("rationale", ""),
                    stop_loss=None,
                    take_profit=None,
                    extra={
                        "hold_horizon":      "days_to_weeks",
                        "stop_loss_pct":     float(item.get("stop_loss_pct", 0.06)),
                        "target_return_pct": float(item.get("target_return_pct", 0.12)),
                        "name":              item.get("name", item["symbol"]),
                    },
                ))
            except (KeyError, ValueError, TypeError) as e:
                self.logger.warning(f"{self.name}: proposalパース失敗 {e}")

        return proposals

    def revise_proposal(
        self,
        proposal: "TradeProposal",
        issues: list[str],
        suggestion: str,
        ctx: "MarketContext",
    ) -> "TradeProposal | None":
        prop_dict = {
            "symbol":         proposal.symbol,
            "qty":            proposal.qty,
            "stop_loss_pct":  proposal.extra.get("stop_loss_pct", 0.06),
            "target_return":  proposal.extra.get("target_return_pct", 0.12),
            "rationale":      proposal.rationale,
        }
        prompt = f"""
あなたがモメンタム×スイング提案を出したところ、審査担当（Critic・Opusモデル）から以下の指摘を受けました。
指摘を踏まえて提案を修正してください。

## あなたの元の提案
{json.dumps(prop_dict, ensure_ascii=False, indent=2)}

## 市場コンテキスト（CIO判断）
- リスク水準: {ctx.risk_level}
- セクターローテーション: {ctx.rotation_signal}

## 審査担当の指摘
{json.dumps(issues, ensure_ascii=False)}

## 修正ヒント
{suggestion}

## 修正の注意点
- stop_loss_pct: 5〜12% の範囲で設定（モメンタム戦略では浅めのSLを推奨）
- target_return_pct: stop_loss_pct × 1.5 以上（R:R ≥ 1.5 を維持）
- {self._position_limit_text()}
- モメンタムが崩れた（逆行・出来高急減）場合は {{"action": "withdraw"}} を返す

{{
  "action": "buy" | "withdraw",
  "qty": 整数,
  "stop_loss_pct": 損切り割合（例 0.06 = 6%）,
  "target_return_pct": 目標リターン割合,
  "rationale": "修正後の根拠（80文字以内）"
}}
"""
        data = self._ask_llm_json(prompt)
        if not data or data.get("action") == "withdraw":
            self.logger.info(f"{proposal.symbol}: 投資提案修正断念")
            return None

        proposal.qty = max(1, int(data.get("qty", proposal.qty)))
        sl_pct = float(data.get("stop_loss_pct",    proposal.extra.get("stop_loss_pct", 0.06)))
        tp_pct = float(data.get("target_return_pct", proposal.extra.get("target_return_pct", 0.12)))
        proposal.extra["stop_loss_pct"]     = sl_pct
        proposal.extra["target_return_pct"] = tp_pct
        proposal.rationale = data.get("rationale", proposal.rationale)
        if proposal.price and proposal.price > 0:
            proposal.stop_loss   = round(proposal.price * (1 - sl_pct), 4)
            proposal.take_profit = round(proposal.price * (1 + tp_pct), 4)
        self.logger.info(
            f"{proposal.symbol}: 投資提案修正完了 qty={proposal.qty} "
            f"SL={proposal.stop_loss} TP={proposal.take_profit}"
        )
        return proposal


class MomentSwing_US(MomentSwingBase):
    """米国株モメンタム×スイング（Alpaca・USD建て、保有期間：数日〜数週間）。"""
    name = "MomentSwing_US"
    system_prompt = (
        "あなたは米国株のモメンタム×スイングトレード専門家です。\n"
        "価格モメンタム・出来高増加・相対強度・テクニカルシグナルを軸に、\n"
        "数日〜数週間の保有で最大リターンを狙う銘柄を選定します。\n"
        "セクターローテーションとCIOの活性セクター判断を重視し、\n"
        "出来高が薄い銘柄・過熱した銘柄は対象外とします。"
    )
    _market = "US"
    _currency = "USD"
    _currency_symbol = "$"

    def _position_limit_text(self) -> str:
        from config.settings import RISK
        return f"qty: ドル建て上限 ${RISK.max_us_position_usd:,} ÷ 株価で算出"


class MomentSwing_JP(MomentSwingBase):
    """日本株モメンタム×スイング（kabu API・JPY建て、保有期間：数日〜数週間）。"""
    name = "MomentSwing_JP"
    system_prompt = (
        "あなたは日本株のモメンタム×スイングトレード専門家です。\n"
        "価格モメンタム・出来高増加・相対強度・テクニカルシグナルを軸に、\n"
        "数日〜数週間の保有で最大リターンを狙う銘柄を選定します。\n"
        "セクターローテーションとCIOの活性セクター判断を重視し、\n"
        "出来高が薄い銘柄・過熱した銘柄・信用倍率が極端な銘柄は対象外とします。"
    )
    _market = "JP"
    _currency = "JPY"
    _currency_symbol = "¥"
    _min_unit: int = 100  # 100株単位

    def _position_limit_text(self) -> str:
        from config.settings import RISK
        return (
            f"qty: 円建て上限 ¥{RISK.max_position_jpy:,} ÷ 株価で算出（100株単位に切り捨て）"
        )
