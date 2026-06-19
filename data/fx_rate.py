"""
data/fx_rate.py
USD/JPY レートを Frankfurter API から取得し、キャッシュ付きで返す。

フォールバック優先順:
  1. Frankfurter API (タイムアウト 4 秒)
  2. 24h 以内のキャッシュ JSON (logs/fx_rate_cache.json)
  3. 暫定値 155.0（ログに明示）
"""
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_API_URL       = "https://api.frankfurter.app/latest?from=USD&to=JPY"
_CACHE_PATH    = Path("logs/fx_rate_cache.json")
_FALLBACK_RATE = 155.0
_API_TIMEOUT   = 4       # 秒
_CACHE_TTL_H   = 24      # 時間


def get_usd_jpy() -> tuple[float, str, str]:
    """
    USD/JPY レートを返す。

    Returns:
        (rate, fetched_at, source)
        - rate       : float — レート値
        - fetched_at : str   — "6/19" 形式の日付（Frankfurter は日次更新）
        - source     : str   — "api" | "cache" | "fallback"
    """
    # 1. Frankfurter API (4 秒タイムアウト)
    try:
        with urllib.request.urlopen(_API_URL, timeout=_API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        rate     = float(data["rates"]["JPY"])
        date_str = data.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        fetched_at = _fmt_date(date_str)
        _write_cache(rate, date_str)
        logger.info("USD/JPY API取得成功: %.2f (%s時点)", rate, fetched_at)
        return rate, fetched_at, "api"
    except Exception as exc:
        logger.warning("USD/JPY API取得失敗: %s", exc)

    # 2. キャッシュ (24h 以内)
    cached = _read_cache()
    if cached is not None:
        rate, date_str = cached
        fetched_at = _fmt_date(date_str)
        logger.info("USD/JPY キャッシュ使用: %.2f (%s時点)", rate, fetched_at)
        return rate, fetched_at, "cache"

    # 3. 最終フォールバック
    today = _fmt_date(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    logger.warning("USD/JPY レート取得失敗・暫定値%.1f使用", _FALLBACK_RATE)
    return _FALLBACK_RATE, today, "fallback"


# ── 内部ユーティリティ ────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    """'2026-06-19' → '6/19'"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.month}/{dt.day}"
    except Exception:
        return date_str


def _write_cache(rate: float, date_str: str) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({
                "rate":      rate,
                "date":      date_str,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("為替キャッシュ書き込み失敗: %s", exc)


def _read_cache() -> tuple[float, str] | None:
    try:
        if not _CACHE_PATH.exists():
            return None
        data      = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data["cached_at"])
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - cached_at
        if age > timedelta(hours=_CACHE_TTL_H):
            logger.info("USD/JPY キャッシュ期限切れ (%.1f時間超)", age.total_seconds() / 3600)
            return None
        return float(data["rate"]), data["date"]
    except Exception as exc:
        logger.warning("為替キャッシュ読み込み失敗: %s", exc)
        return None
