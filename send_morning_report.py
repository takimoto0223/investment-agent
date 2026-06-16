"""朝次レポートを生成し、HTMLファイルに保存してからメール送信を試みる。"""
import os
import sys
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from agents.cxo import CXOAgent
from report.template import (
    MorningReportData, HoldingItem, SectorScore,
    DaytradeCandidate, DiscussionItem, DaytradeRecord, ValueDecision,
    build_morning_html,
)

now = datetime.now()

agent = CXOAgent()
raw = agent._collect_common_data()
d = agent._build_report_data(raw)
ctx = d["ctx"]

usdjpy = raw["usdjpy_rate"]
report_data = MorningReportData(
    generated_at=now,
    total_assets_jpy=d["total_jpy"],
    total_assets_change_pct=0.0,
    jp_holdings=d["jp_holdings"],
    us_holdings=d["us_holdings"],
    risk_score=d["risk_score"],
    risk_level=ctx.risk_level,
    jpy_asset_ratio=d["jpy_asset_ratio"],
    usd_asset_ratio=d["usd_asset_ratio"],
    jpy_cash_ratio=d["jpy_cash_ratio"],
    usd_cash_ratio=d["usd_cash_ratio"],
    fx_signal=d["fx_label"],
    fx_rationale=d["fx_rationale"],
    usdjpy_rate=usdjpy,
    margin_positions=[],
    sector_scores=d["sector_scores"],
    all_positions=d["us_holdings"],
    pre_us_fx_signal=d["fx_label"],
    pre_us_fx_rationale=d["fx_rationale"],
    cxo_memo=d["cxo_memo"],
    macro_notes=ctx.macro_notes,
    rotation_signal=ctx.rotation_signal,
    us_realized_pl_usd=0.0,
    us_realized_pl_jpy=0.0,
    us_trade_count=0,
    daytrade_candidates=[],
    overnight_fx_summary=f"USD/JPY {usdjpy:.2f}",
    overnight_fx_high=usdjpy * 1.005,
    overnight_fx_low=usdjpy * 0.995,
    overnight_fx_change_pct=0.0,
    discussion_items=[],
    discussion_session_date="",
    daytrade_records=[],
    daytrade_gross_pl=0.0,
    daytrade_fees=0.0,
    daytrade_net_pl=0.0,
    value_decisions=[],
)

html = build_morning_html(report_data)

out_path = Path("logs") / f"morning_report_{now.strftime('%Y%m%d_%H%M%S')}.html"
out_path.write_text(html, encoding="utf-8")
print(f"HTMLレポート保存: {out_path}")

import smtplib
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from_email   = os.getenv("REPORT_FROM_EMAIL", "").strip()
to_email     = os.getenv("REPORT_TO_EMAIL", "").strip()
app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
subject = f"[投資レポート] 朝次サマリー {now.strftime('%Y/%m/%d %H:%M')}"

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = from_email
msg["To"]      = to_email
msg.attach(MIMEText(html, "html", "utf-8"))

sent = False
for port, method in [(587, "STARTTLS"), (465, "SSL")]:
    try:
        print(f"SMTP port {port} ({method}) を試みます...")
        if method == "STARTTLS":
            with smtplib.SMTP("smtp.gmail.com", port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(from_email, app_password)
                smtp.sendmail(from_email, to_email, msg.as_bytes())
                print(f"メール送信成功 (port {port}): {subject!r} → {to_email}")
                sent = True
                break
        else:
            ipv4 = socket.getaddrinfo("smtp.gmail.com", port, socket.AF_INET)[0][4][0]
            with smtplib.SMTP_SSL(ipv4, port, timeout=30) as smtp:
                smtp.login(from_email, app_password)
                smtp.sendmail(from_email, to_email, msg.as_bytes())
                print(f"メール送信成功 (port {port}): {subject!r} → {to_email}")
                sent = True
                break
    except Exception as exc:
        print(f"port {port} 失敗: {exc}")

if not sent:
    print("メール送信は全ポートで失敗しました。HTMLレポートは logs/ に保存済みです。")
    sys.exit(1)
