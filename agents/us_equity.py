"""
agents/us_equity.py
米国株バリュー・中長期投資エージェント。
LLMの知識ベースを活用して、ファンダメンタルズ観点から割安・高品質な銘柄を選定する。
保有期間の目安: 数週間〜数ヶ月。
"""
import json
import logging
from agents.base import BaseAgent, MarketContext, TradeProposal

logger = logging.getLogger(__name__)


class USEquityAgent(BaseAgent):
    name = "USEquityAgent"
    system_prompt = (
        "あなたは米国株の中長期バリュー投資専門家です。"
        "P/E・P/B・ROE・FCF・競争優位性（モート）・カタリストを軸に、"
        "数週間〜数ヶ月の保有に値する銘柄を厳選します。"
        "過熱した銘柄・投機的な銘柄は対象外とし、割安感と安全余裕率（MOS）を重視します。"
    )

    def screen_value(
        self,
        universe: list[dict],
        ctx: MarketContext,
        existing_symbols: list[str],
        max_position_usd: float,
        cash_usd: float,
    ) -> list[TradeProposal]:
        """
        バリュー株スクリーニング。既保有銘柄を除外し最大3銘柄を提案する。
        """
        available = [u for u in universe if u["symbol"] not in existing_symbols]
        if not available:
            logger.info("USEquityAgent: スクリーニング対象なし（全銘柄保有済み）")
            return []

        prompt = f"""
## 候補銘柄ユニバース
{json.dumps(available, ensure_ascii=False, indent=2)}

## 市場コンテキスト（CIO判断）
- リスク水準: {ctx.risk_level}
- セクターローテーション: {ctx.rotation_signal}
- セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}
- マクロノート: {ctx.macro_notes}

## 制約
- 利用可能キャッシュ: ${cash_usd:,.0f} USD
- 1銘柄最大投資額: ${max_position_usd:,.0f} USD
- リスク水準が "high" の場合は提案数を1銘柄に絞ること

## 指示
ファンダメンタルズ（P/E・ROE・FCF・モート）とCIOコンテキストを踏まえ、
中長期保有に値する銘柄を最大3銘柄選定してください。
バリュエーション的に割高・投機的な銘柄は選ばないこと。

以下のJSON配列で返してください。候補なければ空配列[]：

[
  {{
    "symbol": "TICKER",
    "name": "銘柄名",
    "qty": 株数(整数、max_position_usd÷概算株価で算出),
    "price": 0,
    "rationale": "選定理由（ファンダメンタルズ・カタリスト・バリュエーション、100文字以内）",
    "stop_loss_pct": 損切り割合(例: 0.08 = 8%下落で損切り),
    "target_return_pct": 目標リターン(例: 0.20 = 20%)
  }}
]
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
                    market="US",
                    side="buy",
                    qty=max(1, int(item.get("qty", 1))),
                    price=0.0,
                    strategy="value_hold",
                    rationale=item.get("rationale", ""),
                    stop_loss=None,
                    take_profit=None,
                    extra={
                        "hold_horizon": "weeks_to_months",
                        "stop_loss_pct": float(item.get("stop_loss_pct", 0.08)),
                        "target_return_pct": float(item.get("target_return_pct", 0.15)),
                        "name": item.get("name", item["symbol"]),
                    },
                ))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"USEquityAgent: proposalパース失敗 {e}")

        return proposals

    def revise_proposal(
        self,
        proposal: "TradeProposal",
        issues: list[str],
        suggestion: str,
        ctx: "MarketContext",
    ) -> "TradeProposal | None":
        """
        CriticUS の指摘を受けてバリュー投資提案を修正する。
        """
        import json as _json
        prop_dict = {
            "symbol":         proposal.symbol,
            "qty":            proposal.qty,
            "stop_loss_pct":  proposal.extra.get("stop_loss_pct", 0.08),
            "target_return":  proposal.extra.get("target_return_pct", 0.15),
            "rationale":      proposal.rationale,
        }
        prompt = f"""
あなたがバリュー投資提案を出したところ、審査担当（CriticUSAgent・Opusモデル）から以下の指摘を受けました。
指摘を踏まえて提案を修正してください。

## あなたの元の提案
{_json.dumps(prop_dict, ensure_ascii=False, indent=2)}

## 市場コンテキスト（CIO判断）
- リスク水準: {ctx.risk_level}
- セクターローテーション: {ctx.rotation_signal}

## 審査担当の指摘
{_json.dumps(issues, ensure_ascii=False)}

## 修正ヒント
{suggestion}

## 修正の注意点
- stop_loss_pct: 8〜15% の範囲で設定（高ボラ銘柄は大きめに）
- qty: ドル建て上限 $3,000 ÷ 株価で算出
- 修正不能（バリュエーション的に不適切等）なら {{"action": "withdraw"}} を返す

{{
  "action": "buy" | "withdraw",
  "qty": 整数,
  "stop_loss_pct": 損切り割合（例 0.10 = 10%）,
  "target_return_pct": 目標リターン割合,
  "rationale": "修正後の根拠（80文字以内）"
}}
"""
        data = self._ask_llm_json(prompt)
        if not data or data.get("action") == "withdraw":
            self.logger.info(f"{proposal.symbol}: バリュー提案修正断念")
            return None

        proposal.qty = max(1, int(data.get("qty", proposal.qty)))
        sl_pct = float(data.get("stop_loss_pct",    proposal.extra.get("stop_loss_pct", 0.08)))
        tp_pct = float(data.get("target_return_pct", proposal.extra.get("target_return_pct", 0.15)))
        proposal.extra["stop_loss_pct"]     = sl_pct
        proposal.extra["target_return_pct"] = tp_pct
        proposal.rationale = data.get("rationale", proposal.rationale)
        # price が確定している場合は絶対価格も更新
        if proposal.price and proposal.price > 0:
            proposal.stop_loss   = round(proposal.price * (1 - sl_pct), 4)
            proposal.take_profit = round(proposal.price * (1 + tp_pct), 4)
        self.logger.info(
            f"{proposal.symbol}: バリュー提案修正完了 qty={proposal.qty} "
            f"SL=${proposal.stop_loss} TP=${proposal.take_profit}"
        )
        return proposal

    def panel_review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        fx_signal: dict,
        risk_notes: str,
    ) -> dict:
        """
        CIO・FX・リスク視点からの簡易パネル議論。
        CriticUSAgent の前段に呼ぶことで多角的な審議を行う。
        """
        prompt = f"""
## 投資提案
- 銘柄: {proposal.symbol}
- 戦略: 中長期バリュー保有
- 根拠: {proposal.rationale}

## 市場コンテキスト（CIO）
- リスク水準: {ctx.risk_level}
- ローテーション: {ctx.rotation_signal}

## FXシグナル
- シグナル: {fx_signal.get('fx_signal', 'hold')}
- 米国株ウェイト: {fx_signal.get('us_weight_bias', 'neutral')}

## リスク状況
{risk_notes}

## 指示
CIO・FX戦略家・リスクマネージャーの3視点から、この提案に対する意見を返してください。

{{
  "cio_opinion": "賛成 | 反対 | 保留",
  "cio_reason": "50文字以内",
  "fx_opinion": "賛成 | 反対 | 保留",
  "fx_reason": "50文字以内",
  "risk_opinion": "賛成 | 反対 | 保留",
  "risk_reason": "50文字以内",
  "consensus": "execute | defer | reject",
  "consensus_reason": "100文字以内"
}}
"""
        data = self._ask_llm_json(prompt)
        return {
            "symbol":           proposal.symbol,
            "cio_opinion":      data.get("cio_opinion", "保留"),
            "cio_reason":       data.get("cio_reason", ""),
            "fx_opinion":       data.get("fx_opinion", "保留"),
            "fx_reason":        data.get("fx_reason", ""),
            "risk_opinion":     data.get("risk_opinion", "保留"),
            "risk_reason":      data.get("risk_reason", ""),
            "consensus":        data.get("consensus", "defer"),
            "consensus_reason": data.get("consensus_reason", ""),
        }
