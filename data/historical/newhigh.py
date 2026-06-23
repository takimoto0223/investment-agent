"""
data/historical/newhigh.py
新高値スクリーニング: rolling max で過去 N 日の最高値と比較し、
ブレイク(新高値更新)・圏内(高値圏)を判定する。
"""
import pandas as pd


def screen_new_high(
    df: pd.DataFrame,
    window: int = 252,
    near_high_pct: float = 5.0,
) -> pd.DataFrame:
    """新高値スクリーニングを実施し、判定列を追加して返す。

    Args:
        df: 共通スキーマ日足 DataFrame (ts_utc でソート済み推奨)
        window: rolling 期間（営業日数）。252 ≒ 1 年、126 ≒ 半年。
        near_high_pct: 「高値圏」と判定する最高値からの乖離率(%)上限。
                       例: 5.0 → 最高値の 95% 以上なら圏内。

    Returns:
        元の DataFrame に以下の列を追加したもの:
          rolling_max     : 前日までの rolling 最高値
          is_new_high     : 終値 > rolling_max (新高値更新)
          pct_from_high   : (終値 - rolling_max) / rolling_max * 100
          is_near_high    : pct_from_high >= -near_high_pct (高値圏)
    """
    df = df.sort_values("ts_utc").copy()

    # shift(1) で当日終値は含めない（前日までの最高値と比較する）
    df["rolling_max"] = df["close"].shift(1).rolling(window, min_periods=20).max()

    df["is_new_high"] = df["close"] > df["rolling_max"]
    df["pct_from_high"] = (df["close"] - df["rolling_max"]) / df["rolling_max"] * 100
    df["is_near_high"] = df["pct_from_high"] >= -near_high_pct

    return df
