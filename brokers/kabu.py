"""
brokers/kabu.py
kabu STATION API（三菱UFJ eスマート証券）への接続・発注を担う。

前提：
  - kabuステーションがローカルPCで起動・ログイン済みであること
  - KABU_ENV=test → http://localhost:18081/kabusapi（検証環境・デフォルト）
  - KABU_ENV=live → http://localhost:18080/kabusapi（本番環境）
"""
import os
import requests
import logging
from dataclasses import dataclass
from typing import Optional
from config.settings import KABU

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    message: str
    raw: Optional[dict] = None


class KabuBroker:
    """kabu STATION APIのラッパー。発注・残高・ポジション取得を担う。"""

    def __init__(self, config=None):
        _cfg = config or KABU
        self.base_url = _cfg.base_url
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # 内部ガード
    # ------------------------------------------------------------------
    def _guard_live_order(self) -> None:
        """本番環境での誤発注を防ぐガード。
        判定は KABU_ENV の宣言値ではなく解決後の base_url が :18080 を含むかで行う。
        Why: KABU_ENV=test でも KABU_BASE_URL で本番ポートを直指定した場合など、
        宣言と実体がずれた設定でも確実にガードが効くようにするため。
        """
        if ":18080" in self.base_url and os.getenv("KABU_ALLOW_LIVE_ORDER") != "1":
            raise RuntimeError(
                f"本番ポート(base_url={self.base_url})への発注をブロックしました。"
                " 意図的な発注の場合は KABU_ALLOW_LIVE_ORDER=1 を設定してください。"
            )

    # ------------------------------------------------------------------
    # 認証
    # ------------------------------------------------------------------
    def get_token(self) -> str:
        """APIトークンを取得（kabuステーション起動中のみ有効）。"""
        resp = requests.post(
            f"{self.base_url}/token",
            json={"APIPassword": KABU.password},
            timeout=KABU.timeout_sec,
        )
        resp.raise_for_status()
        self._token = resp.json()["Token"]
        logger.info("kabu STATION: トークン取得成功")
        return self._token

    @property
    def headers(self) -> dict:
        if not self._token:
            self.get_token()
        return {"X-API-KEY": self._token, "Content-Type": "application/json"}

    # ------------------------------------------------------------------
    # 口座情報
    # ------------------------------------------------------------------
    def get_wallet_cash(self) -> dict:
        """現物取引余力を取得。"""
        resp = requests.get(f"{self.base_url}/wallet/cash", headers=self.headers, timeout=KABU.timeout_sec)
        resp.raise_for_status()
        return resp.json()

    def get_wallet_margin(self) -> dict:
        """信用取引余力を取得。"""
        resp = requests.get(f"{self.base_url}/wallet/margin", headers=self.headers, timeout=KABU.timeout_sec)
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> list[dict]:
        """保有ポジション一覧を取得。"""
        resp = requests.get(f"{self.base_url}/positions", headers=self.headers, timeout=KABU.timeout_sec)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 銘柄登録
    # ------------------------------------------------------------------
    def register_symbols(self, symbols: list[dict]) -> dict:
        """銘柄をリアルタイム配信登録する（PUT /register）。
        Why: REST /board は事前の銘柄登録が前提となっている。公式リファレンスには
        自動登録の記載があるが実際は登録なしでは board が空/エラーになる。
        symbols 例: [{"Symbol": "7203", "Exchange": 1}]
        """
        resp = requests.put(
            f"{self.base_url}/register",
            headers=self.headers,
            json={"Symbols": symbols},
            timeout=KABU.timeout_sec,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 株価情報
    # ------------------------------------------------------------------
    def get_board(self, symbol: str, exchange: int = 1) -> dict:
        """
        リアルタイム板情報を取得。
        exchange: 1=東証, 3=名証, 5=福証, 6=札証
        """
        resp = requests.get(
            f"{self.base_url}/board/{symbol}@{exchange}",
            headers=self.headers,
            timeout=KABU.timeout_sec,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 注文照会
    # ------------------------------------------------------------------
    def get_orders(self, product: int = 1) -> list[dict]:
        """注文一覧を取得。product: 1=株式(デフォルト), 0=全商品"""
        resp = requests.get(
            f"{self.base_url}/orders",
            headers=self.headers,
            params={"product": product},
            timeout=KABU.timeout_sec,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 発注（現物）
    # ------------------------------------------------------------------
    def send_cash_order(
        self,
        symbol: str,
        side: str,          # "2" = 買, "1" = 売
        qty: int,
        price: float = 0,   # 0 = 成行
        exchange: int = 1,
    ) -> OrderResult:
        """
        現物発注。
        side: "2"=買い, "1"=売り
        price=0 で成行注文。
        """
        self._guard_live_order()
        order_type = 4 if price == 0 else 2   # 4=成行, 2=指値（概念上）
        body = {
            "Password": KABU.password,
            "Symbol": symbol,
            "Exchange": exchange,
            "SecurityType": 1,       # 1=株式
            "Side": side,
            "CashMargin": 1,         # 1=現物
            "DelivType": 2,          # 2=自動振替
            "FundType": "  ",
            "AccountType": 2,        # 2=一般
            "Qty": qty,
            "FrontOrderType": order_type,
            "Price": price,
            "ExpireDay": 0,          # 0=当日
        }
        try:
            resp = requests.post(f"{self.base_url}/sendorder", headers=self.headers, json=body, timeout=KABU.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(success=True, order_id=data.get("OrderId"), message="現物発注成功", raw=data)
        except Exception as e:
            logger.error(f"現物発注失敗 {symbol}: {e}")
            return OrderResult(success=False, order_id=None, message=str(e))

    # ------------------------------------------------------------------
    # 発注（信用）
    # ------------------------------------------------------------------
    def send_margin_order(
        self,
        symbol: str,
        side: str,          # "2"=買建, "1"=売建
        qty: int,
        price: float = 0,
        margin_trade_type: int = 1,  # 1=制度信用
        exchange: int = 1,
    ) -> OrderResult:
        """
        信用発注（デイトレ向け）。
        margin_trade_type: 1=制度信用, 3=一般信用（短期）
        """
        self._guard_live_order()
        order_type = 4 if price == 0 else 2
        body = {
            "Password": KABU.password,
            "Symbol": symbol,
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": side,
            "CashMargin": 2,              # 2=信用新規
            "MarginTradeType": margin_trade_type,
            "DelivType": 0,               # 0=指定なし（信用）
            "AccountType": 2,
            "Qty": qty,
            "FrontOrderType": order_type,
            "Price": price,
            "ExpireDay": 0,
        }
        try:
            resp = requests.post(f"{self.base_url}/sendorder", headers=self.headers, json=body, timeout=KABU.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(success=True, order_id=data.get("OrderId"), message="信用発注成功", raw=data)
        except Exception as e:
            logger.error(f"信用発注失敗 {symbol}: {e}")
            return OrderResult(success=False, order_id=None, message=str(e))

    def cancel_order(self, order_id: str) -> OrderResult:
        """注文キャンセル。"""
        self._guard_live_order()
        body = {"OrderId": order_id, "Password": KABU.password}
        try:
            resp = requests.put(f"{self.base_url}/cancelorder", headers=self.headers, json=body, timeout=KABU.timeout_sec)
            resp.raise_for_status()
            return OrderResult(success=True, order_id=order_id, message="キャンセル成功")
        except Exception as e:
            return OrderResult(success=False, order_id=order_id, message=str(e))
