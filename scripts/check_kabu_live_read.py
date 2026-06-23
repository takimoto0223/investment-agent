"""
scripts/check_kabu_live_read.py
【本番(18080)の読み取り専用確認】発注系は import も呼び出しもしない。
発注ガード(KABU_ALLOW_LIVE_ORDER)には一切触れない。

このプロセス内でのみ KABU_BASE_URL を本番(18080)に設定する（.env は変更しない）。
token / wallet / board / positions の疎通を実データで確認する。
send_cash_order / send_margin_order / cancel_order は import も呼び出しも一切しない。

実行: python -m scripts.check_kabu_live_read
"""
import os
import sys

# .env を書き換えずにこのプロセス内だけ本番(18080)を向かせる。
# load_dotenv() は既に設定済みの env var を上書きしないため、
# config.settings の import より前にここで設定することが必要。
os.environ["KABU_BASE_URL"] = "http://localhost:18080/kabusapi"

# Windows コンソールの文字化け対策（CLAUDE.md 規約）
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

import json
import requests
from config.settings import KABU
from brokers.kabu import KabuBroker

_SEP = "-" * 50


def _show(label: str, data) -> None:
    print(f"\n{_SEP}")
    print(f"[{label}]")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _print_http_error(label: str, e: requests.exceptions.HTTPError) -> None:
    resp = e.response
    print(f"  [HTTP エラー] {label} ステータス: {resp.status_code}")
    try:
        print(f"  レスポンス本文: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")
    except Exception:
        print(f"  レスポンス本文(raw): {resp.text}")


def main() -> int:
    print(f"env      : {KABU.env}")
    print(f"base_url : {KABU.base_url}")

    if ":18080" not in KABU.base_url:
        print("\n[ERROR] base_url が :18080 を指していません。中断します。")
        return 1

    broker = KabuBroker()

    # ── Step 1: トークン取得 ────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 1] トークン取得（本番）")
    try:
        token = broker.get_token()
        print(f"  成功（{len(token)} 文字）")
    except requests.exceptions.ConnectionError as e:
        print("  [接続エラー] kabu STATION(本番)が起動・ログイン済みか確認してください")
        print(f"  詳細: {e}")
        return 1
    except requests.exceptions.HTTPError as e:
        _print_http_error("トークン取得", e)
        return 1

    # ── Step 2: 現物余力 ─────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 2] 現物余力 GET /wallet/cash")
    try:
        data = broker.get_wallet_cash()
        _show("wallet/cash", data)
    except requests.exceptions.HTTPError as e:
        _print_http_error("wallet/cash", e)

    # ── Step 3: 信用余力 ─────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 3] 信用余力 GET /wallet/margin")
    try:
        data = broker.get_wallet_margin()
        _show("wallet/margin", data)
    except requests.exceptions.HTTPError as e:
        _print_http_error("wallet/margin", e)

    # ── Step 4: 銘柄登録 → 板情報 ───────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 4-a] 銘柄登録（7203@東証） PUT /register")
    try:
        data = broker.register_symbols([{"Symbol": "7203", "Exchange": 1}])
        _show("register", data)
    except requests.exceptions.HTTPError as e:
        _print_http_error("register", e)

    print(f"\n{_SEP}")
    print("[Step 4-b] 板情報 GET /board/7203@1")
    try:
        data = broker.get_board("7203", exchange=1)
        _show("board 7203@東証", data)
    except requests.exceptions.HTTPError as e:
        _print_http_error("board", e)

    # ── Step 5: 保有建玉 ─────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 5] 保有建玉 GET /positions")
    try:
        data = broker.get_positions()
        _show("positions", data)
    except requests.exceptions.HTTPError as e:
        _print_http_error("positions", e)

    print(f"\n{_SEP}")
    print("完了（読み取りのみ・発注なし）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
