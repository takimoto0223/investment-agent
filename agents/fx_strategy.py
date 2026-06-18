"""
agents/fx_strategy.py
FX戦略エージェント：
USD/JPY の方向性を分析し、ポートフォリオの通貨エクスポージャー調整シグナルを生成する。
生成したシグナルは CriticFXAgent の審査を経て CIOAgent に通知される。
"""
import json
from agents.base import BaseAgent, MarketContext
from prompts.loader import get_prompt


class FXStrategyAgent(BaseAgent):
    name = "FXStrategyAgent"
    system_prompt = get_prompt("fx_strategy")

    def generate_signal(
        self,
        macro_data: str,
        current_usd_ratio: float,
        ctx: MarketContext,
    ) -> dict:
        """
        マクロデータと現在のドル比率から FX シグナルを生成する。
        macro_data:        USD/JPY・金利差・VIX 等の数値文字列
        current_usd_ratio: 現在のドル建て資産比率（0.0〜1.0）
        """
        prompt = f"""
## 現在の市場データ
{macro_data or "（未取得）"}

## ポートフォリオの通貨状況
- 現在のドル建て資産比率: {current_usd_ratio * 100:.1f}%

## 市場コンテキスト（CIOより）
- リスク水準: {ctx.risk_level}
- マクロノート: {ctx.macro_notes}

上記を分析し、FX戦略シグナルを JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            self.logger.warning("FXStrategy: LLM応答失敗。デフォルトシグナル（hold）を返します")
            return {
                "fx_signal": "hold",
                "target_usd_ratio": round(current_usd_ratio * 100, 1),
                "current_usd_ratio": round(current_usd_ratio * 100, 1),
                "rationale": "LLM応答失敗のためデフォルト値",
                "us_weight_bias": "neutral",
                "caution": "LLM応答が取得できませんでした",
            }

        self.logger.info(
            f"FXシグナル: {data.get('fx_signal')} "
            f"目標ドル比率={data.get('target_usd_ratio')}% "
            f"us_weight_bias={data.get('us_weight_bias')}"
        )
        return data

