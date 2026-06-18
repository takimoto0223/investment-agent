"""
data/us_market.py
Alpaca API から米国株の市場データを取得するユーティリティ。
日足・5分足・最新気配値を提供し、ユニバース構築を担う。
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from config.settings import ALPACA
from data.market import calc_atr_pct, calc_volume_ratio

logger = logging.getLogger(__name__)

_data_client: StockHistoricalDataClient | None = None

# インプロセス TTL キャッシュ（5分）— セッション内の重複API呼び出しを防ぐ
_CACHE_TTL = 300  # seconds
_cache: dict[str, tuple[float, object]] = {}  # key → (timestamp, data)


def _cache_get(key: str) -> object | None:
    entry = _cache.get(key)
    if entry and time.monotonic() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, data: object) -> None:
    _cache[key] = (time.monotonic(), data)


def _get_client() -> StockHistoricalDataClient:
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(
            api_key=ALPACA.api_key,
            secret_key=ALPACA.secret_key,
        )
    return _data_client


# ──────────────────────────────────────────────────
# 日足データ（ATR・出来高比率の計算用）
# ──────────────────────────────────────────────────

def get_daily_bars_us(symbol: str, limit: int = 22) -> list[dict]:
    """
    米国株の日足データを取得する。
    返り値のキー: date, open, high, low, close, volume
    """
    key = f"daily:{symbol}:{limit}"
    cached = _cache_get(key)
    if cached is not None:
        logger.debug(f"日足キャッシュヒット: {symbol}")
        return cached  # type: ignore[return-value]

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=limit * 2),
        limit=limit,
    )
    try:
        bars = _get_client().get_stock_bars(req)
        result = [
            {
                "date":   b.timestamp.strftime("%Y-%m-%d"),
                "open":   float(b.open),
                "high":   float(b.high),
                "low":    float(b.low),
                "close":  float(b.close),
                "volume": int(b.volume),
            }
            for b in bars[symbol]
        ][-limit:]
        _cache_set(key, result)
        return result
    except Exception as e:
        logger.warning(f"米国株日足取得失敗 {symbol}: {e}")
        return []


# ──────────────────────────────────────────────────
# 5分足データ
# ──────────────────────────────────────────────────

def get_bars_5min_us(symbol: str, hours: int = 2) -> list[dict]:
    """
    米国株の5分足データを取得する（直近 hours 時間分）。
    返り値のキー: time, open, high, low, close, volume, vwap_ref
    """
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=datetime.now(timezone.utc) - timedelta(hours=hours),
    )
    try:
        bars = _get_client().get_stock_bars(req)
        result = []
        for b in bars[symbol]:
            # VWAP は (O+H+L+C)/4 の簡易計算
            vwap_ref = (b.open + b.high + b.low + b.close) / 4
            result.append({
                "time":     b.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00"),
                "open":     float(b.open),
                "high":     float(b.high),
                "low":      float(b.low),
                "close":    float(b.close),
                "volume":   int(b.volume),
                "vwap_ref": round(vwap_ref, 4),
            })
        logger.debug(f"米国株5分足取得: {symbol} {len(result)}本")
        return result
    except Exception as e:
        logger.warning(f"米国株5分足取得失敗 {symbol}: {e}")
        return []


# ──────────────────────────────────────────────────
# 最新気配値（板情報代替）
# ──────────────────────────────────────────────────

def get_quote_us(symbol: str) -> dict:
    """
    米国株の最新気配値を取得する。
    kabu STATION の get_board() に相当するインターフェースで返す。
    """
    key = f"quote:{symbol}"
    cached = _cache_get(key)
    if cached is not None:
        logger.debug(f"気配値キャッシュヒット: {symbol}")
        return cached  # type: ignore[return-value]

    req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
    try:
        quotes = _get_client().get_stock_latest_quote(req)
        q = quotes[symbol]
        mid = (float(q.ask_price) + float(q.bid_price)) / 2
        result = {
            "Symbol":        symbol,
            "CurrentPrice":  round(mid, 4),
            "CalcPrice":     round(mid, 4),
            "AskPrice":      float(q.ask_price),
            "BidPrice":      float(q.bid_price),
        }
        _cache_set(key, result)
        return result
    except Exception as e:
        logger.warning(f"米国株気配値取得失敗 {symbol}: {e}")
        return {"Symbol": symbol, "CurrentPrice": 0, "CalcPrice": 0}


# ──────────────────────────────────────────────────
# ユニバース構築
# ──────────────────────────────────────────────────

def build_us_universe(base_list: list[dict]) -> list[dict]:
    """
    米国株ユニバースに volume_ratio・atr_pct・current_price を付加して返す。
    DaytradeAgent.screen_candidates() に渡す形式に合わせる。

    base_list: [{"symbol": "NVDA", "name": "エヌビディア"}, ...]
    """
    result = []
    for item in base_list:
        sym = item["symbol"]
        try:
            daily = get_daily_bars_us(sym)
            quote = get_quote_us(sym)
            today_vol = daily[-1]["volume"] if daily else 0

            current = quote.get("CurrentPrice", 0)
            prev_close = daily[-1]["close"] if len(daily) >= 1 else current
            price_change_pct = round((current - prev_close) / prev_close, 4) if prev_close > 0 else 0.0

            entry = {
                **item,
                "volume_ratio":     calc_volume_ratio(daily, today_vol),
                "atr_pct":          calc_atr_pct(daily),
                "current_price":    current,
                "price_change_pct": price_change_pct,
            }
            result.append(entry)
            logger.info(
                f"米国株ユニバース: {sym} "
                f"${entry['current_price']:.2f} "
                f"volume_ratio={entry['volume_ratio']} "
                f"atr_pct={entry['atr_pct']}% "
                f"price_change={price_change_pct:+.2%}"
            )
        except Exception as e:
            logger.warning(f"米国株ユニバース構築失敗 {sym}: {e}")
            result.append({**item, "volume_ratio": 0.0, "atr_pct": 0.0, "current_price": 0.0, "price_change_pct": 0.0})
    return result
