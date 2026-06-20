"""
data/macro_rates.py
VIX と 米10年利回りを FRED API から取得し、キャッシュ付きで返す。

USD/JPY を FRED(DEXJPUS) でなく Frankfurter(data/fx_rate.py) から取得している理由:
  FRED DEXJPUS は実測で約 7 営業日遅延(例: 6/19 時点で最新値が 6/12)。
  為替レートは allocate_budgets の円換算に使うため当日値が必要。
  Frankfurter は ECB 公表の当日値を返す。
  VIX・米10Y は「18 か 25 か」という方向感が重要で T+1 で十分なので FRED で一本化。
  将来「FRED 一本化できないか?」と考えたときはこの遅延を実測で確認してから判断すること。

フォールバック優先順(VIX/10Y 共通):
  1. FRED API (タイムアウト 6 秒)
  2. 24h 以内のキャッシュ JSON (logs/macro_rates_cache.json)
  3. 暫定値 (ログに明示)
"""
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_FRED_BASE    = "https://api.stlouisfed.org/fred/series/observations"
_CACHE_PATH   = Path("logs/macro_rates_cache.json")
_API_TIMEOUT  = 6       # 秒
_CACHE_TTL_H  = 24      # 時間

_FALLBACK_VIX   = 18.5
_FALLBACK_US10Y = 4.35

_SERIES_VIX   = "VIXCLS"
_SERIES_US10Y = "DGS10"


# ── 共通 FRED フェッチ ────────────────────────────────────────────

def _fred_api_key() -> str:
    return os.getenv("FRED_API_KEY", "")


def _fetch_fred(series_id: str) -> tuple[float, str] | None:
    """
    FRED から最新の有効値を取得する。
    欠損値 "." は週末・祝日に返るためスキップし、直近の有効観測値を使う。
    API キー未設定 / ネットワーク失敗 / キー無効 はすべて None を返す。
    """
    key = _fred_api_key()
    if not key:
        logger.warning("FRED_API_KEY 未設定 → %s 取得スキップ", series_id)
        return None
    url = (
        f"{_FRED_BASE}?series_id={series_id}"
        f"&api_key={key}"
        f"&sort_order=desc&limit=5&file_type=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        for obs in data.get("observations", []):
            if obs.get("value", ".") != ".":
                return float(obs["value"]), obs["date"]
        logger.warning("FRED %s: 有効な観測値が取得できなかった", series_id)
        return None
    except Exception as exc:
        logger.warning("FRED %s 取得失敗: %s", series_id, exc)
        return None


# ── キャッシュ読み書き ────────────────────────────────────────────

def _read_cache(key: str) -> tuple[float, str] | None:
    """指定キーのキャッシュを読む。期限切れ・不正なら None。"""
    try:
        if not _CACHE_PATH.exists():
            return None
        cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        entry = cache.get(key)
        if not entry:
            return None
        cached_at = datetime.fromisoformat(entry["cached_at"])
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - cached_at > timedelta(hours=_CACHE_TTL_H):
            logger.info("macro_rates キャッシュ期限切れ: %s", key)
            return None
        return float(entry["value"]), entry["date"]
    except Exception as exc:
        logger.warning("macro_rates キャッシュ読み込み失敗 (%s): %s", key, exc)
        return None


def _write_cache(key: str, value: float, date_str: str) -> None:
    """指定キーをキャッシュに書き込む。他キーは保持する。"""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache: dict = {}
        if _CACHE_PATH.exists():
            try:
                cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        cache[key] = {
            "value":     value,
            "date":      date_str,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        _CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("macro_rates キャッシュ書き込み失敗 (%s): %s", key, exc)


def _fmt_date(date_str: str) -> str:
    """'2026-06-17' → '6/17'"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.month}/{dt.day}"
    except Exception:
        return date_str


# ── 公開関数 ─────────────────────────────────────────────────────

def get_vix() -> tuple[float, str, str]:
    """
    VIX 終値を返す。

    Returns:
        (value, fetched_at, source)
        - value      : float — VIX 値
        - fetched_at : str   — "6/17" 形式の日付
        - source     : str   — "api" | "cache" | "fallback"
    """
    result = _fetch_fred(_SERIES_VIX)
    if result is not None:
        value, date_str = result
        _write_cache("vix", value, date_str)
        fetched_at = _fmt_date(date_str)
        logger.info("VIX API取得成功: %.2f (%s時点)", value, fetched_at)
        return value, fetched_at, "api"

    cached = _read_cache("vix")
    if cached is not None:
        value, date_str = cached
        fetched_at = _fmt_date(date_str)
        logger.info("VIX キャッシュ使用: %.2f (%s時点)", value, fetched_at)
        return value, fetched_at, "cache"

    today = _fmt_date(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    logger.warning("VIX 取得失敗・暫定値 %.1f 使用", _FALLBACK_VIX)
    return _FALLBACK_VIX, today, "fallback"


def get_us_10y() -> tuple[float, str, str]:
    """
    米国債10年利回りを返す。

    Returns:
        (value, fetched_at, source)
        - value      : float — 利回り(%) 例: 4.49
        - fetched_at : str   — "6/17" 形式の日付
        - source     : str   — "api" | "cache" | "fallback"
    """
    result = _fetch_fred(_SERIES_US10Y)
    if result is not None:
        value, date_str = result
        _write_cache("us_10y", value, date_str)
        fetched_at = _fmt_date(date_str)
        logger.info("米10Y API取得成功: %.2f%% (%s時点)", value, fetched_at)
        return value, fetched_at, "api"

    cached = _read_cache("us_10y")
    if cached is not None:
        value, date_str = cached
        fetched_at = _fmt_date(date_str)
        logger.info("米10Y キャッシュ使用: %.2f%% (%s時点)", value, fetched_at)
        return value, fetched_at, "cache"

    today = _fmt_date(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    logger.warning("米10Y 取得失敗・暫定値 %.2f%% 使用", _FALLBACK_US10Y)
    return _FALLBACK_US10Y, today, "fallback"
