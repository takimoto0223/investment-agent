"""
agents/scalp_day.py
スキャルプ/デイトレ戦略エージェント群。

ScalpDayBase  ─ シグナル判定・提案生成・損切り監視の戦略ロジック（市場非依存）
  ScalpDay_JP ─ kabu STATION経由の日本株信用デイトレ（100株単位・JPY建て）
  ScalpDay_US ─ Alpaca経由の米国株デイトレ（1株単位・USD建て）

市場依存部分（サブクラスに実装）:
  _market              "JP" | "US"
  _min_unit            最小発注単位（JP=100, US=1）
  _calc_max_qty()      上限額から最大株数を算出
  _position_limit_text() revise_proposal プロンプト用の制約テキスト
  system_prompt        市場固有のトレーダー設定
"""
import json
from agents.base import BaseAgent, MarketContext, TradeProposal
from config.settings import RISK

# ── 共通の戦略ルール（市場非依存） ────────────────────────────
_STRATEGY_RULES = """
選定基準（すべて満たすこと）：
1. 出来高：本日の出来高が過去20日平均の 1.5倍以上
2. ボラティリティ：ATR（14日）が株価の 1.5〜4.0%（小さすぎず大きすぎず）
3. 値動きのパターン：直近5日間でトレンドが明確（一方向に動いている）
4. 流動性：板の厚みが十分（スプレッドが株価の0.1%以内）
5. 個別リスクがない：決算・大口動向異常がない

シグナル判定ロジック：
- 買いシグナル：当日5分足がVWAP上方、RSI(14)が45〜65、出来高増加
- 売りシグナル：当日5分足がVWAP下方、RSI(14)が35〜55、出来高増加
- 見送り：RSI過熱域（>75 または <25）、出来高急増で不明確なとき

リスク原則：
- 建玉は1銘柄に集中しない（最大2銘柄同時）
- ストップロス：建値から 2% 逆行で必ず決済
- 利確目標：建値から 1.5〜3%（R:R = 1:0.75以上）
- 当日中に必ず全決済（翌日持ち越し禁止）
"""

_JP_SYSTEM_PROMPT = (
    "あなたはプロのデイトレード専門の証券トレーダーです。\n"
    "現物株を担保にした信用取引（日計り取引）を専門とします。\n"
    "市場：日本株（kabu STATION経由）/ 発注単位：100株 / 価格単位：円（JPY）\n"
    + _STRATEGY_RULES
)

_US_SYSTEM_PROMPT = (
    "あなたはプロの米国株デイトレード専門トレーダーです。\n"
    "キャッシュ/マージンアカウントで米国株のデイトレを行います。\n"
    "市場：米国株（Alpaca経由）/ 発注単位：1株 / 価格単位：ドル（USD）\n"
    "取引時間：米国東部時間 9:30〜16:00（ET）\n"
    + _STRATEGY_RULES
)


class ScalpDayBase(BaseAgent):
    """スキャルプ/デイトレ戦略ロジックの基底。市場差分はサブクラスに委譲する。"""

    _market:   str = ""
    _min_unit: int = 1

    def _calc_max_qty(self, current_price: float) -> int:
        """上限額から最大株数を計算する（サブクラスで実装）。"""
        raise NotImplementedError

    def _position_limit_text(self) -> str:
        """revise_proposal プロンプト用の建玉制約テキスト（サブクラスで実装）。"""
        raise NotImplementedError

    def screen_candidates(self, universe: list[dict], ctx: MarketContext) -> list[str]:
        """ユニバースから当日のデイトレ候補銘柄を最大3件返す。"""
        prompt = f"""
## 本日の市場コンテキスト
- リスク水準: {ctx.risk_level}
- マクロノート: {ctx.macro_notes}

## スクリーニング対象ユニバース
{json.dumps(universe, ensure_ascii=False, indent=2)}

上記の選定基準を厳格に適用し、本日デイトレ対象とすべき銘柄コードを
最大3銘柄、JSONリストで返してください。
基準を満たす銘柄がなければ空リスト [] を返してください。

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
        board_data: dict,
        bars_5min: list[dict],
        ctx: MarketContext,
    ) -> TradeProposal | None:
        current_price = board_data.get("CurrentPrice", 0)
        if current_price == 0:
            return None

        max_qty = self._calc_max_qty(current_price)
        if max_qty < self._min_unit:
            self.logger.warning(f"{symbol}: 上限内では最小単元({self._min_unit})も不可。スキップ")
            return None

        prompt = f"""
## 対象銘柄
コード: {symbol} / 名称: {symbol_name}
現在値: {current_price}
最大建玉可能株数: {max_qty}（{self._position_limit_text()}）

## リアルタイム板情報（抜粋）
{json.dumps(board_data, ensure_ascii=False)}

## 直近の5分足（最新10本）
{json.dumps(bars_5min[-10:], ensure_ascii=False)}

## 市場コンテキスト
リスク水準: {ctx.risk_level}

以下をJSON形式で出力してください。
見送りの場合は {{"action": "skip", "reason": "理由"}} を返してください。

{{
  "action": "buy" | "sell" | "skip",
  "qty": 整数（{self._min_unit}単位）,
  "price": 指値価格（0なら成行）,
  "stop_loss": ストップロス価格,
  "take_profit": 利確目標価格,
  "rationale": "判断根拠を100文字以内で"
}}
"""
        data = self._ask_llm_json(prompt)
        if not data or data.get("action") == "skip":
            self.logger.info(f"{symbol}: 見送り - {data.get('reason', '不明') if data else '不明'}")
            return None

        proposal = TradeProposal(
            agent=self.name,
            symbol=symbol,
            market=self._market,
            side=data["action"],
            qty=int(data.get("qty", self._min_unit)),
            price=float(data.get("price", 0)),
            strategy="scalpday",
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
あなたがデイトレ提案を出したところ、審査担当から以下の指摘を受けました。
指摘を踏まえて修正した提案を返してください。

## あなたの元の提案
{json.dumps(prop_dict, ensure_ascii=False, indent=2)}

## 審査担当の指摘
{json.dumps(issues, ensure_ascii=False)}

## 修正ヒント
{suggestion}

## 修正の注意点
- 株数は {self._position_limit_text()} で計算してください
- stop_loss は必ず設定してください（建値から ATR×1.5 以上離すこと）
- R:R = (take_profit - price) / (price - stop_loss) が 0.75 以上になるようにしてください
- 修正が不可能な場合は {{"action": "withdraw"}} を返してください

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
            self.logger.info(f"{proposal.symbol}: 修正断念")
            return None

        proposal.qty         = int(data.get("qty",         proposal.qty))
        proposal.price       = float(data.get("price",       proposal.price))
        proposal.stop_loss   = data.get("stop_loss",   proposal.stop_loss)
        proposal.take_profit = data.get("take_profit", proposal.take_profit)
        proposal.rationale   = data.get("rationale",   proposal.rationale)
        self.logger.info(
            f"{proposal.symbol}: 提案修正完了 qty={proposal.qty} stop_loss={proposal.stop_loss}"
        )
        return proposal

    def should_emergency_exit(self, position: dict, current_price: float) -> bool:
        """ポジション保有中に強制損切りすべきか判定する。"""
        entry    = position["entry_price"]
        side     = position["side"]
        loss_pct = (
            (current_price - entry) / entry if side == "buy"
            else (entry - current_price) / entry
        )
        if loss_pct <= -RISK.daytrade_stop_loss_pct:
            self.logger.warning(
                f"緊急損切りトリガー: entry={entry} current={current_price} loss={loss_pct:.2%}"
            )
            return True
        return False


class ScalpDay_JP(ScalpDayBase):
    """日本株スキャルプ/デイトレ（kabu STATION・100株単位・JPY建て）。"""
    name          = "ScalpDay_JP"
    system_prompt = _JP_SYSTEM_PROMPT
    _market       = "JP"
    _min_unit     = 100

    def _calc_max_qty(self, current_price: float) -> int:
        return int(RISK.max_daytrade_margin_jpy / current_price / 100) * 100

    def _position_limit_text(self) -> str:
        return f"JPY上限 {RISK.max_daytrade_margin_jpy:,}円（100株単位）"


class ScalpDay_US(ScalpDayBase):
    """米国株スキャルプ/デイトレ（Alpaca・1株単位・USD建て）。"""
    name          = "ScalpDay_US"
    system_prompt = _US_SYSTEM_PROMPT
    _market       = "US"
    _min_unit     = 1

    def _calc_max_qty(self, current_price: float) -> int:
        return max(1, int(RISK.max_us_position_usd / current_price))

    def _position_limit_text(self) -> str:
        return f"USD上限 ${RISK.max_us_position_usd:,}"
