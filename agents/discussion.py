"""
agents/discussion.py
議論オーケストレーター：relevance_score 0.75 以上のシグナルが検出された際に
CIO・バリュー株・FX・リスクMGR の意見を収集し、最終裁定を行う。
"""
import json
from datetime import datetime
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

        avg_cooperation = sum(float(item.get("cooperation", item.get("cooperation_score", 0)) or 0) for item in evaluations) / len(evaluations)
        avg_communication = sum(float(item.get("communication", item.get("communication_score", 0)) or 0) for item in evaluations) / len(evaluations)
        avg_quality = sum(float(item.get("quality", item.get("quality_score", 0)) or 0) for item in evaluations) / len(evaluations)

        weak_agents = [
            item for item in evaluations
            if (float(item.get("cooperation", item.get("cooperation_score", 0)) or 0)
                + float(item.get("communication", item.get("communication_score", 0)) or 0)
                + float(item.get("quality", item.get("quality_score", 0)) or 0)) / 3.0 < 0.65
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

    # 議論に必要な最小エージェント数
    _MIN_OPINIONS = 2

    def run(
        self,
        signal: dict,
        ctx: MarketContext,
        cio_agent=None,
        equity_agent=None,
        fx_agent=None,
        risk_agent=None,
    ) -> dict:
        """
        高スコアシグナルについて各エージェントの意見を収集し、裁定結果を返す。

        裁定優先順位：
          1. リスクMGR 反対 → reject
          2. 全員賛成       → execute
          3. 意見割れ       → defer
        """
        opinions: list[dict] = []

        for agent_obj, role_name in [
            (cio_agent,    "CIO"),
            (equity_agent, "EquityAgent"),
            (fx_agent,     "FXStrategyAgent"),
            (risk_agent,   "RiskManagerAgent"),
        ]:
            if agent_obj is None or not hasattr(agent_obj, "evaluate"):
                continue
            try:
                opinion = agent_obj.evaluate(signal, ctx)
                opinion["agent"] = role_name
                opinions.append(opinion)
                self.logger.info(
                    f"[議論] {role_name}: {opinion.get('opinion')} "
                    f"- {opinion.get('rationale', '')[:60]}"
                )
            except Exception as e:
                self.logger.warning(f"{role_name}.evaluate() 失敗: {e}")

        verdict = self._arbitrate(opinions)

        result = {
            "signal_title":  signal.get("title", ""),
            "signal_score":  signal.get("relevance_score", 0),
            "sectors":       signal.get("sectors", []),
            "supply_chain":  signal.get("supply_chain_position", "不明"),
            "opinions":      opinions,
            "verdict":       verdict,
            "cxo_override":  None,
            "timestamp":     datetime.now().isoformat(),
        }

        self.logger.info(
            f"=== 議論裁定: {verdict} | {signal.get('title', '')[:60]} ==="
        )
        return result

    def run_batch(
        self,
        signals: list[dict],
        ctx: MarketContext,
        **agent_kwargs,
    ) -> list[dict]:
        """
        高スコアシグナル（relevance_score >= 0.75）のみを対象に一括議論を実行する。
        戻り値: 裁定結果リスト
        """
        high_score = [s for s in signals if s.get("relevance_score", 0) >= 0.75]
        self.logger.info(
            f"議論対象: {len(high_score)} 件 / 全 {len(signals)} 件 "
            f"（score >= 0.75 フィルタ後）"
        )

        results: list[dict] = []
        for signal in high_score:
            result = self.run(signal, ctx, **agent_kwargs)
            results.append(result)

        return results

    # ──────────────────────────────────────────────
    # 裁定ロジック
    # ──────────────────────────────────────────────

    def _arbitrate(self, opinions: list[dict]) -> str:
        """意見リストから最終裁定を決定する。"""
        if len(opinions) < self._MIN_OPINIONS:
            self.logger.warning(
                f"議論エージェント不足（{len(opinions)} < {self._MIN_OPINIONS}）→ defer"
            )
            return "defer"

        opinion_map = {o["agent"]: o.get("opinion", "保留") for o in opinions}

        # リスクMGR 反対 → 即否決
        if opinion_map.get("RiskManagerAgent") == "反対":
            return "reject"

        opinion_values = list(opinion_map.values())

        # 全員賛成 → 実行
        if all(op == "賛成" for op in opinion_values):
            return "execute"

        # 全員反対 → 否決
        if all(op == "反対" for op in opinion_values):
            return "reject"

        # それ以外（賛否混在・保留あり） → 翌日再議論
        return "defer"
