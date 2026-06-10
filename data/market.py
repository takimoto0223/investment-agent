"""
data/market.py
kabu STATION API からの市場データ取得・計算ユーティリティ。
kabuステーション未起動時はモックデータで自動代替する（ペーパートレード向け）。

注意：
  kabu STATION REST API は日足までしか対応しておらず、5分足は直接取得できない。
  本番化の際は WebSocket Push API でティックを蓄積→集計する実装に置き換える。
  ペーパートレード中は当日の板情報を元に合成した5分足を使用する。
"""
import logging
import random
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from config.settings import KABU

logger = logging.getLogger(__name__)

# セッション内で接続確認を1回だけ行うためのキャッシュ
_kabu_available: Optional[bool] = None
_cached_token: Optional[str] = None

# 銘柄ごとの基準株価（モック用）
_MOCK_BASE_PRICES: dict[str, float] = {
    "9984": 9200.0,   # ソフトバンクG
    "6857": 8500.0,   # アドバンテスト
    "4063": 6200.0,   # 信越化学
    "2330": 1800.0,   # フィックスターズ
    "7203": 3500.0,   # トヨタ
    "6758": 2800.0,   # ソニーG
}


# ──────────────────────────────────────────────────
# 接続確認
# ──────────────────────────────────────────────────

def check_kabu_connection() -> bool:
    """
    kabuステーションへの接続を確認する。
    セッション内で1回だけ実施し、結果をキャッシュする。
    """
    global _kabu_available
    if _kabu_available is not None:
        return _kabu_available
    try:
        resp = requests.get(
            f"{KABU.base_url}/board/9984@1",
            headers={"X-API-KEY": "ping"},
            timeout=KABU.timeout_sec,
        )
        # 200/400/401/403 ならサーバーは起動している
        _kabu_available = resp.status_code in (200, 400, 401, 403)
    except (requests.ConnectionError, requests.Timeout):
        _kabu_available = False
        logger.info("kabuステーション未接続 → モックデータで代替します")
    return _kabu_available


def reset_connection_cache() -> None:
    """接続確認キャッシュをリセットする（テスト・再起動時に使用）。"""
    global _kabu_available, _cached_token
    _kabu_available = None
    _cached_token = None


# ──────────────────────────────────────────────────
# 板情報
# ──────────────────────────────────────────────────

def get_board(symbol: str, exchange: int = 1) -> dict:
    """
    リアルタイム板情報を取得する。
    kabuステーション未起動時はモック板情報を返す。

    返り値のキー（実API・モック共通）:
      CurrentPrice, CalcPrice, PreviousClose, OpeningPrice,
      HighPrice, LowPrice, TradingVolume, VWAP, BidPrice, AskPrice
    """
    if not check_kabu_connection():
        return _mock_board(symbol)
    try:
        token = _get_token()
        resp = requests.get(
            f"{KABU.base_url}/board/{symbol}@{exchange}",
            headers={"X-API-KEY": token},
            timeout=KABU.timeout_sec,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"板情報取得失敗 {symbol}: {e} → モックで代替")
        return _mock_board(symbol)


# ──────────────────────────────────────────────────
# 5分足データ
# ──────────────────────────────────────────────────

def get_bars_5min(symbol: str, exchange: int = 1) -> list[dict]:
    """
    当日の5分足データを返す。

    kabu STATION REST API は5分足に非対応のため、
    接続中でも板情報から合成したデータを返す。
    本番化の際は WebSocket Push API の実装に置き換える。

    返り値のキー: time, open, high, low, close, volume, vwap_ref
    """
    board = get_board(symbol, exchange)
    return _synthesize_bars_5min(symbol, board)


# ──────────────────────────────────────────────────
# 日足データ（ATR・出来高比率の計算用）
# ──────────────────────────────────────────────────

def get_daily_bars(symbol: str, exchange: int = 1, limit: int = 20) -> list[dict]:
    """
    日足データを取得する（ATR・出来高比率の計算に使用）。
    kabuステーション未起動時はモック日足データを返す。

    返り値のキー: date, open, high, low, close, volume
    """
    if not check_kabu_connection():
        return _mock_daily_bars(symbol, limit)
    try:
        token = _get_token()
        resp = requests.get(
            f"{KABU.base_url}/chartdata/{symbol}@{exchange}",
            headers={"X-API-KEY": token},
            params={"registered": 1},  # 1=日足
            timeout=KABU.timeout_sec,
        )
        resp.raise_for_status()
        raw = resp.json()
        bars = [
            {
                "date": b.get("Date", ""),
                "open":   float(b.get("Open",   0)),
                "high":   float(b.get("High",   0)),
                "low":    float(b.get("Low",    0)),
                "close":  float(b.get("Close",  0)),
                "volume": int(b.get("Volume",   0)),
            }
            for b in raw
            if b.get("Close", 0) > 0
        ]
        return bars[-limit:]
    except Exception as e:
        logger.warning(f"日足データ取得失敗 {symbol}: {e} → モックで代替")
        return _mock_daily_bars(symbol, limit)


# ──────────────────────────────────────────────────
# 計算ユーティリティ
# ──────────────────────────────────────────────────

def calc_atr(daily_bars: list[dict], period: int = 14) -> float:
    """
    ATR（Average True Range）を計算して返す。
    True Range = max(高値-安値, |高値-前日終値|, |安値-前日終値|)
    """
    if len(daily_bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(daily_bars)):
        h = daily_bars[i]["high"]
        l = daily_bars[i]["low"]
        prev_c = daily_bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    recent = trs[-period:] if len(trs) >= period else trs
    return sum(recent) / len(recent) if recent else 0.0


def calc_atr_pct(daily_bars: list[dict], period: int = 14) -> float:
    """ATR を終値に対するパーセントで返す（DaytradeAgent の atr_pct に対応）。"""
    if not daily_bars:
        return 0.0
    atr = calc_atr(daily_bars, period)
    price = daily_bars[-1]["close"]
    return round(atr / price * 100, 2) if price > 0 else 0.0


def calc_volume_ratio(daily_bars: list[dict], today_volume: int, window: int = 20) -> float:
    """
    当日出来高 / 過去 N 日平均出来高 を返す（DaytradeAgent の volume_ratio に対応）。
    today_volume: board データの TradingVolume（当日累積出来高）
    """
    if not daily_bars or today_volume == 0:
        return 1.0
    avg_vol = sum(b["volume"] for b in daily_bars[-window:]) / min(len(daily_bars), window)
    return round(today_volume / avg_vol, 2) if avg_vol > 0 else 1.0


def build_universe(base_list: list[dict], exchange: int = 1) -> list[dict]:
    """
    銘柄リストに volume_ratio・atr_pct・current_price・vwap を付加して返す。
    DaytradeAgent.screen_candidates() に渡すユニバースを生成する。

    base_list: [{"symbol": "9984", "name": "ソフトバンクG"}, ...]
    """
    result = []
    for item in base_list:
        sym = item["symbol"]
        try:
            board = get_board(sym, exchange)
            daily = get_daily_bars(sym, exchange)
            today_vol = board.get("TradingVolume", 0)

            entry = {
                **item,
                "volume_ratio":  calc_volume_ratio(daily, today_vol),
                "atr_pct":       calc_atr_pct(daily),
                "current_price": board.get("CurrentPrice", 0),
                "vwap":          board.get("VWAP", 0),
            }
            result.append(entry)
            logger.debug(
                f"ユニバース構築: {sym} "
                f"volume_ratio={entry['volume_ratio']} "
                f"atr_pct={entry['atr_pct']}%"
            )
        except Exception as e:
            logger.warning(f"ユニバース構築失敗 {sym}: {e}")
            result.append({**item, "volume_ratio": 0.0, "atr_pct": 0.0})
    return result


# ──────────────────────────────────────────────────
# 内部ユーティリティ
# ──────────────────────────────────────────────────

def _get_token() -> str:
    """kabuステーションのAPIトークンを取得する（セッション内キャッシュあり）。"""
    global _cached_token
    if _cached_token:
        return _cached_token
    resp = requests.post(
        f"{KABU.base_url}/token",
        json={"APIPassword": KABU.password},
        timeout=KABU.timeout_sec,
    )
    resp.raise_for_status()
    _cached_token = resp.json()["Token"]
    logger.info("kabuステーション: APIトークン取得成功")
    return _cached_token


def _seed(symbol: str) -> int:
    """銘柄コードから一定の乱数シードを生成する（モック再現性のため）。"""
    return sum(ord(c) * (i + 1) for i, c in enumerate(symbol))


def _base_price(symbol: str) -> float:
    return _MOCK_BASE_PRICES.get(symbol, 2000.0)


# ──────────────────────────────────────────────────
# モックデータ生成
# ──────────────────────────────────────────────────

def _mock_board(symbol: str) -> dict:
    """
    モック板情報を生成する。
    同じ銘柄コードに対して同じ値を返すよう乱数シードを固定する。
    """
    base = _base_price(symbol)
    rng = random.Random(_seed(symbol))

    prev_close  = round(base * rng.uniform(0.990, 1.010), 0)
    open_price  = round(prev_close * rng.uniform(0.997, 1.003), 0)
    current     = round(open_price * rng.uniform(0.990, 1.010), 0)
    high        = round(max(open_price, current) * rng.uniform(1.002, 1.015), 0)
    low         = round(min(open_price, current) * rng.uniform(0.985, 0.998), 0)
    vwap        = round((open_price + high + low + current) / 4, 1)
    avg_vol     = 500_000 + _seed(symbol) % 2_000_000
    today_vol   = int(avg_vol * rng.uniform(1.3, 2.5))

    logger.debug(f"モック板情報: {symbol} current={current} vol={today_vol:,}")
    return {
        "Symbol":         symbol,
        "CurrentPrice":   current,
        "CalcPrice":      current,
        "PreviousClose":  prev_close,
        "OpeningPrice":   open_price,
        "HighPrice":      high,
        "LowPrice":       low,
        "TradingVolume":  today_vol,
        "VWAP":           vwap,
        "BidPrice":       current - 1,
        "AskPrice":       current + 1,
        "_mock":          True,
    }


def _mock_daily_bars(symbol: str, limit: int = 20) -> list[dict]:
    """
    モック日足データを生成する（ATR・出来高比率の計算に使用）。
    同じ銘柄コードに対して再現性のあるデータを返す。
    """
    base = _base_price(symbol)
    rng = random.Random(_seed(symbol))
    bars: list[dict] = []
    price = base
    today = date.today()
    day = today - timedelta(days=limit + 15)  # 土日分を考慮して余裕を持たせる

    while len(bars) < limit:
        day += timedelta(days=1)
        if day.weekday() >= 5:  # 土日スキップ
            continue
        change = rng.uniform(-0.025, 0.025)
        open_p  = round(price  * rng.uniform(0.995, 1.005), 0)
        close_p = round(price  * (1 + change), 0)
        high_p  = round(max(open_p, close_p) * rng.uniform(1.002, 1.015), 0)
        low_p   = round(min(open_p, close_p) * rng.uniform(0.985, 0.998), 0)
        vol     = int((500_000 + _seed(symbol) % 2_000_000) * rng.uniform(0.7, 1.5))
        bars.append({
            "date":   day.isoformat(),
            "open":   open_p,
            "high":   high_p,
            "low":    low_p,
            "close":  close_p,
            "volume": vol,
        })
        price = close_p

    return bars


def _synthesize_bars_5min(symbol: str, board: dict) -> list[dict]:
    """
    板情報を元に当日の5分足を合成する。
    9:00〜現在時刻分の本数を生成し、終値が CurrentPrice に収束するよう設計する。
    昼休み（11:30〜12:30）はスキップする。

    返り値のキー: time, open, high, low, close, volume, vwap_ref
    """
    current   = float(board.get("CurrentPrice",  _base_price(symbol)))
    open_p    = float(board.get("OpeningPrice",  current * 0.998))
    vwap      = float(board.get("VWAP",          current))
    total_vol = int(board.get("TradingVolume",   500_000))

    now = datetime.now()
    session_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_min = max((now - session_start).seconds // 60, 0)
    n_bars = min(elapsed_min // 5, 78)  # 最大78本（9:00〜15:25）

    # 取引時間外（9時前・15時半以降）は最低1本は返す
    if n_bars == 0:
        n_bars = 1

    rng = random.Random(_seed(symbol))
    bars: list[dict] = []
    price = float(open_p)
    drift_per_bar = (current - price) / n_bars
    vol_per_bar   = total_vol // n_bars if n_bars > 0 else 1

    for i in range(n_bars):
        t = session_start + timedelta(minutes=i * 5)
        # 昼休みをスキップ
        if (t.hour == 11 and t.minute >= 30) or (t.hour == 12 and t.minute < 30):
            continue

        noise = rng.uniform(-0.003, 0.003)
        target = price + drift_per_bar
        o = round(price, 0)
        c = round(target * (1 + noise), 0)
        h = round(max(o, c) * rng.uniform(1.001, 1.008), 0)
        l = round(min(o, c) * rng.uniform(0.992, 0.999), 0)
        vol = int(vol_per_bar * rng.uniform(0.5, 1.8))

        bars.append({
            "time":     t.strftime("%Y-%m-%dT%H:%M:00"),
            "open":     o,
            "high":     h,
            "low":      l,
            "close":    c,
            "volume":   vol,
            "vwap_ref": vwap,
        })
        price = c

    logger.debug(f"5分足合成: {symbol} {len(bars)}本 現在値={current}")
    return bars
