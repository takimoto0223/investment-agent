"""
backtests/moment_swing/darvas_backtest.py
Darvas ボックスによる MomentSwing バックテストエンジン。

ルックアヘッドバイアス回避ポリシー（WHY: ルックアヘッドがあると過去に遡って未来の値を
利用することになり、実運用で再現不可能な成績が出る）:
  - シグナル判定: 当日の high / low / close のみ使用
  - 約定: 常に翌営業日の open で執行（buy/sell とも）
  - シグナル日の終値での即時約定は行わない

売買ルール（ベースライン Darvas Box）:
  1. 天井確定: 新高値後 N 日間高値を更新しなければ天井確定 (default N=3)
  2. 床確定: 天井確定後 M 日間安値を更新しなければ床確定 (default M=3)
  3. 週足フィルタ: 直近確定週足が新高値圏/高値圏内のときのみ entry 許可
  4. エントリー: 終値 > 天井 → 翌日始値で買い
  5. エグジット: 終値 < 現在の床 → 翌日始値で損切り
  6. トレイリング: ポジション中に新しい床が確定し、現在の床より高ければ引き上げ
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple
from typing import Optional

import pandas as pd

from config.settings import RISK
from data.historical.jp_bars import fetch_daily_bars, load_bars, save_bars
from data.historical.resample import resample_bars
from data.historical.newhigh import screen_new_high


# ---------------------------------------------------------------------------
# パラメータ・データクラス
# ---------------------------------------------------------------------------

@dataclass
class BacktestParams:
    top_n: int = 3              # 天井確定: N日間新高値なしで確定
    bottom_m: int = 3           # 床確定: M日間新安値なしで確定
    slippage_pct: float = 0.1   # 片道スリッページ %
    commission_pct: float = 0.0 # 片道手数料 %（kabu.com オンライン: 無料想定）
    weekly_filter: bool = True  # 週足フィルタ使用
    near_high_pct: float = 5.0  # 週足「高値圏」しきい値 %
    weekly_window: int = 52     # 週足 rolling max の期間（週）
    position_size_jpy: int = field(
        default_factory=lambda: RISK.max_position_jpy
    )


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str        # "stop" | "eod" (end-of-data)
    shares: int
    pnl_pct: float          # スリッページ・手数料込み
    pnl_jpy: float
    hold_days: int
    box_top: float          # エントリー時の天井
    box_bottom: float       # エントリー時の床（トレイリング前）
    stop_history: List[Tuple[pd.Timestamp, float]] = field(default_factory=list)
    # [(date, stop_level), ...] ストップ水準の時系列（エントリー日を含む全更新点）


@dataclass
class BacktestResult:
    symbol: str
    params: BacktestParams
    trades: list[Trade]


# ---------------------------------------------------------------------------
# 週足フィルタ構築
# ---------------------------------------------------------------------------

def _build_weekly_filter(df_daily: pd.DataFrame, params: BacktestParams) -> dict[pd.Timestamp, bool]:
    """
    日付 → 週足フィルタ通過か否か の dict を返す。
    当日より前に確定した最新週足バーが is_new_high or is_near_high なら True。

    Why: 当日の週足バーは未確定（週中）のため、完全に確定した前週以前のみ参照する。
    """
    df_w = resample_bars(df_daily, "W")
    df_ws = screen_new_high(df_w, window=params.weekly_window, near_high_pct=params.near_high_pct)
    df_ws = df_ws.sort_values("ts_utc").set_index("ts_utc")

    result: dict[pd.Timestamp, bool] = {}
    for _, row in df_daily.iterrows():
        date = row["ts_utc"]
        # 当日より前に終端する週足バーのみ（当日以前を含む週は未確定）
        prev = df_ws[df_ws.index < date]
        if prev.empty:
            result[date] = False
        else:
            last = prev.iloc[-1]
            result[date] = bool(last["is_new_high"] or last["is_near_high"])
    return result


# ---------------------------------------------------------------------------
# シミュレーション本体
# ---------------------------------------------------------------------------

def _simulate(
    df: pd.DataFrame,
    weekly_ok: dict[pd.Timestamp, bool],
    params: BacktestParams,
    symbol: str,
) -> list[Trade]:
    """
    日次ループによるルックアヘッドなしシミュレーション。

    Why: ベクトル演算では「前日の信号→翌日の始値約定」の境界を誤りやすいため、
    明示的な日次ループで pending フラグを1日ずらして約定する。
    """
    daily = df.reset_index(drop=True)
    n = len(daily)
    trades: list[Trade] = []

    # --- 箱形成ステート（エントリー前） ---
    phase = "search_top"        # "search_top" | "search_bottom" | "box_ready"
    cand_top = -math.inf
    no_new_high = 0
    box_top: Optional[float] = None
    cand_bottom = math.inf
    no_new_low = 0
    box_bottom: Optional[float] = None

    # --- ポジションステート ---
    in_position = False
    entry_price = 0.0
    entry_date: Optional[pd.Timestamp] = None
    entry_box_top = 0.0
    entry_box_bottom = 0.0
    entry_shares = 0
    stop_level = 0.0            # 現在の損切り水準（トレイリングで更新）

    # --- トレイリング箱形成（ポジション中） ---
    t_phase = "search_top"
    t_cand_top = -math.inf
    t_no_new_high = 0
    t_box_top: Optional[float] = None
    t_cand_bottom = math.inf
    t_no_new_low = 0

    # --- 翌日約定待ちシグナル ---
    pending: Optional[str] = None   # "buy" | "sell"
    p_box_top = 0.0
    p_box_bottom = 0.0

    # --- ストップ水準の時系列（可視化用） ---
    stop_history_cur: List[Tuple[pd.Timestamp, float]] = []

    for i in range(n):
        row = daily.iloc[i]
        date: pd.Timestamp = row["ts_utc"]
        o: float = row["open"]
        h: float = row["high"]
        lo: float = row["low"]
        c: float = row["close"]

        # ================================================================
        # A. 翌日始値約定（前日シグナルをここで執行）
        # ================================================================
        if pending == "buy" and not in_position:
            slip = o * params.slippage_pct / 100
            entry_price = o + slip
            entry_date = date
            entry_box_top = p_box_top
            entry_box_bottom = p_box_bottom
            stop_level = p_box_bottom
            # 単元株(100株)単位でサイジング
            raw_shares = int(params.position_size_jpy / entry_price / 100) * 100
            entry_shares = max(raw_shares, 100)
            in_position = True
            pending = None
            stop_history_cur = [(entry_date, stop_level)]  # 初期ストップを記録
            # トレイリング箱の初期化
            t_phase = "search_top"
            t_cand_top = h
            t_no_new_high = 0
            t_box_top = None
            t_cand_bottom = lo
            t_no_new_low = 0

        elif pending == "sell" and in_position:
            slip = o * params.slippage_pct / 100
            exit_price = o - slip
            comm = (entry_price + exit_price) * params.commission_pct / 100
            pnl_jpy = (exit_price - entry_price) * entry_shares - comm
            pnl_pct = pnl_jpy / (entry_price * entry_shares) * 100
            trades.append(Trade(
                symbol=symbol,
                entry_date=entry_date,
                entry_price=round(entry_price, 1),
                exit_date=date,
                exit_price=round(exit_price, 1),
                exit_reason="stop",
                shares=entry_shares,
                pnl_pct=round(pnl_pct, 2),
                pnl_jpy=round(pnl_jpy),
                hold_days=(date - entry_date).days,
                box_top=entry_box_top,
                box_bottom=entry_box_bottom,
                stop_history=list(stop_history_cur),
            ))
            in_position = False
            pending = None
            stop_history_cur = []
            # 箱形成を初期化してリスタート
            phase = "search_top"
            cand_top = -math.inf
            no_new_high = 0
            box_top = None
            cand_bottom = math.inf
            no_new_low = 0
            box_bottom = None

        # ================================================================
        # B. エントリー前の箱形成ステート更新
        # ================================================================
        if not in_position:
            if phase == "search_top":
                if h > cand_top:
                    cand_top = h
                    no_new_high = 0
                else:
                    no_new_high += 1
                    if no_new_high >= params.top_n:
                        box_top = cand_top
                        phase = "search_bottom"
                        cand_bottom = lo
                        no_new_low = 0

            elif phase == "search_bottom":
                if h > box_top:                 # 天井を上抜け → 箱をリセット
                    cand_top = h
                    no_new_high = 0
                    box_top = None
                    phase = "search_top"
                elif lo < cand_bottom:
                    cand_bottom = lo
                    no_new_low = 0
                else:
                    no_new_low += 1
                    if no_new_low >= params.bottom_m:
                        box_bottom = cand_bottom
                        phase = "box_ready"

            elif phase == "box_ready":
                if c > box_top:
                    # 終値が天井を上抜け → 翌営業日始値で買い（ルックアヘッド回避）
                    # Why: h > box_top（日中ヒゲ）は箱維持。確定は終値のみで判断する。
                    if not params.weekly_filter or weekly_ok.get(date, False):
                        pending = "buy"
                        p_box_top = box_top
                        p_box_bottom = box_bottom

        # ================================================================
        # C. ポジション中: 損切り判定 & トレイリング床更新
        # ================================================================
        else:
            if c < stop_level and pending is None:
                # 終値が床を下回り → 翌日損切り
                pending = "sell"

            elif pending is None:
                # トレイリング箱形成
                if t_phase == "search_top":
                    if h > t_cand_top:
                        t_cand_top = h
                        t_no_new_high = 0
                    else:
                        t_no_new_high += 1
                        if t_no_new_high >= params.top_n:
                            t_box_top = t_cand_top
                            t_phase = "search_bottom"
                            t_cand_bottom = lo
                            t_no_new_low = 0

                elif t_phase == "search_bottom":
                    if h > t_box_top:
                        t_cand_top = h
                        t_no_new_high = 0
                        t_box_top = None
                        t_phase = "search_top"
                    elif lo < t_cand_bottom:
                        t_cand_bottom = lo
                        t_no_new_low = 0
                    else:
                        t_no_new_low += 1
                        if t_no_new_low >= params.bottom_m:
                            new_floor = t_cand_bottom
                            if new_floor > stop_level:  # 床を切り上げる場合のみ更新
                                stop_level = new_floor
                                stop_history_cur.append((date, stop_level))
                            # トレイリング追跡をリセットして次の箱を探す
                            t_phase = "search_top"
                            t_cand_top = h
                            t_no_new_high = 0
                            t_box_top = None
                            t_cand_bottom = lo
                            t_no_new_low = 0

    # ================================================================
    # D. データ末尾でポジションを終値で手仕舞い
    # pending=="sell" のときはトレイリング損切りが発動済みだが次の足がないケース
    # ================================================================
    if in_position:
        last = daily.iloc[-1]
        slip = last["close"] * params.slippage_pct / 100
        exit_price = last["close"] - slip
        comm = (entry_price + exit_price) * params.commission_pct / 100
        pnl_jpy = (exit_price - entry_price) * entry_shares - comm
        pnl_pct = pnl_jpy / (entry_price * entry_shares) * 100
        reason = "stop_eod" if pending == "sell" else "eod"
        trades.append(Trade(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=round(entry_price, 1),
            exit_date=last["ts_utc"],
            exit_price=round(exit_price, 1),
            exit_reason=reason,
            shares=entry_shares,
            pnl_pct=round(pnl_pct, 2),
            pnl_jpy=round(pnl_jpy),
            hold_days=(last["ts_utc"] - entry_date).days,
            box_top=entry_box_top,
            box_bottom=entry_box_bottom,
            stop_history=list(stop_history_cur),
        ))

    return trades


# ---------------------------------------------------------------------------
# 成績集計
# ---------------------------------------------------------------------------

def _summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {}

    pnls = [t.pnl_jpy for t in trades]
    pnl_pcts = [t.pnl_pct for t in trades]
    wins = [t for t in trades if t.pnl_jpy > 0]

    # 最大ドローダウン（複利 equity curve ベース）
    # Why: 旧式は peak 初期値0のため第1トレードが損失だと必ず -100% になる欠陥があった。
    # pnl_pct を複利合成した equity curve（初期値 1.0）を使い、
    # 確定トレードの収益率ベースの最大ドローダウンを正確に計測する。
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in pnl_pcts:
        equity *= (1 + r / 100)
        peak = max(peak, equity)
        dd = (equity - peak) / peak
        max_dd = min(max_dd, dd)

    return {
        "トレード数": len(trades),
        "勝率": f"{len(wins)/len(trades)*100:.1f}%",
        "平均損益率": f"{sum(pnl_pcts)/len(pnl_pcts):+.2f}%",
        "損益合計": f"{sum(pnls):+,.0f} 円",
        "最大ドローダウン": f"{max_dd*100:.1f}%",
        "平均保有日数": f"{sum(t.hold_days for t in trades)/len(trades):.1f} 日",
        "勝ちトレード数": len(wins),
        "負けトレード数": len(trades) - len(wins),
    }


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def run_backtest(symbol: str, params: Optional[BacktestParams] = None) -> BacktestResult:
    """Darvas ボックスバックテストを実行して結果を返す。"""
    if params is None:
        params = BacktestParams()

    # キャッシュがあれば使用、なければ取得して保存
    try:
        df = load_bars(symbol)
    except FileNotFoundError:
        df = fetch_daily_bars(symbol, years=3)
        if not df.empty:
            save_bars(df)

    if df.empty:
        raise ValueError(f"{symbol} のデータが取得できませんでした。")

    weekly_ok = _build_weekly_filter(df, params)
    trades = _simulate(df, weekly_ok, params, symbol)
    return BacktestResult(symbol=symbol, params=params, trades=trades)
