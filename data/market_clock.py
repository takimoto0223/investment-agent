"""
data/market_clock.py
市場開閉ガード。JP (東証) / US (Alpaca clock API) の通常立会時間を判定する。

- is_jp_open(now=None) : 東証が現在通常立会時間かどうか（JST で判断）
- is_us_open()         : NYSE が現在通常立会時間かどうか（Alpaca clock API で判断）

いずれも「閉場/時間外」は False を返すだけで、例外を送出しない。
テスト用に now 引数（JP）と _fetch_alpaca_clock_is_open（US）をモック可能にしている。
"""
import logging
from datetime import datetime, time, date
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")
_ET  = ZoneInfo("America/New_York")

# 東証 通常立会時間（JST）
_JP_AM_OPEN  = time(9,  0)
_JP_AM_CLOSE = time(11, 30)
_JP_PM_OPEN  = time(12, 30)
_JP_PM_CLOSE = time(15, 30)

# NYSE 通常立会時間（ET）— Alpaca API フォールバック用
_US_OPEN  = time(9,  30)
_US_CLOSE = time(16,  0)


# ── JP ──────────────────────────────────────────────────────────────────

def _is_jp_holiday(d: date) -> bool:
    """jpholiday で日本の祝日（振替休日含む）を判定。"""
    import jpholiday
    return bool(jpholiday.is_holiday(d))


def _is_year_end_new_year(d: date) -> bool:
    """東証 年末年始休場 (12/31 〜 1/3)。"""
    return (d.month == 12 and d.day == 31) or (d.month == 1 and d.day <= 3)


def is_jp_open(now: datetime | None = None) -> bool:
    """
    東証が現在通常立会時間かどうかを返す。

    now: 省略時は datetime.now(JST)。テストでは任意の datetime を渡せる。
    戻り値: True = 通常立会中, False = 閉場/時間外/休場
    """
    if now is None:
        now = datetime.now(_JST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_JST)

    d = now.date()
    t = now.time()

    if d.weekday() >= 5:                # 土日
        return False
    if _is_year_end_new_year(d):        # 年末年始
        return False
    if _is_jp_holiday(d):               # 祝日
        return False

    in_am = _JP_AM_OPEN  <= t < _JP_AM_CLOSE
    in_pm = _JP_PM_OPEN  <= t < _JP_PM_CLOSE
    return in_am or in_pm


# ── US ──────────────────────────────────────────────────────────────────

def _fetch_alpaca_clock_is_open() -> bool:
    """
    Alpaca clock API を呼び、NYSE が通常立会時間かどうかを返す。
    テストでは patch("data.market_clock._fetch_alpaca_clock_is_open", ...) で差し替える。
    """
    from brokers.alpaca import AlpacaBroker
    clock = AlpacaBroker().client.get_clock()
    return bool(clock.is_open)


def _is_us_open_local(now: datetime | None = None) -> bool:
    """Alpaca API 失敗時のフォールバック: ET の現地時刻で週平日 9:30-16:00 を判定。"""
    if now is None:
        now = datetime.now(_ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_ET)
    if now.date().weekday() >= 5:
        return False
    return _US_OPEN <= now.time() < _US_CLOSE


def is_us_open() -> bool:
    """
    NYSE が現在通常立会時間かどうかを返す。

    Alpaca の clock.is_open は pre-market/after-hours/休日では False を返すため、
    「通常時間のみ True」という要件をそのまま満たす。
    API 例外時は安全側 (False) にフォールバックする。
    """
    try:
        return _fetch_alpaca_clock_is_open()
    except Exception as exc:
        logger.warning(f"Alpaca clock API 失敗 → False (安全側フォールバック): {exc}")
        return False
