"""
agents/moment_swing.py
モメンタム×スイング戦略エージェント群。

MomentSwingBase  ─ スクリーニング・提案生成・修正の戦略ロジック（市場非依存）
  MomentSwing_US ─ 米国株モメンタム×スイング（Alpaca・USD建て）
  MomentSwing_JP ─ 日本株モメンタム×スイング（kabu API・JPY建て）

統合第一段（機械的Darvasへの統一）:
  screen_value  : Darvas ボックスブレイクアウトを検出してエントリー提案を返す。
                  LLM 裁量選択は廃止。翌営業日始値でのエントリーを前提とする。
  check_exits   : 既存ポジションのトレイリング床を再計算し、床割れ銘柄の売り提案を返す。
  revise_proposal: Critic の指摘を受けて提案を修正（Darvas パラメータの範囲内で）。

市場依存部分（サブクラスに実装）:
  _market           "JP" | "US"
  _currency         "JPY" | "USD"
  _currency_symbol  "¥"  | "$"
  _load_bars(symbol) → pd.DataFrame  日足 OHLCV を返す
"""
import logging
from typing import Optional

import pandas as pd

from agents.base import BaseAgent, MarketContext, TradeProposal
from strategies.darvas import (
    DarvasParams,
    EntrySignal,
    build_weekly_filter,
    scan_entry_signal,
    compute_trailing_stop,
)

logger = logging.getLogger(__name__)

# strategies/darvas.py を Darvasパラメータの単一の真実源とする。
# DarvasParams() のデフォルト値をそのまま使うことで、
# strategies/darvas.py を変更すればバックテスト・JP・US が自動追従する。
# 将来 JP/US でパラメータを変えたい場合は各サブクラスで
# _darvas_params プロパティをオーバーライドして差し替える形が自然。
_DEFAULT_DARVAS = DarvasParams()


class MomentSwingBase(BaseAgent):
    """
    モメンタム×スイング戦略ロジックの基底。
    売買判断は機械的 Darvas ボックス（strategies.darvas 共通モジュール）を使う。
    市場差分（データ取得方法・通貨単位）はサブクラスに委譲する。
    """

    _market: str = ""
    _currency: str = ""
    _currency_symbol: str = ""

    def _load_bars(self, symbol: str) -> pd.DataFrame:
        """銘柄の日足 OHLCV を返す。サブクラスでオーバーライドする。"""
        raise NotImplementedError

    def _calc_unit_qty(self, price: float, max_position: float) -> int:
        """ポジション上限に収まる最大株数（100株単位）を返す。"""
        raw = int(max_position / price / 100) * 100
        return max(raw, 100)

    def screen_value(
        self,
        universe: list[dict],
        ctx: MarketContext,
        existing_symbols: list[str],
        max_position: float,
        cash: float,
    ) -> list[TradeProposal]:
        """
        ユニバースから Darvas ブレイクアウト銘柄を検出してエントリー提案を返す。

        判断基準（LLM なし、機械的 Darvas）:
          1. 日足 OHLCV を取得
          2. 週足フィルタ：直近確定週足が新高値圏のとき Entry 許可
          3. 終値が確定箱の天井を初めて上回れば "buy" 提案
          4. 提案の初期ストップ = 箱の床（Darvas floor）

        max_position / cash の通貨単位はサブクラスの _currency に従う。
        existing_symbols に含まれる銘柄はスキップ（重複エントリー防止）。
        """
        available = [u for u in universe if u["symbol"] not in existing_symbols]
        if not available:
            self.logger.info(f"{self.name}: スクリーニング対象なし（全銘柄保有済み）")
            return []

        params = _DEFAULT_DARVAS
        proposals: list[TradeProposal] = []

        for u in available:
            symbol = u["symbol"]
            try:
                df = self._load_bars(symbol)
            except Exception as e:
                self.logger.warning(f"{self.name}: {symbol} データ取得失敗 → スキップ ({e})")
                continue

            if df.empty or len(df) < params.top_n + params.bottom_m + 2:
                self.logger.debug(f"{self.name}: {symbol} データ不足 → スキップ")
                continue

            weekly_ok = build_weekly_filter(df, params)
            signal: EntrySignal = scan_entry_signal(df, params, weekly_ok)

            if not signal.has_signal:
                self.logger.debug(
                    f"{self.name}: {symbol} シグナルなし (phase={signal.phase})"
                )
                continue

            # 最終バーの終値を暫定価格として使う（実際は翌営業日始値）
            last_close = float(df.iloc[-1]["close"])
            qty = self._calc_unit_qty(last_close, max_position)

            # stop_loss_pct: 箱床 → 現在終値の下落率（参考値。実際は絶対額で管理）
            sl_pct = round((last_close - signal.box_bottom) / last_close, 4) if last_close > 0 else 0.06

            self.logger.info(
                f"{self.name}: {symbol} Darvas ブレイク "
                f"box_top={signal.box_top:.1f} close={last_close:.1f} "
                f"floor={signal.box_bottom:.1f} sl_pct={sl_pct:.1%}"
            )

            proposals.append(TradeProposal(
                agent=self.name,
                symbol=symbol,
                market=self._market,
                side="buy",
                qty=qty,
                price=0.0,          # 翌営業日始値の成行
                strategy="momentum_swing",
                rationale=(
                    f"Darvas箱ブレイク: "
                    f"天井{signal.box_top:.1f}→終値{last_close:.1f} "
                    f"床（初期SL）{signal.box_bottom:.1f}"
                ),
                stop_loss=signal.box_bottom,   # Darvas 床が初期ストップ（絶対価格）
                take_profit=None,              # 固定 TP 廃止（床トレイリングが出口）
                extra={
                    "hold_horizon":    "weeks_to_months",
                    "darvas_box_top":  signal.box_top,
                    "darvas_floor":    signal.box_bottom,   # 初期ストップ絶対価格
                    "stop_loss_pct":   sl_pct,              # Darvas floor から算出（参考値）
                    "name":            u.get("name", symbol),
                },
            ))

        self.logger.info(f"{self.name}: エントリー候補 {len(proposals)} 銘柄")
        return proposals

    def check_exits(
        self,
        positions: list[dict],
    ) -> list[TradeProposal]:
        """
        既存ポジションの Darvas トレイリング床を再計算し、床割れ銘柄の売り提案を返す。

        Args:
            positions: 保有ポジションのリスト。各要素に以下を含む:
              - "symbol":        銘柄コード
              - "qty":           保有株数
              - "entry_date":    エントリー日（pd.Timestamp または ISO 文字列）
              - "darvas_floor":  エントリー時の Darvas 床（初期ストップ絶対価格）

        Returns:
            床割れした銘柄の sell TradeProposal リスト（床割れなければ空リスト）。
        """
        params = _DEFAULT_DARVAS
        proposals: list[TradeProposal] = []

        for pos in positions:
            symbol = pos.get("symbol", "")
            qty = pos.get("qty", 0)
            entry_date_raw = pos.get("entry_date")
            initial_stop = float(pos.get("darvas_floor", 0.0))

            if not symbol or not entry_date_raw or initial_stop <= 0:
                self.logger.warning(f"{self.name}: {symbol} exit チェックに必要なフィールド不足 → スキップ")
                continue

            # entry_date を pd.Timestamp に変換
            entry_date: pd.Timestamp
            if isinstance(entry_date_raw, pd.Timestamp):
                entry_date = entry_date_raw
            else:
                entry_date = pd.Timestamp(str(entry_date_raw), tz="Asia/Tokyo")

            try:
                df = self._load_bars(symbol)
            except Exception as e:
                self.logger.warning(f"{self.name}: {symbol} exit データ取得失敗 → スキップ ({e})")
                continue

            if df.empty:
                continue

            current_stop = compute_trailing_stop(df, params, entry_date, initial_stop)
            last_close = float(df.iloc[-1]["close"])

            self.logger.debug(
                f"{self.name}: {symbol} trailing_stop={current_stop:.1f} close={last_close:.1f}"
            )

            if last_close < current_stop:
                self.logger.info(
                    f"{self.name}: {symbol} 床割れ → sell提案 "
                    f"close={last_close:.1f} < floor={current_stop:.1f}"
                )
                proposals.append(TradeProposal(
                    agent=self.name,
                    symbol=symbol,
                    market=self._market,
                    side="sell",
                    qty=qty,
                    price=0.0,
                    strategy="momentum_swing",
                    rationale=(
                        f"Darvas床割れ: 終値{last_close:.1f} < "
                        f"トレイリング床{current_stop:.1f}"
                    ),
                    stop_loss=None,
                    take_profit=None,
                    extra={"current_darvas_floor": current_stop},
                ))

        return proposals

    def revise_proposal(
        self,
        proposal: "TradeProposal",
        issues: list[str],
        suggestion: str,
        ctx: "MarketContext",
    ) -> "TradeProposal | None":
        """
        Critic の指摘を受けて提案を修正する。

        Darvas ベースの提案では:
          - stop_loss は Darvas 床（絶対価格）が基準。箱床より浅いストップは認めない
          - take_profit は設定しない（床トレイリングが出口のため）
          - 株数の調整のみ Critic からの修正を反映する
          - "withdraw" が返ったら None を返す

        LLM は "箱床より浅い SL を設定していないか" のみ確認する簡易プロンプト。
        """
        import json

        darvas_floor = proposal.extra.get("darvas_floor", proposal.stop_loss or 0.0)
        prop_dict = {
            "symbol":       proposal.symbol,
            "qty":          proposal.qty,
            "darvas_floor": darvas_floor,
            "rationale":    proposal.rationale,
        }
        prompt = f"""
あなたがDarvasボックスブレイクアウト提案を出したところ、審査担当（Critic）から以下の指摘を受けました。
提案を修正するか、撤回するかを判断してください。

## あなたの元の提案
{json.dumps(prop_dict, ensure_ascii=False, indent=2)}

## 市場コンテキスト（CIO判断）
- リスク水準: {ctx.risk_level}
- セクターローテーション: {ctx.rotation_signal}

## 審査担当の指摘
{json.dumps(issues, ensure_ascii=False)}

## 修正ヒント
{suggestion}

## 修正の注意点
- 出口はDarvas床トレイリングのため、固定の損切り率や利確率は設定しない
- stop_loss（絶対価格）はDarvas床 {darvas_floor:.1f} を下回ってはならない
- qty のみ調整可能（株数を減らす方向のみ Critic の指摘を反映）
- モメンタムが明確に崩れた（逆行・出来高急減）場合のみ {{"action": "withdraw"}} を返す

{{
  "action": "buy" | "withdraw",
  "qty": 整数,
  "rationale": "修正後の根拠（80文字以内）"
}}
"""
        data = self._ask_llm_json(prompt)
        if not data or data.get("action") == "withdraw":
            self.logger.info(f"{proposal.symbol}: 投資提案修正断念")
            return None

        proposal.qty = max(1, int(data.get("qty", proposal.qty)))
        proposal.rationale = data.get("rationale", proposal.rationale)
        self.logger.info(
            f"{proposal.symbol}: 投資提案修正完了 qty={proposal.qty}"
        )
        return proposal


class MomentSwing_US(MomentSwingBase):
    """米国株モメンタム×スイング（Alpaca・USD建て、保有期間：数週間〜数ヶ月）。"""
    name = "MomentSwing_US"
    system_prompt = (
        "あなたは米国株のモメンタム×スイングトレード専門家です。\n"
        "Darvas ボックス理論に基づき、価格ブレイクアウト・出来高増加・相対強度を軸に、\n"
        "数週間〜数ヶ月の保有で最大リターンを狙います。\n"
        "セクターローテーションとCIOの活性セクター判断を重視し、\n"
        "出来高が薄い銘柄・過熱した銘柄は対象外とします。"
    )
    _market = "US"
    _currency = "USD"
    _currency_symbol = "$"

    def _load_bars(self, symbol: str) -> pd.DataFrame:
        """米国株日足をAlpaca APIから取得する。"""
        from data.us_market import get_daily_bars_us
        bars = get_daily_bars_us(symbol, limit=252)  # 約1年分
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame(bars)
        df["ts_utc"] = pd.to_datetime(df["date"], utc=True)
        # resample_bars / build_weekly_filter が要求する共通スキーマ列を追加
        df["symbol"] = symbol
        df["market"] = "US"
        return df

    def _calc_unit_qty(self, price: float, max_position: float) -> int:
        """米国株は1株単位（100株単位なし）。"""
        return max(1, int(max_position / price))


class MomentSwing_JP(MomentSwingBase):
    """日本株モメンタム×スイング（kabu API・JPY建て、保有期間：数週間〜数ヶ月）。"""
    name = "MomentSwing_JP"
    system_prompt = (
        "あなたは日本株のモメンタム×スイングトレード専門家です。\n"
        "Darvas ボックス理論に基づき、価格ブレイクアウト・出来高増加・相対強度を軸に、\n"
        "数週間〜数ヶ月の保有で最大リターンを狙います。\n"
        "セクターローテーションとCIOの活性セクター判断を重視し、\n"
        "出来高が薄い銘柄・過熱した銘柄・信用倍率が極端な銘柄は対象外とします。"
    )
    _market = "JP"
    _currency = "JPY"
    _currency_symbol = "¥"
    _min_unit: int = 100  # 100株単位

    def _load_bars(self, symbol: str) -> pd.DataFrame:
        """日本株日足をキャッシュ（またはJ-Quants）から取得する。"""
        from data.historical.jp_bars import load_bars
        return load_bars(symbol)
