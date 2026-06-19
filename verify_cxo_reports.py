"""
verify_cxo_reports.py
PR⑥後の run_morning_report / run_evening_report 確認スクリプト。

- 外部依存(Alpaca/kabu/CIO LLM)はモックで代替
- send_report を差し替えてHTMLをファイル保存
- 保存後に必須セクションの有無を検証してレポート
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── モックデータ ──────────────────────────────────────────────────────

from agents.base import MarketContext
from agents.cxo import CXOAgent, CXOReportContext
from report.template import ScalpDayCandidate, DaytradeRecord

_CTX = MarketContext(
    date=date.today().isoformat(),
    sector_scores={
        "AI半導体":             0.90,
        "データセンターインフラ": 0.75,
        "宇宙インフラ":           0.65,
        "半導体装置・材料":       0.55,
        "エネルギー":             0.40,
        "金融":                   0.38,
        "ディフェンシブ":         0.30,
    },
    macro_notes="USD/JPY=155.2, VIX=18.5, 米10Y=4.35%",
    rotation_signal="テクノロジーへ移行",
    risk_level="medium",
)

_FX_SIGNAL = {
    "fx_signal":      "buy_usd",
    "rationale":      "米金利高止まりでドル優位継続",
    "usd_jpy_rate":   155.2,
    "us_weight_bias": 0.35,
}

_US_POSITIONS = [
    {"symbol": "NVDA", "qty": "2",  "avg_entry_price": "800.00",
     "current_price": "850.00", "market_value": "1700.00"},
    {"symbol": "MSFT", "qty": "3",  "avg_entry_price": "410.00",
     "current_price": "420.00", "market_value": "1260.00"},
    {"symbol": "AAPL", "qty": "5",  "avg_entry_price": "195.00",
     "current_price": "192.00", "market_value":  "960.00"},
]

_REPORT_CTX = CXOReportContext(
    ctx=_CTX,
    fx_signal_dict=_FX_SIGNAL,
    us_positions=_US_POSITIONS,
    jp_cash_jpy=450_000.0,
    usd_cash=2_500.0,
    us_equity_usd=4_800.0,
    usdjpy_rate=155.2,
)

_DAYTRADE_PL = {
    "total_gross": 87.50,
    "total_fees":   0.42,
    "total_net":   87.08,
    "trades": [
        {
            "symbol": "NVDA", "qty": 1,
            "buy_price": 820.0, "sell_price": 907.5,
            "gross_pl": 87.5, "fees": 0.42, "net_pl": 87.08,
        },
    ],
}

_CANDIDATES = [
    ScalpDayCandidate("6857", "アドバンテスト", "buy",  "出来高急増・AI半導体セクター優位"),
    ScalpDayCandidate("9984", "ソフトバンクG",  "buy",  "DCインフラ連動・モメンタム継続"),
    ScalpDayCandidate("4063", "信越化学",        "sell", "ATR拡大・利確水準接近"),
]

# ── HTML 保存先 ───────────────────────────────────────────────────────

Path("logs").mkdir(exist_ok=True)
_EVENING_PATH = Path("logs/verify_evening_report.html")
_MORNING_PATH = Path("logs/verify_morning_report.html")

# ── 検証ヘルパー ─────────────────────────────────────────────────────

_REQUIRED_SECTIONS = {
    "ピーチャート(JP保有)":    ["ソフトバンクG", "アドバンテスト", "信越化学"],
    "ピーチャート(US保有)":    ["NVDA", "MSFT", "AAPL"],
    "リスクメーター":           ["risk", "medium", "リスク"],
    "円ドル比率":               ["USD", "JPY", "ドル", "円"],
    "セクタースコア":           ["AI半導体", "データセンターインフラ", "宇宙インフラ"],
    "FXシグナル":               ["ドル買い", "FX", "155"],
    "CxOメモ":                  ["CxO", "cxo", "方針"],
}

_MORNING_EXTRA_SECTIONS = {
    "デイトレ損益":             ["87", "NVDA", "損益", "daytrade", "gross", "net"],
    "デイトレ候補(JP)":        ["アドバンテスト", "ソフトバンクG", "候補"],
}


def check_sections(html: str, sections: dict, report_name: str) -> list[str]:
    """必須セクションの有無をチェックしてレポートを返す。"""
    results = []
    for section_name, keywords in sections.items():
        found = any(kw.lower() in html.lower() for kw in keywords)
        status = "[OK]" if found else "[NG]"
        results.append(f"  {status}  {section_name}")
        if not found:
            print(f"  [WARN] {report_name}: '{section_name}' が見当たらない (探したキーワード: {keywords})")
    return results


def verify_no_discussion(html: str, report_name: str) -> str:
    """discussion_log 依存の内容が残っていないことを確認。"""
    discussion_markers = ["DiscussionItem", "discussion_session_date", "議論セッション"]
    for marker in discussion_markers:
        if marker in html:
            return f"  [WARN] {report_name}: '{marker}' が残っている（要確認）"
    return f"  [OK]  discussion_log 残骸なし"


# ── メイン ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("PR⑥ CXO レポート検証")
    print("=" * 60)

    # _generate_cxo_memo だけ LLM を差し替え（他は実データを使う）
    memo_patch = patch.object(
        CXOAgent, "_generate_cxo_memo",
        return_value="[モック] リスク中程度。テクノロジーセクターを中心に自律運転継続。",
    )

    # _load_session_logs はファイル不在でも空リストを返すだけなので差し替えなし

    # ════════════════════════════════════════════════════
    # ① 夜間レポート
    # ════════════════════════════════════════════════════
    print("\n── ① 夜間レポート生成 ──────────────────────────────")

    captured_evening: dict = {}

    def capture_send_evening(subject: str, html: str) -> bool:
        captured_evening["subject"] = subject
        captured_evening["html"]    = html
        _EVENING_PATH.write_text(html, encoding="utf-8")
        print(f"  保存先: {_EVENING_PATH}")
        return True

    with memo_patch, patch("agents.cxo.send_report", side_effect=capture_send_evening):
        ok = CXOAgent().generate_evening_report(_REPORT_CTX)

    print(f"  送信結果: {'OK' if ok else 'NG'}")
    print(f"  件名: {captured_evening.get('subject', '(なし)')}")
    html_e = captured_evening.get("html", "")
    print(f"  HTML サイズ: {len(html_e):,} bytes")

    print("\n  [必須セクション確認]")
    for line in check_sections(html_e, _REQUIRED_SECTIONS, "夜間レポート"):
        print(line)
    print(verify_no_discussion(html_e, "夜間レポート"))

    # ════════════════════════════════════════════════════
    # ② 朝次レポート
    # ════════════════════════════════════════════════════
    print("\n── ② 朝次レポート生成 ──────────────────────────────")

    captured_morning: dict = {}

    def capture_send_morning(subject: str, html: str) -> bool:
        captured_morning["subject"] = subject
        captured_morning["html"]    = html
        _MORNING_PATH.write_text(html, encoding="utf-8")
        print(f"  保存先: {_MORNING_PATH}")
        return True

    with memo_patch, patch("agents.cxo.send_report", side_effect=capture_send_morning):
        ok = CXOAgent().generate_morning_report(
            _REPORT_CTX,
            daytrade_pl=_DAYTRADE_PL,
            scalpday_candidates=_CANDIDATES,
        )

    print(f"  送信結果: {'OK' if ok else 'NG'}")
    print(f"  件名: {captured_morning.get('subject', '(なし)')}")
    html_m = captured_morning.get("html", "")
    print(f"  HTML サイズ: {len(html_m):,} bytes")

    print("\n  [必須セクション確認]")
    all_sections = {**_REQUIRED_SECTIONS, **_MORNING_EXTRA_SECTIONS}
    for line in check_sections(html_m, all_sections, "朝次レポート"):
        print(line)
    print(verify_no_discussion(html_m, "朝次レポート"))

    # ════════════════════════════════════════════════════
    # ③ 差分チェック: 注入値 vs レポート内の具体的な数値
    # ════════════════════════════════════════════════════
    print("\n── ③ 注入値とレポート値の照合 ─────────────────────")

    checks = [
        ("夜間", html_e, "155",   "USD/JPY=155.2"),
        ("夜間", html_e, "1,194", "総資産 1,194,960 (jp_cash 450k + US株 744k)"),
        ("朝次", html_m, "87",    "デイトレ損益 87.08"),
        ("朝次", html_m, "アドバンテスト", "デイトレ候補 アドバンテスト"),
        ("朝次", html_m, "ソフトバンクG",  "デイトレ候補 ソフトバンクG"),
    ]
    for report_label, html, needle, description in checks:
        found = needle in html
        print(f"  {'[OK]' if found else '[NG]'} [{report_label}] {description}")

    # ════════════════════════════════════════════════════
    # 総評
    # ════════════════════════════════════════════════════
    any_missing_e = any("[NG]" in l for l in check_sections(html_e, _REQUIRED_SECTIONS, ""))
    any_missing_m = any("[NG]" in l for l in check_sections(html_m, all_sections, ""))

    print("\n" + "=" * 60)
    if not any_missing_e and not any_missing_m:
        print("[OK] 全セクション確認完了。旧来と同等のレポートが出力されています。")
    else:
        print("[NG]セクションあり。上記の [WARN] を確認してください。")
    print(f"\n生成ファイル:")
    print(f"  夜間: {_EVENING_PATH.resolve()}")
    print(f"  朝次: {_MORNING_PATH.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
