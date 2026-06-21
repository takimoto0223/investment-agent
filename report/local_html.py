"""
report/local_html.py
ブラウザで開くローカルHTML版レポートを生成する。
メール用 template.py とは独立。CSS Grid/Flex 使用可。max-width: 900px。

単一ソース保証:
  us_positions_raw（EveningReportData に昇格）が資産配分ドーナツと
  MomentSwing_US マトリクスセルの両方に使われ、同一レポート内で銘柄が食い違わない。
"""
from __future__ import annotations

from .template import (
    EveningReportData, MorningReportData,
    _PALETTE,
    _donut_img, _risk_meter, _usdjpy_label, _fx_badge,
    _sector_bars, _margin_table, _position_table,
)


# ══════════════════════════════════════════════════════
# ページ骨格
# ══════════════════════════════════════════════════════

def _html_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;}}
body{{margin:0;padding:20px 12px;background:#f1f5f9;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     color:#111827;font-size:14px;line-height:1.5;}}
.page{{max-width:900px;margin:0 auto;}}
.sec{{background:#fff;border-radius:10px;padding:20px 24px;
      margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.07);}}
.sec-title{{font-size:10px;font-weight:700;color:#9ca3af;
            text-transform:uppercase;letter-spacing:.08em;margin-bottom:14px;}}
.row{{display:flex;gap:14px;}}
.half{{flex:1;min-width:0;}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
.cell{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;}}
.cell-jp{{background:#fffbeb;}}
.cell-hdr{{font-size:10px;font-weight:700;color:#6b7280;text-transform:uppercase;
           letter-spacing:.05em;margin-bottom:10px;}}
.badge{{display:inline-block;padding:1px 6px;border-radius:3px;
        font-size:10px;font-weight:700;}}
details{{margin-bottom:8px;}}
details summary{{cursor:pointer;font-size:13px;font-weight:600;color:#374151;
                 padding:8px 0;list-style:none;user-select:none;}}
details summary::before{{content:'▶ ';font-size:10px;color:#9ca3af;}}
details[open] summary::before{{content:'▼ ';}}
.divrow{{border-bottom:1px solid #f3f4f6;padding:5px 0;
         display:flex;justify-content:space-between;align-items:center;}}
</style>
</head>
<body>
<div class="page">
{body}
</div>
</body>
</html>"""


# ══════════════════════════════════════════════════════
# ① ヘッダーバー（絵文字なし・テキスト+ドット）
# ══════════════════════════════════════════════════════

def _sec_header(data: EveningReportData, is_morning: bool) -> str:
    ts = data.generated_at.strftime("%Y/%m/%d %H:%M")
    if is_morning:
        label = "日本市場 まもなく開場"
        dot_c = "#f59e0b"   # amber = 朝の東証
        rtype = "朝次レポート"
    else:
        label = "米国市場 まもなく開場"
        dot_c = "#3b82f6"   # blue = 夜のNYSE
        rtype = "夜間レポート"

    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:10px 0 14px;">'
        f'<div style="font-size:18px;font-weight:700;color:#111827;">'
        f'{rtype} '
        f'<span style="font-weight:400;color:#6b7280;font-size:13px;">{ts}</span></div>'
        f'<div style="display:flex;align-items:center;gap:7px;">'
        f'<span style="display:inline-block;width:9px;height:9px;background:{dot_c};'
        f'border-radius:50%;flex-shrink:0;"></span>'
        f'<span style="font-size:13px;font-weight:600;color:#374151;">{label}</span>'
        f'</div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════
# ② CxO方針メモ（リスクメーター内包）
# ══════════════════════════════════════════════════════

def _sec_cxo(data: EveningReportData) -> str:
    return (
        f'<div class="sec" style="border-left:4px solid #3b82f6;">'
        f'<div class="sec-title">今日の方針 — CxO方針メモ</div>'
        f'<div class="row" style="align-items:flex-start;gap:20px;">'
        f'<div style="min-width:155px;">{_risk_meter(data.risk_score)}</div>'
        f'<div style="flex:1;">'
        f'<div style="font-size:14px;color:#1e3a5f;line-height:1.75;font-weight:500;">'
        f'{data.cxo_memo}</div>'
        f'<div style="font-size:11px;color:#6b7280;margin-top:8px;">'
        f'{data.macro_notes}'
        f'&nbsp;&nbsp;|&nbsp;&nbsp;ローテーション: {data.rotation_signal}</div>'
        f'</div>'
        f'</div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════
# ③ 総資産 + 為替（横並び）
# ══════════════════════════════════════════════════════

def _sec_assets_fx(data: EveningReportData) -> str:
    chg_c = "#16a34a" if data.total_assets_change_pct >= 0 else "#dc2626"
    sign  = "+" if data.total_assets_change_pct >= 0 else ""

    assets_card = (
        f'<div class="sec half" style="padding:16px 20px;">'
        f'<div class="sec-title">総資産</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<div style="font-size:26px;font-weight:700;letter-spacing:-.02em;">'
        f'¥{data.total_assets_jpy:,.0f}</div>'
        f'<span style="background:{chg_c};color:white;padding:3px 12px;'
        f'border-radius:20px;font-size:13px;font-weight:700;">'
        f'{sign}{data.total_assets_change_pct:.2f}%</span>'
        f'</div>'
        f'</div>'
    )

    fx_card = (
        f'<div class="sec half" style="padding:16px 20px;">'
        f'<div class="sec-title">為替</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-size:20px;font-weight:700;">'
        f'USD/JPY {_usdjpy_label(data)}</span>'
        f'{_fx_badge(data.fx_signal)}'
        f'</div>'
        f'<div style="font-size:11px;color:#6b7280;margin-top:6px;">'
        f'{data.fx_rationale[:90]}</div>'
        f'</div>'
    )

    return f'<div class="row">{assets_card}{fx_card}</div>'


# ══════════════════════════════════════════════════════
# ④ 資産配分（通貨バー + 円グラフ×2）
# ══════════════════════════════════════════════════════

def _legend(holdings: list) -> str:
    total = sum(h.value_jpy for h in holdings if h.value_jpy > 0)
    rows  = []
    for i, h in enumerate(holdings):
        if h.value_jpy <= 0:
            continue
        c    = _PALETTE[i % len(_PALETTE)]
        pct  = h.value_jpy / total * 100 if total > 0 else 0
        cc   = "#16a34a" if h.change_pct >= 0 else "#dc2626"
        s    = "+" if h.change_pct >= 0 else ""
        rows.append(
            f'<div style="display:flex;gap:5px;align-items:center;margin:2px 0;">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'background:{c};border-radius:2px;flex-shrink:0;"></span>'
            f'<span style="font-size:11px;">{h.name}&nbsp;{pct:.1f}%</span>'
            f'<span style="font-size:10px;color:{cc};">({s}{h.change_pct:.1f}%)</span>'
            f'</div>'
        )
    return "".join(rows) if rows else '<span style="font-size:11px;color:#9ca3af;">なし</span>'


def _sec_allocation(data: EveningReportData) -> str:
    j = data.jpy_asset_ratio * 100
    u = data.usd_asset_ratio * 100

    currency_bar = (
        f'<div style="margin-bottom:16px;">'
        f'<div style="font-size:11px;color:#6b7280;margin-bottom:4px;">'
        f'通貨配分 '
        f'<span style="color:#9ca3af;font-size:10px;">'
        f'円 = JP株時価+kabu円余力 ｜ ドル = US株時価+Alpacaドル余力</span></div>'
        f'<div style="height:12px;border-radius:6px;overflow:hidden;'
        f'background:#e5e7eb;display:flex;">'
        f'<div style="width:{j:.1f}%;background:#3b82f6;"></div>'
        f'<div style="width:{u:.1f}%;background:#f97316;"></div>'
        f'</div>'
        f'<div style="font-size:11px;margin-top:3px;">'
        f'<span style="color:#3b82f6;font-weight:600;">■ 円 {j:.1f}%</span>'
        f'&nbsp;&nbsp;'
        f'<span style="color:#f97316;font-weight:600;">■ ドル {u:.1f}%</span>'
        f'</div>'
        f'</div>'
    )

    jp_items = [(h.name or h.symbol, h.value_jpy, _PALETTE[i % len(_PALETTE)])
                for i, h in enumerate(data.jp_holdings)]
    us_items = [(h.name or h.symbol, h.value_jpy, _PALETTE[i % len(_PALETTE)])
                for i, h in enumerate(data.us_holdings)]

    jp_donut = (
        f'<div style="flex:1;text-align:center;">'
        f'<div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:8px;">'
        f'日本株 '
        f'<span style="font-size:10px;color:#f59e0b;font-weight:400;">'
        f'⚠ kabu接続後に実値へ</span></div>'
        f'{_donut_img(jp_items, size=180)}'
        f'<div style="margin-top:8px;text-align:left;display:inline-block;">'
        f'{_legend(data.jp_holdings)}</div>'
        f'</div>'
    )

    us_donut = (
        f'<div style="flex:1;text-align:center;">'
        f'<div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:8px;">'
        f'米国株 '
        f'<span style="font-size:10px;color:#16a34a;font-weight:400;">'
        f'Alpaca実値</span></div>'
        f'{_donut_img(us_items, size=180)}'
        f'<div style="margin-top:8px;text-align:left;display:inline-block;">'
        f'{_legend(data.us_holdings)}</div>'
        f'</div>'
    )

    return (
        f'<div class="sec">'
        f'<div class="sec-title">資産配分</div>'
        f'{currency_bar}'
        f'<div class="row" style="margin-top:8px;">{jp_donut}{us_donut}</div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════
# ⑤ 2×2 戦略マトリクス
# ══════════════════════════════════════════════════════

_KABU_BADGE = (
    '<span class="badge" '
    'style="background:#fef3c7;color:#92400e;">kabu待ち</span>'
)
_REAL_BADGE = (
    '<span class="badge" '
    'style="background:#dcfce7;color:#166534;">実値</span>'
)


def _cell_jp_scalpday(data: EveningReportData) -> str:
    if isinstance(data, MorningReportData) and data.scalpday_candidates:
        rows = ""
        for c in data.scalpday_candidates[:5]:
            sc = "#16a34a" if c.signal == "buy" else "#dc2626"
            sig_txt = "BUY" if c.signal == "buy" else "SELL"
            rows += (
                f'<div class="divrow">'
                f'<div>'
                f'<span style="font-weight:600;">{c.symbol}</span>'
                f'&nbsp;<span style="font-size:11px;color:#6b7280;">{c.name}</span>'
                f'<div style="font-size:10px;color:#6b7280;">{c.rationale[:50]}</div>'
                f'</div>'
                f'<span class="badge" style="background:{sc};color:white;">'
                f'{sig_txt}</span>'
                f'</div>'
            )
        body = (
            f'<div style="font-size:11px;font-weight:600;margin-bottom:6px;">'
            f'本日候補 {len(data.scalpday_candidates)}件</div>{rows}'
        )
    elif isinstance(data, MorningReportData):
        body = '<div style="color:#9ca3af;font-size:12px;">候補なし</div>'
    else:
        body = '<div style="color:#9ca3af;font-size:12px;">日本市場は閉場</div>'

    return (
        body +
        f'<div style="font-size:11px;color:#6b7280;margin-top:8px;">'
        f'含み損益・確定P&amp;L: <span style="color:#b45309;">kabu接続待ち</span></div>'
    )


def _cell_us_scalpday(data: EveningReportData) -> str:
    if not isinstance(data, MorningReportData):
        return (
            '<div style="color:#9ca3af;font-size:12px;">'
            '米国市場 22:30 開場前<br>今夜のセッション待機中</div>'
        )

    net_c = "#16a34a" if data.daytrade_net_pl >= 0 else "#dc2626"
    net_s = "+" if data.daytrade_net_pl >= 0 else ""
    grs_s = "+" if data.daytrade_gross_pl >= 0 else ""
    fee_s = f'-${data.daytrade_fees:.4f}' if data.daytrade_fees > 0 else '$0'

    rows = ""
    for t in data.daytrade_records[:5]:
        tc  = "#16a34a" if t["net_pl"] >= 0 else "#dc2626"
        ts  = "+" if t["net_pl"] >= 0 else "-"
        rows += (
            f'<div class="divrow">'
            f'<div>'
            f'<span style="font-weight:600;">{t["symbol"]}</span>'
            f'<span style="font-size:10px;color:#6b7280;margin-left:6px;">'
            f'${t["buy_price"]:.0f}→${t["sell_price"]:.0f} ×{t["qty"]:.0f}</span>'
            f'</div>'
            f'<span style="color:{tc};font-size:12px;font-weight:600;">'
            f'{ts}${abs(t["net_pl"]):.2f}</span>'
            f'</div>'
        )
    if not rows:
        rows = '<div style="color:#9ca3af;font-size:12px;">約定なし（昨夜）</div>'

    return (
        f'<div style="font-size:24px;font-weight:700;color:{net_c};">'
        f'{net_s}${data.daytrade_net_pl:,.2f}</div>'
        f'<div style="font-size:10px;color:#6b7280;margin-bottom:10px;">'
        f'ネット | グロス {grs_s}${data.daytrade_gross_pl:,.2f} | 手数料 {fee_s}</div>'
        f'{rows}'
        f'<div style="font-size:10px;color:#9ca3af;margin-top:8px;">'
        f'含み損益: なし（当日決済前提）</div>'
    )


def _cell_jp_swing(data: EveningReportData) -> str:
    return (
        '<div style="color:#9ca3af;font-size:12px;margin-bottom:8px;">'
        'セッション未実行（kabu API 未接続）</div>'
        '<div style="font-size:11px;color:#6b7280;">'
        '保有ポジション・含み損益・確定P&amp;L: '
        '<span style="color:#b45309;">kabu接続待ち</span></div>'
    )


def _cell_us_swing(data: EveningReportData) -> str:
    rows = ""
    for pos in data.us_positions_raw:
        try:
            symbol = pos.get("symbol", "")
            qty    = float(pos.get("qty", 0))
            curr   = float(pos.get("current_price", 0))
            upl    = float(pos.get("unrealized_pl", 0))
            upl_c  = "#16a34a" if upl >= 0 else "#dc2626"
            upl_s  = "+" if upl >= 0 else "-"
            rows += (
                f'<div class="divrow">'
                f'<div>'
                f'<span style="font-weight:600;">{symbol}</span>'
                f'<span style="font-size:11px;color:#6b7280;margin-left:6px;">'
                f'{qty:.0f}株 @${curr:.2f}</span>'
                f'</div>'
                f'<span style="color:{upl_c};font-weight:600;">'
                f'{upl_s}${abs(upl):,.2f}</span>'
                f'</div>'
            )
        except Exception:
            continue

    if not rows:
        rows = '<div style="color:#9ca3af;font-size:12px;">保有なし</div>'

    decisions = ""
    if isinstance(data, MorningReportData) and data.swing_decisions:
        dec_rows = ""
        for v in data.swing_decisions[:3]:
            badge_c = "#16a34a" if v.action == "buy" else "#6b7280"
            badge_t = "買付" if v.action == "buy" else "見送"
            qty_txt = f" ×{v.qty:.0f}株" if getattr(v, "qty", 0) > 0 else ""
            dec_rows += (
                f'<div class="divrow">'
                f'<span class="badge" style="background:{badge_c};color:white;">'
                f'{badge_t}</span>'
                f'<span style="font-size:11px;font-weight:600;margin-left:6px;">'
                f'{getattr(v, "name", "") or getattr(v, "symbol", "")}{qty_txt}</span>'
                f'</div>'
            )
        decisions = (
            f'<div style="margin-top:10px;">'
            f'<div style="font-size:10px;color:#6b7280;margin-bottom:4px;">昨日の判断</div>'
            f'{dec_rows}'
            f'</div>'
        )

    return (
        rows + decisions +
        '<div style="font-size:10px;color:#9ca3af;margin-top:8px;">'
        '確定P&L: 未実装（スイング確定損益の追跡なし）</div>'
    )


def _sec_matrix(data: EveningReportData) -> str:
    is_m = isinstance(data, MorningReportData)
    us_badge = _REAL_BADGE if is_m else ""

    return (
        f'<div class="sec">'
        f'<div class="sec-title">戦略別サマリー 日米×戦略マトリクス</div>'
        f'<div class="grid2">'

        f'<div class="cell cell-jp">'
        f'<div class="cell-hdr">ScalpDay（スキャル）— 日本株 {_KABU_BADGE}</div>'
        f'{_cell_jp_scalpday(data)}'
        f'</div>'

        f'<div class="cell">'
        f'<div class="cell-hdr">ScalpDay（スキャル）— 米国株 {us_badge}</div>'
        f'{_cell_us_scalpday(data)}'
        f'</div>'

        f'<div class="cell cell-jp">'
        f'<div class="cell-hdr">MomentSwing（スイング）— 日本株 {_KABU_BADGE}</div>'
        f'{_cell_jp_swing(data)}'
        f'</div>'

        f'<div class="cell">'
        f'<div class="cell-hdr">MomentSwing（スイング）— 米国株 {_REAL_BADGE}</div>'
        f'{_cell_us_swing(data)}'
        f'</div>'

        f'</div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════
# ⑥ 深掘り（<details> 折りたたみ）
# ══════════════════════════════════════════════════════

def _sec_deep_dive(data: EveningReportData) -> str:
    return (
        f'<div class="sec">'
        f'<div class="sec-title">深掘り</div>'

        f'<details>'
        f'<summary>セクター分析</summary>'
        f'<div style="margin-top:12px;">{_sector_bars(data.sector_scores)}</div>'
        f'</details>'

        f'<details>'
        f'<summary>保有ポジション一覧</summary>'
        f'<div style="margin-top:12px;">{_position_table(data.all_positions)}</div>'
        f'</details>'

        f'<details>'
        f'<summary>信用建玉</summary>'
        f'<div style="margin-top:12px;">{_margin_table(data.margin_positions)}</div>'
        f'</details>'

        f'<details>'
        f'<summary>IntelScout ダイジェスト（連携予定）</summary>'
        f'<div style="margin-top:12px;color:#9ca3af;font-size:12px;">'
        f'logs/digests/ との連携は別タスク予定。</div>'
        f'</details>'

        f'</div>'
    )


# ══════════════════════════════════════════════════════
# 公開関数
# ══════════════════════════════════════════════════════

def build_morning_html_local(data: MorningReportData) -> str:
    ts = data.generated_at.strftime("%Y/%m/%d %H:%M")
    body = "\n".join([
        _sec_header(data, is_morning=True),
        _sec_cxo(data),
        _sec_assets_fx(data),
        _sec_allocation(data),
        _sec_matrix(data),
        _sec_deep_dive(data),
    ])
    return _html_page(f"朝次レポート {ts}", body)


def build_evening_html_local(data: EveningReportData) -> str:
    ts = data.generated_at.strftime("%Y/%m/%d %H:%M")
    body = "\n".join([
        _sec_header(data, is_morning=False),
        _sec_cxo(data),
        _sec_assets_fx(data),
        _sec_allocation(data),
        _sec_matrix(data),
        _sec_deep_dive(data),
    ])
    return _html_page(f"夜間レポート {ts}", body)
