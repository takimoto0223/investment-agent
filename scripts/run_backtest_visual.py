"""
scripts/run_backtest_visual.py
MomentSwing Darvas バックテスト結果を HTML に可視化するスクリプト。

実行: python -m scripts.run_backtest_visual
出力: backtests/runs/{symbol}_darvas_{YYYYmmdd_HHMM}.html
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from config.settings import KABU  # noqa: F401 — dotenv ロード目的
from backtests.moment_swing.darvas_backtest import BacktestParams, run_backtest
from backtests.moment_swing.html_report import save_html
from data.historical.jp_bars import load_bars

SYMBOL = "7735"   # SCREEN Holdings


def main() -> int:
    params = BacktestParams(
        top_n=3,
        bottom_m=3,
        slippage_pct=0.1,
        commission_pct=0.0,
        weekly_filter=True,
        near_high_pct=5.0,
    )

    print(f"[1/3] バックテスト実行: {SYMBOL} ...")
    try:
        result = run_backtest(SYMBOL, params)
    except Exception as e:
        print(f"[エラー] {type(e).__name__}: {e}")
        return 1

    print(f"      トレード数: {len(result.trades)}")

    print("[2/3] 日足データ読み込み ...")
    try:
        df = load_bars(SYMBOL)
    except FileNotFoundError:
        print(f"[エラー] キャッシュが見つかりません。先に run_backtest_moment_swing を実行してください。")
        return 1

    print(f"      行数: {len(df)}, 期間: {df['ts_utc'].iloc[0].strftime('%Y-%m-%d')} 〜 {df['ts_utc'].iloc[-1].strftime('%Y-%m-%d')}")

    print("[3/3] HTML 生成・保存 ...")
    try:
        path = save_html(result, df)
    except Exception as e:
        print(f"[エラー] {type(e).__name__}: {e}")
        return 1

    print()
    print(f"  保存先: {path}")
    print()
    print("  ※ CDN を使用しています（インターネット接続が必要です）。")
    print("  ※ このファイルは .gitignore 対象です（コミットされません）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
