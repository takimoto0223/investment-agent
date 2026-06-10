"""
agents/critic_day.py
デイトレ提案審査エージェント：
信用デイトレエージェントの提案をリスク管理視点で審査し、承認・否決を返す。

呼び出し元: main.py の run_daytrade_session()
"""
import json
from agents.base import BaseAgent, MarketContext, TradeProposal, CriticVerdict
from prompts.loader import get_prompt
from config.settings import RISK


class CriticDayAgent(BaseAgent):
    name = "CriticDayAgent"
    system_prompt = get_prompt("critic_daytrade")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        wallet: dict,
    ) -> CriticVerdict:
        """
        デイトレ提案を審査し、CriticVerdict を返す。
        wallet: KabuBroker.get_wallet_margin() の応答（MarginAccountWallet 等を含む）
        """
        margin_wallet = wallet.get("MarginAccountWallet", 0)
        order_amount = proposal.price * proposal.qty if proposal.price > 0 else 0

        # ストップロス未設定は LLM に渡す前に即否決（絶対ルール）
        if proposal.stop_loss is None:
            self.logger.warning(f"{proposal.symbol}: ストップロス未設定のため即否決")
            return CriticVerdict(
                approved=False,
                score=0.0,
                issues=["ストップロスが設定されていません（絶対禁止）"],
                suggestion="stop_loss を設定してから再提案してください",
            )

        prompt = f"""
## 審査対象の取引提案
{json.dumps({
    "symbol": proposal.symbol,
    "side": proposal.side,
    "qty": proposal.qty,
    "price": proposal.price,
    "stop_loss": proposal.stop_loss,
    "take_profit": proposal.take_profit,
    "rationale": proposal.rationale,
}, ensure_ascii=False, indent=2)}

## 現在の市場コンテキスト
- リスク水準: {ctx.risk_level}

## 口座情報
- 信用取引余力: {margin_wallet:,}円
- 発注想定額（概算）: {order_amount:,}円
- 信用建玉上限: {RISK.max_daytrade_margin_jpy:,}円

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            self.logger.warning("CriticDay: LLM応答失敗。デフォルト否決を返します")
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
