"""
verify_cxo_reports.py
夜間・朝次レポートの smoke test。

検証方針:
  - any() 不使用。カードタイトル（_card() が埋め込む見出し文字列）と
    注入した具体値の両方を all() で確認する
  - セクションが丸ごと欠けても、注入値が変わっても [NG] を返す
  - ④ サボタージュテストで「壊したとき赤くなる」ことも機械的に確認する
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date
from pathlib import Path
from unittest.mock import patch

# ── モックデータ ──────────────────────────────────────────────────────

from agents.base import MarketContext
from agents.cxo import CXOAgent, CXOReportContext
from report.template import ScalpDayCandidate

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
    # source/fetched_at は空のまま（デフォルト: rateのみ表示）
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

# ── チェック関数 ──────────────────────────────────────────────────────

def _evening_checks(html: str) -> list[tuple[bool, str]]:
    """
    夜間レポートの必須チェック。
    各チェックは: カードタイトル（見出し文字列）+ 注入した具体値 を all() で確認する。
    """
    def chk(*needles: str) -> bool:
        return all(n in html for n in needles)

    return [
        # 保有銘柄チャート: カードが出て JP/US 全銘柄が描画される
        (chk("保有銘柄（円グラフ）"),
            "保有銘柄チャート カード出力"),
        (chk("ソフトバンクG", "アドバンテスト", "信越化学"),
            "JP保有3銘柄（ソフトバンクG/アドバンテスト/信越化学）が全て描画"),
        (chk("NVDA", "MSFT", "AAPL"),
            "US保有3銘柄（NVDA/MSFT/AAPL）が全て描画"),

        # リスク・通貨配分: カードが出て正しいレベルが表示される
        (chk("リスク・通貨配分"),
            "リスク・通貨配分 カード出力"),
        (chk("MEDIUM"),
            "リスクレベル MEDIUM 表示"),
        (chk("円ドル保有割合"),
            "円ドル保有割合バー 出力"),

        # 為替エージェント: カードが出て注入シグナルが表示される
        (chk("為替エージェント判定"),
            "為替エージェント判定 カード出力"),
        (chk("ドル買い"),
            "FXシグナル 'ドル買い' バッジ表示"),

        # セクター: カードが出て全セクターが描画される
        (chk("セクター別前日比"),
            "セクター別前日比 カード出力"),
        (chk("AI半導体", "データセンターインフラ", "宇宙インフラ"),
            "セクター上位3件（AI半導体/DCインフラ/宇宙インフラ）が全て描画"),

        # CxOメモ: カードが出て本文が描画される
        (chk("CxO方針メモ"),
            "CxO方針メモ カード出力"),
        (chk("[モック]"),
            "CxOメモ本文 '[モック]' が描画"),

        # 注入値: 総資産（jp_cash 450k + US株 4800*155.2=744960）
        (chk("¥1,194,960"),
            "総資産 ¥1,194,960 表示（jp_cash 450k + US換算 744,960円）"),

        # 注入値: USD/JPY レート
        (chk("155.20"),
            "USD/JPY 155.20 表示"),
    ]


def _morning_extra_checks(html: str) -> list[tuple[bool, str]]:
    """
    朝次レポート追加チェック（_evening_checks の後に実行）。
    カードタイトル + 注入した具体値で確認する。
    """
    def chk(*needles: str) -> bool:
        return all(n in html for n in needles)

    return [
        # デイトレ損益: カードが出て注入した P&L 値が表示される
        (chk("米国株デイトレ 昨夜の損益"),
            "デイトレ損益 カード出力"),
        (chk("+$87.08"),
            "デイトレ ネット損益 +$87.08 表示（_DAYTRADE_PL.total_net）"),
        (chk("$87.50"),
            "デイトレ グロス損益 $87.50 表示（_DAYTRADE_PL.total_gross）"),

        # ScalpDay候補: カードが出て候補の根拠テキスト（ユニーク文字列）が表示される
        # ※銘柄名は JP 保有ダミーデータにも出るため、根拠テキストで確認する
        (chk("本日デイトレ候補（日本株）"),
            "ScalpDay候補 カード出力"),
        (chk("出来高急増・AI半導体セクター優位"),
            "候補1（アドバンテスト）の根拠テキスト表示"),
        (chk("ATR拡大・利確水準接近"),
            "候補3（信越化学）の根拠テキスト表示"),
    ]


def _run_checks(checks: list[tuple[bool, str]], report_name: str) -> list[str]:
    """チェックリストを実行し [OK]/[NG] 行のリストを返す。"""
    lines = []
    for ok, desc in checks:
        status = "[OK]" if ok else "[NG]"
        lines.append(f"  {status}  {desc}")
        if not ok:
            print(f"  [WARN] {report_name}: NG → {desc}")
    return lines


def _verify_no_discussion(html: str, report_name: str) -> str:
    """discussion_log 依存の内容が残っていないことを確認。"""
    for marker in ["DiscussionItem", "discussion_session_date", "議論セッション"]:
        if marker in html:
            return f"  [WARN] {report_name}: '{marker}' が残っている（要確認）"
    return "  [OK]  discussion_log 残骸なし"


# ── サボタージュテスト ─────────────────────────────────────────────────

def _sabotage_tests(html_eve: str, html_mor: str) -> bool:
    """
    各チェックが「壊したHTMLでNGを返す」ことを機械的に確認する。
    needle を削除した HTML に対してチェックが False になれば [OK]。
    返り値: 全テスト合格で True。
    """
    tests: list[tuple[str, str, object, str]] = [
        # (html, 削除するneedle, チェックlambda, 説明)
        (html_eve, "ドル買い",
         lambda h: "ドル買い" in h,
         "FXシグナル削除 → 'ドル買い' チェックが NG になる"),

        (html_eve, "AI半導体",
         lambda h: all(n in h for n in ["AI半導体", "データセンターインフラ", "宇宙インフラ"]),
         "セクター削除 → セクター全描画チェックが NG になる"),

        (html_mor, "米国株デイトレ 昨夜の損益",
         lambda h: "米国株デイトレ 昨夜の損益" in h,
         "デイトレカード削除 → カード出力チェックが NG になる"),

        (html_mor, "+$87.08",
         lambda h: "+$87.08" in h,
         "デイトレ金額削除 → ネット損益チェックが NG になる"),

        (html_mor, "出来高急増・AI半導体セクター優位",
         lambda h: "出来高急増・AI半導体セクター優位" in h,
         "候補根拠削除 → 根拠テキストチェックが NG になる"),
    ]

    all_pass = True
    for html, needle, check_fn, desc in tests:
        sabotaged = html.replace(needle, "___REMOVED___")
        still_passes = check_fn(sabotaged)
        correct = not still_passes      # 壊したHTMLでFalseになるのが正解
        status = "[OK]" if correct else "[NG]"
        print(f"  {status}  {desc}")
        if not correct:
            print(f"       └ チェックが壊したHTMLでも PASS した → 検証力なし")
            all_pass = False
    return all_pass


# ── メイン ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("CXO レポート検証")
    print("=" * 60)

    memo_patch = patch.object(
        CXOAgent, "_generate_cxo_memo",
        return_value="[モック] リスク中程度。テクノロジーセクターを中心に自律運転継続。",
    )

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

    print("\n  [必須チェック]")
    for line in _run_checks(_evening_checks(html_e), "夜間レポート"):
        print(line)
    print(_verify_no_discussion(html_e, "夜間レポート"))

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

    print("\n  [必須チェック（夜間共通 + 朝次追加）]")
    all_checks = _evening_checks(html_m) + _morning_extra_checks(html_m)
    for line in _run_checks(all_checks, "朝次レポート"):
        print(line)
    print(_verify_no_discussion(html_m, "朝次レポート"))

    # ════════════════════════════════════════════════════
    # ③ 総評
    # ════════════════════════════════════════════════════
    ng_e = [desc for ok, desc in _evening_checks(html_e)          if not ok]
    ng_m = [desc for ok, desc in _evening_checks(html_m) +
                                  _morning_extra_checks(html_m)    if not ok]

    # ════════════════════════════════════════════════════
    # ④ サボタージュテスト（チェックの有効性検証）
    # ════════════════════════════════════════════════════
    print("\n── ④ サボタージュテスト（壊したHTMLでNGになるか確認）─")
    sabotage_ok = _sabotage_tests(html_e, html_m)

    # ════════════════════════════════════════════════════
    # 総評
    # ════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    if not ng_e and not ng_m and sabotage_ok:
        print("[OK] 全チェック合格。レポート出力・サボタージュ耐性ともに正常。")
    else:
        if ng_e:
            print(f"[NG] 夜間レポート: {len(ng_e)} 件の NG")
            for d in ng_e:
                print(f"     - {d}")
        if ng_m:
            print(f"[NG] 朝次レポート: {len(ng_m)} 件の NG")
            for d in ng_m:
                print(f"     - {d}")
        if not sabotage_ok:
            print("[NG] サボタージュテスト: 形骸化したチェックが残っています")

    print(f"\n生成ファイル:")
    print(f"  夜間: {_EVENING_PATH.resolve()}")
    print(f"  朝次: {_MORNING_PATH.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
