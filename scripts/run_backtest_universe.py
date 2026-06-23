"""
scripts/run_backtest_universe.py
MomentSwing Darvas ボックスバックテストを 9 銘柄ユニバースで実行する。

ロジックはベースラインのまま変更しない（改良はまだ入れない）。
9 銘柄での傾向確認が目的。

実行: python -m scripts.run_backtest_universe
出力:
  - 各銘柄のトレード履歴・成績（コンソール）
  - 9 銘柄横断サマリー表（コンソール）
  - backlog 改良候補の所見（コンソール）
  - 各銘柄チャート HTML（backtests/runs/）
  - 横断サマリー HTML（backtests/runs/）
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from config.settings import KABU  # noqa: F401 — dotenv ロード目的
from backtests.moment_swing.darvas_backtest import BacktestParams, run_backtest, Trade
from backtests.moment_swing.html_report import save_html
from backtests.moment_swing.universe_report import raw_stats, save_universe_html
from data.historical.jp_bars import fetch_daily_bars, load_bars, save_bars

# ---------------------------------------------------------------------------
# ユニバース定義
# ---------------------------------------------------------------------------
UNIVERSE: dict[str, str] = {
    "8035": "東京エレクトロン",
    "6857": "アドバンテスト",
    "7735": "SCREEN HD",
    "6146": "ディスコ",
    "4063": "信越化学工業",
    "3436": "SUMCO",
    "5803": "フジクラ",
    "5801": "古河電工",
    "5802": "住友電工",
}

PARAMS = BacktestParams(
    top_n=3,
    bottom_m=3,
    slippage_pct=0.1,
    commission_pct=0.0,
    weekly_filter=True,
    near_high_pct=5.0,
)

_SEP  = "=" * 72
_SEP2 = "-" * 72


def _jst(ts) -> str:
    return ts.tz_convert("Asia/Tokyo").strftime("%Y-%m-%d")


def _reason_jp(r: str) -> str:
    return {"stop": "損切", "stop_eod": "損切*", "eod": "末尾"}.get(r, r)


# ---------------------------------------------------------------------------
# 観察判定（backlog 改良候補との照合）
# ---------------------------------------------------------------------------
# 観察1: 床トレイリング更新なし or 少ない（長期据え置き）
_TRAILING_STALE_DAYS = 30   # 保有 N 日以上でトレイリング 1 回以下をフラグ
# 観察2: 箱サイズ過大
_BOX_SIZE_THR_PCT = 30.0    # 天井-床が entry_price の N%超をフラグ
# 観察3: 箱の鮮度（判定は古い箱検知プロキシとして stop_history[0] の初期 stop と box_top 距離を使う）


def _observe_trades(symbol: str, trades: list[Trade]) -> list[str]:
    """backlog 改良候補に対応する観察フラグの文字列リストを返す。"""
    obs: list[str] = []
    for t in trades:
        box_size_pct = (t.box_top - t.box_bottom) / t.box_bottom * 100
        n_updates    = len(t.stop_history) - 1   # 初期ストップを除く更新回数
        initial_stop = t.stop_history[0][1] if t.stop_history else t.box_bottom
        final_stop   = t.stop_history[-1][1] if t.stop_history else t.box_bottom
        stop_drift   = (final_stop - initial_stop) / t.entry_price * 100

        # 改良候補1: トレイリング据え置き
        if t.hold_days >= _TRAILING_STALE_DAYS and n_updates == 0:
            obs.append(
                f"  [観察1・トレイリング] {symbol}: 保有{t.hold_days}日間 "
                f"stop更新 0 回（床={initial_stop:,.0f} 据え置き）"
            )
        elif n_updates >= 1:
            obs.append(
                f"  [観察1・トレイリング] {symbol}: stop更新 {n_updates} 回, "
                f"{initial_stop:,.0f}→{final_stop:,.0f} "
                f"(+{stop_drift:.1f}% of entry)"
            )

        # 改良候補2: 箱サイズ過大
        if box_size_pct > _BOX_SIZE_THR_PCT:
            obs.append(
                f"  [観察2・箱サイズ  ] {symbol}: 箱サイズ {box_size_pct:.0f}% "
                f"({t.box_bottom:,.0f}〜{t.box_top:,.0f}) >{_BOX_SIZE_THR_PCT:.0f}% 閾値"
            )

    return obs


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> int:
    # ── Step 1: データ取得 ──────────────────────────────────────
    print(_SEP)
    print("  Step 1: 日足データ確認・取得")
    print(_SEP)
    dfs: dict[str, object] = {}

    _fetch_count = 0
    for symbol in UNIVERSE:
        try:
            df = load_bars(symbol)
            status = "キャッシュ"
        except FileNotFoundError:
            # J-Quants 429 回避: 連続リクエストに指数バックオフを適用
            wait = 5 * (2 ** _fetch_count)   # 5s, 10s, 20s, 40s ...
            if _fetch_count > 0:
                print(f"  (レート制限回避: {wait}s 待機...)")
                time.sleep(wait)
            print(f"  {symbol}: 取得中...")
            last_exc = None
            for attempt in range(3):
                try:
                    df = fetch_daily_bars(symbol, years=3)
                    last_exc = None
                    break
                except Exception as exc:
                    if "429" in str(exc) or "RetryError" in type(exc).__name__:
                        wait_retry = 30 * (attempt + 1)
                        print(f"  {symbol}: 429 → {wait_retry}s 待機してリトライ ({attempt+1}/3)")
                        time.sleep(wait_retry)
                        last_exc = exc
                    else:
                        raise
            if last_exc is not None:
                print(f"  {symbol}: 取得失敗（429 リトライ上限） → スキップ")
                continue
            _fetch_count += 1
            if df.empty:
                print(f"  {symbol}: データ取得失敗（空） → スキップ")
                continue
            save_bars(df)
            status = "新規取得"

        dfs[symbol] = df
        first = _jst(df["ts_utc"].iloc[0])
        last  = _jst(df["ts_utc"].iloc[-1])
        print(f"  {symbol} {UNIVERSE[symbol]:12}  {status}  "
              f"{len(df):4} 行  {first} 〜 {last}")

    print()

    # ── Step 2: バックテスト実行 ───────────────────────────────
    print(_SEP)
    print("  Step 2: バックテスト実行")
    print(_SEP)
    results: dict = {}
    all_obs: list[str] = []

    for symbol in dfs:
        try:
            result = run_backtest(symbol, PARAMS)
        except Exception as e:
            print(f"  {symbol}: バックテスト失敗 — {e}")
            continue
        results[symbol] = result
        name = UNIVERSE[symbol]

        trades = result.trades
        print()
        print(f"  ── {symbol} {name} ({'トレード: ' + str(len(trades)) + ' 件' if trades else 'トレードなし'}) ──")

        if not trades:
            continue

        # トレード履歴
        print(f"  {'#':>2}  {'エントリー':10} {'買値':>7}  {'エグジット':10} {'売値':>7}  "
              f"{'理由':5} {'株数':>5} {'損益率':>7} {'損益円':>9} {'保有':>4}  "
              f"{'箱天/床':>16}  stop更新")
        print("  " + "-" * 90)
        for i, t in enumerate(trades, 1):
            sh = t.stop_history
            upd = len(sh) - 1
            upd_str = f"{sh[0][1]:,.0f}→{sh[-1][1]:,.0f}" if upd > 0 else f"{sh[0][1]:,.0f}(据置)"
            print(
                f"  {i:>2}  {_jst(t.entry_date):10} {t.entry_price:>7,.1f}  "
                f"{_jst(t.exit_date):10} {t.exit_price:>7,.1f}  "
                f"{_reason_jp(t.exit_reason):5} {t.shares:>5} "
                f"{t.pnl_pct:>+7.2f}% {t.pnl_jpy:>9,.0f} {t.hold_days:>4}日  "
                f"{t.box_top:>7,.0f}/{t.box_bottom:>6,.0f}  {upd_str}"
            )

        # サマリー
        st = raw_stats(trades)
        print(f"  勝率={st['win_rate']:.0f}%  "
              f"平均損益率={st['avg_pnl_pct']:+.2f}%  "
              f"損益合計={st['total_pnl_jpy']:+,.0f}円  "
              f"平均保有={st['avg_hold_days']:.0f}日  "
              f"最大DD={st['max_dd_pct']:.1f}%")

        # 観察フラグ収集
        obs = _observe_trades(symbol, trades)
        all_obs.extend(obs)

        # トレードなし銘柄も観察対象
        # → backlog 改良候補3（箱の鮮度）との関連を后で出力

    no_trade_symbols = [s for s in results if not results[s].trades]

    # ── Step 3: 9 銘柄横断サマリー ────────────────────────────
    print()
    print(_SEP)
    print("  Step 3: 9 銘柄横断サマリー")
    print(_SEP)

    hdr = (f"  {'銘柄':6} {'名称':14} {'T数':>4} {'勝率':>6} "
           f"{'損益合計(円)':>14} {'平均損益%':>9} {'平均保有日':>8} {'最大DD':>7}")
    print(hdr)
    print("  " + "-" * 78)

    all_trades_agg: list[Trade] = []
    for symbol in UNIVERSE:
        name = UNIVERSE[symbol]
        if symbol not in results:
            print(f"  {symbol:6} {name:14} (データなし)")
            continue
        trades = results[symbol].trades
        all_trades_agg.extend(trades)
        if not trades:
            print(f"  {symbol:6} {name:14}    0   —          —          —         —       —")
            continue
        st = raw_stats(trades)
        print(
            f"  {symbol:6} {name:14} {st['n_trades']:>4} "
            f"{st['win_rate']:>5.0f}% "
            f"{st['total_pnl_jpy']:>14,.0f} "
            f"{st['avg_pnl_pct']:>+9.2f}% "
            f"{st['avg_hold_days']:>8.0f} 日 "
            f"{st['max_dd_pct']:>7.1f}%"
        )

    print("  " + "=" * 78)
    ag = raw_stats(all_trades_agg)
    if ag:
        print(
            f"  {'合算':6} {'（全銘柄）':14} {ag['n_trades']:>4} "
            f"{ag['win_rate']:>5.0f}% "
            f"{ag['total_pnl_jpy']:>14,.0f} "
            f"{ag['avg_pnl_pct']:>+9.2f}% "
            f"{ag['avg_hold_days']:>8.0f} 日 "
            f"{ag['max_dd_pct']:>7.1f}%"
        )

    # ── Step 4: backlog 改良候補の所見 ─────────────────────────
    print()
    print(_SEP)
    print("  Step 4: backlog 改良候補との照合（観察）")
    print(_SEP)
    print()
    print(f"  ■ 観察1 — 床トレイリング（保有{_TRAILING_STALE_DAYS}日以上かつ更新0回をフラグ）")
    obs1 = [o for o in all_obs if "観察1" in o]
    if obs1:
        for o in obs1: print(o)
    else:
        print(f"  （トレードなし or 全トレードが保有{_TRAILING_STALE_DAYS}日未満）")

    print()
    print(f"  ■ 観察2 — 箱サイズ（天井-床が>{_BOX_SIZE_THR_PCT:.0f}%をフラグ）")
    obs2 = [o for o in all_obs if "観察2" in o]
    if obs2:
        for o in obs2: print(o)
    else:
        print(f"  （{_BOX_SIZE_THR_PCT:.0f}%超の箱なし）")

    print()
    print("  ■ 観察3 — 古い箱（トレードなし銘柄）")
    if no_trade_symbols:
        for s in no_trade_symbols:
            print(f"  {s} {UNIVERSE[s]}: トレードなし → 箱が未確定 or box_ready のまま "
                  f"終値がブレイクせず（diagnose_darvas_7735.py で詳細確認可）")
    else:
        print("  （全銘柄にトレードあり）")

    print()

    # ── Step 5: HTML 生成 ──────────────────────────────────────
    print(_SEP)
    print("  Step 5: HTML 生成")
    print(_SEP)

    # 銘柄別チャート HTML
    for symbol in results:
        if not results[symbol].trades:
            print(f"  {symbol}: トレードなし → チャート HTML スキップ")
            continue
        if symbol not in dfs:
            continue
        try:
            p = save_html(results[symbol], dfs[symbol])
            print(f"  {symbol}: {p}")
        except Exception as e:
            print(f"  {symbol}: HTML 生成失敗 — {e}")

    # 横断サマリー HTML
    try:
        p_univ = save_universe_html(results, UNIVERSE, PARAMS)
        print(f"  横断サマリー: {p_univ}")
    except Exception as e:
        print(f"  横断サマリー: 生成失敗 — {e}")

    print()
    print(_SEP)
    print("  完了")
    print(_SEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
