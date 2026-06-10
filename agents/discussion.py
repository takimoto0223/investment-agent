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
