"""
data/intel_store.py
IntelScout の状態管理・ダイジェスト読み書き。

ファイル構成:
  logs/intel_state.json          - 最終収集時刻 (last_collected_at, JST ISO 8601)
  logs/intel_digest.json         - 最新ダイジェスト・機械用 (常に上書き)
  logs/digests/YYYY-MM-DD.md     - 日次ダイジェスト・人間閲覧用 (常に上書き)

鮮度ポリシー:
  generated_at が _STALE_HOURS(48h) を超えたダイジェストは古いとみなし、
  get_news_summary_for_cio() は ("", "CIO") を返す。WARNING ログで IntelScout 停止を検知。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_JST = timezone(timedelta(hours=9))

_STATE_PATH  = Path("logs/intel_state.json")
_DIGEST_PATH = Path("logs/intel_digest.json")
_DIGESTS_DIR = Path("logs/digests")
_STALE_HOURS = 48


@dataclass
class IntelState:
    last_collected_at: datetime | None = None


def load_state() -> IntelState:
    """intel_state.json から最終収集時刻を読み込む。ファイル不在・エラー時は空の IntelState。"""
    if not _STATE_PATH.exists():
        return IntelState()
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        ts = raw.get("last_collected_at")
        if ts:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_JST)
            return IntelState(last_collected_at=dt)
    except Exception as e:
        logger.warning(f"intel_state.json 読み込み失敗: {e}")
    return IntelState()


def save_state(state: IntelState) -> None:
    """intel_state.json に最終収集時刻を書き込む。"""
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_collected_at": state.last_collected_at.isoformat() if state.last_collected_at else None,
    }
    _STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_intel_digest() -> dict:
    """
    最新ダイジェスト (logs/intel_digest.json) を返す。
    ファイル不在・読み込みエラー時は空 dict。
    呼び出し元は空 dict を正常パス（初回/未収集）として扱うこと。
    """
    if not _DIGEST_PATH.exists():
        return {}
    try:
        return json.loads(_DIGEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"intel_digest.json 読み込み失敗: {e}")
        return {}


def get_news_summary_for_cio() -> tuple[str, str]:
    """
    CIO.generate_market_context(news_summary=) に渡す値を返す。

    戻り値: (news_summary: str, obs_generated_by: str)
      - ダイジェスト存在かつ 48h 以内 → (digest_text, "IntelScout")
      - 空 / 古すぎ / エラー           → ("", "CIO")   ← 正常フォールバック
    """
    digest = read_intel_digest()
    if not digest:
        return "", "CIO"

    ga = digest.get("generated_at", "")
    if ga:
        try:
            dt = datetime.fromisoformat(ga)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_JST)
            age_h = (datetime.now(_JST) - dt).total_seconds() / 3600
            if age_h > _STALE_HOURS:
                logger.warning(
                    f"IntelScout ダイジェストが {age_h:.0f}h 前のものです（上限 {_STALE_HOURS}h）。"
                    "IntelScout ジョブが停止している可能性があります。空フォールバックを使用。"
                )
                return "", "CIO"
        except Exception as e:
            logger.warning(f"ダイジェスト generated_at 解析失敗: {e}")
            return "", "CIO"

    text = digest.get("digest_text", "")
    if not text:
        return "", "CIO"

    return text, "IntelScout"


def write_intel_digest(rollup: dict) -> None:
    """
    ダイジェストを両形式で保存する。
      - logs/intel_digest.json       : 機械用・セッションが読む (常に上書き)
      - logs/digests/YYYY-MM-DD.md   : 人間閲覧用 (常に上書き)
    """
    _DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DIGEST_PATH.write_text(json.dumps(rollup, ensure_ascii=False, indent=2), encoding="utf-8")

    _DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = rollup.get("date", datetime.now(_JST).strftime("%Y-%m-%d"))
    md_path  = _DIGESTS_DIR / f"{date_str}.md"
    md_path.write_text(build_digest_md(rollup), encoding="utf-8")


def build_digest_md(rollup: dict) -> str:
    """ロールアップ dict から Markdown テキストを生成する（人間閲覧用）。"""
    date_str     = rollup.get("date", "")
    generated_at = rollup.get("generated_at", "")
    windows      = rollup.get("windows", [])
    signal_count = rollup.get("signal_count", 0)
    top_signals  = rollup.get("top_signals", [])
    sector_hl    = rollup.get("sector_highlights", {})
    macro_sum    = rollup.get("macro_summary", "")
    digest_text  = rollup.get("digest_text", "")

    windows_str = ", ".join(windows) if windows else "—"
    lines = [
        f"# IntelScout Daily Digest — {date_str}",
        "",
        f"> 生成: {generated_at}  収集窓: [{windows_str}]  シグナル数: {signal_count}件",
        "",
        "## セクター別ハイライト",
    ]
    if sector_hl:
        lines += ["| セクター | 動向 |", "|---|---|"]
        for sector, hl in sector_hl.items():
            lines.append(f"| {sector} | {hl} |")
    else:
        lines.append("（なし）")

    lines += ["", "## 今日のトップシグナル"]
    if top_signals:
        for i, sig in enumerate(top_signals[:5], 1):
            score   = sig.get("score", sig.get("relevance_score", 0))
            title   = sig.get("title", "")
            summary = sig.get("summary", "")
            lines.append(f"{i}. **[{score:.2f}] {title}** — {summary}")
    else:
        lines.append("（なし）")

    lines += [
        "",
        "## マクロサマリー",
        macro_sum or "（なし）",
        "",
        "## CIO 注入テキスト（digest_text）",
        "```",
        digest_text or "（空）",
        "```",
    ]
    return "\n".join(lines) + "\n"
