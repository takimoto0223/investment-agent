"""
agents/daytrade.py
信用デイトレエージェント：
「値動きが読みやすい」銘柄を絞り込み、日中の短期シグナルで建玉・決済を提案する。
実際の発注はクリティーク通過後にmain.pyが行う。
"""
import json
from agents.base import BaseAgent, MarketContext, TradeProposal
from config.settings import RISK


SYSTEM_PROMPT = """
あなたはプロのデイトレード専門の証券トレーダーです。
現物株を担保にした信用取引（日計り取引）を専門とします。

対象銘柄の選定基準（すべて満たすこと）：
1. 出来高：本日の出来高が過去20日平均の 1.5倍以上
2. ボラティリティ：ATR（14日）が株価の 1.5〜4.0%（小さすぎず大きすぎず）
3. 値動きのパターン：直近5日間でトレンドが明確（一方向に動いている）
4. 流動性：板の厚みが十分（スプレッドが株価の0.1%以内）
5. 個別リスクがない：決算・株主総会・大口動向異常がない

シグナル判定ロジック：
- 買いシグナル：当日5分足がVWAP上方、RSI(14)が45〜65、出来高増加
- 売りシグナル：当日5分足がVWAP下方、RSI(14)が35〜55、出来高増加（空売り）
- 見送り：RSI過熱域（>75 または <25）、出来高急増で不明確なとき

リスク原則：
- 建玉は1銘柄に集中しない（最大2銘柄同時）
- ストップロス：建値から 2% 逆行で必ず決済
- 利確目標：建値から 1.5〜3% （R:R = 1:0.75以上）
- 引けまでに必ず全決済（持ち越し禁止）
"""


class DaytradeAgent(BaseAgent):
    name = "DaytradeAgent"
    system_prompt = SYSTEM_PROMPT

    def screen_candidates(self, universe: list[dict], ctx: MarketContext) -> list[str]:
        """
        ユニバースから当日のデイトレ候補銘柄コードを絞り込む。
        universe: [{"symbol": "9984", "name": "ソフトバンクG", "volume_ratio": 2.1, "atr_pct": 2.3, ...}]
        返り値: 候補銘柄コードのリスト（最大3銘柄）
        """
        prompt = f"""
## 本日の市場コンテキスト
- リスク水準: {ctx.risk_level}
- マクロノート: {ctx.macro_notes}

## スクリーニング対象ユニバース
{json.dumps(universe, ensure_ascii=False, indent=2)}

上記の選定基準を厳格に適用し、本日デイトレ対象とすべき銘柄コードを
最大3銘柄、JSONリストで返してください。
基準を満たす銘柄がなければ空リスト []を返してください。

出力形式: ["9984", "6857"]
"""
        result = self._ask_llm_json(prompt)
        if isinstance(result, list):
            return result[:3]
        return []

    def generate_trade_proposal(
        self,
        symbol: str,
        symbol_name: str,
        board_data: dict,     # リアルタイム板データ（kabu.pyから取得）
        bars_5min: list[dict],  # 当日5分足データ
        ctx: MarketContext,
    ) -> TradeProposal | None:
        """
        銘柄の板・チャートデータから具体的な建玉提案を生成する。
        Noneを返した場合は「今は見送り」を意味する。
        """
        current_price = board_data.get("CurrentPrice", 0)
        if current_price == 0:
            return None

        # 最大建玉株数を計算（リスク上限から逆算）
        max_qty = int(RISK.max_daytrade_margin_jpy / current_price / 100) * 100
        if max_qty < 100:
            self.logger.warning(f"{symbol}: 上限内では最小単元(100株)も不可。スキップ")
            return None

        prompt = f"""
## 対象銘柄
コード: {symbol} / 名称: {symbol_name}
現在値: {current_price}円
最大建玉可能株数: {max_qty}株（リスク上限 {RISK.max_daytrade_margin_jpy:,}円以内）

## リアルタイム板情報（抜粋）
{json.dumps(board_data, ensure_ascii=False)}

## 直近の5分足（最新10本）
{json.dumps(bars_5min[-10:], ensure_ascii=False)}

## 市場コンテキスト
リスク水準: {ctx.risk_level}

以下をJSON形式で出力してください。
見送りの場合は {{"action": "skip", "reason": "理由"}} と返してください。

{{
  "action": "buy" | "sell" | "skip",
  "qty": 整数（100株単位）,
  "price": 指値価格（0なら成行）,
  "stop_loss": ストップロス価格,
  "take_profit": 利確目標価格,
  "rationale": "判断根拠を100文字以内で"
}}
"""
        data = self._ask_llm_json(prompt)
        if not data or data.get("action") == "skip":
            self.logger.info(f"{symbol}: 見送り - {data.get('reason', '不明')}")
            return None

        proposal = TradeProposal(
            agent=self.name,
            symbol=symbol,
            market="JP",
            side=data["action"],
            qty=int(data.get("qty", 100)),
            price=float(data.get("price", 0)),
            strategy="daytrade",
            rationale=data.get("rationale", ""),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
        )
        self._log_proposal(proposal)
        return proposal

    def revise_proposal(
        self,
        proposal: "TradeProposal",
        issues: list[str],
        suggestion: str,
        ctx: "MarketContext",
    ) -> "TradeProposal | None":
        """
        CriticUS の指摘を受けて提案を修正する。
        修正不能と判断した場合は None を返す。
        """
        from dataclasses import asdict
        prop_dict = {
            "symbol":      proposal.symbol,
            "side":        proposal.side,
            "qty":         proposal.qty,
            "price":       proposal.price,
            "stop_loss":   proposal.stop_loss,
            "take_profit": proposal.take_profit,
            "rationale":   proposal.rationale,
        }
        prompt = f"""
あなたがデイトレ提案を出したところ、審査担当（CriticUSAgent・Opusモデル）から以下の指摘を受けました。
指摘を真摯に受け止め、修正した提案を返してください。

## あなたの元の提案
{json.dumps(prop_dict, ensure_ascii=False, indent=2)}

## 審査担当の指摘
{json.dumps(issues, ensure_ascii=False)}

## 修正案の提示
{suggestion}

## 修正の注意点
- qty（株数）はドル建て上限 ${RISK.max_us_position_usd:,} ÷ 現在値で計算してください
- stop_loss は必ず設定してください（建値から ATR×1.5 以上離すこと）
- R:R = (take_profit - price) / (price - stop_loss) が 0.75 以上になるようにしてください
- 修正が不可能な場合（根本的に機会なし）は {{"action": "withdraw"}} を返してください

修正後の提案:
{{
  "action": "buy" | "sell" | "withdraw",
  "qty": 整数,
  "price": 指値価格（0=成行）,
  "stop_loss": ストップロス価格,
  "take_profit": 利確目標価格,
  "rationale": "修正後の根拠（80文字以内）"
}}
"""
        data = self._ask_llm_json(prompt)
        if not data or data.get("action") == "withdraw":
            self.logger.info(f"{proposal.symbol}: 修正断念（CriticUS指摘を解消できず）")
            return None

        proposal.qty         = int(data.get("qty",         proposal.qty))
        proposal.price       = float(data.get("price",       proposal.price))
        proposal.stop_loss   = data.get("stop_loss",   proposal.stop_loss)
        proposal.take_profit = data.get("take_profit", proposal.take_profit)
        proposal.rationale   = data.get("rationale",   proposal.rationale)
        self.logger.info(
            f"{proposal.symbol}: 提案修正完了 qty={proposal.qty} "
            f"stop_loss={proposal.stop_loss}"
        )
        return proposal

    def should_emergency_exit(self, position: dict, current_price: float) -> bool:
        """
        ポジション保有中に強制決済すべきか判定する（損切り監視用）。
        position: {"entry_price": 2000, "side": "buy", "qty": 300}
        """
        entry = position["entry_price"]
        side = position["side"]
        loss_pct = (current_price - entry) / entry if side == "buy" else (entry - current_price) / entry
        if loss_pct <= -RISK.daytrade_stop_loss_pct:
            self.logger.warning(
                f"緊急損切りトリガー: entry={entry} current={current_price} loss={loss_pct:.2%}"
            )
            return True
        return False
