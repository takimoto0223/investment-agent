"""
data/universe.py
ポッドユニバース生成：CIO セクターフィルタ + カタリスト例外枠。
"""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# カタリスト判定閾値（控えめ設定: 常時発火・絶対門番の両方を避ける）
_CATALYST_VOLUME_RATIO_MIN = 2.0  # 平均出来高の 2 倍以上
_CATALYST_PRICE_CHANGE_MIN = 0.03  # 当日変動率の絶対値 3% 以上


def _is_catalyst_candidate(item: dict) -> bool:
    """出来高・値動きの両方が異常な銘柄かどうか判定する。"""
    return (
        float(item.get("volume_ratio", 0)) >= _CATALYST_VOLUME_RATIO_MIN
        and abs(float(item.get("price_change_pct", 0))) >= _CATALYST_PRICE_CHANGE_MIN
    )


def filter_universe(
    universe: list[dict],
    active_sectors: list[str],
    catalyst_slots: int,
) -> list[dict]:
    """
    active_sectors に含まれる銘柄を基本ユニバースとし、
    catalyst_slots 分だけセクター外の異常銘柄を追加して返す。

    Args:
        universe:        build_universe() / build_us_universe() で enriched された銘柄リスト
        active_sectors:  CIO Allocation.active_sectors（スコア >= 0.6 のセクター名リスト）
        catalyst_slots:  許可する例外枠数（FXRebalance は 0, 株式ポッドは 1）
    Returns:
        フィルタ後のユニバース（基本銘柄 + カタリスト例外）
    """
    base = [item for item in universe if item.get("sector") in active_sectors]

    if catalyst_slots <= 0:
        logger.debug(f"catalyst_slots=0: セクター外除外 (base={len(base)}銘柄)")
        return base

    base_symbols = {item.get("symbol") or item.get("Symbol", "") for item in base}
    catalyst_candidates = [
        item for item in universe
        if (item.get("symbol") or item.get("Symbol", "")) not in base_symbols
        and _is_catalyst_candidate(item)
    ]
    # volume_ratio 降順で上位 slots 件を採用
    catalyst_candidates.sort(key=lambda x: float(x.get("volume_ratio", 0)), reverse=True)
    extras = catalyst_candidates[:catalyst_slots]

    if extras:
        syms = [item.get("symbol") or item.get("Symbol") for item in extras]
        logger.info(f"カタリスト例外採用: {syms} (slots={catalyst_slots})")

    return base + extras


def build_pod_universe(
    market: str,
    active_sectors: list[str],
    catalyst_slots: int,
) -> list[dict]:
    """
    watchlist → build_universe → filter_universe を一括実行して
    ポッドが使えるユニバースを返す。

    Args:
        market:          "JP" または "US"
        active_sectors:  CIO Allocation.active_sectors
        catalyst_slots:  CIO Allocation.catalyst_slots
    """
    from data import watchlist

    base_list = watchlist.get_tradeable_by_market(market)
    logger.debug(f"build_pod_universe: market={market} base={len(base_list)}銘柄")

    if market.upper() == "JP":
        from data import market as mkt
        enriched = mkt.build_universe(base_list)
    else:
        from data import us_market as us_mkt
        enriched = us_mkt.build_us_universe(base_list)

    result = filter_universe(enriched, active_sectors, catalyst_slots)
    logger.info(
        f"ポッドユニバース確定: market={market} "
        f"{len(result)}銘柄 (base={len(base_list)}, "
        f"active_sectors={active_sectors}, catalyst_slots={catalyst_slots})"
    )
    return result
