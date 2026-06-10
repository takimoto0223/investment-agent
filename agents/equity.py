"""
agents/equity.py
バリュー株選定エージェント：割安・高品質・カタリスト待ちの銘柄を発掘する。
CIO のセクタースコアが 0.6 以上のセクターを優先的にスクリーニングする。
"""
import json
from agents.base import BaseAgent, MarketContext, TradeProposal
from prompts.loader import get_prompt
from config.settings import RISK


class EquityAgent(BaseAgent):
    name = "EquityAgent"
    system_prompt = get_prompt("equity")

    def screen(
        self,
        candidates: list[dict],
        ctx: MarketContext,
        portfolio_total_jpy: float,
    ) -> list[TradeProposal]:
        """
        候補銘柄リストからバリュー株提案を生成する。
        candidates: [{"symbol": ..., "name": ..., "sector": ..., "per": ..., "pbr": ...}, ...]
        """
        prompt = f"""
## 候補銘柄
{json.dumps(candidates, ensure_ascii=False, indent=2)}

## 市場コンテキスト（CIO より）
- セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}
- ローテーションシグナル: {ctx.rotation_signal}
- リスク水準: {ctx.risk_level}

## ポートフォリオ制約
- 総資産: {portfolio_total_jpy:,.0f} 円
- 1 銘柄最大集中度: {RISK.max_concentration_pct * 100:.0f}%

スクリーニング基準を満たす銘柄について TradeProposal を JSON 配列で返してください。
基準を満たす銘柄がなければ空配列 [] を返してください。
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
                    market=item.get("market", "JP"),
                    side=item.get("side", "buy"),
                    qty=int(item.get("qty", 0)),
                    price=float(item.get("price", 0)),
                    strategy="value",
                    rationale=item.get("rationale", ""),
                    stop_loss=item.get("stop_loss"),
                    take_profit=item.get("target_price"),
                    extra={"catalyst": item.get("catalyst", "")},
                ))
            except (KeyError, ValueError, TypeError):
                pass

        return proposals

    def evaluate(self, signal: dict, ctx: MarketContext) -> dict:
        """
        議論オーケストレーター用：シグナルに関連する銘柄のバリュエーション機会を評価する。
        """
        prompt = f"""
## 評価対象シグナル
{json.dumps(signal, ensure_ascii=False, indent=2)}

## 市場コンテキスト
- セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}
- リスク水準: {ctx.risk_level}

このシグナルに基づき、バリュー株投資家の視点から投資機会を評価し、
JSON で返してください。

{{
  "opinion": "賛成 | 反対 | 保留",
  "rationale": "根拠 100 文字以内（バリュエーション・カタリスト観点）",
  "suggested_action": "具体的な提案（ウォッチリスト追加・スクリーニング等）"
}}
"""
        data = self._ask_llm_json(prompt)
        return {
            "agent":            self.name,
            "opinion":          data.get("opinion", "保留"),
            "rationale":        data.get("rationale", ""),
            "suggested_action": data.get("suggested_action", ""),
        }
