"""
agents/risk_manager.py
リスクマネージャーエージェント：
ポートフォリオ全体のリスクを統合管理し、定期レポートと取引停止判断を担う。
個別銘柄の売買判断には介入せず、システム全体のリスク総量を監視する。
"""
import json
from agents.base import BaseAgent, MarketContext
from prompts.loader import get_prompt
from config.settings import RISK


class RiskManagerAgent(BaseAgent):
    name = "RiskManagerAgent"
    system_prompt = get_prompt("risk_manager")

    @staticmethod
    def is_large_order(order_amount_jpy: float, portfolio_total_jpy: float) -> bool:
        """
        大口注文判定：発注比率 40% 以上 かつ 1,000万円以上 の場合のみ True。
        両条件を同時に満たす場合のみ大口判定 → CxO へエスカレーション。
        """
        ratio = order_amount_jpy / portfolio_total_jpy if portfolio_total_jpy > 0 else 0
        return ratio >= 0.40 and order_amount_jpy >= 10_000_000

    def check_trading_suspended(self, daily_loss_jpy: float) -> bool:
        """日次損失上限を超えた場合に取引停止フラグを返す。"""
        suspended = daily_loss_jpy >= RISK.max_loss_per_day_jpy
        if suspended:
            self.logger.warning(
                f"取引停止: 日次損失 {daily_loss_jpy:,}円 が上限 {RISK.max_loss_per_day_jpy:,}円 を超過"
            )
        return suspended

    def generate_report(
        self,
        positions: list[dict],
        portfolio_total_jpy: float,
        daily_loss_jpy: float,
        margin_used_jpy: float = 0,
        ctx: MarketContext | None = None,
    ) -> dict:
        """
        ポートフォリオ全体のリスクレポートを生成する。
        positions:           保有ポジション一覧 [{"symbol":..., "sector":..., "value_jpy":...}]
        portfolio_total_jpy: 総資産額（円換算）
        daily_loss_jpy:      当日の実現損失額（円）
        margin_used_jpy:     信用建玉合計額（円）
        """
        daily_loss_pct = (daily_loss_jpy / RISK.max_loss_per_day_jpy * 100) if RISK.max_loss_per_day_jpy > 0 else 0
        margin_ratio = (margin_used_jpy / portfolio_total_jpy) if portfolio_total_jpy > 0 else 0

        prompt = f"""
## 現在のポートフォリオ状況
{json.dumps(positions, ensure_ascii=False, indent=2)}

## リスク指標
- ポートフォリオ総額: {portfolio_total_jpy:,.0f}円
- 当日実現損失: {daily_loss_jpy:,.0f}円（上限 {RISK.max_loss_per_day_jpy:,}円 の {daily_loss_pct:.1f}%）
- 信用倍率: {margin_ratio:.2f}倍（上限: 2.0倍）

## 市場コンテキスト
{f"- リスク水準: {ctx.risk_level}" if ctx else "（未取得）"}

上記を踏まえ、ポートフォリオリスクレポートを JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            self.logger.warning("RiskManager: LLM応答失敗。計算値ベースのレポートを返します")
            return {
                "portfolio_risk_level": "medium",
                "daily_loss_used_pct": round(daily_loss_pct, 1),
                "sector_concentration": {},
                "margin_ratio": round(margin_ratio, 2),
                "alerts": ["LLM応答失敗のため詳細分析不可"],
                "trading_suspended": self.check_trading_suspended(daily_loss_jpy),
            }

        self.logger.info(
            f"リスクレポート: level={data.get('portfolio_risk_level')} "
            f"日次消化率={data.get('daily_loss_used_pct')}% "
            f"取引停止={data.get('trading_suspended')}"
        )
        return data
