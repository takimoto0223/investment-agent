"""
data/watchlist.py
ウォッチリスト管理：セクターごとに銘柄を発注対象・監視のみ・ブラックリストで管理する。
永続化先: data/watchlist.json（なければ初期値で自動生成）
"""
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"

# ──────────────────────────────────────────────
# 初期値
# ──────────────────────────────────────────────

_DEFAULT_WATCHLIST: dict = {
    "tradeable": [
        # 発注対象銘柄（スクリーニング対象）
        {
            "symbol":     "IONQ",
            "name":       "IonQ",
            "market":     "US",
            "sector":     "量子コンピューター",
            "max_weight": 0.02,   # PF 最大 2%
            "added_at":   "2026-06-10",
            "note":       "量子コンピューター商用化先行。trapped-ion 方式。",
        },
        {
            "symbol":     "QBTS",
            "name":       "D-Wave Quantum",
            "market":     "US",
            "sector":     "量子コンピューター",
            "max_weight": 0.02,
            "added_at":   "2026-06-10",
            "note":       "量子アニーリング方式。最適化問題に特化。",
        },
    ],
    "watchonly": [
        # 監視のみ（発注対象外）
        {
            "symbol":  "RGTI",
            "name":    "Rigetti Computing",
            "market":  "US",
            "sector":  "量子コンピューター",
            "added_at":"2026-06-10",
            "note":    "技術は有望だが財務リスクが高い。量子エラー訂正の進捗を監視。",
        },
    ],
    "blacklist": [
        # 対象外銘柄（理由付き）
        # {
        #   "symbol": "XXXX",
        #   "reason": "不正会計疑惑（2026-01 報告）",
        #   "added_at": "2026-01-15",
        # }
    ],
}


# ──────────────────────────────────────────────
# 内部ヘルパー
# ──────────────────────────────────────────────

def _load() -> dict:
    """JSON ファイルを読み込む。なければ初期値を返す。"""
    if _WATCHLIST_PATH.exists():
        try:
            return json.loads(_WATCHLIST_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"watchlist.json 読み込み失敗（初期値を使用）: {e}")
    return {
        "tradeable": list(_DEFAULT_WATCHLIST["tradeable"]),
        "watchonly": list(_DEFAULT_WATCHLIST["watchonly"]),
        "blacklist": list(_DEFAULT_WATCHLIST["blacklist"]),
    }


def _save(data: dict) -> None:
    """JSON ファイルに保存する。"""
    try:
        _WATCHLIST_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.error(f"watchlist.json 保存失敗: {e}")


# ──────────────────────────────────────────────
# 公開 API
# ──────────────────────────────────────────────

def get_tradeable(sector: str | None = None) -> list[dict]:
    """発注対象銘柄を返す。sector を指定するとそのセクターだけ返す。"""
    items = _load()["tradeable"]
    if sector:
        items = [i for i in items if i.get("sector") == sector]
    return items


def get_watchonly(sector: str | None = None) -> list[dict]:
    """監視のみ銘柄を返す。"""
    items = _load()["watchonly"]
    if sector:
        items = [i for i in items if i.get("sector") == sector]
    return items


def get_blacklist() -> list[dict]:
    """ブラックリスト銘柄を返す。"""
    return _load()["blacklist"]


def is_blacklisted(symbol: str) -> bool:
    """指定銘柄がブラックリストに登録されているか確認する。"""
    return any(item["symbol"].upper() == symbol.upper() for item in get_blacklist())


def add_tradeable(
    symbol: str,
    name: str,
    market: str,
    sector: str,
    max_weight: float = 0.02,
    note: str = "",
) -> None:
    """発注対象銘柄を追加する。"""
    data = _load()
    # 既存チェック
    if any(i["symbol"].upper() == symbol.upper() for i in data["tradeable"]):
        logger.info(f"{symbol} は既に発注対象リストに存在します")
        return
    # ブラックリストに入っていないか確認
    if is_blacklisted(symbol):
        logger.warning(f"{symbol} はブラックリストに登録されているため追加できません")
        return
    data["tradeable"].append({
        "symbol":     symbol.upper(),
        "name":       name,
        "market":     market,
        "sector":     sector,
        "max_weight": max_weight,
        "added_at":   datetime.now().date().isoformat(),
        "note":       note,
    })
    _save(data)
    logger.info(f"発注対象に追加: {symbol} ({sector})")


def add_watchonly(
    symbol: str,
    name: str,
    market: str,
    sector: str,
    note: str = "",
) -> None:
    """監視のみ銘柄を追加する。"""
    data = _load()
    if any(i["symbol"].upper() == symbol.upper() for i in data["watchonly"]):
        logger.info(f"{symbol} は既に監視リストに存在します")
        return
    data["watchonly"].append({
        "symbol":   symbol.upper(),
        "name":     name,
        "market":   market,
        "sector":   sector,
        "added_at": datetime.now().date().isoformat(),
        "note":     note,
    })
    _save(data)
    logger.info(f"監視リストに追加: {symbol} ({sector})")


def add_blacklist(symbol: str, reason: str) -> None:
    """銘柄をブラックリストに追加し、発注対象・監視リストから除外する。"""
    data = _load()
    sym = symbol.upper()
    if any(i["symbol"] == sym for i in data["blacklist"]):
        logger.info(f"{sym} は既にブラックリストに存在します")
        return
    # 他リストから削除
    data["tradeable"] = [i for i in data["tradeable"] if i["symbol"] != sym]
    data["watchonly"]  = [i for i in data["watchonly"]  if i["symbol"] != sym]
    data["blacklist"].append({
        "symbol":   sym,
        "reason":   reason,
        "added_at": datetime.now().date().isoformat(),
    })
    _save(data)
    logger.info(f"ブラックリストに追加: {sym} | 理由: {reason}")


def promote_to_tradeable(symbol: str, max_weight: float = 0.02) -> None:
    """監視リストから発注対象リストへ昇格する。"""
    data = _load()
    sym = symbol.upper()
    watch = [i for i in data["watchonly"] if i["symbol"] == sym]
    if not watch:
        logger.warning(f"{sym} は監視リストに見つかりません")
        return
    entry = watch[0]
    data["watchonly"] = [i for i in data["watchonly"] if i["symbol"] != sym]
    entry["max_weight"] = max_weight
    entry["promoted_at"] = datetime.now().date().isoformat()
    data["tradeable"].append(entry)
    _save(data)
    logger.info(f"{sym} を監視リスト → 発注対象に昇格（max_weight={max_weight:.1%}）")


def summary() -> str:
    """ウォッチリストのサマリーを文字列で返す。"""
    data = _load()
    lines = [
        f"発注対象: {len(data['tradeable'])} 銘柄",
        f"監視のみ: {len(data['watchonly'])} 銘柄",
        f"ブラックリスト: {len(data['blacklist'])} 銘柄",
    ]
    if data["tradeable"]:
        lines.append("--- 発注対象 ---")
        for i in data["tradeable"]:
            lines.append(f"  {i['symbol']} ({i['sector']}) max={i.get('max_weight', 0):.1%}")
    if data["watchonly"]:
        lines.append("--- 監視のみ ---")
        for i in data["watchonly"]:
            lines.append(f"  {i['symbol']} ({i['sector']})")
    return "\n".join(lines)
