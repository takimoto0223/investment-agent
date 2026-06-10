"""
agents/critic_us.py
米国株デイトレ提案審査エージェント。
critic_day.py をベースに以下の米国株固有チェックを追加している：
  - 建玉額をドル建てで評価し、USD→JPY 換算して大口判定
  - 取引時間チェック（ET 9:30〜16:00 以外は即否決）
  - FX 戦略エージェントの us_weight_bias との整合性確認
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from agents.base import BaseAgent, MarketContext, TradeProposal, CriticVerdict
from prompts.loader import get_prompt
from config.settings import RISK

_ET = ZoneInfo("America/New_York")


def is_us_market_hours() -> bool:
    """米国株式市場の通常取引時間内かどうか（ET 9:30〜16:00、平日のみ）。"""
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return False
    open_  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now_et <= close_


class CriticUSAgent(BaseAgent):
    name = "CriticUSAgent"
    system_prompt = get_prompt("critic_us")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        account: dict,
        fx_signal: dict | None = None,
    ) -> CriticVerdict:
        """
        米国株デイトレ提案を審査し、CriticVerdict を返す。

        account:   AlpacaBroker.get_account() の返り値
                   (equity・buying_power・cash が USD 建てで入っている)
        fx_signal: FXStrategyAgent.generate_signal() の返り値（省略可）
                   us_weight_bias: "overweight" | "neutral" | "underweight"
        """

        # ── 即否決チェック（LLM 呼び出し前に Python で確定） ──────────

        # 1. ストップロス未設定
        if proposal.stop_loss is None:
            self.logger.warning(f"{proposal.symbol}: ストップロス未設定のため即否決")
            return CriticVerdict(
                approved=False,
                score=0.0,
                issues=["ストップロスが設定されていません（絶対禁止）"],
                suggestion="stop_loss を設定してから再提案してください",
            )

        # 2. 取引時間外
        if not is_us_market_hours():
            now_et = datetime.now(_ET)
            self.logger.warning(
                f"{proposal.symbol}: 市場時間外のため即否決 "
                f"(ET {now_et.strftime('%H:%M')})"
            )
            return CriticVerdict(
                approved=False,
                score=0.0,
                issues=[f"米国市場時間外です（ET {now_et.strftime('%H:%M')}）"],
                suggestion="ET 9:30〜16:00 の時間帯に再提案してください",
            )

        # 3. FX 整合性（underweight × buy は即否決）
        us_weight_bias = (fx_signal or {}).get("us_weight_bias", "neutral")
        if us_weight_bias == "underweight" and proposal.side == "buy":
            self.logger.warning(
                f"{proposal.symbol}: FX戦略が underweight のため buy 提案を即否決"
            )
            return CriticVerdict(
                approved=False,
                score=0.1,
                issues=["FX戦略エージェントが米国株 underweight を指示しているため buy 不可"],
                suggestion="FX シグナルが neutral 以上に転じてから再提案してください",
            )

        # ── LLM による総合審査 ───────────────────────────────────────

        order_amount_usd = (proposal.price or 0) * proposal.qty
        order_amount_jpy = order_amount_usd * RISK.usd_jpy_rate
        equity_usd       = account.get("equity", 0)
        portfolio_jpy    = equity_usd * RISK.usd_jpy_rate
        buying_power_usd = account.get("buying_power", 0)

        prompt = f"""
## 審査対象の取引提案
{json.dumps({
    "symbol":      proposal.symbol,
    "market":      "US",
    "side":        proposal.side,
    "qty":         proposal.qty,
    "price":       proposal.price,
    "stop_loss":   proposal.stop_loss,
    "take_profit": proposal.take_profit,
    "rationale":   proposal.rationale,
}, ensure_ascii=False, indent=2)}

## ドル建て評価・円換算
- 発注額（USD）: ${order_amount_usd:,.2f}
- 発注額（JPY換算 @{RISK.usd_jpy_rate:.1f}）: {order_amount_jpy:,.0f}円
- ポートフォリオ総額（JPY換算）: {portfolio_jpy:,.0f}円
- 購買力（USD）: ${buying_power_usd:,.2f}
- 米国株 1 銘柄上限: ${RISK.max_us_position_usd:,}

## FX 戦略シグナル
{json.dumps(fx_signal, ensure_ascii=False, indent=2) if fx_signal else "（未提供 → 中立として扱う）"}

## 現在の市場コンテキスト
- リスク水準: {ctx.risk_level}

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            self.logger.warning("CriticUS: LLM応答失敗。デフォルト否決を返します")
            return CriticVerdict(
                approved=False,
                score=0.0,
                issues=["LLM応答失敗のため審査不能"],
                suggestion="再試行してください",
            )

        verdict = CriticVerdict(
            approved=bool(data.get("approved", False)),
            score=float(data.get("score", 0.0)),
            issues=data.get("issues", []),
            suggestion=data.get("suggestion", ""),
        )
        self._log_verdict(verdict)
        return verdict
