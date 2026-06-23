"""
data/historical/jp_bars.py
J-Quants API v2 から日本株の日足を取得し、共通スキーマ(共通 OHLCV+メタ)に変換する。

認証: .env の JQUANTS_API_KEY のみ使用（config.settings.JQUANTS 経由）。
Why: J-Quants V1 認証エンドポイント(auth_user / auth_refresh)は廃止され 410 を返す。
     V1 の JQUANTS_REFRESH_TOKEN / JQUANTS_MAIL_ADDRESS / JQUANTS_PASSWORD は使わない。

保存フォーマット: CSV.gz (pyarrow が ARM64 Windows でビルド不可のため)
共通スキーマ: ts_utc, symbol, market, open/high/low/close/volume (調整後), raw_*, adj_factor
"""
import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import JQUANTS

_BARS_DIR = Path(__file__).parent / "bars"


def _make_client():
    """jquantsapi.ClientV2 を JQUANTS_API_KEY から生成する。

    Why: J-Quants V1 は廃止されており、ClientV2 + api_key が唯一の有効な認証方式。
    """
    import jquantsapi  # 遅延 import: インストールなし環境でのモジュールレベル破損を防ぐ

    JQUANTS.validate()  # 未設定なら EnvironmentError を送出
    return jquantsapi.ClientV2(api_key=JQUANTS.api_key)


def _normalize(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """J-Quants v2 の日足 DataFrame を共通スキーマに変換する。

    V2 カラム名: O/H/L/C/Vo (未調整), AdjO/AdjH/AdjL/AdjC/AdjVo (調整後), AdjFactor
    調整後株価をメイン OHLCV に使う。
    Why: 株式分割・合併による価格段差が新高値誤検知の原因になるため。
    """
    df = raw.copy()

    # タイムスタンプ: 日本時間の日付を UTC に変換（終値は大引け=15:30 JST）
    df["ts_utc"] = (
        pd.to_datetime(df["Date"])
        .dt.tz_localize("Asia/Tokyo")
        .dt.tz_convert("UTC")
    )
    df["symbol"] = symbol
    df["market"] = "JP"

    # 調整後株価をメイン OHLCV に据える（V2 フィールド名）
    df["open"] = df["AdjO"]
    df["high"] = df["AdjH"]
    df["low"] = df["AdjL"]
    df["close"] = df["AdjC"]
    df["volume"] = df["AdjVo"]

    # 未調整原値も保持
    df["raw_open"] = df["O"]
    df["raw_high"] = df["H"]
    df["raw_low"] = df["L"]
    df["raw_close"] = df["C"]
    df["raw_volume"] = df["Vo"]
    df["adj_factor"] = df.get("AdjFactor", pd.Series(1.0, index=df.index))

    cols = [
        "ts_utc", "symbol", "market",
        "open", "high", "low", "close", "volume",
        "raw_open", "raw_high", "raw_low", "raw_close", "raw_volume",
        "adj_factor",
    ]
    return df[cols].sort_values("ts_utc").reset_index(drop=True)


def _clamp_to_subscription(
    from_dt: datetime.date, to_dt: datetime.date, error_body: str
) -> tuple[datetime.date, datetime.date]:
    """400 エラーの本文からサブスクリプション期間を解析し、日付範囲を丸め込む。

    例: "covers the following dates: 2024-03-31 ~ 2026-03-31"
    """
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", error_body)
    if not m:
        raise  # パターンが見つからなければ元の例外を再送出
    sub_start = datetime.date.fromisoformat(m.group(1))
    sub_end = datetime.date.fromisoformat(m.group(2))
    new_from = max(from_dt, sub_start)
    new_to = min(to_dt, sub_end)
    print(
        f"  ⚠ サブスクリプション期間 {sub_start}〜{sub_end} に丸め込みます"
        f"  (リクエスト: {from_dt}〜{to_dt} → {new_from}〜{new_to})"
    )
    return new_from, new_to


def fetch_daily_bars(symbol: str, years: int = 3) -> pd.DataFrame:
    """指定銘柄の日足を J-Quants v2 から取得して共通スキーマで返す。

    サブスクリプション期間外の日付を要求すると 400 が返る。
    その場合はエラー本文から利用可能期間を解析してリトライする。

    Args:
        symbol: 銘柄コード（例: "7735"）
        years: 取得年数（デフォルト 3 年）

    Returns:
        共通スキーマの日足 DataFrame。空の場合は空 DataFrame。
    """
    end = datetime.date.today()
    start = end - datetime.timedelta(days=365 * years)

    client = _make_client()

    def _call(from_dt: datetime.date, to_dt: datetime.date) -> pd.DataFrame:
        return client.get_eq_bars_daily(
            code=symbol,
            from_yyyymmdd=from_dt.strftime("%Y%m%d"),
            to_yyyymmdd=to_dt.strftime("%Y%m%d"),
        )

    try:
        raw = _call(start, end)
    except Exception as e:
        body = str(e)
        if "400" in body and "subscription" in body.lower():
            clamped_start, clamped_end = _clamp_to_subscription(start, end, body)
            raw = _call(clamped_start, clamped_end)
        else:
            raise

    if raw.empty:
        return pd.DataFrame()

    return _normalize(raw, symbol)


def save_bars(df: pd.DataFrame, out_dir: Optional[Path] = None) -> Path:
    """日足 DataFrame を CSV.gz で保存する。ファイルパスを返す。"""
    if df.empty:
        raise ValueError("空の DataFrame は保存できません。")
    save_dir = out_dir or _BARS_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    symbol = df["symbol"].iloc[0]
    path = save_dir / f"{symbol}_jp_daily.csv.gz"
    df.to_csv(path, index=False, compression="gzip")
    return path


def load_bars(symbol: str, out_dir: Optional[Path] = None) -> pd.DataFrame:
    """save_bars で保存した CSV.gz を読み込む。"""
    load_dir = out_dir or _BARS_DIR
    path = load_dir / f"{symbol}_jp_daily.csv.gz"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} が見つかりません。fetch_daily_bars → save_bars を先に実行してください。"
        )
    df = pd.read_csv(path, compression="gzip")
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df
