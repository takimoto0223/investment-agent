"""
backtests/moment_swing/universe_report.py
9 銘柄横断サマリー HTML を生成する。
individual の html_report.py と同じデザインシステムを使用。

公開 API:
    raw_stats(trades) → dict  # 生数値（HTML 生成・観察判定に使う）
    save_universe_html(results, symbol_names, params, dfs, out_dir) → Path
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from backtests.moment_swing.darvas_backtest import BacktestParams, BacktestResult, Trade

_RUNS_DIR = Path(__file__).parent.parent / "runs"


# ---------------------------------------------------------------------------
# 集計ヘルパー
# ---------------------------------------------------------------------------

def raw_stats(trades: list[Trade]) -> Optional[dict]:
    """
    Trade リストから生数値の集計 dict を返す。
    トレードなしの場合は None。
    """
    if not trades:
        return None
    pnl_jpys = [t.pnl_jpy for t in trades]
    pnl_pcts  = [t.pnl_pct  for t in trades]
    wins      = [t for t in trades if t.pnl_jpy > 0]

    # Why: 旧式は peak 初期値0のため第1トレードが損失だと必ず -100% になる欠陥があった。
    # pnl_pct を複利合成した equity curve（初期値 1.0）を使い、
    # 確定トレードの収益率ベースの最大ドローダウンを正確に計測する。
    equity = 1.0; peak_eq = 1.0; max_dd = 0.0
    for r in pnl_pcts:
        equity *= (1 + r / 100)
        peak_eq = max(peak_eq, equity)
        dd      = (equity - peak_eq) / peak_eq
        max_dd  = min(max_dd, dd)

    return {
        "n_trades":      len(trades),
        "n_wins":        len(wins),
        "win_rate":      len(wins) / len(trades) * 100,
        "avg_pnl_pct":   sum(pnl_pcts) / len(pnl_pcts),
        "total_pnl_jpy": sum(pnl_jpys),
        "avg_hold_days": sum(t.hold_days for t in trades) / len(trades),
        "max_dd_pct":    max_dd * 100,
    }


def _agg_stats(all_trades: list[Trade]) -> Optional[dict]:
    """全銘柄の全 Trade を合算して集計。"""
    return raw_stats(all_trades)


# ---------------------------------------------------------------------------
# HTML テンプレート
# ---------------------------------------------------------------------------

def _color(v: float, positive_is_green: bool = True) -> str:
    if v > 0:
        return "#16a34a" if positive_is_green else "#dc2626"
    if v < 0:
        return "#dc2626" if positive_is_green else "#16a34a"
    return "#374151"


def _pct(v: float) -> str:
    return f"{v:+.1f}%"


def _jpy(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f}"


def _build_html(
    results: dict[str, BacktestResult],
    symbol_names: dict[str, str],
    params: BacktestParams,
    gen_at: str,
) -> str:
    param_str = (
        f"top_n={params.top_n}, bottom_m={params.bottom_m}, "
        f"slip={params.slippage_pct}%, weekly_filter={params.weekly_filter}"
    )

    # ── 銘柄別行 ──────────────────────────────────────────────────
    rows_html = ""
    all_trades: list[Trade] = []

    for symbol in results:
        trades = results[symbol].trades
        all_trades.extend(trades)
        name   = symbol_names.get(symbol, "")
        st     = raw_stats(trades)

        if st is None:
            rows_html += f"""
        <tr class="no-trade">
          <td><span class="code">{symbol}</span> {name}</td>
          <td class="num muted" colspan="6">トレードなし</td>
        </tr>"""
            continue

        win_c   = _color(st["win_rate"] - 50)
        pnl_c   = _color(st["total_pnl_jpy"])
        ppct_c  = _color(st["avg_pnl_pct"])
        dd_c    = "#dc2626" if st["max_dd_pct"] < -15 else "#374151"

        rows_html += f"""
        <tr>
          <td><span class="code">{symbol}</span> {name}</td>
          <td class="num">{st['n_trades']}</td>
          <td class="num" style="color:{win_c}">{st['win_rate']:.0f}%</td>
          <td class="num" style="color:{pnl_c}">{_jpy(st['total_pnl_jpy'])} 円</td>
          <td class="num" style="color:{ppct_c}">{_pct(st['avg_pnl_pct'])}</td>
          <td class="num">{st['avg_hold_days']:.0f} 日</td>
          <td class="num" style="color:{dd_c}">{st['max_dd_pct']:.1f}%</td>
        </tr>"""

    # ── 合算行 ──────────────────────────────────────────────────
    ag = _agg_stats(all_trades)
    if ag:
        total_win_c  = _color(ag["win_rate"] - 50)
        total_pnl_c  = _color(ag["total_pnl_jpy"])
        total_ppct_c = _color(ag["avg_pnl_pct"])
        agg_row = f"""
        <tr class="total-row">
          <td><strong>全 {len(results)} 銘柄 合算</strong></td>
          <td class="num"><strong>{ag['n_trades']}</strong></td>
          <td class="num" style="color:{total_win_c}"><strong>{ag['win_rate']:.0f}%</strong></td>
          <td class="num" style="color:{total_pnl_c}"><strong>{_jpy(ag['total_pnl_jpy'])} 円</strong></td>
          <td class="num" style="color:{total_ppct_c}"><strong>{_pct(ag['avg_pnl_pct'])}</strong></td>
          <td class="num"><strong>{ag['avg_hold_days']:.0f} 日</strong></td>
          <td class="num"><strong>{ag['max_dd_pct']:.1f}%</strong></td>
        </tr>"""
    else:
        agg_row = ""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MomentSwing Darvas — 9 銘柄横断サマリー</title>
<style>
:root {{
  --bg:          #f1f5f9;
  --card:        #ffffff;
  --border:      #e5e7eb;
  --border-lt:   #f3f4f6;
  --text:        #374151;
  --text-strong: #111827;
  --text-muted:  #6b7280;
  --text-xs:     #9ca3af;
  --green:       #16a34a;
  --red:         #dc2626;
  --hdr-bg:      #1e3a5f;
  --font:        Arial,'Helvetica Neue',Helvetica,sans-serif;
}}
*,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--bg); font-family:var(--font); font-size:13px; color:var(--text); line-height:1.5; }}
.wrap {{ max-width:900px; margin:0 auto; padding:24px 16px; }}
.page-hdr {{ background:var(--hdr-bg); color:#fff; padding:18px 24px 14px; border-radius:10px 10px 0 0; }}
.page-hdr h1 {{ font-size:17px; font-weight:500; letter-spacing:.02em; }}
.page-hdr .sub {{ font-size:11px; color:rgba(255,255,255,.6); margin-top:4px; letter-spacing:.03em; }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:0 0 8px 8px; overflow:hidden; }}
.card-hdr {{
  font-size:10px; font-weight:500; color:var(--text-muted);
  text-transform:uppercase; letter-spacing:.07em;
  padding:12px 16px 8px; border-bottom:1px solid var(--border-lt);
}}
.params {{ font-size:11px; color:var(--text-muted); padding:8px 16px 10px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
thead th {{
  font-size:10px; font-weight:500; color:var(--text-muted);
  text-transform:uppercase; letter-spacing:.06em;
  padding:8px 10px; border-bottom:2px solid var(--border);
  text-align:left; white-space:nowrap;
}}
thead th.num {{ text-align:right; }}
tbody tr {{ border-bottom:1px solid var(--border-lt); }}
tbody tr:last-child {{ border-bottom:none; }}
tbody td {{ padding:9px 10px; vertical-align:middle; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.muted {{ color:var(--text-muted); }}
.code {{
  display:inline-block; background:#f3f4f6; border-radius:3px;
  padding:1px 5px; font-size:11px; font-variant-numeric:tabular-nums;
  margin-right:4px; color:var(--text-muted);
}}
tr.total-row {{ background:#f9fafb; border-top:2px solid var(--border); }}
tr.no-trade td {{ color:var(--text-xs); font-style:italic; }}
.footer {{ text-align:center; font-size:11px; color:var(--text-xs); padding:14px; border-top:1px solid var(--border-lt); }}
</style>
</head>
<body>
<div class="wrap">
  <div class="page-hdr">
    <h1>MomentSwing バックテスト — 9 銘柄横断サマリー（Darvas Box ベースライン）</h1>
    <div class="sub">生成: {gen_at} &nbsp;／&nbsp; ※ 検証専用</div>
  </div>
  <div class="card">
    <div class="card-hdr">銘柄別成績</div>
    <div class="params">パラメータ: {param_str}</div>
    <table>
      <thead>
        <tr>
          <th>銘柄</th>
          <th class="num">トレード数</th>
          <th class="num">勝率</th>
          <th class="num">損益合計</th>
          <th class="num">平均損益率</th>
          <th class="num">平均保有日数</th>
          <th class="num">最大 DD</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
        {agg_row}
      </tbody>
    </table>
    <div class="footer">
      損切*: データ末尾でストップ発動（翌営業日約定不可）
    </div>
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def save_universe_html(
    results: dict[str, BacktestResult],
    symbol_names: dict[str, str],
    params: BacktestParams,
    out_dir: Optional[Path] = None,
) -> Path:
    """9 銘柄横断サマリー HTML を保存して、保存パスを返す。"""
    save_dir = out_dir or _RUNS_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    gen_at = datetime.now().strftime("%Y/%m/%d %H:%M")
    ts     = datetime.now().strftime("%Y%m%d_%H%M")
    path   = save_dir / f"universe_darvas_{ts}.html"
    html   = _build_html(results, symbol_names, params, gen_at)
    path.write_text(html, encoding="utf-8")
    return path
