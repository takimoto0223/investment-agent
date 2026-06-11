"""
agents/critic_equity.py
バリュー株提案審査エージェント：
バリュー株選定エージェントの提案を PF 集中度・大口判定・CIO 整合性の観点で審査する。
"""
import json
from agents.base import BaseAgent, MarketContext, TradeProposal, CriticVerdict
from prompts.loader import get_prompt
from config.settings import RISK


class CriticEquityAgent(BaseAgent):
    name = "CriticEquityAgent"
    system_prompt = get_prompt("critic_equity")
    model = "claude-opus-4-8"

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        portfolio_total_jpy: float,
    ) -> CriticVerdict:
        """
        バリュー株提案を審査し、CriticVerdict を返す。
        portfolio_total_jpy: 現在のポートフォリオ総額（円換算）
        """
        order_amount = proposal.price * proposal.qty if proposal.price > 0 else 0
        concentration_pct = (order_amount / portfolio_total_jpy * 100) if portfolio_total_jpy > 0 else 0

        prompt = f"""
## 審査対象の取引提案
{json.dumps({
    "symbol": proposal.symbol,
    "market": proposal.market,
    "side": proposal.side,
    "qty": proposal.qty,
    "price": proposal.price,
    "strategy": proposal.strategy,
    "rationale": proposal.rationale,
    "stop_loss": proposal.stop_loss,
    "take_profit": proposal.take_profit,
    **proposal.extra,
}, ensure_ascii=False, indent=2)}

## ポートフォリオ情報
- ポートフォリオ総額（概算）: {portfolio_total_jpy:,.0f}円
- 当該銘柄発注後の推定集中度: {concentration_pct:.1f}%
- 発注想定額（概算）: {order_amount:,.0f}円

## 現在の市場コンテキスト
- リスク水準: {ctx.risk_level}
- セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            self.logger.warning("CriticEquity: LLM応答失敗。デフォルト否決を返します")
            return CriticVerdict(
                approved=False,
                score=0.0,
                issues=["LLM応答失敗のため審査不能"],
                suggestion="再試行してください",
            )

        verdict = CriticVerdict(
            approved=bool(data.get("approved", False)),
            score=float(data.get("score", 0.0)),
            issues=data.get("issues", []),
            suggestion=data.get("suggestion", ""),
        )
        self._log_verdict(verdict)
        return verdict
