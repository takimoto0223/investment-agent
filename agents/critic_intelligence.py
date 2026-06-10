"""
agents/critic_intelligence.py
情報クリティークエージェント：IntelligenceAgent が収集したシグナルを
信頼性スコア・誇大情報フィルタ・サプライチェーン波及評価の観点で審査する。
"""
import json
from agents.base import BaseAgent, MarketContext
from prompts.loader import get_prompt


class CriticIntelligenceAgent(BaseAgent):
    name = "CriticIntelligenceAgent"
    system_prompt = get_prompt("critic_intelligence")

    def review(
        self,
        signals: list[dict],
        ctx: MarketContext | None = None,
    ) -> list[dict]:
        """
        シグナルリストを審査し、精査済みリストを返す。
        - relevance_score < 0.6 は除外
        - PR 記事・誇大情報は approved=false で除外
        - 上流シグナルの下流波及効果を付与
        """
        if not signals:
            return []

        prompt = f"""
## 審査対象のインテリジェンスシグナル（{len(signals)} 件）
{json.dumps(signals, ensure_ascii=False, indent=2)}

## 市場コンテキスト
{f"セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}" if ctx else "（未取得）"}

上記シグナルを審査し、精査済みリストを JSON 配列で返してください。
relevance_score 0.6 未満および PR 記事は approved=false にしてください。

[
  {{
    "source": "arxiv | github | hackernews",
    "title": "タイトル",
    "relevance_score": 再評価後 0.0〜1.0,
    "reliability_score": 0.0〜1.0,
    "sectors": ["セクター名"],
    "supply_chain_position": "上流 | 中流 | 下流 | 不明",
    "downstream_impact": "波及効果の説明（なければ null）",
    "classification": "業界内話題 | 公式発表 | 研究成果 | PR記事",
    "summary": "100 文字以内",
    "url": "URL",
    "approved": true | false
  }}
]
"""
        data = self._ask_llm_json(prompt)
        if not isinstance(data, list):
            self.logger.warning("CriticIntelligence: LLM 応答失敗。元シグナルを返します")
            return signals

        approved = [
            s for s in data
            if s.get("approved", True) and s.get("relevance_score", 0) >= 0.6
        ]
        self.logger.info(
            f"クリティーク結果: {len(signals)} 件 → {len(approved)} 件承認"
        )
        return approved
