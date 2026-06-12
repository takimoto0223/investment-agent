"""
report/mailer.py
Gmail SMTP経由でHTMLレポートメールを送信する。

必要な環境変数 (.env):
  REPORT_FROM_EMAIL   - 送信元Gmailアドレス
  REPORT_TO_EMAIL     - 送信先メールアドレス
  GMAIL_APP_PASSWORD  - Gmailアプリパスワード（Googleアカウント設定で発行）
"""
import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465


def send_report(subject: str, html_body: str) -> bool:
    """
    HTMLメールをGmail SMTP_SSL (port 465) で送信する。
    送信成功で True、設定未完了または送信失敗で False を返す。
    """
    from_email   = os.getenv("REPORT_FROM_EMAIL", "").strip()
    to_email     = os.getenv("REPORT_TO_EMAIL", "").strip()
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    if not all([from_email, to_email, app_password]):
        logger.warning(
            "メール送信設定が未完了です。"
            ".env に REPORT_FROM_EMAIL / REPORT_TO_EMAIL / GMAIL_APP_PASSWORD を設定してください。"
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_email
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT) as smtp:
            smtp.login(from_email, app_password)
            smtp.sendmail(from_email, to_email, msg.as_bytes())
        logger.info(f"メール送信成功: {subject!r} → {to_email}")
        return True
    except Exception as exc:
        logger.error(f"メール送信失敗: {exc}")
        return False
