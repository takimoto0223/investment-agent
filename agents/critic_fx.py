"""
agents/critic_fx.py
FX戦略審査エージェント：
FX戦略エージェントの提案を審査し、段階的・慎重な為替変更かどうかを確認する。
イベントウィンドウ内の変更・過大な変更幅・比率逸脱を検出して否決する。
"""
import json
from agents.base import BaseAgent, MarketContext, CriticVerdict
from prompts.loader import get_prompt


class CriticFXAgent(BaseAgent):
    name = "CriticFXAgent"
    system_prompt = get_prompt("critic_fx")

    def review(
        self,
        fx_proposal: dict,
        ctx: MarketContext,
        portfolio_total_jpy: float,
    ) -> CriticVerdict:
        """
        FX戦略提案を審査し、CriticVerdict を返す。
        fx_proposal:         FXStrategyAgent.generate_signal() の返り値
        portfolio_total_jpy: ポートフォリオ総額（円換算）
        """
        current_ratio = fx_proposal.get("current_usd_ratio", 0)
        target_ratio = fx_proposal.get("target_usd_ratio", 0)
        change_pct = abs(target_ratio - current_ratio)
        fx_amount_jpy = portfolio_total_jpy * change_pct / 100

        prompt = f"""
## 審査対象のFX戦略提案
{json.dumps(fx_proposal, ensure_ascii=False, indent=2)}

## ポートフォリオ情報
- ポートフォリオ総額（概算）: {portfolio_total_jpy:,.0f}円
- 変更幅: {change_pct:.1f}%
- 推定円転/ドル転額: {fx_amount_jpy:,.0f}円

## 現在の市場コンテキスト（CIOより）
- リスク水準: {ctx.risk_level}
- マクロノート: {ctx.macro_notes}

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            self.logger.warning("CriticFX: LLM応答失敗。デフォルト否決を返します")
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
