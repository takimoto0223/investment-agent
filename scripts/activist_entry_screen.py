"""
scripts/activist_entry_screen.py
アクティビスト大量保有報告のエントリー水準スクリーニング。

理論:
  アクティビスト/エンゲージメントファンドが新規保有・買い増しした際の
  大量保有報告書から「報告義務発生日」前後の株価をエントリー水準の近似とみなし、
  現在株価がその水準以下（または許容乖離内）で推移している銘柄を
  「ファンドと同水準で仕込めるチャンス候補」として抽出する。

入力:
  大量保有報告の一覧 CSV（既定: data/activist_filings.csv）
  列: fund,name,code,filing_date,obligation_date,ratio,note,source
    - filing_date / obligation_date: YYYY-MM-DD（obligation_date 空なら filing_date から近似）
    - ratio: 表示用の自由記述（例 "新規 5.02%" / "6.74%→7.90%"）

エントリー水準の推定:
  日足終値で、報告義務発生日（なければ提出日の5営業日前相当）を終端とする
  直近 N 営業日（既定60）の出来高加重平均（VWAP）。
  ファンドは5%到達までの期間に分散して買うため、単日終値より VWAP を採用する。
  ※ あくまで近似。取得単価の実額は報告書本文（取得資金÷株数）でしか分からない。

データ源（優先順）:
  1. J-Quants ローカルキャッシュ（data/historical/bars/*.csv.gz — 過去の save_bars 分）
  2. J-Quants API v2（.env の JQUANTS_API_KEY。取得分はキャッシュに保存）
     ※ Free プランは約12週遅延のため「現在値」が古くなる。時点を必ず確認すること
  3. yfinance（{code}.T）
  現在値の時点が7日超古い場合は ⚠ を付けて明示する。

使い方:
  python -m scripts.activist_entry_screen
  python -m scripts.activist_entry_screen --csv path/to/filings.csv --window 60 --tolerance 0.05 --out report.md
"""
import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Windows コンソール UTF-8 化（CLAUDE.md 規約）
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

_DEFAULT_CSV       = Path("data/activist_filings.csv")
_DEFAULT_WINDOW    = 60      # エントリー推定に使う営業日数
_DEFAULT_TOLERANCE = 0.05    # エントリー水準からの許容上方乖離（5%）
_FILING_LAG_DAYS   = 7       # 報告義務発生日不明時: 提出日から差し引く暦日数（法定提出期限は5営業日）
_STALE_DAYS        = 7       # 現在値がこの暦日数より古ければ ⚠ を付ける


def load_filings(csv_path: Path) -> list[dict]:
    """CSV を読み込み、必須列の欠けた行はスキップして返す。"""
    rows = []
    with csv_path.open(encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            code = (row.get("code") or "").strip()
            date = (row.get("filing_date") or "").strip()
            if not code or not date:
                print(f"⚠ {csv_path}:{i} 行目: code/filing_date 欠落のためスキップ")
                continue
            rows.append(row)
    return rows


# ── 日足取得（J-Quants 優先・yfinance フォールバック） ─────────────────

def _bars_from_jquants(code: str) -> pd.DataFrame | None:
    """J-Quants（キャッシュ→API）から共通スキーマ日足を取得。不可なら None。"""
    try:
        from data.historical.jp_bars import fetch_daily_bars, load_bars, save_bars
    except ImportError:
        return None
    try:
        return load_bars(code)
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"⚠ {code} J-Quantsキャッシュ読込失敗: {exc}")
    try:
        df = fetch_daily_bars(code, years=2)
        if df.empty:
            return None
        save_bars(df)
        return df
    except Exception as exc:
        print(f"⚠ {code} J-Quants取得失敗: {exc}")
        return None


def _bars_from_yfinance(code: str) -> pd.DataFrame | None:
    """yfinance から日足を取得し共通スキーマ相当（ts_utc/close/volume）に変換。不可なら None。"""
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        hist = yf.Ticker(f"{code}.T").history(period="2y", interval="1d", auto_adjust=False)
        if len(hist) == 0:
            return None
        return pd.DataFrame({
            "ts_utc": pd.to_datetime(hist.index, utc=True),
            "close":  hist["Close"].to_numpy(),
            "volume": hist["Volume"].to_numpy(),
        })
    except Exception as exc:
        print(f"⚠ {code} yfinance取得失敗: {exc}")
        return None


def get_bars(code: str, cache: dict) -> tuple[pd.DataFrame | None, str]:
    """銘柄の日足と、そのデータ源ラベルを返す（同一銘柄はプロセス内キャッシュ）。"""
    if code in cache:
        return cache[code]
    df = _bars_from_jquants(code)
    source = "J-Quants"
    if df is None:
        df = _bars_from_yfinance(code)
        source = "yfinance"
    if df is None:
        source = "-"
    cache[code] = (df, source)
    return df, source


# ── 推定ロジック ──────────────────────────────────────────────────────

def estimate(
    bars: pd.DataFrame, filing_date: str, obligation_date: str, window: int,
) -> tuple[float | None, float | None, str, str]:
    """
    エントリー水準（VWAP近似）と現在値を返す。

    Returns:
        (entry_vwap, current_price, current_asof, basis)
        - current_asof: 現在値の時点 "YYYY-MM-DD"（古ければ呼び出し側で ⚠ 判断）
        - basis: エントリー推定の基準日説明（"義務発生日" | "提出日-7d近似"）
    """
    if obligation_date:
        end_dt, basis = datetime.strptime(obligation_date, "%Y-%m-%d"), "義務発生日"
    else:
        end_dt = datetime.strptime(filing_date, "%Y-%m-%d") - timedelta(days=_FILING_LAG_DAYS)
        basis  = f"提出日-{_FILING_LAG_DAYS}d近似"

    upto = bars[bars["ts_utc"] <= pd.Timestamp(end_dt, tz="UTC")]
    entry = None
    if len(upto) > 0:
        tail = upto.tail(window)
        vol  = tail["volume"].sum()
        if vol > 0:
            entry = float((tail["close"] * tail["volume"]).sum() / vol)
        else:
            entry = float(tail["close"].mean())

    current      = float(bars["close"].iloc[-1])
    current_asof = str(bars["ts_utc"].iloc[-1].date())
    return entry, current, current_asof, basis


def screen(rows: list[dict], window: int, tolerance: float) -> list[dict]:
    """全行を評価し、判定列を付けて返す。"""
    results, bars_cache = [], {}
    for row in rows:
        code = row["code"].strip()
        bars, src = get_bars(code, bars_cache)
        if bars is None:
            entry = current = diff = None
            asof, basis = "-", "-"
            print(f"⚠ {code} 全データ源で株価取得失敗")
        else:
            entry, current, asof, basis = estimate(
                bars, row["filing_date"].strip(),
                (row.get("obligation_date") or "").strip(), window,
            )
            diff = (current / entry - 1) if (entry and current) else None
        results.append({
            **row,
            "entry_vwap": entry,
            "current":    current,
            "asof":       asof,
            "src":        src,
            "diff":       diff,
            "basis":      basis,
            "is_chance":  (diff is not None and diff <= tolerance),
        })
    return results


def render(results: list[dict], window: int, tolerance: float) -> str:
    """Markdown テーブルを組み立てる。"""
    today = datetime.now()
    lines = [
        f"# アクティビスト・エントリー水準スクリーニング ({today:%Y-%m-%d})",
        "",
        f"- エントリー水準 = 基準日までの直近{window}営業日VWAP（終値×出来高、⚠近似値）",
        f"- 判定 ◎ = 現在値がエントリー水準の +{tolerance:.0%} 以内（同水準以下で仕込める候補）",
        f"- 現在値の時点が{_STALE_DAYS}日超古い行は時点に ⚠（J-Quants Freeは約12週遅延）",
        "",
        "| 判定 | 銘柄 | コード | ファンド | 保有割合 | 提出日 | エントリー水準⚠ | 現在値 | 時点 | 乖離 | 基準 | 源 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (x["diff"] is None, x["diff"] or 0)):
        mark    = "◎" if r["is_chance"] else ("−" if r["diff"] is not None else "?")
        entry   = f"{r['entry_vwap']:,.0f}円" if r["entry_vwap"] else "取得不可"
        current = f"{r['current']:,.0f}円" if r["current"] else "取得不可"
        diff    = f"{r['diff']:+.1%}" if r["diff"] is not None else "-"
        asof    = r["asof"]
        if asof != "-" and (today - datetime.strptime(asof, "%Y-%m-%d")).days > _STALE_DAYS:
            asof += "⚠"
        lines.append(
            f"| {mark} | {r.get('name','')} | {r['code']} | {r.get('fund','')} "
            f"| {r.get('ratio','')} | {r.get('filing_date','')} "
            f"| {entry} | {current} | {asof} | {diff} | {r['basis']} | {r['src']} |"
        )
    lines += [
        "",
        "※ エントリー水準は市場データからの推定（⚠近似値）。実際の取得単価は各報告書の",
        "「取得資金」÷「保有株券等の数」を EDINET 原本で確認すること。",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="アクティビスト大量保有のエントリー水準スクリーニング")
    parser.add_argument("--csv", type=Path, default=_DEFAULT_CSV, help="大量保有報告一覧CSV")
    parser.add_argument("--window", type=int, default=_DEFAULT_WINDOW, help="VWAP算出の営業日数")
    parser.add_argument("--tolerance", type=float, default=_DEFAULT_TOLERANCE, help="許容上方乖離(0.05=5%%)")
    parser.add_argument("--out", type=Path, default=None, help="Markdown出力先（省略時は標準出力のみ）")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"入力CSVが見つかりません: {args.csv}")
        sys.exit(1)

    # .env の JQUANTS_* を読み込む（config.settings の load_dotenv() 経由・任意）
    try:
        from config.settings import KABU  # noqa: F401 — import side-effect で dotenv を実行させる
    except Exception:
        pass  # 設定が無くても yfinance フォールバックで動かす

    rows = load_filings(args.csv)
    print(f"{len(rows)} 件の報告を評価中...")
    results = screen(rows, args.window, args.tolerance)
    report = render(results, args.window, args.tolerance)
    print()
    print(report)
    if args.out:
        args.out.write_text(report + "\n", encoding="utf-8")
        print(f"\n書き出し: {args.out}")


if __name__ == "__main__":
    main()
