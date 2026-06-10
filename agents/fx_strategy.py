"""
agents/fx_strategy.py
FX戦略エージェント：
USD/JPY の方向性を分析し、ポートフォリオの通貨エクスポージャー調整シグナルを生成する。
生成したシグナルは CriticFXAgent の審査を経て CIOAgent に通知される。
"""
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

    def evaluate(self, signal: dict, ctx: MarketContext) -> dict:
        """
        議論オーケストレーター用：シグナルがドル資産比率に与える影響を評価する。
        """
        import json
        prompt = f"""
## 評価対象シグナル
{json.dumps(signal, ensure_ascii=False, indent=2)}

## 市場コンテキスト
- リスク水準: {ctx.risk_level}
- マクロノート: {ctx.macro_notes}

FX ストラテジストの視点からこのシグナルを評価してください。
米国テック株シグナルはドル資産比率・米国株ウェイトにどう影響するかを考慮し JSON で返してください。

{{
  "opinion": "賛成 | 反対 | 保留",
  "rationale": "根拠 100 文字以内（ドル資産比率・為替リスクを含める）",
  "suggested_action": "具体的な提案（ドル比率調整・ヘッジ等）"
}}
"""
        data = self._ask_llm_json(prompt)
        return {
            "agent":            self.name,
            "opinion":          data.get("opinion", "保留"),
            "rationale":        data.get("rationale", ""),
            "suggested_action": data.get("suggested_action", ""),
        }
