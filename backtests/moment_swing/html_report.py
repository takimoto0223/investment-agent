"""
backtests/moment_swing/html_report.py
MomentSwing バックテスト結果の検証用 HTML を生成する。

運用レポート（report/template.py）とは独立した検証専用ツール。
lightweight-charts を CDN から読み込みローソク足チャートを描画する。

デザインは既存レポートのシステムに合わせる:
  - CSS 変数: --green=#16a34a, --red=#dc2626, --border=#e5e7eb
  - フォント: Arial/'Helvetica Neue', weight 400/500
  - 損益のみ緑/赤、数値は tabular-nums
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from backtests.moment_swing.darvas_backtest import BacktestResult, _summarize

_RUNS_DIR = Path(__file__).parent.parent / "runs"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _jst(ts: pd.Timestamp) -> str:
    """UTC aware Timestamp → JST 日付文字列 (YYYY-MM-DD)。"""
    return ts.tz_convert("Asia/Tokyo").strftime("%Y-%m-%d")


def _sign(v: float) -> str:
    return "+" if v >= 0 else ""


# ---------------------------------------------------------------------------
# チャートデータ組み立て
# ---------------------------------------------------------------------------

def _build_chart_data(result: BacktestResult, df: pd.DataFrame) -> dict:
    """lightweight-charts に渡す JSON シリアライズ可能な dict を返す。"""
    trades = result.trades

    # OHLC（調整後・JST 日付）
    ohlc = []
    for _, row in df.iterrows():
        ohlc.append({
            "time": _jst(row["ts_utc"]),
            "open":  round(float(row["open"]),  1),
            "high":  round(float(row["high"]),  1),
            "low":   round(float(row["low"]),   1),
            "close": round(float(row["close"]), 1),
        })

    # エントリー/エグジット マーカー
    # Why: 約定は翌営業日始値のため、marker の time = 実際の約定日（始値ベース）を使う
    markers = []
    for t in trades:
        markers.append({
            "time":     _jst(t.entry_date),
            "position": "belowBar",
            "color":    "#16a34a",
            "shape":    "arrowUp",
            "text":     f"BUY {t.entry_price:,.0f}",
        })
        markers.append({
            "time":     _jst(t.exit_date),
            "position": "aboveBar",
            "color":    "#dc2626",
            "shape":    "arrowDown",
            "text":     f"SELL {t.exit_price:,.0f}",
        })
    markers.sort(key=lambda m: m["time"])

    # 箱の天井/床ライン（データ期間開始 → エントリー直前まで）
    first_date = _jst(df["ts_utc"].iloc[0])
    box_lines = []
    for t in trades:
        # 天井: データ開始からエントリー前日まで（どのレベルを狙っていたかを示す）
        box_lines.append({
            "label":  f"天井 {t.box_top:,.0f}",
            "color":  "#3b82f6",
            "dash":   [4, 3],
            "data": [
                {"time": first_date,        "value": t.box_top},
                {"time": _jst(t.entry_date), "value": t.box_top},
            ],
        })
        # 床（初期損切り水準）: データ開始からエントリー前日まで
        box_lines.append({
            "label":  f"床 {t.box_bottom:,.0f}",
            "color":  "#f97316",
            "dash":   [4, 3],
            "data": [
                {"time": first_date,        "value": t.box_bottom},
                {"time": _jst(t.entry_date), "value": t.box_bottom},
            ],
        })
        # トレイリングストップ: stop_history のステップ関数で描画
        # Why: 固定 box_bottom では実際の切り上がりが反映されない。
        # stop_history は [(date, level), ...] の時系列で、JavaScript 側で
        # lineType:1 (WithSteps) を使ってステップ関数として描く。
        if t.stop_history:
            stop_data = [{"time": _jst(ts), "value": lvl}
                         for ts, lvl in t.stop_history]
            # 最終点: エグジット日まで最後のストップ水準を延ばす
            stop_data.append({"time": _jst(t.exit_date), "value": stop_data[-1]["value"]})
            last_stop = stop_data[-1]["value"]
            label = (f"トレイリングストップ "
                     f"({len(t.stop_history)}点, 最終 {last_stop:,.1f})")
        else:
            stop_data = [
                {"time": _jst(t.entry_date), "value": t.box_bottom},
                {"time": _jst(t.exit_date),  "value": t.box_bottom},
            ]
            label = f"ストップ {t.box_bottom:,.0f}"
        box_lines.append({
            "label":  label,
            "color":  "#ef4444",
            "dash":   [2, 2],
            "steps":  True,
            "data":   stop_data,
        })

    # 保有期間をハイライトする背景バーデータ（per-candle histogram）
    # Why: value は常に 1 にしてオーバーレイスケール（priceScaleId:''）に載せる。
    # high * 1.5 を右スケールに載せると価格軸が狂うため。
    holding_bars = []
    if trades:
        entry_dates = {_jst(t.entry_date) for t in trades}
        exit_dates  = {_jst(t.exit_date)  for t in trades}
        in_pos = False
        for row in ohlc:
            d = row["time"]
            if d in entry_dates:
                in_pos = True
            if in_pos:
                holding_bars.append({"time": d, "color": "rgba(59,130,246,0.07)"})
            if d in exit_dates:
                in_pos = False

    return {
        "ohlc":         ohlc,
        "markers":      markers,
        "box_lines":    box_lines,
        "holding_bars": holding_bars,
    }


# ---------------------------------------------------------------------------
# HTML テンプレート
# ---------------------------------------------------------------------------

def _html(result: BacktestResult, df: pd.DataFrame, chart_data: dict) -> str:
    trades  = result.trades
    params  = result.params
    summary = _summarize(trades)
    symbol  = result.symbol
    period  = f"{_jst(df['ts_utc'].iloc[0])} 〜 {_jst(df['ts_utc'].iloc[-1])}"
    gen_at  = datetime.now().strftime("%Y/%m/%d %H:%M")

    # トレード行
    trade_rows = ""
    for i, t in enumerate(trades, 1):
        pl_c    = "#16a34a" if t.pnl_jpy >= 0 else "#dc2626"
        pl_sign = _sign(t.pnl_jpy)
        reason_map = {"stop": "損切", "stop_eod": "損切*", "eod": "末尾"}
        reason = reason_map.get(t.exit_reason, t.exit_reason)
        trade_rows += f"""
        <tr>
          <td class="num">{i}</td>
          <td>{_jst(t.entry_date)}</td>
          <td class="num">{t.entry_price:,.1f}</td>
          <td>{_jst(t.exit_date)}</td>
          <td class="num">{t.exit_price:,.1f}</td>
          <td class="num">{t.hold_days}</td>
          <td class="num" style="color:{pl_c}">{pl_sign}{t.pnl_pct:.2f}%</td>
          <td class="num" style="color:{pl_c}">{pl_sign}{t.pnl_jpy:,.0f}</td>
          <td>{reason}</td>
          <td class="num muted">{t.box_top:,.0f} / {t.box_bottom:,.0f}</td>
        </tr>"""

    # サマリーカード
    def _kv(k: str, v: str, color: str = "") -> str:
        style = f'style="color:{color}"' if color else ""
        return f'<div class="kv"><span class="kv-k">{k}</span><span class="kv-v" {style}>{v}</span></div>'

    summary_html = ""
    for k, v in summary.items():
        color = ""
        if "損益合計" in k:
            color = "#16a34a" if "+" in v else "#dc2626"
        elif "ドローダウン" in k:
            color = "#dc2626" if v.startswith("-") else "#6b7280"
        summary_html += _kv(k, v, color)

    # パラメータ表示
    param_str = (
        f"top_n={params.top_n}, bottom_m={params.bottom_m}, "
        f"slip={params.slippage_pct}%, comm={params.commission_pct}%, "
        f"weekly_filter={params.weekly_filter}"
    )

    chart_json = json.dumps(chart_data, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Backtest {symbol} — MomentSwing Darvas Box</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
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
  --blue:        #3b82f6;
  --hdr-bg:      #1e3a5f;
  --font:        Arial,'Helvetica Neue',Helvetica,sans-serif;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  font-family: var(--font);
  font-size: 13px;
  color: var(--text);
  line-height: 1.5;
}}
.wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px; }}
/* ── ヘッダー ── */
.page-hdr {{
  background: var(--hdr-bg);
  color: #fff;
  padding: 18px 24px 14px;
  border-radius: 10px 10px 0 0;
}}
.page-hdr h1 {{ font-size: 17px; font-weight: 500; letter-spacing: .02em; }}
.page-hdr .sub {{
  font-size: 11px; color: rgba(255,255,255,.6);
  margin-top: 4px; letter-spacing: .03em;
}}
/* ── カード ── */
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 16px;
}}
.card-hdr {{
  font-size: 10px; font-weight: 500;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .07em;
  padding: 12px 16px 8px;
  border-bottom: 1px solid var(--border-lt);
}}
.card-body {{ padding: 16px; }}
/* ── チャート ── */
#chart {{ width: 100%; height: 480px; }}
/* ── サマリー ── */
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1px;
  background: var(--border-lt);
  border: 1px solid var(--border-lt);
  border-radius: 6px;
  overflow: hidden;
}}
.kv {{
  background: var(--card);
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}}
.kv-k {{
  font-size: 10px; font-weight: 500;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .06em;
}}
.kv-v {{
  font-size: 18px; font-weight: 400;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}}
/* ── テーブル ── */
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
thead th {{
  font-size: 10px; font-weight: 500;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .06em;
  padding: 7px 8px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  white-space: nowrap;
}}
tbody tr {{ border-bottom: 1px solid var(--border-lt); }}
tbody tr:last-child {{ border-bottom: none; }}
tbody td {{ padding: 8px 8px; vertical-align: middle; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.muted {{ color: var(--text-muted); font-size: 11px; }}
/* ── 凡例 ── */
.legend {{
  display: flex; gap: 16px; flex-wrap: wrap;
  font-size: 11px; color: var(--text-muted);
  padding: 8px 16px 10px;
}}
.legend-item {{ display: flex; align-items: center; gap: 5px; }}
.legend-dot {{
  width: 10px; height: 3px; border-radius: 2px;
}}
/* ── パラメータ ── */
.params {{
  font-size: 11px; color: var(--text-muted);
  padding: 0 16px 10px;
}}
/* ── フッター ── */
.footer {{
  text-align: center; font-size: 11px;
  color: var(--text-xs); padding: 14px;
  border-top: 1px solid var(--border-lt);
}}
</style>
</head>
<body>
<div class="wrap">

  <div class="page-hdr">
    <h1>MomentSwing バックテスト — {symbol} &nbsp;Darvas Box ベースライン</h1>
    <div class="sub">
      データ期間: {period} &nbsp;／&nbsp;
      生成: {gen_at} &nbsp;／&nbsp; ※ 検証専用（運用レポートとは別）
    </div>
  </div>

  <!-- チャート -->
  <div class="card" style="border-radius:0 0 8px 8px; margin-bottom:16px;">
    <div class="card-hdr">価格チャート（調整後ローソク足・調整後 OHLCV）</div>
    <div class="params">
      パラメータ: {param_str}
    </div>
    <div class="legend">
      <div class="legend-item">
        <div class="legend-dot" style="background:#3b82f6;border-top:2px dashed #3b82f6;height:0;width:18px;"></div>
        天井ライン（箱の天井）
      </div>
      <div class="legend-item">
        <div class="legend-dot" style="background:#f97316;border-top:2px dashed #f97316;height:0;width:18px;"></div>
        床ライン（初期損切り）
      </div>
      <div class="legend-item">
        <div class="legend-dot" style="background:#ef4444;border-top:2px dashed #ef4444;height:0;width:18px;"></div>
        トレイリングストップ（段差=切り上がり点）
      </div>
      <div class="legend-item">▲ エントリー（翌日始値約定）</div>
      <div class="legend-item">▼ エグジット（翌日始値約定）</div>
    </div>
    <div id="chart"></div>
  </div>

  <!-- サマリー -->
  <div class="card">
    <div class="card-hdr">成績サマリー</div>
    <div class="card-body">
      <div class="summary-grid">
        {summary_html}
      </div>
    </div>
  </div>

  <!-- トレード履歴 -->
  <div class="card">
    <div class="card-hdr">トレード履歴</div>
    <div class="card-body" style="padding:0;">
      <table>
        <thead>
          <tr>
            <th class="num">#</th>
            <th>エントリー日</th>
            <th class="num">買値</th>
            <th>エグジット日</th>
            <th class="num">売値</th>
            <th class="num">保有日</th>
            <th class="num">損益率</th>
            <th class="num">損益（円）</th>
            <th>理由</th>
            <th class="num">天井/床</th>
          </tr>
        </thead>
        <tbody>
          {trade_rows if trade_rows else '<tr><td colspan="10" style="padding:16px;color:var(--text-xs);text-align:center;">トレードなし</td></tr>'}
        </tbody>
      </table>
    </div>
    <div class="footer">損切*: データ末尾でストップ発動（翌営業日約定不可）</div>
  </div>

</div>

<script>
(function() {{
  const DATA = {chart_json};

  const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
    width:  document.getElementById('chart').clientWidth,
    height: 480,
    layout: {{
      background: {{ type: 'solid', color: '#ffffff' }},
      textColor:  '#374151',
      fontSize:   11,
    }},
    grid: {{
      vertLines:  {{ color: '#f3f4f6' }},
      horzLines:  {{ color: '#f3f4f6' }},
    }},
    crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    rightPriceScale: {{ borderColor: '#e5e7eb' }},
    timeScale: {{
      borderColor:     '#e5e7eb',
      timeVisible:     true,
      secondsVisible:  false,
    }},
  }});

  // ── ローソク足 ──────────────────────────────────────────────
  const candles = chart.addCandlestickSeries({{
    upColor:        '#22c55e',
    downColor:      '#ef4444',
    borderUpColor:  '#16a34a',
    borderDownColor:'#dc2626',
    wickUpColor:    '#22c55e',
    wickDownColor:  '#ef4444',
  }});
  candles.setData(DATA.ohlc);

  // ── マーカー（エントリー・エグジット）─────────────────────
  candles.setMarkers(DATA.markers);

  // ── 保有期間ハイライト（overlay histogram）──────────────
  // Why: priceScaleId:'' = overlay scale でローソク足スケールに影響しない。
  // value:1 で固定し scaleMargins top:0/bottom:0 でチャート全高を塗る。
  if (DATA.holding_bars.length > 0) {{
    const hlBar = chart.addHistogramSeries({{
      priceScaleId: '',
      base: 0,
      scaleMargins: {{ top: 0, bottom: 0 }},
      priceLineVisible: false,
      lastValueVisible: false,
    }});
    hlBar.setData(DATA.holding_bars.map(function(d) {{
      return {{ time: d.time, value: 1, color: d.color }};
    }}));
  }}

  // ── 箱ラインを line series で描画 ───────────────────────────
  const dashMap = {{
    '4,3': LightweightCharts.LineStyle.Dashed,
    '2,2': LightweightCharts.LineStyle.Dotted,
  }};
  DATA.box_lines.forEach(function(bl) {{
    // steps:true のラインは WithSteps (1) で描くことで水平→段差のステップ関数になる
    const ls = chart.addLineSeries({{
      color:             bl.color,
      lineWidth:         bl.steps ? 1.5 : 1,
      lineStyle:         dashMap[bl.dash.join(',')] || LightweightCharts.LineStyle.Dashed,
      lineType:          bl.steps ? 1 : 0,
      priceLineVisible:  false,
      lastValueVisible:  bl.steps,
      crosshairMarkerVisible: false,
    }});
    ls.setData(bl.data);
  }});

  // リサイズ対応
  window.addEventListener('resize', function() {{
    chart.applyOptions({{ width: document.getElementById('chart').clientWidth }});
  }});
}})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def save_html(
    result: BacktestResult,
    df: pd.DataFrame,
    out_dir: Optional[Path] = None,
) -> Path:
    """バックテスト結果を HTML に書き出し、保存パスを返す。"""
    save_dir = out_dir or _RUNS_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = save_dir / f"{result.symbol}_darvas_{ts}.html"
    chart_data = _build_chart_data(result, df)
    html = _html(result, df, chart_data)
    path.write_text(html, encoding="utf-8")
    return path
