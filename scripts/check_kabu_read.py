"""
scripts/check_kabu_read.py
【検証環境(18081)の読み取り確認専用】発注系は呼ばない。

base_url が :18081 でなければ即中断する。
token / register / board / wallet/cash の疎通を確認する。
send_cash_order / send_margin_order / cancel_order は呼ばない。

実行: python -m scripts.check_kabu_read
"""
import json
import sys

import requests

# Windows コンソールの既定エンコードは cp932 のため日本語が化ける。
# このターミナルは UTF-8 を期待しているため encoding も指定し、
# 表現できない文字は crashes でなく \uXXXX 表記に倒す。
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from config.settings import KABU
from brokers.kabu import KabuBroker

_SEP = "-" * 50


def _show(label: str, data) -> None:
    print(f"\n{_SEP}")
    print(f"[{label}]")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> int:
    print(f"env      : {KABU.env}")
    print(f"base_url : {KABU.base_url}")

    if ":18081" not in KABU.base_url:
        print("\nこのスクリプトは検証環境専用です。")
        print("base_url が :18081 を指していません。中断します。")
        return 1

    broker = KabuBroker()

    # ── Step 1: トークン取得 ────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 1] トークン取得")
    try:
        token = broker.get_token()
        print(f"  成功（{len(token)} 文字）")
    except requests.exceptions.ConnectionError as e:
        print(f"  [接続エラー] kabu STATION が起動・ログイン済みか確認してください")
        print(f"  詳細: {e}")
        return 1
    except requests.exceptions.HTTPError as e:
        _print_http_error("トークン取得", e)
        return 1

    # ── Step 2: 銘柄登録 ────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 2] 銘柄登録（7203@東証） PUT /register")
    try:
        result = broker.register_symbols([{"Symbol": "7203", "Exchange": 1}])
        _show("register レスポンス", result)
    except requests.exceptions.HTTPError as e:
        _print_http_error("銘柄登録", e)
        # 登録失敗でも後続ステップを続ける（board は失敗する可能性あり）

    # ── Step 3: 板情報取得 ───────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 3] 板情報取得（7203@東証） GET /board")
    print("  ※ 検証環境では多くの項目が null/空になる想定（正常）")
    try:
        board = broker.get_board("7203", exchange=1)
        _show("board レスポンス", board)
    except requests.exceptions.HTTPError as e:
        _print_http_error("板情報取得", e)

    # ── Step 4: 現物余力取得 ────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 4] 現物余力取得 GET /wallet/cash")
    try:
        wallet = broker.get_wallet_cash()
        _show("wallet/cash レスポンス", wallet)
    except requests.exceptions.HTTPError as e:
        _print_http_error("現物余力取得", e)

    print(f"\n{_SEP}")
    print("完了")
    return 0


def _print_http_error(label: str, e: requests.exceptions.HTTPError) -> None:
    resp = e.response
    print(f"  [HTTP エラー] {label} ステータス: {resp.status_code}")
    try:
        body = resp.json()
        print(f"  ResultCode : {body.get('ResultCode', '(なし)')}")
        print(f"  Message    : {body.get('Message', '(なし)')}")
    except Exception:
        print(f"  レスポンス本文: {resp.text[:300]}")


if __name__ == "__main__":
    sys.exit(main())
