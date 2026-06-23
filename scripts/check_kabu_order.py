"""
scripts/check_kabu_order.py
【検証環境(18081)の発注経路確認専用】実弾は動かないが実際に sendorder を投げる。
base_url が :18081 でなければ即中断する。本番では絶対に実行しないこと。

現物・成行・買いを1件発注し OrderId と注文照会の疎通を確認する。
send_margin_order / cancel_order は呼ばない。
KABU_ALLOW_LIVE_ORDER には一切触れない。

実行: python -m scripts.check_kabu_order
"""
import json
import sys

import requests

# Windows コンソールの既定エンコードは cp932 のため日本語が化ける。
# encoding でコードを揃え、errors で表現不能文字もクラッシュさせない二段構え（CLAUDE.md 規約）。
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

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


def _send_order(broker: KabuBroker, exchange: int) -> dict:
    """現物・成行・買い発注を試みる。レスポンス dict を返す。"""
    body = {
        "Password": KABU.password,
        "Symbol": "7203",
        "Exchange": exchange,
        "SecurityType": 1,        # 1=株式
        "Side": "2",              # 2=買い
        "CashMargin": 1,          # 1=現物
        "DelivType": 2,           # 2=自動振替（現物買いに必要）
        "FundType": "AA",         # AA=自動振替（"  "空白2文字は未設定扱いでエラーになる）
        "AccountType": 2,         # 2=一般
        "Qty": 100,
        "FrontOrderType": 10,     # 10=成行
        "Price": 0,
        "ExpireDay": 0,           # 0=当日
    }
    resp = requests.post(
        f"{broker.base_url}/sendorder",
        headers=broker.headers,
        json=body,
        timeout=KABU.timeout_sec,
    )
    resp.raise_for_status()
    return resp.json()


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

    # ── Step 2: 現物・成行・買い発注 ────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 2] 現物発注（7203 東証 成行 買い 100株）")

    order_data = None
    for exchange, label in [(1, "東証(1)"), (9, "SOR(9)")]:
        print(f"  Exchange={label} で試みます…")
        try:
            order_data = _send_order(broker, exchange)
            _show(f"sendorder レスポンス（Exchange={label}）", order_data)
            break
        except requests.exceptions.HTTPError as e:
            _print_http_error(f"sendorder Exchange={label}", e)
            if exchange == 1:
                print("  → Exchange=9(SOR) で再試行します")
            else:
                print("  → 両方のExchangeで失敗しました")

    if order_data is None:
        print("\n発注に失敗したため注文照会をスキップします。")
        return 1

    order_id = order_data.get("OrderId")
    result_code = order_data.get("Result")
    if order_id:
        print(f"\n  OrderId: {order_id}")
    else:
        # 検証環境では Result=0(受付成功) でも OrderId=null になる場合がある
        print(f"\n  OrderId は null（Result={result_code}）")
        print("  ※ 検証環境では実注文が発生しないため null は正常の可能性あり")

    # ── Step 3: 注文照会 ─────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("[Step 3] 注文照会 GET /orders（全件取得）")
    try:
        orders = broker.get_orders(product=1)
        if order_id:
            matched = [o for o in orders if o.get("ID") == order_id or o.get("OrderId") == order_id]
            if matched:
                _show("該当注文", matched[0])
            else:
                print(f"  OrderId={order_id} に一致する注文は含まれていませんでした")
                _show("全注文一覧", orders)
        else:
            _show("全注文一覧（OrderId=null のため全件表示）", orders)
    except requests.exceptions.HTTPError as e:
        _print_http_error("注文照会", e)

    print(f"\n{_SEP}")
    print("完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
