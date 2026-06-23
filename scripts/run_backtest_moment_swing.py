"""
scripts/run_backtest_moment_swing.py
MomentSwing Darvas ボックスバックテスト実行スクリプト。

実行: python -m scripts.run_backtest_moment_swing
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from config.settings import KABU  # noqa: F401 — dotenv ロード目的
from backtests.moment_swing.darvas_backtest import (
    BacktestParams,
    run_backtest,
    _summarize,
)

SYMBOL = "7735"   # SCREEN Holdings
_SEP = "=" * 68
_SEP2 = "-" * 68


def _fmt_date(ts) -> str:
    return ts.strftime("%Y-%m-%d") if ts is not None else "-"


def main() -> int:
    params = BacktestParams(
        top_n=3,
        bottom_m=3,
        slippage_pct=0.1,
        commission_pct=0.0,
        weekly_filter=True,
        near_high_pct=5.0,
    )

    print(_SEP)
    print(f"  MomentSwing バックテスト — {SYMBOL} (Darvas Box ベースライン)")
    print(_SEP)
    print(f"  パラメータ: top_n={params.top_n}, bottom_m={params.bottom_m}, "
          f"slip={params.slippage_pct}%, weekly_filter={params.weekly_filter}")
    print()

    try:
        result = run_backtest(SYMBOL, params)
    except Exception as e:
        print(f"[エラー] {type(e).__name__}: {e}")
        return 1

    trades = result.trades

    if not trades:
        print("  トレードなし（データ期間内にシグナル未発生）")
        return 0

    # ----------------------------------------------------------------
    # トレード履歴
    # ----------------------------------------------------------------
    print(f"{'#':>3}  {'エントリー日':12} {'買値':>8}  {'エグジット日':12} {'売値':>8}  "
          f"{'理由':6} {'株数':>5} {'損益率':>7} {'損益(円)':>10} {'保有日':>5}")
    print(_SEP2)

    for i, t in enumerate(trades, 1):
        reason_map = {"stop": "損切", "stop_eod": "損切*", "eod": "末尾"}
        reason = reason_map.get(t.exit_reason, t.exit_reason)
        print(
            f"{i:>3}  {_fmt_date(t.entry_date):12} {t.entry_price:>8.1f}  "
            f"{_fmt_date(t.exit_date):12} {t.exit_price:>8.1f}  "
            f"{reason:6} {t.shares:>5} {t.pnl_pct:>+7.2f}% {t.pnl_jpy:>10,.0f}  {t.hold_days:>4}日"
        )

    print(_SEP2)

    # ----------------------------------------------------------------
    # サマリー
    # ----------------------------------------------------------------
    summary = _summarize(trades)
    print()
    print("  【成績サマリー】")
    for k, v in summary.items():
        print(f"    {k:<16}: {v}")

    # 箱ブレイク参考情報
    print()
    print("  【エントリー箱（参考）】")
    print(f"  {'#':>3}  {'天井':>8} {'床':>8}")
    for i, t in enumerate(trades, 1):
        print(f"  {i:>3}  {t.box_top:>8.1f} {t.box_bottom:>8.1f}")

    print()
    print(_SEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
