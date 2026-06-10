"""
brokers/alpaca.py
Alpaca Trading API（米国株）への接続・発注を担う。

ペーパートレード中は ALPACA_BASE_URL=https://paper-api.alpaca.markets
本番移行時は      ALPACA_BASE_URL=https://api.alpaca.markets
"""
import logging
from dataclasses import dataclass
from typing import Optional
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from config.settings import ALPACA

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    message: str
    raw: Optional[object] = None


class AlpacaBroker:
    """Alpaca SDK のラッパー。米国株の現物・マージン発注を担う。"""

    def __init__(self):
        paper = "paper-api" in ALPACA.base_url
        self.client = TradingClient(
            api_key=ALPACA.api_key,
            secret_key=ALPACA.secret_key,
            paper=paper,
        )
        self.data_client = StockHistoricalDataClient(
            api_key=ALPACA.api_key,
            secret_key=ALPACA.secret_key,
        )
        logger.info(f"Alpaca: {'ペーパー' if paper else '本番'}環境で接続")

    # ------------------------------------------------------------------
    # 口座情報
    # ------------------------------------------------------------------
    def get_account(self) -> dict:
        """口座情報（残高・余力・マージン状況）を取得。"""
        acct = self.client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "margin_multiplier": float(acct.multiplier),
            "daytrade_count": acct.daytrade_count,
        }

    def get_positions(self) -> list[dict]:
        """保有ポジション一覧を取得。"""
        positions = self.client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": float(p.unrealized_pl),
                "market_value": float(p.market_value),
            }
            for p in positions
        ]

    # ------------------------------------------------------------------
    # 株価データ
    # ------------------------------------------------------------------
    def get_bars(self, symbol: str, timeframe: TimeFrame = TimeFrame.Day, limit: int = 30) -> list[dict]:
        """
        ヒストリカルバー（OHLCV）を取得。
        timeframe: TimeFrame.Minute, TimeFrame.Hour, TimeFrame.Day など
        """
        from datetime import datetime, timedelta
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=datetime.now() - timedelta(days=limit * 2),
            limit=limit,
        )
        bars = self.data_client.get_stock_bars(req)
        return [
            {
                "t": bar.timestamp.isoformat(),
                "o": bar.open, "h": bar.high, "l": bar.low,
                "c": bar.close, "v": bar.volume,
            }
            for bar in bars[symbol]
        ]

    # ------------------------------------------------------------------
    # 発注
    # ------------------------------------------------------------------
    def send_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,  # "buy" | "sell"
        tif: TimeInForce = TimeInForce.DAY,
    ) -> OrderResult:
        """成行注文。"""
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=tif,
        )
        try:
            order = self.client.submit_order(req)
            return OrderResult(success=True, order_id=str(order.id), message="成行発注成功", raw=order)
        except Exception as e:
            logger.error(f"Alpaca成行発注失敗 {symbol}: {e}")
            return OrderResult(success=False, order_id=None, message=str(e))

    def send_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        tif: TimeInForce = TimeInForce.DAY,
    ) -> OrderResult:
        """指値注文。"""
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=tif,
            limit_price=limit_price,
        )
        try:
            order = self.client.submit_order(req)
            return OrderResult(success=True, order_id=str(order.id), message="指値発注成功", raw=order)
        except Exception as e:
            logger.error(f"Alpaca指値発注失敗 {symbol}: {e}")
            return OrderResult(success=False, order_id=None, message=str(e))

    def cancel_order(self, order_id: str) -> OrderResult:
        """注文キャンセル。"""
        try:
            self.client.cancel_order_by_id(order_id)
            return OrderResult(success=True, order_id=order_id, message="キャンセル成功")
        except Exception as e:
            return OrderResult(success=False, order_id=order_id, message=str(e))

    def close_position(self, symbol: str) -> OrderResult:
        """指定銘柄のポジションを全決済。"""
        try:
            resp = self.client.close_position(symbol)
            return OrderResult(success=True, order_id=str(resp.id), message="全決済成功", raw=resp)
        except Exception as e:
            return OrderResult(success=False, order_id=None, message=str(e))
