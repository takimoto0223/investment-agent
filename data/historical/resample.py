"""
data/historical/resample.py
共通スキーマ日足 DataFrame を週足・月足にリサンプルする。
"""
import pandas as pd


_FREQS = {
    "weekly": "W",
    "monthly": "ME",
    "W": "W",
    "ME": "ME",
}


def resample_bars(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """日足を週足(W)・月足(ME)にリサンプルする。

    Args:
        df: 共通スキーマ日足 DataFrame (ts_utc が UTC aware datetime)
        freq: "W" | "weekly" | "ME" | "monthly"

    Returns:
        同じ列構造のリサンプル済み DataFrame。ts_utc は各期間の末日 UTC。
    """
    pd_freq = _FREQS.get(freq)
    if pd_freq is None:
        raise ValueError(f"freq は {list(_FREQS)} のいずれかを指定してください。")

    if df.empty:
        return df.copy()

    symbol = df["symbol"].iloc[0]
    market = df["market"].iloc[0]

    indexed = df.set_index("ts_utc").sort_index()

    agg = (
        indexed[["open", "high", "low", "close", "volume"]]
        .resample(pd_freq)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
    )
    agg["symbol"] = symbol
    agg["market"] = market
    return agg.reset_index()[["ts_utc", "symbol", "market", "open", "high", "low", "close", "volume"]]
