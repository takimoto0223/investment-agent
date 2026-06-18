"""
agents/critics.py
全クリティーク一覧。CriticBase を共通基底とし、市場・戦略固有の差分のみサブクラスに実装する。

CriticBase             ─ refine_and_review ループ・フォールバック・ログの共通実装
  ScalpDay_JP_Critic   ─ JP デイトレ提案の審査（旧 CriticDayAgent）
  ScalpDay_US_Critic   ─ US デイトレ提案の審査（旧 CriticUSAgent の daytrade 側）
  MomentSwing_US_Critic─ US スイング提案の審査（旧 CriticUSAgent の swing 側）
  MomentSwing_JP_Critic─ JP スイング提案の審査（新規）
  FXRebalance_Critic   ─ FX シグナルの審査（旧 critic_fx.py 再構築）
  IntelCritic          ─ 情報シグナルの審査（旧 CriticIntelligenceAgent）

── refine_and_review の返り値 ──────────────────────────────
  (proposal, verdict)  : 承認確定（途中ラウンドでの承認含む）
  (None, verdict)      : 全ラウンド否決 or fixable=False or 提案者が修正断念
                         → 呼び出し元は None チェックで「提案ゼロ」として扱う
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from agents.base import BaseAgent, MarketContext, TradeProposal, CriticVerdict
from prompts.loader import get_prompt
from config.settings import RISK

_ET = ZoneInfo("America/New_York")


def _is_us_market_hours() -> bool:
    """米国株式市場の通常取引時間内か（ET 9:30〜16:00・平日のみ）。"""
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return False
    open_  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now_et <= close_


# ──────────────────────────────────────────────────────────────────
# 共通基底
# ──────────────────────────────────────────────────────────────────

class CriticBase(BaseAgent):
    """
    全クリティークの共通基底。
    - model はデフォルト Opus（発注最終判断者はケチらない）
    - refine_and_review() ループを一元実装
    - 具体的な review() はサブクラスが実装する
    """
    model = "claude-opus-4-8"
    _max_revision_rounds: int = 2  # 修正往復の最大回数（この回数だけ revise → review を繰り返す）

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        **review_kwargs,
    ) -> CriticVerdict:
        """提案を審査して CriticVerdict を返す（サブクラスで実装）。"""
        raise NotImplementedError

    def _fallback_verdict(self) -> CriticVerdict:
        return CriticVerdict(
            approved=False, score=0.0,
            issues=["LLM応答失敗のため審査不能"], suggestion="再試行してください",
        )

    def _parse_verdict(self, data: dict) -> CriticVerdict:
        """LLM 応答 dict を CriticVerdict に変換する共通ヘルパー。"""
        return CriticVerdict(
            approved=bool(data.get("approved", False)),
            score=float(data.get("score", 0.0)),
            issues=data.get("issues", []),
            suggestion=data.get("suggestion", ""),
            fixable=bool(data.get("fixable", True)),
        )

    def refine_and_review(
        self,
        proposer,
        proposal: TradeProposal,
        ctx: MarketContext,
        max_rounds: int | None = None,
        **review_kwargs,
    ) -> tuple["TradeProposal | None", "CriticVerdict | None"]:
        """
        提案 → 審査 → (否決なら修正 → 再審査) × max_rounds のループ。

        Returns:
            (proposal, verdict) : 承認確定
            (None, verdict)     : 全ラウンド否決 / fixable=False / 提案者が修正断念
                                  ※ エラーは発生しない。呼び出し元で None チェックすること。

        総レビュー回数 = max_rounds + 1（デフォルト3回）
        修正機会     = max_rounds 回（デフォルト2回）
        """
        rounds = max_rounds if max_rounds is not None else self._max_revision_rounds
        current = proposal
        last_verdict: CriticVerdict | None = None

        for round_n in range(rounds + 1):  # 0..rounds → 合計 rounds+1 回レビュー
            last_verdict = self.review(current, ctx, **review_kwargs)
            self._log_verdict(last_verdict)

            if last_verdict.approved:
                if round_n > 0:
                    self.logger.info(f"[{current.symbol}] {round_n}回修正後に承認")
                return current, last_verdict

            self.logger.info(
                f"[{current.symbol}] 審査{round_n + 1}/{rounds + 1}回目 否決 — "
                f"{', '.join(last_verdict.issues[:2])}"
            )

            # 修正不能（市場時間外・絶対禁止違反等）→ 即打ち切り
            if not last_verdict.fixable:
                self.logger.info(f"[{current.symbol}] fixable=False → 修正打ち切り")
                return None, last_verdict

            # 最後のラウンドで否決 → 修正せず終了
            if round_n == rounds:
                break

            # 提案者が revise_proposal を持たない → 修正不可
            if not hasattr(proposer, "revise_proposal"):
                return None, last_verdict

            revised = proposer.revise_proposal(
                current, last_verdict.issues, last_verdict.suggestion, ctx
            )
            if revised is None:
                self.logger.info(f"[{current.symbol}] 提案者が修正断念")
                return None, last_verdict

            current = revised

        self.logger.info(
            f"[{current.symbol}] 最大修正回数({rounds})到達後も否決確定 → 提案を棄却"
        )
        return None, last_verdict


# ──────────────────────────────────────────────────────────────────
# ScalpDay_JP_Critic（旧 CriticDayAgent）
# ──────────────────────────────────────────────────────────────────

class ScalpDay_JP_Critic(CriticBase):
    """JP デイトレ提案の審査。stop_loss 必須・当日決済前提。"""
    name = "ScalpDay_JP_Critic"
    system_prompt = get_prompt("critic_daytrade")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        **review_kwargs,
    ) -> CriticVerdict:
        wallet = review_kwargs.get("wallet", {})
        margin_wallet = wallet.get("MarginAccountWallet", 0)
        order_amount  = proposal.price * proposal.qty if proposal.price > 0 else 0

        # stop_loss 未設定は即否決（絶対ルール）
        if proposal.stop_loss is None:
            self.logger.warning(f"{proposal.symbol}: ストップロス未設定のため即否決")
            return CriticVerdict(
                approved=False, score=0.0,
                issues=["ストップロスが設定されていません（絶対禁止）"],
                suggestion="stop_loss を設定してから再提案してください",
                fixable=True,
            )

        prompt = f"""
## 審査対象の取引提案
{json.dumps({
    "symbol": proposal.symbol, "side": proposal.side,
    "qty": proposal.qty, "price": proposal.price,
    "stop_loss": proposal.stop_loss, "take_profit": proposal.take_profit,
    "rationale": proposal.rationale,
}, ensure_ascii=False, indent=2)}

## 市場コンテキスト
- リスク水準: {ctx.risk_level}

## 口座情報
- 信用取引余力: {margin_wallet:,}円
- 発注想定額（概算）: {order_amount:,}円
- 信用建玉上限: {RISK.max_daytrade_margin_jpy:,}円

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            return self._fallback_verdict()
        verdict = self._parse_verdict(data)
        self._log_verdict(verdict)
        return verdict


# ──────────────────────────────────────────────────────────────────
# ScalpDay_US_Critic（旧 CriticUSAgent の daytrade 側）
# ──────────────────────────────────────────────────────────────────

class ScalpDay_US_Critic(CriticBase):
    """US デイトレ提案の審査。市場時間外は fixable=False で即打ち切り。"""
    name = "ScalpDay_US_Critic"
    system_prompt = get_prompt("critic_us")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        **review_kwargs,
    ) -> CriticVerdict:
        account   = review_kwargs.get("account", {})
        fx_signal = review_kwargs.get("fx_signal")

        # 1. stop_loss 未設定
        stop_loss_pct = float((proposal.extra or {}).get("stop_loss_pct", 0) or 0)
        if proposal.stop_loss is None and not stop_loss_pct:
            return CriticVerdict(
                approved=False, score=0.0, fixable=True,
                issues=["ストップロスが設定されていません（絶対禁止）"],
                suggestion="stop_loss または stop_loss_pct を設定してから再提案してください",
            )

        # 2. 市場時間外（デイトレは ET 9:30〜16:00 厳守）
        if not _is_us_market_hours():
            now_et = datetime.now(_ET)
            return CriticVerdict(
                approved=False, score=0.0, fixable=False,
                issues=[f"米国市場時間外です（ET {now_et.strftime('%H:%M')}）"],
                suggestion="ET 9:30〜16:00 の時間帯に再提案してください",
            )

        # 3. FX 整合性
        us_weight_bias = (fx_signal or {}).get("us_weight_bias", "neutral")
        if us_weight_bias == "underweight" and proposal.side == "buy":
            return CriticVerdict(
                approved=False, score=0.1, fixable=False,
                issues=["FX戦略エージェントが米国株 underweight を指示"],
                suggestion="FX シグナルが neutral 以上に転じてから再提案してください",
            )

        effective_stop = proposal.stop_loss or (
            round(proposal.price * (1 - stop_loss_pct), 4) if proposal.price else None
        )
        order_usd  = (proposal.price or 0) * proposal.qty
        order_jpy  = order_usd * RISK.usd_jpy_rate
        equity_usd = account.get("equity", 0)

        prompt = f"""
## 審査対象の取引提案
{json.dumps({
    "symbol": proposal.symbol, "market": "US", "side": proposal.side,
    "qty": proposal.qty, "price": proposal.price,
    "stop_loss": effective_stop, "stop_loss_pct": stop_loss_pct or None,
    "take_profit": proposal.take_profit, "rationale": proposal.rationale,
}, ensure_ascii=False, indent=2)}

## ドル建て評価
- 発注額（USD）: ${order_usd:,.2f}  換算JPY: {order_jpy:,.0f}円
- 口座エクイティ（USD）: ${equity_usd:,.2f}
- 米国株1銘柄上限: ${RISK.max_us_position_usd:,}

## FX シグナル
{json.dumps(fx_signal, ensure_ascii=False, indent=2) if fx_signal else "（未提供）"}

## 市場コンテキスト
- リスク水準: {ctx.risk_level}

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            return self._fallback_verdict()
        return self._parse_verdict(data)


# ──────────────────────────────────────────────────────────────────
# MomentSwing_US_Critic（旧 CriticUSAgent の swing 側）
# ──────────────────────────────────────────────────────────────────

class MomentSwing_US_Critic(CriticBase):
    """US スイング提案の審査。保有期間は数日〜数週間、市場時間外でも fixable=True。"""
    name = "MomentSwing_US_Critic"
    system_prompt = get_prompt("critic_moment_swing_us")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        **review_kwargs,
    ) -> CriticVerdict:
        account   = review_kwargs.get("account", {})
        fx_signal = review_kwargs.get("fx_signal")

        # stop_loss 未設定（pct 代替可）
        stop_loss_pct = float((proposal.extra or {}).get("stop_loss_pct", 0) or 0)
        if proposal.stop_loss is None and not stop_loss_pct:
            return CriticVerdict(
                approved=False, score=0.0, fixable=True,
                issues=["ストップロス(stop_loss_pct)が設定されていません"],
                suggestion="stop_loss_pct を設定してから再提案してください",
            )

        # FX 整合性（スイングも buy は underweight 時に否決）
        us_weight_bias = (fx_signal or {}).get("us_weight_bias", "neutral")
        if us_weight_bias == "underweight" and proposal.side == "buy":
            return CriticVerdict(
                approved=False, score=0.1, fixable=False,
                issues=["FX戦略エージェントが米国株 underweight を指示"],
                suggestion="FX シグナルが neutral 以上に転じてから再提案してください",
            )

        effective_stop = proposal.stop_loss or (
            round(proposal.price * (1 - stop_loss_pct), 4) if proposal.price else None
        )
        tp_pct = float((proposal.extra or {}).get("target_return_pct", 0) or 0)
        order_usd  = (proposal.price or 0) * proposal.qty
        order_jpy  = order_usd * RISK.usd_jpy_rate

        prompt = f"""
## 審査対象のスイング提案
{json.dumps({
    "symbol": proposal.symbol, "market": "US", "side": proposal.side,
    "qty": proposal.qty, "price": proposal.price,
    "stop_loss": effective_stop, "stop_loss_pct": stop_loss_pct or None,
    "target_return_pct": tp_pct or None,
    "take_profit": proposal.take_profit, "rationale": proposal.rationale,
}, ensure_ascii=False, indent=2)}

## ドル建て評価
- 発注額（USD）: ${order_usd:,.2f}  換算JPY: {order_jpy:,.0f}円
- 口座エクイティ（USD）: ${account.get('equity', 0):,.2f}
- 米国株1銘柄上限: ${RISK.max_us_position_usd:,}

## FX シグナル
{json.dumps(fx_signal, ensure_ascii=False, indent=2) if fx_signal else "（未提供）"}

## 市場コンテキスト
- リスク水準: {ctx.risk_level}

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            return self._fallback_verdict()
        return self._parse_verdict(data)


# ──────────────────────────────────────────────────────────────────
# MomentSwing_JP_Critic（新規）
# ──────────────────────────────────────────────────────────────────

class MomentSwing_JP_Critic(CriticBase):
    """JP スイング提案の審査。stop_loss_pct 代替可・当日クローズ不要。"""
    name = "MomentSwing_JP_Critic"
    system_prompt = get_prompt("critic_moment_swing_jp")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        **review_kwargs,
    ) -> CriticVerdict:
        wallet = review_kwargs.get("wallet", {})
        margin_wallet = wallet.get("MarginAccountWallet", 0)

        # stop_loss 未設定（pct 代替可）
        stop_loss_pct = float((proposal.extra or {}).get("stop_loss_pct", 0) or 0)
        if proposal.stop_loss is None and not stop_loss_pct:
            return CriticVerdict(
                approved=False, score=0.0, fixable=True,
                issues=["ストップロス(stop_loss_pct)が設定されていません"],
                suggestion="stop_loss_pct を設定してから再提案してください",
            )

        effective_stop = proposal.stop_loss or (
            round(proposal.price * (1 - stop_loss_pct), 4) if proposal.price else None
        )
        tp_pct    = float((proposal.extra or {}).get("target_return_pct", 0) or 0)
        order_jpy = (proposal.price or 0) * proposal.qty

        prompt = f"""
## 審査対象のスイング提案（日本株）
{json.dumps({
    "symbol": proposal.symbol, "market": "JP", "side": proposal.side,
    "qty": proposal.qty, "price": proposal.price,
    "stop_loss": effective_stop, "stop_loss_pct": stop_loss_pct or None,
    "target_return_pct": tp_pct or None,
    "take_profit": proposal.take_profit, "rationale": proposal.rationale,
}, ensure_ascii=False, indent=2)}

## 口座情報
- 信用取引余力: {margin_wallet:,}円
- 発注想定額: {order_jpy:,.0f}円
- 1銘柄上限: {RISK.max_position_jpy:,}円

## 市場コンテキスト
- リスク水準: {ctx.risk_level}

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            return self._fallback_verdict()
        return self._parse_verdict(data)


# ──────────────────────────────────────────────────────────────────
# FXRebalance_Critic（旧 critic_fx.py 再構築）
# ──────────────────────────────────────────────────────────────────

class FXRebalance_Critic(CriticBase):
    """FX シグナルの審査。TradeProposal ではなくシグナル dict を受け取る点に注意。"""
    name = "FXRebalance_Critic"
    system_prompt = get_prompt("critic_fx")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        **review_kwargs,
    ) -> CriticVerdict:
        """TradeProposal を使う標準 review インターフェース（呼び出し元が合わせる場合用）。"""
        fx_signal = review_kwargs.get("fx_signal", {})
        return self.review_signal(fx_signal, ctx)

    def review_signal(
        self,
        fx_signal: dict,
        ctx: MarketContext,
    ) -> CriticVerdict:
        """FX シグナル dict を直接受け取って審査する。"""
        target_ratio = float(fx_signal.get("target_usd_ratio", 50))
        current_ratio = float(fx_signal.get("current_usd_ratio", 50))
        change = abs(target_ratio - current_ratio)

        # 変更幅が 20% 超は即否決（段階的変更のみ許可）
        if change > 20:
            return CriticVerdict(
                approved=False, score=0.2, fixable=True,
                issues=[f"一度のドル比率変更が{change:.0f}%超（上限20%）"],
                suggestion=f"変更幅を20%以下に分割してください（例: {current_ratio:.0f}% → {current_ratio + 20:.0f}%）",
            )

        prompt = f"""
## 審査対象の FX シグナル
{json.dumps(fx_signal, ensure_ascii=False, indent=2)}

## 市場コンテキスト
- リスク水準: {ctx.risk_level}

上記の審査チェックリストを順番に確認し、審査結果を JSON で返してください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            return self._fallback_verdict()
        return self._parse_verdict(data)


# ──────────────────────────────────────────────────────────────────
# IntelCritic（旧 CriticIntelligenceAgent）
# ──────────────────────────────────────────────────────────────────

class IntelCritic(CriticBase):
    """情報シグナルの審査。TradeProposal でなくシグナルリストを受け取る。"""
    name = "IntelCritic"
    system_prompt = get_prompt("critic_intelligence")

    def review(
        self,
        proposal: TradeProposal,
        ctx: MarketContext,
        **review_kwargs,
    ) -> CriticVerdict:
        """標準インターフェース（呼び出し元が合わせる場合用）。通常は review_signals() を使うこと。"""
        raise NotImplementedError("IntelCritic は review_signals() を使ってください")

    def review_signals(
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
    "relevance_score": 0.0〜1.0（再評価後）,
    "reliability_score": 0.0〜1.0,
    "sectors": ["セクター名"],
    "supply_chain_position": "上流 | 中流 | 下流 | 不明",
    "downstream_impact": "波及効果（なければ null）",
    "classification": "業界内話題 | 公式発表 | 研究成果 | PR記事",
    "summary": "100文字以内",
    "url": "URL",
    "approved": true | false
  }}
]
"""
        data = self._ask_llm_json(prompt)
        if not isinstance(data, list):
            self.logger.warning("IntelCritic: LLM 応答失敗。元シグナルを返します")
            return signals

        approved = [
            s for s in data
            if s.get("approved", True) and s.get("relevance_score", 0) >= 0.6
        ]
        self.logger.info(f"IntelCritic: {len(signals)} 件 → {len(approved)} 件承認")
        return approved
