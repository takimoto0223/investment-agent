"""
scripts/check_kabu_token.py
kabu STATION API への疎通確認（トークン取得のみ）。

使い方:
    python scripts/check_kabu_token.py

確認内容: env / base_url の表示 → KabuBroker.get_token() の成否
wallet / positions / 発注は一切呼ばない。
"""
import sys
import requests
from config.settings import KABU
from brokers.kabu import KabuBroker


def main() -> int:
    print(f"env      : {KABU.env}")
    print(f"base_url : {KABU.base_url}")
    print()

    broker = KabuBroker()
    try:
        token = broker.get_token()
        print(f"トークン取得成功（{len(token)} 文字）")
        return 0

    except requests.exceptions.ConnectionError as e:
        print(f"[接続エラー] kabu STATION が起動・ログイン済みか確認してください")
        print(f"  詳細: {e}")
        return 1

    except requests.exceptions.HTTPError as e:
        resp = e.response
        print(f"[HTTP エラー] ステータス: {resp.status_code}")
        try:
            body = resp.json()
            print(f"  ResultCode : {body.get('ResultCode', '(なし)')}")
            print(f"  Message    : {body.get('Message', '(なし)')}")
        except Exception:
            print(f"  レスポンス本文: {resp.text[:200]}")
        return 1

    except Exception as e:
        print(f"[予期しないエラー] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
