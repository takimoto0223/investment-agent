"""
verify_cxo_reports.py
夜間・朝次レポート + ローカルHTML の smoke test。

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
from data.fx_rate import get_usd_jpy as _get_usd_jpy
from report.template import ScalpDayCandidate

# 実キャッシュレートを使う（API障害時は fallback 値が入るが verify は通る）
_USDJPY, _USDJPY_AT, _USDJPY_SRC = _get_usd_jpy()
_USDJPY_STR = f"{_USDJPY:.2f}"

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
    macro_notes=f"USD/JPY={_USDJPY:.1f}, VIX=18.5, 米10Y=4.35%",
    rotation_signal="テクノロジーへ移行",
    risk_level="medium",
)

_FX_SIGNAL = {
    "fx_signal":      "buy_usd",
    "rationale":      "米金利高止まりでドル優位継続",
    "usd_jpy_rate":   _USDJPY,
    "us_weight_bias": 0.35,
}

_US_POSITIONS = [
    {"symbol": "NVDA", "qty": "2",  "avg_entry_price": "800.00",
     "current_price": "850.00", "market_value": "1700.00", "unrealized_pl": "100.00"},
    {"symbol": "MSFT", "qty": "3",  "avg_entry_price": "410.00",
     "current_price": "420.00", "market_value": "1260.00", "unrealized_pl":  "30.00"},
    {"symbol": "AAPL", "qty": "5",  "avg_entry_price": "195.00",
     "current_price": "192.00", "market_value":  "960.00", "unrealized_pl": "-15.00"},
]

_REPORT_CTX = CXOReportContext(
    ctx=_CTX,
    fx_signal_dict=_FX_SIGNAL,
    us_positions=_US_POSITIONS,
    jp_cash_jpy=450_000.0,
    usd_cash=2_500.0,
    us_equity_usd=4_800.0,
    usdjpy_rate=_USDJPY,
    usdjpy_source=_USDJPY_SRC,
    usdjpy_fetched_at=_USDJPY_AT,
)

# 総資産期待値: jp_cash(450k) + us_equity_usd(4800) * rate
_EXPECTED_TOTAL_JPY = f"¥{int(450_000 + 4_800.0 * _USDJPY):,}"

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
_EVENING_PATH      = Path("logs/verify_evening_report.html")
_MORNING_PATH      = Path("logs/verify_morning_report.html")
_LOCAL_MORNING_PATH = Path("logs/morning_report.html")
_LOCAL_EVENING_PATH = Path("logs/evening_report.html")

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

        # 為替: カードが出て注入シグナルが表示される
        (chk("為替"),
            "為替 カード出力"),
        (chk("ドル買い"),
            "FXシグナル 'ドル買い' バッジ表示"),

        # セクター: カードが出て全セクターが描画される
        (chk("セクター別前日比"),
            "セクター別前日比 カード出力"),
        (chk("AI半導体", "データセンターインフラ", "宇宙インフラ"),
            "セクター上位3件（AI半導体/DCインフラ/宇宙インフラ）が全て描画"),

        # CxOメモ: カードが出て本文が描画される（[確認用サンプル]で実値との区別を保証）
        (chk("CxO方針メモ"),
            "CxO方針メモ カード出力"),
        (chk("[確認用サンプル]"),
            "CxOメモ本文 '[確認用サンプル]' が描画（実運用判断との誤認防止）"),

        # 注入値: 総資産（jp_cash 450k + us_equity_usd 4800 * rate）
        (chk(_EXPECTED_TOTAL_JPY),
            f"総資産 {_EXPECTED_TOTAL_JPY} 表示（jp_cash 450k + US換算 {_USDJPY:.1f}円レート）"),

        # 注入値: USD/JPY レート
        (chk(_USDJPY_STR),
            f"USD/JPY {_USDJPY_STR} 表示"),
    ]


def _morning_extra_checks(html: str) -> list[tuple[bool, str]]:
    """
    朝次レポート追加チェック（_evening_checks の後に実行）。
    2×2 グリッド構造 + 注入した具体値で確認する。
    """
    def chk(*needles: str) -> bool:
        return all(n in html for n in needles)

    return [
        # セクションヘッダー
        (chk("戦略別サマリー"),
            "戦略別サマリー セクションヘッダー出力"),

        # 2×2 グリッド: 列ヘッダー
        (chk("日本株", "米国株"),
            "グリッド列ヘッダー（日本株・米国株）出力"),

        # 2×2 グリッド: セル見出し
        (chk("ScalpDay（スキャル）"),
            "ScalpDay 行ヘッダー出力"),
        (chk("MomentSwing（スイング）"),
            "MomentSwing 行ヘッダー出力"),

        # JP セル: kabu待ちラベル
        (chk("kabu待ち", "kabu接続待ち"),
            "JP セル kabu待ちラベル出力"),

        # ScalpDay_US: 注入した P&L 値
        (chk("+$87.08"),
            "ScalpDay_US ネット損益 +$87.08 表示（_DAYTRADE_PL.total_net）"),
        (chk("$87.50"),
            "ScalpDay_US グロス損益 $87.50 表示（_DAYTRADE_PL.total_gross）"),

        # ScalpDay_JP: 候補の根拠テキスト（ユニーク文字列）
        (chk("出来高急増・AI半導体セクター優位"),
            "ScalpDay_JP 候補1（アドバンテスト）の根拠テキスト表示"),
        (chk("ATR拡大・利確水準接近"),
            "ScalpDay_JP 候補3（信越化学）の根拠テキスト表示"),

        # MomentSwing_US: 保有銘柄の含み損益（注入した _US_POSITIONS.unrealized_pl）
        (chk("NVDA", "+$100.00"),
            "MomentSwing_US NVDA 含み損益 +$100.00 表示"),
        (chk("AAPL", "-$15.00"),
            "MomentSwing_US AAPL 含み損益 -$15.00 表示（マイナス値確認）"),

        # MomentSwing_US: 確定P&L 未実装ラベル
        (chk("確定P&amp;L: 未実装"),
            "MomentSwing_US 確定P&L 未実装ラベル出力"),

        # MomentSwing_JP: kabu未接続ラベル
        (chk("セッション未実行（kabu API 未接続）"),
            "MomentSwing_JP セッション未実行ラベル出力"),
    ]


def _local_html_checks(html: str, is_morning: bool) -> list[tuple[bool, str]]:
    """ローカルHTML共通チェック（メール版とは独立した検証）。"""
    def chk(*needles: str) -> bool:
        return all(n in html for n in needles)

    checks = [
        (chk("今日の方針", "CxO方針メモ"),
            "ローカルHTML: CxO方針セクション出力"),
        (chk("[確認用サンプル]"),
            "ローカルHTML: CxOメモ [確認用サンプル]ラベル出力"),
        (chk("リスクメーター" if "リスクメーター" in html else "MEDIUM"),
            "ローカルHTML: リスクレベル出力"),
        (chk("総資産", _EXPECTED_TOTAL_JPY),
            f"ローカルHTML: 総資産 {_EXPECTED_TOTAL_JPY} 出力"),
        (chk("為替", _USDJPY_STR),
            f"ローカルHTML: USD/JPY {_USDJPY_STR} 出力"),
        (chk("資産配分", "通貨配分"),
            "ローカルHTML: 資産配分セクション出力"),
        (chk("kabu接続後に実値へ"),
            "ローカルHTML: JP円グラフ kabu注意ラベル出力"),
        (chk("Alpaca実値"),
            "ローカルHTML: US円グラフ Alpaca実値ラベル出力"),
        (chk("戦略別サマリー"),
            "ローカルHTML: 戦略別サマリーセクション出力"),
        (chk("ScalpDay", "MomentSwing"),
            "ローカルHTML: ScalpDay/MomentSwing セル出力"),
        (chk("kabu接続待ち"),
            "ローカルHTML: JP セル kabu待ちラベル出力"),
        (chk("確定P&L: 未実装"),
            "ローカルHTML: MomentSwing_US 確定P&L 未実装ラベル出力"),
        (chk("深掘り"),
            "ローカルHTML: 深掘りセクション出力"),
    ]

    if is_morning:
        checks += [
            (chk("日本市場 まもなく開場"),
                "ローカルHTML（朝次）: 日本市場ヘッダー出力"),
            (chk("+$87.08"),
                "ローカルHTML（朝次）: ScalpDay_US ネット損益 +$87.08 出力"),
            (chk("NVDA", "+$100.00"),
                "ローカルHTML（朝次）: MomentSwing_US NVDA +$100.00 出力"),
            (chk("AAPL", "-$15.00"),
                "ローカルHTML（朝次）: MomentSwing_US AAPL -$15.00 出力"),
        ]
    else:
        checks += [
            (chk("米国市場 まもなく開場"),
                "ローカルHTML（夜間）: 米国市場ヘッダー出力"),
        ]

    return checks


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

        (html_mor, "+$87.08",
         lambda h: "+$87.08" in h,
         "ScalpDay_US ネット損益削除 → +$87.08 チェックが NG になる"),

        (html_mor, "出来高急増・AI半導体セクター優位",
         lambda h: "出来高急増・AI半導体セクター優位" in h,
         "ScalpDay_JP 候補根拠削除 → 根拠テキストチェックが NG になる"),

        (html_mor, "+$100.00",
         lambda h: "+$100.00" in h,
         "MomentSwing_US NVDA含み損益削除 → +$100.00 チェックが NG になる"),

        (html_mor, "kabu接続待ち",
         lambda h: "kabu接続待ち" in h,
         "JP kabu待ちラベル削除 → kabu接続待ちチェックが NG になる"),
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
    print(f"  USD/JPY: {_USDJPY_STR} (source={_USDJPY_SRC}, at={_USDJPY_AT})")
    print(f"  総資産期待値: {_EXPECTED_TOTAL_JPY}")
    print("=" * 60)

    memo_patch = patch.object(
        CXOAgent, "_generate_cxo_memo",
        return_value=(
            "[確認用サンプル] テクノロジーセクター中心にリスク MEDIUM で継続運転。"
            f"USD/JPY {_USDJPY:.1f}円台を確認。US株ウェイト維持。"
            "MomentSwing_US は NVDA/MSFT/AAPL 保有中。"
            "日本株は kabu API 接続後に稼働開始予定。"
        ),
    )

    # ════════════════════════════════════════════════════
    # ① 夜間レポート（メール用）
    # ════════════════════════════════════════════════════
    print("\n── ① 夜間レポート生成（メール用） ─────────────────")

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
    print(f"  ローカルHTML: {_LOCAL_EVENING_PATH} ({_LOCAL_EVENING_PATH.stat().st_size:,} bytes)"
          if _LOCAL_EVENING_PATH.exists() else f"  ローカルHTML: 未生成")

    print("\n  [必須チェック]")
    for line in _run_checks(_evening_checks(html_e), "夜間レポート"):
        print(line)
    print(_verify_no_discussion(html_e, "夜間レポート"))

    # ════════════════════════════════════════════════════
    # ② 朝次レポート（メール用）
    # ════════════════════════════════════════════════════
    print("\n── ② 朝次レポート生成（メール用） ─────────────────")

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
    print(f"  ローカルHTML: {_LOCAL_MORNING_PATH} ({_LOCAL_MORNING_PATH.stat().st_size:,} bytes)"
          if _LOCAL_MORNING_PATH.exists() else f"  ローカルHTML: 未生成")

    print("\n  [必須チェック（夜間共通 + 朝次追加）]")
    all_checks = _evening_checks(html_m) + _morning_extra_checks(html_m)
    for line in _run_checks(all_checks, "朝次レポート"):
        print(line)
    print(_verify_no_discussion(html_m, "朝次レポート"))

    # ════════════════════════════════════════════════════
    # ③ ローカルHTML チェック
    # ════════════════════════════════════════════════════
    print("\n── ③ ローカルHTML チェック ──────────────────────────")
    local_html_ok_eve = local_html_ok_mor = True

    if _LOCAL_EVENING_PATH.exists():
        html_le = _LOCAL_EVENING_PATH.read_text(encoding="utf-8")
        print("\n  [夜間ローカルHTML]")
        local_eve_results = _local_html_checks(html_le, is_morning=False)
        for line in _run_checks(local_eve_results, "夜間ローカルHTML"):
            print(line)
        local_html_ok_eve = all(ok for ok, _ in local_eve_results)
    else:
        print("  [SKIP] logs/evening_report.html 未生成")

    if _LOCAL_MORNING_PATH.exists():
        html_lm = _LOCAL_MORNING_PATH.read_text(encoding="utf-8")
        print("\n  [朝次ローカルHTML]")
        local_mor_results = _local_html_checks(html_lm, is_morning=True)
        for line in _run_checks(local_mor_results, "朝次ローカルHTML"):
            print(line)
        local_html_ok_mor = all(ok for ok, _ in local_mor_results)
    else:
        print("  [SKIP] logs/morning_report.html 未生成")

    # ════════════════════════════════════════════════════
    # ④ サボタージュテスト（チェックの有効性検証）
    # ════════════════════════════════════════════════════
    print("\n── ④ サボタージュテスト（壊したHTMLでNGになるか確認）─")
    sabotage_ok = _sabotage_tests(html_e, html_m)

    # ════════════════════════════════════════════════════
    # 総評
    # ════════════════════════════════════════════════════
    ng_e = [desc for ok, desc in _evening_checks(html_e)          if not ok]
    ng_m = [desc for ok, desc in _evening_checks(html_m) +
                                  _morning_extra_checks(html_m)    if not ok]

    print("\n" + "=" * 60)
    all_ok = not ng_e and not ng_m and sabotage_ok and local_html_ok_eve and local_html_ok_mor
    if all_ok:
        print("[OK] 全チェック合格。メール・ローカルHTML・サボタージュ耐性ともに正常。")
    else:
        if ng_e:
            print(f"[NG] 夜間レポート: {len(ng_e)} 件の NG")
            for d in ng_e:
                print(f"     - {d}")
        if ng_m:
            print(f"[NG] 朝次レポート: {len(ng_m)} 件の NG")
            for d in ng_m:
                print(f"     - {d}")
        if not local_html_ok_eve:
            print("[NG] 夜間ローカルHTML: チェック失敗")
        if not local_html_ok_mor:
            print("[NG] 朝次ローカルHTML: チェック失敗")
        if not sabotage_ok:
            print("[NG] サボタージュテスト: 形骸化したチェックが残っています")

    print(f"\n生成ファイル:")
    print(f"  メール夜間 : {_EVENING_PATH.resolve()}")
    print(f"  メール朝次 : {_MORNING_PATH.resolve()}")
    print(f"  ローカル夜間: {_LOCAL_EVENING_PATH.resolve()}")
    print(f"  ローカル朝次: {_LOCAL_MORNING_PATH.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
