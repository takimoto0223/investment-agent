"""
scripts/check_jquants_newhigh.py
J-Quants 日足取得＋新高値スクリーニング動作確認（SCREEN 7735 単独）。

確認内容:
  1. J-Quants から 7735 の 3 年分日足を取得
  2. 共通スキーマに正規化 → CSV.gz 保存
  3. 週足・月足にリサンプル → 行数確認
  4. 新高値スクリーニング → 最新状況 + 直近 10 回のブレイク一覧

実行: python -m scripts.check_jquants_newhigh
"""
import sys
from pathlib import Path

import pandas as pd

# Windows コンソール UTF-8 化（CLAUDE.md 規約）
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

# .env の JQUANTS_* を読み込む（config.settings の load_dotenv() 経由）
from config.settings import KABU  # noqa: F401 — import side-effect で dotenv を実行させる

from data.historical.jp_bars import fetch_daily_bars, save_bars
from data.historical.resample import resample_bars
from data.historical.newhigh import screen_new_high

SYMBOL = "7735"  # SCREEN Holdings（SCREENホールディングス）
YEARS = 2        # プランの提供期間に合わせる（Free: ~2年分）
_SEP = "-" * 60


def main() -> int:
    # ── Step 1: 日足取得 ────────────────────────────────────────────────
    print(f"{_SEP}")
    print(f"[Step 1] J-Quants から {SYMBOL} の 3 年分日足を取得")
    try:
        df = fetch_daily_bars(SYMBOL, years=YEARS)
    except EnvironmentError as e:
        print(f"\n[認証エラー]\n{e}")
        return 1
    except Exception as e:
        print(f"\n[取得エラー] {type(e).__name__}: {e}")
        return 1

    if df.empty:
        print(f"  データなし（{SYMBOL} のデータが返りませんでした）")
        return 1

    print(f"  取得: {len(df)} 行  ({df['ts_utc'].min().date()} 〜 {df['ts_utc'].max().date()})")
    print(f"\n  直近 5 行:")
    print(df[["ts_utc", "close", "volume", "adj_factor"]].tail(5).to_string(index=False))

    # ── Step 2: CSV.gz 保存 ─────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 2] CSV.gz 保存")
    path = save_bars(df)
    print(f"  保存先: {path}  ({path.stat().st_size // 1024} KB)")

    # ── Step 3: 週足・月足リサンプル ────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 3] リサンプル")
    df_weekly = resample_bars(df, "W")
    df_monthly = resample_bars(df, "ME")
    print(f"  日足: {len(df)} 行")
    print(f"  週足: {len(df_weekly)} 行")
    print(f"  月足: {len(df_monthly)} 行")
    print(f"\n  月足 直近 6 ヶ月:")
    print(df_monthly[["ts_utc", "open", "high", "low", "close", "volume"]].tail(6).to_string(index=False))

    # ── Step 4: 新高値スクリーニング ────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 4] 新高値スクリーニング（window=252 営業日 ≒ 1 年、高値圏 5% 以内）")
    df_s = screen_new_high(df, window=252, near_high_pct=5.0)

    latest = df_s.iloc[-1]
    print(f"\n  最新日: {latest['ts_utc'].date()}")
    print(f"  終値       : {latest['close']:.1f} 円")
    rmax = latest["rolling_max"]
    if pd.notna(rmax):
        print(f"  52週高値   : {rmax:.1f} 円")
        print(f"  高値比     : {latest['pct_from_high']:+.1f}%")
        print(f"  新高値更新 : {'✓ YES' if latest['is_new_high'] else 'NO'}")
        print(f"  高値圏内   : {'✓ YES (5%以内)' if latest['is_near_high'] else 'NO'}")
    else:
        print(f"  52週高値   : (データ不足)")

    new_highs = df_s[df_s["is_new_high"]]
    print(f"\n  過去 3 年の新高値更新: {len(new_highs)} 回")
    if not new_highs.empty:
        print(f"\n  直近 10 回のブレイク:")
        cols = ["ts_utc", "close", "rolling_max", "pct_from_high"]
        print(new_highs[cols].tail(10).to_string(index=False))

    print(f"\n{_SEP}")
    print("完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
