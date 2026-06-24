"""
strategies/darvas.py
Darvas ボックス状態機械 ── バックテスト / エージェント共通コア。

Why ここに分離した:
  darvas_backtest.py のシミュレーションループと agents/moment_swing.py の
  リアルタイム判断が同一アルゴリズムを使う。重複実装を避けるため共通化した。

公開 API:
  DarvasParams        ── ボックス形成パラメータ
  EntrySignal         ── scan_entry_signal の返り値
  DarvasBoxMachine    ── ボックス形成ステートマシン（エントリー・トレイリング共用）
  build_weekly_filter ── 日付 → 週足フィルタ通過フラグ の dict
  scan_entry_signal   ── 最終バーのブレイクアウトシグナルを返す（エージェント用）
  compute_trailing_stop ── エントリー日以降のトレイリング床を再生して返す（エージェント用）

ルックアヘッドバイアス回避ポリシー（backtests/moment_swing/darvas_backtest.py と同一）:
  シグナル判定: 当日の high / low / close のみ使用。
  約定: 翌営業日の open で執行を前提とする（本モジュールは約定処理を持たない）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# パラメータ
# ---------------------------------------------------------------------------

@dataclass
class DarvasParams:
    """Darvas ボックス形成パラメータ。BacktestParams のうちロジック部分のみ定義。"""
    top_n: int = 3              # 天井確定: N 日間新高値なしで確定
    bottom_m: int = 3           # 床確定: M 日間新安値なしで確定
    weekly_filter: bool = True  # 週足フィルタ使用
    near_high_pct: float = 5.0  # 週足「高値圏」しきい値 %
    weekly_window: int = 52     # 週足 rolling max の期間（週）


# ---------------------------------------------------------------------------
# 返り値型
# ---------------------------------------------------------------------------

@dataclass
class EntrySignal:
    """scan_entry_signal の返り値。"""
    has_signal: bool         # True = 最終バーがブレイクアウト日
    box_top: float           # 確定した箱天井（has_signal=False でも現在値を保持）
    box_bottom: float        # 確定した箱床 = 初期ストップ候補
    phase: str = "unknown"   # 現在のフェーズ（デバッグ・ロギング用）


# ---------------------------------------------------------------------------
# ステートマシン
# ---------------------------------------------------------------------------

class DarvasBoxMachine:
    """
    Darvas ボックス形成ステートマシン（search_top → search_bottom → box_ready）。

    エントリー前の箱検出とポジション中のトレイリングの両方に使用できる。
    各フェーズの詳細:
      search_top   : 新高値を追いかけて候補天井を更新
      search_bottom: 天井確定後、候補床を追いかける
      box_ready    : 天井・床ともに確定した完成箱

    ステートは外部から直接参照してよい:
      machine.phase      "search_top" | "search_bottom" | "box_ready"
      machine.box_top    確定した天井 (box_ready のときのみ有効)
      machine.box_bottom 確定した床   (box_ready のときのみ有効)
    """

    def __init__(
        self,
        top_n: int = 3,
        bottom_m: int = 3,
        init_h: float = -math.inf,   # 初期候補天井（エントリー後のトレイリング初期化に使う）
        init_lo: float = math.inf,   # 初期候補床
    ):
        self.top_n = top_n
        self.bottom_m = bottom_m
        self.phase = "search_top"
        self._cand_top: float = init_h
        self._no_new_high: int = 0
        self.box_top: Optional[float] = None
        self._cand_bottom: float = init_lo
        self._no_new_low: int = 0
        self.box_bottom: Optional[float] = None

    def step(self, h: float, lo: float) -> bool:
        """
        1 本のバーを処理する。

        Returns:
            True  → この呼び出しで新しい箱（box_top + box_bottom）が確定した
            False → まだ確定していない（or 既に box_ready）
        """
        if self.phase == "search_top":
            if h > self._cand_top:
                self._cand_top = h
                self._no_new_high = 0
            else:
                self._no_new_high += 1
                if self._no_new_high >= self.top_n:
                    self.box_top = self._cand_top
                    self.phase = "search_bottom"
                    self._cand_bottom = lo
                    self._no_new_low = 0
            return False

        elif self.phase == "search_bottom":
            if h > self.box_top:              # 天井を上抜け → 箱をリセット
                self._cand_top = h
                self._no_new_high = 0
                self.box_top = None
                self.phase = "search_top"
            elif lo < self._cand_bottom:
                self._cand_bottom = lo
                self._no_new_low = 0
            else:
                self._no_new_low += 1
                if self._no_new_low >= self.bottom_m:
                    self.box_bottom = self._cand_bottom
                    self.phase = "box_ready"
                    return True               # 箱確定!
            return False

        # box_ready: step は何もしない（呼び出し側がブレイクアウト検査を行う）
        return False

    def next_box(self, h: float, lo: float) -> None:
        """
        箱確定後、次の箱の探索を開始する（トレイリング用リセット）。

        Why: エントリー前は box_ready のまま待機してブレイクアウトを検出するが、
        ポジション中のトレイリングでは箱確定→床更新→即座にリセットが正しい挙動。
        backtest の `t_phase = "search_top"; t_cand_top = h; ...` に相当する。
        """
        self.phase = "search_top"
        self._cand_top = h
        self._no_new_high = 0
        self.box_top = None
        self._cand_bottom = lo
        self._no_new_low = 0
        self.box_bottom = None


# ---------------------------------------------------------------------------
# 週足フィルタ
# ---------------------------------------------------------------------------

def build_weekly_filter(
    df_daily: pd.DataFrame,
    params: DarvasParams,
) -> dict[pd.Timestamp, bool]:
    """
    日付 → 週足フィルタ通過フラグ の dict を返す。

    当日より前に確定した最新週足バーが is_new_high or is_near_high なら True。
    Why: 当日の週足バーは週中のため未確定。前週以前の確定バーのみ参照する。
    """
    from data.historical.resample import resample_bars
    from data.historical.newhigh import screen_new_high

    df_w = resample_bars(df_daily, "W")
    df_ws = screen_new_high(df_w, window=params.weekly_window, near_high_pct=params.near_high_pct)
    df_ws = df_ws.sort_values("ts_utc").set_index("ts_utc")

    result: dict[pd.Timestamp, bool] = {}
    for _, row in df_daily.iterrows():
        date = row["ts_utc"]
        prev = df_ws[df_ws.index < date]
        if prev.empty:
            result[date] = False
        else:
            last = prev.iloc[-1]
            result[date] = bool(last["is_new_high"] or last["is_near_high"])
    return result


# ---------------------------------------------------------------------------
# エントリーシグナルスキャン（エージェント用）
# ---------------------------------------------------------------------------

def scan_entry_signal(
    df: pd.DataFrame,
    params: DarvasParams,
    weekly_ok: Optional[dict[pd.Timestamp, bool]] = None,
) -> EntrySignal:
    """
    historical OHLCV（df）を処理し、最終バーのエントリーシグナルを返す。

    has_signal=True のとき:
      最終バーの終値が確定箱の天井を初めて上回った（ブレイクアウト日）。
      呼び出し側は翌営業日始値でエントリーする。

    アルゴリズム:
      - 全バーを順に処理し DarvasBoxMachine でボックス形成を追う
      - 箱確定後に終値がブレイクすれば → 最終バーならシグナル、それ以前なら
        リセットして次の箱を探す（過去エントリーの代替として箱をスキップ）
      - 週足フィルタ: weekly_ok が None なら weekly_filter = False 扱い

    Why 過去ブレイクでリセット:
      エージェントが毎日実行されるため、2日以上前のブレイクは「機会を逃した」
      とみなし、現在の最新ボックスの状態を正確に返すほうが実用的。
    """
    daily = df.reset_index(drop=True)
    n = len(daily)
    if n == 0:
        return EntrySignal(has_signal=False, box_top=0.0, box_bottom=0.0, phase="empty")

    machine = DarvasBoxMachine(params.top_n, params.bottom_m)

    for i in range(n):
        row = daily.iloc[i]
        h: float = row["high"]
        lo: float = row["low"]
        c: float = row["close"]
        date: pd.Timestamp = row["ts_utc"]

        if machine.phase == "box_ready":
            if c > machine.box_top:
                # ブレイクアウト発生
                use_weekly = params.weekly_filter and (weekly_ok is not None)
                weekly_pass = (not use_weekly) or weekly_ok.get(date, False)
                if i == n - 1 and weekly_pass:
                    # 最終バーのブレイク → シグナル
                    return EntrySignal(
                        has_signal=True,
                        box_top=machine.box_top,
                        box_bottom=machine.box_bottom,
                        phase="box_ready",
                    )
                # 過去のブレイク → リセットして次の箱を探す
                machine = DarvasBoxMachine(params.top_n, params.bottom_m)
                machine._cand_top = h   # 現在バーの高値で初期化
                machine._cand_bottom = lo
        else:
            machine.step(h, lo)

    return EntrySignal(
        has_signal=False,
        box_top=machine.box_top or 0.0,
        box_bottom=machine.box_bottom or 0.0,
        phase=machine.phase,
    )


# ---------------------------------------------------------------------------
# トレイリング床の再計算（エージェント用）
# ---------------------------------------------------------------------------

def compute_trailing_stop(
    df: pd.DataFrame,
    params: DarvasParams,
    entry_date: pd.Timestamp,
    initial_stop: float,
) -> float:
    """
    entry_date 以降のデータを処理して現在のトレイリング床を返す。

    backtest の Phase C（ポジション中のトレイリング箱形成）と同一アルゴリズム。
    initial_stop を下回る新しい床は採用しない（床は単調増加）。

    エントリー日当日のバーも処理対象に含む（backtest の Phase A→C と同じ順序）。
    """
    daily = df[df["ts_utc"] >= entry_date].reset_index(drop=True)
    if daily.empty:
        return initial_stop

    stop_level = initial_stop
    first = daily.iloc[0]

    # エントリー日当日の high/low でトレイリング機械を初期化
    # (backtest: Phase A の直後に t_cand_top=h, t_cand_bottom=lo でリセットする挙動に対応)
    machine = DarvasBoxMachine(
        params.top_n,
        params.bottom_m,
        init_h=first["high"],
        init_lo=first["low"],
    )

    # 全バーを処理（エントリー日を含む: backtest の Phase C 処理に対応）
    for i in range(len(daily)):
        row = daily.iloc[i]
        h: float = row["high"]
        lo: float = row["low"]

        box_confirmed = machine.step(h, lo)
        if box_confirmed:
            new_floor = machine.box_bottom
            if new_floor > stop_level:   # 床は上昇のみ（単調増加）
                stop_level = new_floor
            machine.next_box(h, lo)      # 即座にリセットして次の箱を探す

    return stop_level
