"""
agents/discussion.py
議論オーケストレーター：relevance_score 0.75 以上のシグナルが検出された際に
CIO・バリュー株・FX・リスクMGR の意見を収集し、最終裁定を行う。
"""
import json
from agents.base import BaseAgent, MarketContext
from prompts.loader import get_prompt


class DiscussionOrchestratorAgent(BaseAgent):
    name = "DiscussionOrchestrator"
    system_prompt = get_prompt("discussion")
    model = "claude-opus-4-8"

    def suggest_reorganization(self, evaluations: list[dict], ctx: MarketContext | None = None) -> dict:
        """
        CxO と DiscussionOrchestrator の観点から、エージェント評価スコアをもとに
        再編成提案を返す。

        期待する入力例:
          [
            {"agent": "EquityAgent", "cooperation": 0.75, "communication": 0.80,
             "quality": 0.72, "issues": ["..."]},
            ...
          ]
        """
        if not evaluations:
            return {
                "summary": "評価データがありません。現状維持を推奨します。",
                "recommendation": "keep_current_structure",
                "proposals": [],
                "cxo_notes": "CxO review: 追加データがないため、再編成は見送り。",
                "discussion_notes": "DiscussionOrchestrator: 追加の評価指標を待機。",
            }

        def _score(item: dict, field: str) -> float:
            return float(item.get(field, item.get(f"{field}_score", 0)) or 0)

        avg_cooperation   = sum(_score(i, "cooperation")   for i in evaluations) / len(evaluations)
        avg_communication = sum(_score(i, "communication") for i in evaluations) / len(evaluations)
        avg_quality       = sum(_score(i, "quality")       for i in evaluations) / len(evaluations)

        weak_agents = [
            item for item in evaluations
            if (_score(item, "cooperation") + _score(item, "communication") + _score(item, "quality")) / 3.0 < 0.65
        ]

        proposals = []
        seen_actions = set()

        if avg_cooperation < 0.65 or avg_communication < 0.70 or avg_quality < 0.75:
            for item in weak_agents[:2]:
                agent_name = item.get("agent", "unknown")
                proposal = {
                    "action": "reassign",
                    "target": agent_name,
                    "priority": "high" if avg_cooperation < 0.55 else "medium",
                    "rationale": f"{agent_name} の協調性・コミュニケーション指標が低く、手戻りや責任境界の曖昧さが見られます。",
                }
                key = (proposal["action"], proposal["target"])
                if key not in seen_actions:
                    proposals.append(proposal)
                    seen_actions.add(key)

            overlap_items = [item for item in evaluations if any("overlap" in issue.lower() or "重複" in issue for issue in item.get("issues", []))]
            if overlap_items:
                proposals.append({
                    "action": "merge",
                    "target": "重複する責任領域",
                    "priority": "medium",
                    "rationale": "重複した役割があるため、責任境界を整理して実務の重複を減らします。",
                })

            if len(weak_agents) >= 2:
                proposals.append({
                    "action": "split",
                    "target": "高負荷の戦略・審査責任",
                    "priority": "medium",
                    "rationale": "複数の弱点が同時に起きているため、戦略と審査の責任分担を分離して負荷を下げます。",
                })

            recommendation = "restructure"
        else:
            recommendation = "keep_current_structure"

        summary = (
            "CxO/DiscussionOrchestrator review: "
            f"平均協調性={avg_cooperation:.2f}, 平均コミュニケーション={avg_communication:.2f}, "
            f"平均品質={avg_quality:.2f}. "
            + ("弱点が見られるため再編が有効です。" if recommendation == "restructure" else "現状の組織分担は安定しています。")
        )

        if weak_agents:
            weak_names = ", ".join(item.get("agent", "unknown") for item in weak_agents)
            summary += f" 低スコア要因: {weak_names}."

        return {
            "summary": summary,
            "recommendation": recommendation,
            "proposals": proposals,
            "cxo_notes": "CxO review: スコアを主観評価と併用し、責任境界・重複・負荷の観点で再編候補を整理する。",
            "discussion_notes": "DiscussionOrchestrator: 改善提案はスコア低下の原因と責任分担の重複から導く。",
            "scores": {
                "average_cooperation": avg_cooperation,
                "average_communication": avg_communication,
                "average_quality": avg_quality,
            },
        }

