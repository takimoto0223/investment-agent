"""
report/local_html.py
ブラウザで開くローカルHTML版レポートを生成する。
メール用 template.py とは独立。CSS Grid/Flex 使用可。max-width: 900px。

デザイン原則: 規律で品位を出す（引き算）
  - タイポグラフィ: 5サイズ(20/15/14/13/12px)、ウェイト2種(400/500)のみ
    15px = CxO方針メモ専用。24px = 数値強調（総資産・損益）
  - カラー: 無彩色ベース、意味のある色(損益プラス/マイナス・警告⚠)のみ
  - 罫線: 細罫線のみ、影なし、余白で品位を出す
  - 数値: font-variant-numeric: tabular-nums で桁揃え
  - uppercase: 廃止。letter-spacing のみで控えめな際立ちを出す

単一ソース保証:
  us_positions_raw（EveningReportData に昇格）が資産配分ドーナツと
  MomentSwing_US マトリクスセルの両方に使われる。
"""
from __future__ import annotations

from .template import (
    EveningReportData, MorningReportData,
    _PALETTE,
    _donut_img, _risk_meter, _usdjpy_label, _fx_badge,
    _sector_bars, _margin_table, _position_table,
)


# ══════════════════════════════════════════════════════
# ページ骨格 — CSS変数で一元管理
# ══════════════════════════════════════════════════════

def _html_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<style>
/* ─ デザイン変数 ─────────────────────────────────── */
:root {{
  /* タイポグラフィスケール */
  --sz-title: 20px;   /* レポートタイトル */
  --sz-memo:  15px;   /* CxO方針メモ本文（最重要） */
  --sz-body:  14px;   /* 通常本文 */
  --sz-sec:   13px;   /* セクション見出し・補足 */
  --sz-small: 12px;   /* 二次情報・注釈 */
  --sz-num:   24px;   /* 数値強調（総資産・損益） */
  /* ウェイト: 2種のみ */
  --w-n: 400;
  --w-m: 500;
  /* カラー: 無彩色ベース */
  --c-ink:      #1a1a1a;   /* 主テキスト */
  --c-mid:      #555555;   /* 副テキスト */
  --c-muted:    #888888;   /* ラベル・注釈 */
  --c-border:   #ececec;   /* 通常罫線 */
  --c-border-s: #d4d4d4;   /* 強調罫線（CxO） */
  --c-bg:       #f5f5f5;   /* ページ背景 */
  --c-card:     #ffffff;   /* カード背景 */
  --c-card-s:   #fafafa;   /* 薄グレーカード（CxO・JPセル） */
  /* 意味のある色: 損益・警告のみ */
  --c-pos:  #2d7a4e;   /* プラス損益 */
  --c-neg:  #b03030;   /* マイナス損益 */
  --c-warn: #9a6000;   /* 警告・⚠ */
}}

/* ─ リセット ─────────────────────────────────────── */
*,*::before,*::after {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 28px 16px;
  background: var(--c-bg);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', sans-serif;
  font-size: var(--sz-body); font-weight: var(--w-n);
  color: var(--c-ink); line-height: 1.65;
}}
.page {{ max-width: 900px; margin: 0 auto; }}

/* ─ カード ───────────────────────────────────────── */
.sec {{                              /* 通常セクション */
  background: var(--c-card);
  border: 1px solid var(--c-border);
  border-radius: 8px;
  padding: 24px 28px;
  margin-bottom: 16px;
}}
.sec-cxo {{                         /* CxO方針: 薄グレー背景+やや強い罫線+余白大 */
  background: var(--c-card-s);
  border: 1px solid var(--c-border-s);
  border-radius: 8px;
  padding: 32px 36px;
  margin-bottom: 16px;
}}

/* ─ セクション見出し ──────────────────────────────── */
.sec-title {{
  font-size: var(--sz-sec);
  font-weight: var(--w-m);
  color: var(--c-muted);
  letter-spacing: .06em;         /* uppercase は使わず字間で控えめに際立たせる */
  margin-bottom: 20px;
}}

/* ─ レイアウト ───────────────────────────────────── */
.row  {{ display: flex; gap: 16px; }}
.half {{ flex: 1; min-width: 0; }}
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}

/* ─ マトリクスセル ───────────────────────────────── */
.cell {{
  background: var(--c-card);
  border: 1px solid var(--c-border);
  border-radius: 6px;
  padding: 16px 18px;
}}
.cell-jp {{ background: var(--c-card-s); }}   /* JP: 薄グレーで区別（黄廃止） */
.cell-hdr {{
  font-size: var(--sz-sec);
  font-weight: var(--w-m);
  color: var(--c-muted);
  letter-spacing: .04em;
  margin-bottom: 12px;
}}

/* ─ 数値 ─────────────────────────────────────────── */
.num {{ font-variant-numeric: tabular-nums; }}

/* ─ 区切り行 ─────────────────────────────────────── */
.divrow {{
  border-bottom: 1px solid var(--c-border);
  padding: 6px 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}}

/* ─ バッジ ───────────────────────────────────────── */
.badge {{
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: var(--w-m);
}}

/* ─ 深掘り折りたたみ ─────────────────────────────── */
details {{ margin-bottom: 8px; }}
details summary {{
  cursor: pointer;
  font-size: var(--sz-body); font-weight: var(--w-m);
  color: var(--c-mid);
  padding: 8px 0;
  list-style: none;
  user-select: none;
}}
details summary::before {{ content: '▶ '; font-size: 10px; color: var(--c-muted); }}
details[open] summary::before {{ content: '▼ '; }}
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
        dot_c = "#f59e0b"   # amber = 朝の東証（意味のある色: 市場状態）
        rtype = "朝次レポート"
    else:
        label = "米国市場 まもなく開場"
        dot_c = "#60a5fa"   # blue = 夜のNYSE（意味のある色: 市場状態）
        rtype = "夜間レポート"

    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:10px 0 20px;">'
        f'<div style="font-size:var(--sz-title);font-weight:var(--w-m);color:var(--c-ink);">'
        f'{rtype} '
        f'<span style="font-size:var(--sz-sec);font-weight:var(--w-n);'
        f'color:var(--c-muted);">{ts}</span></div>'
        f'<div style="display:flex;align-items:center;gap:7px;">'
        f'<span style="display:inline-block;width:8px;height:8px;background:{dot_c};'
        f'border-radius:50%;flex-shrink:0;"></span>'
        f'<span style="font-size:var(--sz-sec);font-weight:var(--w-m);'
        f'color:var(--c-mid);">{label}</span>'
        f'</div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════
# ② CxO方針メモ（リスクメーター内包）
#    色ではなく余白・背景・サイズで最重要感を出す
# ══════════════════════════════════════════════════════

def _sec_cxo(data: EveningReportData) -> str:
    # マクロ注釈: `, ` を ` ・ ` に置換してコンパクトに区切る
    macro = data.macro_notes.replace(', ', ' ・ ')

    return (
        f'<div class="sec-cxo">'
        f'<div class="sec-title">今日の方針 — CxO方針メモ</div>'
        f'<div class="row" style="align-items:flex-start;gap:28px;">'
        f'<div style="min-width:155px;">{_risk_meter(data.risk_score)}</div>'
        f'<div style="flex:1;">'
        f'<p style="font-size:var(--sz-memo);font-weight:var(--w-m);'
        f'color:var(--c-ink);line-height:1.75;margin:0 0 14px;">'
        f'{data.cxo_memo}</p>'
        f'<p style="font-size:var(--sz-sec);font-weight:var(--w-n);'
        f'color:var(--c-muted);margin:0;">'
        f'{macro} ・ ローテーション: {data.rotation_signal}</p>'
        f'</div>'
        f'</div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════
# ③ 総資産 + 為替（横並び）
# ══════════════════════════════════════════════════════

def _sec_assets_fx(data: EveningReportData) -> str:
    chg_c = "var(--c-pos)" if data.total_assets_change_pct >= 0 else "var(--c-neg)"
    sign  = "+" if data.total_assets_change_pct >= 0 else ""

    _card = (
        "flex:1;min-width:0;background:var(--c-card);"
        "border:1px solid var(--c-border);border-radius:8px;padding:20px 24px;"
    )

    assets = (
        f'<div style="{_card}">'
        f'<div class="sec-title">総資産</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
        f'<div class="num" style="font-size:var(--sz-num);font-weight:var(--w-m);">'
        f'¥{data.total_assets_jpy:,.0f}</div>'
        f'<span class="num" style="font-size:var(--sz-sec);font-weight:var(--w-m);'
        f'color:{chg_c};">{sign}{data.total_assets_change_pct:.2f}%</span>'
        f'</div>'
        f'</div>'
    )

    fx = (
        f'<div style="{_card}">'
        f'<div class="sec-title">為替</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<span class="num" style="font-size:var(--sz-num);font-weight:var(--w-m);">'
        f'USD/JPY {_usdjpy_label(data)}</span>'
        f'{_fx_badge(data.fx_signal)}'
        f'</div>'
        f'<div style="font-size:var(--sz-sec);color:var(--c-muted);margin-top:8px;">'
        f'{data.fx_rationale[:90]}</div>'
        f'</div>'
    )

    return f'<div class="row" style="margin-bottom:16px;">{assets}{fx}</div>'


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
        cc   = "var(--c-pos)" if h.change_pct >= 0 else "var(--c-neg)"
        s    = "+" if h.change_pct >= 0 else ""
        rows.append(
            f'<div style="display:flex;gap:5px;align-items:center;margin:3px 0;">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'background:{c};border-radius:2px;flex-shrink:0;"></span>'
            f'<span style="font-size:var(--sz-small);color:var(--c-mid);">'
            f'{h.name}&nbsp;{pct:.1f}%</span>'
            f'<span class="num" style="font-size:var(--sz-small);color:{cc};">'
            f'({s}{h.change_pct:.1f}%)</span>'
            f'</div>'
        )
    return (
        "".join(rows) if rows else
        f'<span style="font-size:var(--sz-small);color:var(--c-muted);">なし</span>'
    )


def _sec_allocation(data: EveningReportData) -> str:
    j = data.jpy_asset_ratio * 100
    u = data.usd_asset_ratio * 100

    currency_bar = (
        f'<div style="margin-bottom:20px;">'
        f'<div style="font-size:var(--sz-sec);color:var(--c-muted);margin-bottom:6px;">'
        f'通貨配分'
        f'<span style="font-size:var(--sz-small);color:var(--c-muted);margin-left:8px;">'
        f'円 = JP株時価+kabu円余力 ｜ ドル = US株時価+Alpacaドル余力</span></div>'
        f'<div style="height:10px;border-radius:4px;overflow:hidden;'
        f'background:var(--c-border);display:flex;">'
        f'<div style="width:{j:.1f}%;background:#60a5fa;"></div>'
        f'<div style="width:{u:.1f}%;background:#f97316;"></div>'
        f'</div>'
        f'<div class="num" style="font-size:var(--sz-small);margin-top:5px;">'
        f'<span style="color:#60a5fa;font-weight:var(--w-m);">■ 円 {j:.1f}%</span>'
        f'&nbsp;&nbsp;'
        f'<span style="color:#f97316;font-weight:var(--w-m);">■ ドル {u:.1f}%</span>'
        f'</div>'
        f'</div>'
    )

    jp_items = [(h.name or h.symbol, h.value_jpy, _PALETTE[i % len(_PALETTE)])
                for i, h in enumerate(data.jp_holdings)]
    us_items = [(h.name or h.symbol, h.value_jpy, _PALETTE[i % len(_PALETTE)])
                for i, h in enumerate(data.us_holdings)]

    jp_donut = (
        f'<div style="flex:1;text-align:center;">'
        f'<div style="font-size:var(--sz-sec);font-weight:var(--w-m);'
        f'color:var(--c-mid);margin-bottom:8px;">'
        f'日本株 '
        f'<span style="font-size:var(--sz-small);font-weight:var(--w-n);'
        f'color:var(--c-warn);">⚠ kabu接続後に実値へ</span></div>'
        f'{_donut_img(jp_items, size=180)}'
        f'<div style="margin-top:10px;text-align:left;display:inline-block;">'
        f'{_legend(data.jp_holdings)}</div>'
        f'</div>'
    )

    us_donut = (
        f'<div style="flex:1;text-align:center;">'
        f'<div style="font-size:var(--sz-sec);font-weight:var(--w-m);'
        f'color:var(--c-mid);margin-bottom:8px;">'
        f'米国株 '
        f'<span style="font-size:var(--sz-small);font-weight:var(--w-n);'
        f'color:var(--c-muted);">Alpaca実値</span></div>'
        f'{_donut_img(us_items, size=180)}'
        f'<div style="margin-top:10px;text-align:left;display:inline-block;">'
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

# ステータスバッジ: 無彩色（kabu待ち・実値は色を持たせない）
_KABU_BADGE = (
    '<span class="badge" '
    'style="background:var(--c-border);color:var(--c-mid);">kabu待ち</span>'
)
_REAL_BADGE = (
    '<span class="badge" '
    'style="background:var(--c-border);color:var(--c-mid);">実値</span>'
)


def _cell_jp_scalpday(data: EveningReportData) -> str:
    if isinstance(data, MorningReportData) and data.scalpday_candidates:
        rows = ""
        for c in data.scalpday_candidates[:5]:
            # BUY/SELL は売買シグナルなので意味のある色を使う
            sc      = "var(--c-pos)" if c.signal == "buy" else "var(--c-neg)"
            sig_txt = "BUY" if c.signal == "buy" else "SELL"
            rows += (
                f'<div class="divrow">'
                f'<div>'
                f'<span style="font-weight:var(--w-m);">{c.symbol}</span>'
                f'&nbsp;<span style="font-size:var(--sz-small);color:var(--c-mid);">'
                f'{c.name}</span>'
                f'<div style="font-size:var(--sz-small);color:var(--c-muted);">'
                f'{c.rationale[:50]}</div>'
                f'</div>'
                f'<span class="badge" style="background:{sc};color:white;">'
                f'{sig_txt}</span>'
                f'</div>'
            )
        body = (
            f'<div style="font-size:var(--sz-sec);font-weight:var(--w-m);'
            f'margin-bottom:8px;">本日候補 {len(data.scalpday_candidates)}件</div>{rows}'
        )
    elif isinstance(data, MorningReportData):
        body = (
            f'<div style="font-size:var(--sz-small);color:var(--c-muted);">'
            f'候補なし</div>'
        )
    else:
        body = (
            f'<div style="font-size:var(--sz-small);color:var(--c-muted);">'
            f'日本市場は閉場</div>'
        )

    return (
        body +
        f'<div style="font-size:var(--sz-small);color:var(--c-muted);margin-top:10px;">'
        f'含み損益・確定P&amp;L: '
        f'<span style="color:var(--c-warn);">kabu接続待ち</span></div>'
    )


def _cell_us_scalpday(data: EveningReportData) -> str:
    if not isinstance(data, MorningReportData):
        return (
            f'<div style="font-size:var(--sz-small);color:var(--c-muted);">'
            f'米国市場 22:30 開場前<br>今夜のセッション待機中</div>'
        )

    net_c = "var(--c-pos)" if data.daytrade_net_pl >= 0 else "var(--c-neg)"
    net_s = "+" if data.daytrade_net_pl >= 0 else ""
    grs_s = "+" if data.daytrade_gross_pl >= 0 else ""
    fee_s = f'-${data.daytrade_fees:.4f}' if data.daytrade_fees > 0 else '$0'

    rows = ""
    for t in data.daytrade_records[:5]:
        tc  = "var(--c-pos)" if t["net_pl"] >= 0 else "var(--c-neg)"
        ts  = "+" if t["net_pl"] >= 0 else "-"
        rows += (
            f'<div class="divrow">'
            f'<div>'
            f'<span style="font-weight:var(--w-m);">{t["symbol"]}</span>'
            f'<span style="font-size:var(--sz-small);color:var(--c-muted);margin-left:6px;">'
            f'${t["buy_price"]:.0f}→${t["sell_price"]:.0f} ×{t["qty"]:.0f}</span>'
            f'</div>'
            f'<span class="num" style="color:{tc};font-size:var(--sz-sec);'
            f'font-weight:var(--w-m);">{ts}${abs(t["net_pl"]):.2f}</span>'
            f'</div>'
        )
    if not rows:
        rows = (
            f'<div style="font-size:var(--sz-small);color:var(--c-muted);">'
            f'約定なし（昨夜）</div>'
        )

    return (
        f'<div class="num" style="font-size:var(--sz-num);font-weight:var(--w-m);'
        f'color:{net_c};">{net_s}${data.daytrade_net_pl:,.2f}</div>'
        f'<div style="font-size:var(--sz-small);color:var(--c-muted);margin-bottom:12px;">'
        f'ネット ・ グロス {grs_s}${data.daytrade_gross_pl:,.2f} ・ 手数料 {fee_s}</div>'
        f'{rows}'
        f'<div style="font-size:var(--sz-small);color:var(--c-muted);margin-top:10px;">'
        f'含み損益: なし（当日決済前提）</div>'
    )


def _cell_jp_swing(data: EveningReportData) -> str:
    return (
        f'<div style="font-size:var(--sz-small);color:var(--c-muted);margin-bottom:10px;">'
        f'セッション未実行（kabu API 未接続）</div>'
        f'<div style="font-size:var(--sz-small);color:var(--c-muted);">'
        f'保有ポジション・含み損益・確定P&amp;L: '
        f'<span style="color:var(--c-warn);">kabu接続待ち</span></div>'
    )


def _cell_us_swing(data: EveningReportData) -> str:
    rows = ""
    for pos in data.us_positions_raw:
        try:
            symbol = pos.get("symbol", "")
            qty    = float(pos.get("qty", 0))
            curr   = float(pos.get("current_price", 0))
            upl    = float(pos.get("unrealized_pl", 0))
            upl_c  = "var(--c-pos)" if upl >= 0 else "var(--c-neg)"
            upl_s  = "+" if upl >= 0 else "-"
            rows += (
                f'<div class="divrow">'
                f'<div>'
                f'<span style="font-weight:var(--w-m);">{symbol}</span>'
                f'<span style="font-size:var(--sz-small);color:var(--c-muted);'
                f'margin-left:6px;">{qty:.0f}株 @${curr:.2f}</span>'
                f'</div>'
                f'<span class="num" style="color:{upl_c};font-weight:var(--w-m);">'
                f'{upl_s}${abs(upl):,.2f}</span>'
                f'</div>'
            )
        except Exception:
            continue

    if not rows:
        rows = (
            f'<div style="font-size:var(--sz-small);color:var(--c-muted);">保有なし</div>'
        )

    decisions = ""
    if isinstance(data, MorningReportData) and data.swing_decisions:
        dec_rows = ""
        for v in data.swing_decisions[:3]:
            # 買付/見送: 売買判断なので意味のある色
            badge_c = "var(--c-pos)" if v.action == "buy" else "var(--c-mid)"
            badge_t = "買付" if v.action == "buy" else "見送"
            qty_txt = f" ×{v.qty:.0f}株" if getattr(v, "qty", 0) > 0 else ""
            dec_rows += (
                f'<div class="divrow">'
                f'<span class="badge" style="background:{badge_c};color:white;">'
                f'{badge_t}</span>'
                f'<span style="font-size:var(--sz-small);font-weight:var(--w-m);'
                f'margin-left:6px;">'
                f'{getattr(v, "name", "") or getattr(v, "symbol", "")}{qty_txt}</span>'
                f'</div>'
            )
        decisions = (
            f'<div style="margin-top:12px;">'
            f'<div style="font-size:var(--sz-small);color:var(--c-muted);'
            f'margin-bottom:4px;">昨日の判断</div>'
            f'{dec_rows}'
            f'</div>'
        )

    return (
        rows + decisions +
        f'<div style="font-size:var(--sz-small);color:var(--c-muted);margin-top:10px;">'
        f'確定P&L: 未実装（スイング確定損益の追跡なし）</div>'
    )


def _sec_matrix(data: EveningReportData) -> str:
    is_m    = isinstance(data, MorningReportData)
    us_badge = _REAL_BADGE if is_m else ""

    return (
        f'<div class="sec">'
        f'<div class="sec-title">戦略別サマリー — 日米×戦略マトリクス</div>'
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
        f'<div style="margin-top:14px;">{_sector_bars(data.sector_scores)}</div>'
        f'</details>'

        f'<details>'
        f'<summary>保有ポジション一覧</summary>'
        f'<div style="margin-top:14px;">{_position_table(data.all_positions)}</div>'
        f'</details>'

        f'<details>'
        f'<summary>信用建玉</summary>'
        f'<div style="margin-top:14px;">{_margin_table(data.margin_positions)}</div>'
        f'</details>'

        f'<details>'
        f'<summary>IntelScout ダイジェスト（連携予定）</summary>'
        f'<div style="margin-top:14px;font-size:var(--sz-small);color:var(--c-muted);">'
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
