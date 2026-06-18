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
        side: str,  # "buy"=買い | "sell"=売り
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

    def get_activities(self, since_hours: int = 24) -> list[dict]:
        """
        直近 since_hours 時間の約定履歴（FILL）を返す。
        各要素: symbol, side, qty, price, transaction_time
        """
        from datetime import datetime, timedelta, timezone
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderStatus
        try:
            after = datetime.now(timezone.utc) - timedelta(hours=since_hours)
            orders = self.client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=after, limit=200)
            )
            result = []
            for o in orders:
                if o.status != OrderStatus.FILLED:
                    continue
                result.append({
                    "symbol":           o.symbol,
                    "side":             str(o.side).lower().replace("orderside.", ""),
                    "qty":              float(o.filled_qty or 0),
                    "price":            float(o.filled_avg_price or 0),
                    "transaction_time": o.filled_at.isoformat() if o.filled_at else "",
                    "order_id":         str(o.id),
                })
            return result
        except Exception as e:
            logger.warning(f"get_activities失敗: {e}")
            return []


# ── 手数料計算ユーティリティ ─────────────────────────────────────

_SEC_FEE_RATE  = 0.0000278   # 売り約定代金に対して (SEC Regulatory Fee)
_FINRA_TAF_RATE = 0.000166   # 売り株数に対して (FINRA TAF)
_FINRA_TAF_MAX  = 8.30       # 1注文あたり上限


def calculate_us_fees(side: str, qty: float, price: float) -> float:
    """
    Alpaca の米国株取引手数料を計算する。
    - 委託手数料: $0（Alpaca は無料）
    - SEC fee    : 売りのみ。約定代金 × 0.0000278
    - FINRA TAF  : 売りのみ。株数 × $0.000166（上限 $8.30）
    戻り値: 手数料合計（USD）
    """
    if side != "sell":
        return 0.0
    proceeds  = qty * price
    sec_fee   = proceeds * _SEC_FEE_RATE
    finra_taf = min(qty * _FINRA_TAF_RATE, _FINRA_TAF_MAX)
    return round(sec_fee + finra_taf, 4)


def calc_daytrade_pl(activities: list[dict]) -> dict:
    """
    約定履歴からデイトレ損益を計算する。
    同一銘柄の buy → sell ペアをマッチングして実現損益を算出。
    戻り値:
      trades: [{symbol, buy_price, sell_price, qty, gross_pl, fees, net_pl}]
      total_gross: 合計グロス損益
      total_fees : 合計手数料
      total_net  : 合計ネット損益
    """
    from collections import defaultdict
    buys: dict[str, list] = defaultdict(list)
    sells: dict[str, list] = defaultdict(list)

    for a in activities:
        sym = a["symbol"]
        if "buy" in a["side"]:
            buys[sym].append(a)
        else:
            sells[sym].append(a)

    trades = []
    for sym in set(list(buys.keys()) + list(sells.keys())):
        buy_list  = sorted(buys.get(sym, []),  key=lambda x: x["transaction_time"])
        sell_list = sorted(sells.get(sym, []), key=lambda x: x["transaction_time"])
        for b, s in zip(buy_list, sell_list):
            qty       = min(b["qty"], s["qty"])
            gross_pl  = (s["price"] - b["price"]) * qty
            fees      = calculate_us_fees("sell", s["qty"], s["price"])
            net_pl    = gross_pl - fees
            trades.append({
                "symbol":     sym,
                "buy_price":  b["price"],
                "sell_price": s["price"],
                "qty":        qty,
                "gross_pl":   round(gross_pl, 2),
                "fees":       round(fees, 4),
                "net_pl":     round(net_pl, 2),
            })

    total_gross = sum(t["gross_pl"] for t in trades)
    total_fees  = sum(t["fees"]     for t in trades)
    return {
        "trades":      trades,
        "total_gross": round(total_gross, 2),
        "total_fees":  round(total_fees, 4),
        "total_net":   round(total_gross - total_fees, 2),
    }
