"""
report/template.py
朝晩レポートのHTMLを生成する。
メールクライアントで正しく表示されるようインラインCSSで記述。
SVGによる円グラフ・テーブルベースのレイアウトを使用（CSS Grid/Flex不使用）。
"""
import math
from dataclasses import dataclass, field
from datetime import datetime


# ── カラーパレット ──────────────────────────────────────────────
_PALETTE = [
    "#3b82f6", "#06b6d4", "#6366f1", "#8b5cf6", "#ec4899",
    "#f97316", "#eab308", "#22c55e", "#14b8a6", "#f43f5e",
]

_RISK_COLORS = {1: "#22c55e", 2: "#86efac", 3: "#facc15", 4: "#fb923c", 5: "#ef4444"}
_RISK_LABELS = {1: "LOW", 2: "LOW-MED", 3: "MEDIUM", 4: "MED-HIGH", 5: "HIGH"}


# ── データクラス ────────────────────────────────────────────────

@dataclass
class HoldingItem:
    """保有銘柄1件。"""
    symbol: str
    name: str
    value_jpy: float        # JPY換算保有額
    pf_pct: float           # PF比 (0.0 ~ 1.0)
    change_pct: float       # 前日比%
    sector: str = ""


@dataclass
class MarginPosition:
    """信用建玉1件。"""
    symbol: str
    name: str
    side: str               # "buy" | "sell"
    qty: float
    entry_price: float
    current_price: float
    pl_jpy: float           # 含み損益（円）


@dataclass
class SectorScore:
    """セクタースコア1件。"""
    name: str
    score: float            # 0.0 ~ 1.0
    change: float           # 前日比スコア変化
    change_pct: float       # 騰落率%


@dataclass
class DaytradeCandidate:
    """デイトレ候補銘柄1件。"""
    symbol: str
    name: str
    signal: str             # "buy" | "sell"
    rationale: str


@dataclass
class DiscussionItem:
    """議論結果1件。"""
    verdict: str            # "execute" | "defer" | "reject"
    score: float
    title: str
    summary: str = ""


@dataclass
class EveningReportData:
    """夜間（21:00）レポート用データ。"""
    generated_at: datetime
    total_assets_jpy: float
    total_assets_change_pct: float
    jp_holdings: list = field(default_factory=list)     # list[HoldingItem]
    us_holdings: list = field(default_factory=list)     # list[HoldingItem]
    risk_score: int = 3                                 # 1-5
    risk_level: str = "medium"
    jpy_asset_ratio: float = 0.5                        # 総資産ベース円比率
    usd_asset_ratio: float = 0.5                        # 総資産ベースドル比率
    jpy_cash_ratio: float = 0.5                         # 現金のみ円比率
    usd_cash_ratio: float = 0.5                         # 現金のみドル比率
    fx_signal: str = "中立"                             # "円買い" | "中立" | "ドル買い"
    fx_rationale: str = ""
    usdjpy_rate: float = 155.0
    margin_positions: list = field(default_factory=list)  # list[MarginPosition]
    sector_scores: list = field(default_factory=list)     # list[SectorScore]
    all_positions: list = field(default_factory=list)     # list[HoldingItem]（JP+US合算）
    pre_us_fx_signal: str = ""
    pre_us_fx_rationale: str = ""
    cxo_memo: str = "通常運転。"
    macro_notes: str = ""
    rotation_signal: str = "維持"


@dataclass
class MorningReportData(EveningReportData):
    """朝次（06:00）レポート用データ（夜間データを継承）。"""
    us_realized_pl_usd: float = 0.0     # 米国株昨夜確定損益（USD）
    us_realized_pl_jpy: float = 0.0     # 米国株昨夜確定損益（JPY換算）
    us_trade_count: int = 0
    daytrade_candidates: list = field(default_factory=list)  # list[DaytradeCandidate]
    overnight_fx_summary: str = ""
    overnight_fx_high: float = 0.0
    overnight_fx_low: float = 0.0
    overnight_fx_change_pct: float = 0.0
    discussion_items: list = field(default_factory=list)     # list[DiscussionItem]
    discussion_session_date: str = ""


# ── SVGドーナツチャート ─────────────────────────────────────────

def _donut_svg(items: list, size: int = 140) -> str:
    """
    ドーナツ型円グラフをSVGで生成する。
    items: [(label, value, color), ...]
    """
    valid = [(l, v, c) for l, v, c in items if v > 0]
    cx = cy = size / 2
    r  = size * 0.42
    ri = r * 0.55

    if not valid:
        return (
            f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="#e5e7eb"/>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{ri:.1f}" fill="white"/>'
            f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" '
            f'fill="#9ca3af" font-size="11" font-family="Arial">なし</text>'
            f'</svg>'
        )

    if len(valid) == 1:
        _, _, color = valid[0]
        return (
            f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{color}"/>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{ri:.1f}" fill="white"/>'
            f'</svg>'
        )

    total  = sum(v for _, v, _ in valid)
    angle  = -math.pi / 2  # 12時から開始
    paths  = []
    for _, value, color in valid:
        sweep = value / total * 2 * math.pi
        end   = angle + sweep
        large = 1 if sweep > math.pi else 0
        x1  = cx + r  * math.cos(angle);  y1  = cy + r  * math.sin(angle)
        x2  = cx + r  * math.cos(end);    y2  = cy + r  * math.sin(end)
        xi1 = cx + ri * math.cos(end);    yi1 = cy + ri * math.sin(end)
        xi2 = cx + ri * math.cos(angle);  yi2 = cy + ri * math.sin(angle)
        d = (
            f"M {x1:.1f} {y1:.1f} "
            f"A {r:.1f} {r:.1f} 0 {large} 1 {x2:.1f} {y2:.1f} "
            f"L {xi1:.1f} {yi1:.1f} "
            f"A {ri:.1f} {ri:.1f} 0 {large} 0 {xi2:.1f} {yi2:.1f} Z"
        )
        paths.append(f'<path d="{d}" fill="{color}" stroke="white" stroke-width="1.5"/>')
        angle = end

    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        + "".join(paths)
        + "</svg>"
    )


def _donut_legend(items: list) -> str:
    """ドーナツチャートの凡例テーブルを生成する。"""
    total = sum(v for _, v, _ in items if v > 0)
    rows  = []
    for label, value, color in items:
        if value <= 0:
            continue
        pct = f"{value / total * 100:.1f}%" if total > 0 else "0%"
        rows.append(
            f'<tr>'
            f'<td style="padding:2px 5px 2px 0;vertical-align:middle;">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'background:{color};border-radius:2px;"></span></td>'
            f'<td style="padding:2px 0;font-size:11px;color:#374151;'
            f'white-space:nowrap;">{label}&nbsp;{pct}</td>'
            f'</tr>'
        )
    return f'<table cellpadding="0" cellspacing="0">{"".join(rows)}</table>'


# ── リスクメーター（5段階） ──────────────────────────────────────

def _risk_meter(score: int) -> str:
    score = max(1, min(5, score))
    segs  = []
    for i in range(1, 6):
        bg = _RISK_COLORS[i] if i <= score else "#e5e7eb"
        segs.append(
            f'<td style="width:38px;height:20px;background:{bg};'
            f'border-right:2px solid white;"></td>'
        )
    label = _RISK_LABELS[score]
    color = _RISK_COLORS[score]
    return (
        f'<table cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;border-radius:4px;overflow:hidden;">'
        f'<tr>{"".join(segs)}</tr></table>'
        f'<div style="font-size:12px;font-weight:bold;color:{color};margin-top:5px;">'
        f'レベル {score}/5 &nbsp;{label}</div>'
    )


# ── 円ドル割合バー（2段） ──────────────────────────────────────

def _currency_bar(jpy_ratio: float, usd_ratio: float, label: str) -> str:
    j = max(0.0, min(100.0, jpy_ratio * 100))
    u = max(0.0, min(100.0, usd_ratio * 100))
    return (
        f'<div style="margin-bottom:10px;">'
        f'<div style="font-size:11px;color:#6b7280;margin-bottom:3px;">{label}</div>'
        f'<table cellpadding="0" cellspacing="0" width="100%" '
        f'style="border-radius:4px;overflow:hidden;">'
        f'<tr>'
        f'<td style="width:{j:.1f}%;height:16px;background:#3b82f6;"></td>'
        f'<td style="width:{u:.1f}%;height:16px;background:#f97316;"></td>'
        f'</tr></table>'
        f'<div style="font-size:11px;color:#374151;margin-top:3px;">'
        f'<span style="color:#3b82f6;">■ 円&nbsp;{j:.1f}%</span>'
        f'&nbsp;&nbsp;<span style="color:#f97316;">■ ドル&nbsp;{u:.1f}%</span>'
        f'</div></div>'
    )


# ── 為替エージェント判定バッジ ───────────────────────────────────

def _fx_badge(signal: str) -> str:
    palette = {
        "ドル買い": ("#ea580c", "white"),
        "中立":     ("#6b7280", "white"),
        "円買い":   ("#2563eb", "white"),
    }
    bg, fg = palette.get(signal, ("#6b7280", "white"))
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'padding:4px 14px;border-radius:12px;font-size:13px;font-weight:bold;'
        f'letter-spacing:0.03em;">{signal}</span>'
    )


# ── セクター別スコアバー ──────────────────────────────────────

def _sector_bars(sector_scores: list) -> str:
    if not sector_scores:
        return '<p style="color:#9ca3af;font-size:13px;margin:0;">データなし</p>'
    ss = sorted(sector_scores, key=lambda s: s.score, reverse=True)
    rows = []
    for s in ss:
        pct   = s.score * 100
        bar_c = "#3b82f6" if s.score >= 0.7 else "#93c5fd" if s.score >= 0.5 else "#dbeafe"
        ch_c  = "#16a34a" if s.change >= 0 else "#dc2626"
        csign = "+" if s.change >= 0 else ""
        psign = "+" if s.change_pct >= 0 else ""
        rows.append(
            f'<tr style="border-bottom:1px solid #f3f4f6;">'
            f'<td style="padding:6px 8px 6px 0;font-size:12px;color:#374151;'
            f'white-space:nowrap;min-width:120px;">{s.name}</td>'
            f'<td style="padding:6px 8px;">'
            f'<div style="background:#e5e7eb;height:12px;border-radius:6px;'
            f'width:140px;overflow:hidden;">'
            f'<div style="background:{bar_c};width:{pct:.1f}%;height:100%;'
            f'border-radius:6px;"></div></div></td>'
            f'<td style="padding:6px 0 6px 8px;font-size:12px;font-weight:bold;'
            f'color:#111827;white-space:nowrap;">{s.score:.2f}</td>'
            f'<td style="padding:6px 0 6px 8px;font-size:12px;color:{ch_c};'
            f'white-space:nowrap;">{csign}{s.change:.2f}</td>'
            f'<td style="padding:6px 0 6px 8px;font-size:12px;color:{ch_c};'
            f'white-space:nowrap;">{psign}{s.change_pct:.1f}%</td>'
            f'</tr>'
        )
    return (
        f'<table cellpadding="0" cellspacing="0" width="100%">'
        f'<tr style="background:#f9fafb;">'
        f'<th style="padding:5px 8px 5px 0;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">セクター</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">スコアバー</th>'
        f'<th style="padding:5px 0 5px 8px;font-size:11px;color:#6b7280;font-weight:normal;">値</th>'
        f'<th style="padding:5px 0 5px 8px;font-size:11px;color:#6b7280;font-weight:normal;">変化</th>'
        f'<th style="padding:5px 0 5px 8px;font-size:11px;color:#6b7280;font-weight:normal;">騰落率</th>'
        f'</tr>'
        + "".join(rows)
        + "</table>"
    )


# ── 保有ポジション一覧テーブル ──────────────────────────────────

def _position_table(positions: list) -> str:
    if not positions:
        return '<p style="color:#9ca3af;font-size:13px;margin:0;">保有なし</p>'
    rows = []
    for h in positions:
        cc   = "#16a34a" if h.change_pct >= 0 else "#dc2626"
        sign = "+" if h.change_pct >= 0 else ""
        rows.append(
            f'<tr style="border-bottom:1px solid #f3f4f6;">'
            f'<td style="padding:7px 8px 7px 0;font-size:12px;font-weight:bold;'
            f'color:#111827;">{h.symbol}</td>'
            f'<td style="padding:7px 8px;font-size:12px;color:#374151;">{h.name}</td>'
            f'<td style="padding:7px 8px;font-size:12px;color:#374151;text-align:right;">'
            f'¥{h.value_jpy:,.0f}</td>'
            f'<td style="padding:7px 8px;font-size:12px;color:#374151;text-align:right;">'
            f'{h.pf_pct * 100:.1f}%</td>'
            f'<td style="padding:7px 0;font-size:12px;color:{cc};text-align:right;">'
            f'{sign}{h.change_pct:.2f}%</td>'
            f'</tr>'
        )
    return (
        f'<table cellpadding="0" cellspacing="0" width="100%">'
        f'<tr style="background:#f9fafb;">'
        f'<th style="padding:5px 8px 5px 0;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">銘柄</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">名称</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:right;font-weight:normal;">保有額</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:right;font-weight:normal;">PF比</th>'
        f'<th style="padding:5px 0;font-size:11px;color:#6b7280;'
        f'text-align:right;font-weight:normal;">前日比</th>'
        f'</tr>'
        + "".join(rows)
        + "</table>"
    )


# ── 信用建玉テーブル ──────────────────────────────────────────

def _margin_table(positions: list) -> str:
    if not positions:
        return '<p style="color:#9ca3af;font-size:13px;margin:0;">信用建玉なし</p>'
    rows = []
    for p in positions:
        pl_c  = "#16a34a" if p.pl_jpy >= 0 else "#dc2626"
        pl_s  = "+" if p.pl_jpy >= 0 else ""
        side_label = "買" if p.side == "buy" else "売"
        side_color = "#2563eb" if p.side == "buy" else "#dc2626"
        rows.append(
            f'<tr style="border-bottom:1px solid #f3f4f6;">'
            f'<td style="padding:7px 8px 7px 0;font-size:12px;font-weight:bold;'
            f'color:#111827;">{p.symbol}</td>'
            f'<td style="padding:7px 8px;font-size:12px;color:#374151;">{p.name}</td>'
            f'<td style="padding:7px 8px;text-align:center;">'
            f'<span style="background:{side_color};color:white;padding:2px 8px;'
            f'border-radius:3px;font-size:11px;">{side_label}</span></td>'
            f'<td style="padding:7px 8px;font-size:12px;text-align:right;">'
            f'{int(p.qty)}</td>'
            f'<td style="padding:7px 8px;font-size:12px;text-align:right;">'
            f'¥{p.entry_price:,.0f}</td>'
            f'<td style="padding:7px 8px;font-size:12px;text-align:right;">'
            f'¥{p.current_price:,.0f}</td>'
            f'<td style="padding:7px 0;font-size:12px;text-align:right;color:{pl_c};">'
            f'{pl_s}¥{p.pl_jpy:,.0f}</td>'
            f'</tr>'
        )
    return (
        f'<table cellpadding="0" cellspacing="0" width="100%">'
        f'<tr style="background:#f9fafb;">'
        f'<th style="padding:5px 8px 5px 0;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">銘柄</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">名称</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;font-weight:normal;">方向</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:right;font-weight:normal;">数量</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:right;font-weight:normal;">建値</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:right;font-weight:normal;">現在値</th>'
        f'<th style="padding:5px 0;font-size:11px;color:#6b7280;'
        f'text-align:right;font-weight:normal;">損益</th>'
        f'</tr>'
        + "".join(rows)
        + "</table>"
    )


# ── デイトレ候補テーブル ──────────────────────────────────────

def _daytrade_table(candidates: list) -> str:
    if not candidates:
        return '<p style="color:#9ca3af;font-size:13px;margin:0;">候補なし</p>'
    rows = []
    for c in candidates:
        sc = "#16a34a" if c.signal == "buy" else "#dc2626"
        sl = "BUY" if c.signal == "buy" else "SELL"
        rows.append(
            f'<tr style="border-bottom:1px solid #f3f4f6;">'
            f'<td style="padding:7px 8px 7px 0;font-size:12px;font-weight:bold;'
            f'color:#111827;">{c.symbol}</td>'
            f'<td style="padding:7px 8px;font-size:12px;color:#374151;">{c.name}</td>'
            f'<td style="padding:7px 8px;text-align:center;">'
            f'<span style="background:{sc};color:white;padding:2px 8px;'
            f'border-radius:3px;font-size:11px;">{sl}</span></td>'
            f'<td style="padding:7px 0;font-size:12px;color:#6b7280;">'
            f'{c.rationale[:60]}</td>'
            f'</tr>'
        )
    return (
        f'<table cellpadding="0" cellspacing="0" width="100%">'
        f'<tr style="background:#f9fafb;">'
        f'<th style="padding:5px 8px 5px 0;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">銘柄</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">名称</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;font-weight:normal;">シグナル</th>'
        f'<th style="padding:5px 0;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">根拠</th>'
        f'</tr>'
        + "".join(rows)
        + "</table>"
    )


# ── 議論サマリーセクション ──────────────────────────────────────

def _discussion_section(items: list, session_date: str) -> str:
    if not items:
        return '<p style="color:#9ca3af;font-size:13px;margin:0;">議論ログなし</p>'
    verdict_styles = {
        "execute": ("#16a34a", "white", "実行"),
        "defer":   ("#d97706", "white", "保留"),
        "reject":  ("#dc2626", "white", "却下"),
    }
    rows = []
    for item in items:
        bg, fg, label = verdict_styles.get(item.verdict, ("#6b7280", "white", item.verdict))
        rows.append(
            f'<tr style="border-bottom:1px solid #f3f4f6;">'
            f'<td style="padding:7px 8px 7px 0;white-space:nowrap;">'
            f'<span style="background:{bg};color:{fg};padding:2px 8px;'
            f'border-radius:3px;font-size:11px;">{label}</span></td>'
            f'<td style="padding:7px 8px;font-size:11px;color:#6b7280;'
            f'white-space:nowrap;">{item.score:.2f}</td>'
            f'<td style="padding:7px 0;font-size:12px;color:#374151;">{item.title}</td>'
            f'</tr>'
        )
    date_label = f"（{session_date}）" if session_date else ""
    return (
        f'<div style="font-size:11px;color:#9ca3af;margin-bottom:8px;">'
        f'最新セッション{date_label}</div>'
        f'<table cellpadding="0" cellspacing="0" width="100%">'
        f'<tr style="background:#f9fafb;">'
        f'<th style="padding:5px 8px 5px 0;font-size:11px;color:#6b7280;font-weight:normal;">判定</th>'
        f'<th style="padding:5px 8px;font-size:11px;color:#6b7280;font-weight:normal;">スコア</th>'
        f'<th style="padding:5px 0;font-size:11px;color:#6b7280;'
        f'text-align:left;font-weight:normal;">タイトル</th>'
        f'</tr>'
        + "".join(rows)
        + "</table>"
    )


# ── カードラッパー ──────────────────────────────────────────────

def _card(title: str, content: str) -> str:
    """セクションをカード形式の <tr> として返す（メイン外側テーブルに挿入）。"""
    return (
        f'<tr><td style="padding:18px 24px;border-bottom:1px solid #f3f4f6;">'
        f'<div style="font-size:10px;font-weight:bold;color:#9ca3af;'
        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;">'
        f'{title}</div>'
        f'{content}'
        f'</td></tr>'
    )


# ── HTMLラッパー ────────────────────────────────────────────────

def _html_wrap(title: str, header_color: str, body_rows: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;
font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#f1f5f9;padding:24px 0;">
  <tr><td align="center">
  <table width="600" cellpadding="0" cellspacing="0"
         style="background:#ffffff;border-radius:12px;overflow:hidden;
                max-width:600px;box-shadow:0 2px 12px rgba(0,0,0,0.10);">

    <tr><td style="background:{header_color};padding:20px 24px;">
      <div style="color:white;font-size:18px;font-weight:bold;
                  letter-spacing:0.02em;">{title}</div>
    </td></tr>

    {body_rows}

    <tr><td style="background:#f8fafc;padding:14px 24px;text-align:center;">
      <div style="font-size:11px;color:#9ca3af;">
        このメールは投資管理システムが自動生成しました。投資は自己責任でお願いします。
      </div>
    </td></tr>

  </table>
  </td></tr>
</table>
</body>
</html>"""


# ── 夜・朝共通セクション ──────────────────────────────────────

def _build_base_rows(data: EveningReportData) -> list:
    rows = []

    # 総資産（前日比%バッジ右付け）
    cc   = "#16a34a" if data.total_assets_change_pct >= 0 else "#dc2626"
    sign = "+" if data.total_assets_change_pct >= 0 else ""
    rows.append(_card("総資産", (
        f'<table cellpadding="0" cellspacing="0" width="100%"><tr>'
        f'<td><div style="font-size:28px;font-weight:bold;color:#111827;'
        f'letter-spacing:-0.02em;">¥{data.total_assets_jpy:,.0f}</div></td>'
        f'<td align="right" style="vertical-align:middle;">'
        f'<span style="display:inline-block;background:{cc};color:white;'
        f'padding:4px 14px;border-radius:20px;font-size:13px;font-weight:bold;">'
        f'{sign}{data.total_assets_change_pct:.2f}%</span></td>'
        f'</tr></table>'
    )))

    # 日本株・米国株 円グラフ（銘柄ごとの割合・前日比%）
    jp_items = [
        (h.name or h.symbol, h.value_jpy, _PALETTE[i % len(_PALETTE)])
        for i, h in enumerate(data.jp_holdings)
    ]
    us_items = [
        (h.name or h.symbol, h.value_jpy, _PALETTE[i % len(_PALETTE)])
        for i, h in enumerate(data.us_holdings)
    ]
    # 前日比ラベルを凡例に追加するため、legendを拡張
    jp_legend_rows = []
    for i, h in enumerate(data.jp_holdings):
        c    = _PALETTE[i % len(_PALETTE)]
        ch_c = "#16a34a" if h.change_pct >= 0 else "#dc2626"
        s    = "+" if h.change_pct >= 0 else ""
        total_jp = sum(x.value_jpy for x in data.jp_holdings if x.value_jpy > 0)
        pct  = f"{h.value_jpy / total_jp * 100:.1f}%" if total_jp > 0 else "0%"
        jp_legend_rows.append(
            f'<tr><td style="padding:2px 5px 2px 0;vertical-align:middle;">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'background:{c};border-radius:2px;"></span></td>'
            f'<td style="padding:2px 0;font-size:11px;color:#374151;white-space:nowrap;">'
            f'{h.name}&nbsp;{pct}&nbsp;'
            f'<span style="color:{ch_c};">({s}{h.change_pct:.1f}%)</span></td></tr>'
        )
    us_legend_rows = []
    for i, h in enumerate(data.us_holdings):
        c    = _PALETTE[i % len(_PALETTE)]
        ch_c = "#16a34a" if h.change_pct >= 0 else "#dc2626"
        s    = "+" if h.change_pct >= 0 else ""
        total_us = sum(x.value_jpy for x in data.us_holdings if x.value_jpy > 0)
        pct  = f"{h.value_jpy / total_us * 100:.1f}%" if total_us > 0 else "0%"
        us_legend_rows.append(
            f'<tr><td style="padding:2px 5px 2px 0;vertical-align:middle;">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'background:{c};border-radius:2px;"></span></td>'
            f'<td style="padding:2px 0;font-size:11px;color:#374151;white-space:nowrap;">'
            f'{h.name}&nbsp;{pct}&nbsp;'
            f'<span style="color:{ch_c};">({s}{h.change_pct:.1f}%)</span></td></tr>'
        )
    jp_leg = f'<table cellpadding="0" cellspacing="0">{"".join(jp_legend_rows)}</table>'
    us_leg = f'<table cellpadding="0" cellspacing="0">{"".join(us_legend_rows)}</table>'

    rows.append(_card("保有銘柄（円グラフ）", (
        f'<table cellpadding="0" cellspacing="0" width="100%"><tr>'
        f'<td width="50%" style="padding-right:12px;vertical-align:top;text-align:center;">'
        f'<div style="font-size:12px;font-weight:bold;color:#374151;margin-bottom:8px;">'
        f'日本株</div>'
        f'{_donut_svg(jp_items)}'
        f'<div style="margin-top:8px;text-align:left;">{jp_leg}</div>'
        f'</td>'
        f'<td width="50%" style="padding-left:12px;vertical-align:top;text-align:center;">'
        f'<div style="font-size:12px;font-weight:bold;color:#374151;margin-bottom:8px;">'
        f'米国株</div>'
        f'{_donut_svg(us_items)}'
        f'<div style="margin-top:8px;text-align:left;">{us_leg}</div>'
        f'</td>'
        f'</tr></table>'
    )))

    # リスクメーター（5段階）+ 円ドル保有割合バー（2段）
    rows.append(_card("リスク・通貨配分", (
        f'<table cellpadding="0" cellspacing="0" width="100%"><tr>'
        f'<td width="45%" style="padding-right:20px;vertical-align:top;">'
        f'<div style="font-size:12px;font-weight:bold;color:#374151;margin-bottom:8px;">'
        f'リスクメーター（5段階）</div>'
        f'{_risk_meter(data.risk_score)}'
        f'</td>'
        f'<td width="55%" style="vertical-align:top;">'
        f'<div style="font-size:12px;font-weight:bold;color:#374151;margin-bottom:8px;">'
        f'円ドル保有割合</div>'
        f'{_currency_bar(data.jpy_asset_ratio, data.usd_asset_ratio, "総資産ベース")}'
        f'{_currency_bar(data.jpy_cash_ratio,  data.usd_cash_ratio,  "現金のみ")}'
        f'</td>'
        f'</tr></table>'
    )))

    # 為替エージェント判定バッジ
    rationale = (data.fx_rationale or "")[:120]
    rows.append(_card("為替エージェント判定", (
        f'<table cellpadding="0" cellspacing="0"><tr>'
        f'<td style="vertical-align:middle;padding-right:12px;">'
        f'{_fx_badge(data.fx_signal)}</td>'
        f'<td style="font-size:12px;color:#6b7280;vertical-align:middle;">'
        f'{rationale}</td></tr></table>'
        f'<div style="font-size:11px;color:#9ca3af;margin-top:6px;">'
        f'USD/JPY: {data.usdjpy_rate:.2f}</div>'
    )))

    # 信用建玉の状況
    rows.append(_card("信用建玉の状況", _margin_table(data.margin_positions)))

    # セクター別前日比（バー＋スコア＋騰落率）
    rows.append(_card("セクター別前日比", _sector_bars(data.sector_scores)))

    # 保有ポジション一覧（銘柄・保有額・PF比・前日比）
    rows.append(_card("保有ポジション一覧", _position_table(data.all_positions)))

    # 米国株セッション開始前FXシグナル
    pre_sig = data.pre_us_fx_signal or data.fx_signal
    pre_rat = (data.pre_us_fx_rationale or data.fx_rationale or "")[:120]
    rows.append(_card("米国株セッション開始前 FXシグナル", (
        f'<table cellpadding="0" cellspacing="0"><tr>'
        f'<td style="vertical-align:middle;padding-right:12px;">'
        f'{_fx_badge(pre_sig)}</td>'
        f'<td style="font-size:12px;color:#6b7280;vertical-align:middle;">'
        f'{pre_rat}</td></tr></table>'
    )))

    # CxO方針メモ
    rows.append(_card("CxO方針メモ", (
        f'<div style="background:#f0f9ff;border-left:4px solid #3b82f6;'
        f'padding:12px 16px;border-radius:0 6px 6px 0;">'
        f'<div style="font-size:13px;color:#1e3a5f;line-height:1.7;">'
        f'{data.cxo_memo}</div></div>'
        f'<div style="font-size:11px;color:#6b7280;margin-top:10px;">'
        f'<strong>マクロ:</strong> {data.macro_notes}<br>'
        f'<strong>ローテーション:</strong> {data.rotation_signal}'
        f'</div>'
    )))

    return rows


# ── 朝次専用セクション ──────────────────────────────────────

def _build_morning_rows(data: MorningReportData) -> list:
    rows = []

    # 米国株 昨夜の確定損益
    pl_c = "#16a34a" if data.us_realized_pl_usd >= 0 else "#dc2626"
    pl_s = "+" if data.us_realized_pl_usd >= 0 else ""
    rows.append(_card("米国株 昨夜の確定損益", (
        f'<table cellpadding="0" cellspacing="0"><tr>'
        f'<td style="padding-right:20px;vertical-align:top;">'
        f'<div style="font-size:24px;font-weight:bold;color:{pl_c};">'
        f'{pl_s}${data.us_realized_pl_usd:,.2f}</div>'
        f'<div style="font-size:13px;color:{pl_c};">'
        f'{pl_s}¥{data.us_realized_pl_jpy:,.0f}</div>'
        f'</td>'
        f'<td style="vertical-align:bottom;">'
        f'<div style="font-size:12px;color:#9ca3af;">{data.us_trade_count} トレード</div>'
        f'</td></tr></table>'
    )))

    # 日本株 本日デイトレ候補
    rows.append(_card("本日デイトレ候補（日本株）", _daytrade_table(data.daytrade_candidates)))

    # 夜間の為替変動サマリー
    fx_c  = "#16a34a" if data.overnight_fx_change_pct >= 0 else "#dc2626"
    fx_s  = "+" if data.overnight_fx_change_pct >= 0 else ""
    high_s = f"高値 {data.overnight_fx_high:.2f}" if data.overnight_fx_high else ""
    low_s  = f"安値 {data.overnight_fx_low:.2f}"  if data.overnight_fx_low  else ""
    range_s = " / ".join(filter(None, [high_s, low_s])) or "データなし"
    rows.append(_card("夜間の為替変動サマリー", (
        f'<table cellpadding="0" cellspacing="0"><tr>'
        f'<td style="padding-right:24px;vertical-align:top;">'
        f'<div style="font-size:20px;font-weight:bold;color:#111827;">'
        f'USD/JPY {data.usdjpy_rate:.2f}</div>'
        f'<div style="font-size:12px;color:{fx_c};margin-top:2px;">'
        f'{fx_s}{data.overnight_fx_change_pct:.2f}%</div>'
        f'</td>'
        f'<td style="vertical-align:bottom;">'
        f'<div style="font-size:12px;color:#6b7280;">{range_s}</div>'
        f'</td></tr></table>'
        f'<div style="font-size:12px;color:#6b7280;margin-top:6px;">'
        f'{data.overnight_fx_summary}</div>'
    )))

    # インテリジェンスエージェントの議論サマリー
    rows.append(_card(
        "インテリジェンス議論サマリー",
        _discussion_section(data.discussion_items, data.discussion_session_date),
    ))

    return rows


# ── 公開関数 ──────────────────────────────────────────────────

def build_evening_html(data: EveningReportData) -> str:
    """夜間（21:00）レポートのHTMLを生成する。"""
    rows = _build_base_rows(data)
    ts   = data.generated_at.strftime("%Y/%m/%d %H:%M")
    return _html_wrap(
        f"夜間投資レポート  {ts}",
        "#1e3a5f",
        "\n".join(rows),
    )


def build_morning_html(data: MorningReportData) -> str:
    """朝次（06:00）レポートのHTMLを生成する。"""
    rows = _build_base_rows(data) + _build_morning_rows(data)
    ts   = data.generated_at.strftime("%Y/%m/%d %H:%M")
    return _html_wrap(
        f"朝次投資レポート  {ts}",
        "#064e3b",
        "\n".join(rows),
    )
