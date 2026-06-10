"""
main.py
マルチエージェント投資システムのオーケストレーター。

実行フロー（デイトレセッション）：
  1. CIO → MarketContext 生成
  2. DaytradeAgent → 候補銘柄スクリーニング → TradeProposal 生成
  3. CriticDayAgent → 審査 (CriticVerdict)
  4. 承認済みのみ KabuBroker → 発注
  5. 損切り監視ループ（引けまで継続）
  6. 引け前に全ポジション強制決済

実行コマンド:
  python main.py --mode daytrade   # 日本株デイトレセッション
  python main.py --mode paper      # ペーパートレード（発注しない）
"""
import argparse
import logging
import time
from datetime import datetime

from agents.cio import CIOAgent
from agents.daytrade import DaytradeAgent
from agents.critic_day import CriticDayAgent
from brokers.kabu import KabuBroker
from config.settings import RISK

# ── ログ設定 ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/session.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ──────────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────────

def is_trading_hours() -> bool:
    """日本株の取引時間内かどうかを判定（9:00〜15:30）。"""
    now = datetime.now()
    return (
        now.weekday() < 5 and
        (9, 0) <= (now.hour, now.minute) <= (15, 30)
    )

def is_near_close() -> bool:
    """引け30分前かどうか（強制決済タイミング）。"""
    now = datetime.now()
    return now.hour == 15 and now.minute >= 0


# ──────────────────────────────────────────────────
# デイトレセッション
# ──────────────────────────────────────────────────

# サンプルユニバース（本番では data/screener.py が動的に生成する）
SAMPLE_UNIVERSE = [
    {"symbol": "9984", "name": "ソフトバンクG",  "volume_ratio": 2.3, "atr_pct": 2.8},
    {"symbol": "6857", "name": "アドバンテスト",  "volume_ratio": 1.8, "atr_pct": 2.1},
    {"symbol": "4063", "name": "信越化学",         "volume_ratio": 1.1, "atr_pct": 1.2},
    {"symbol": "2330", "name": "フィックスターズ", "volume_ratio": 3.1, "atr_pct": 3.5},
]


def run_daytrade_session(paper: bool = True):
    """デイトレセッションのメインループ。"""
    logger.info(f"=== デイトレセッション開始 {'[ペーパー]' if paper else '[本番]'} ===")

    cio = CIOAgent()
    daytrade_agent = DaytradeAgent()
    critic = CriticDayAgent()
    broker = KabuBroker()

    # 1. MarketContext 生成
    logger.info("CIO: マーケットコンテキスト生成中...")
    ctx = cio.generate_market_context(
        news_summary="（本番では data/market.py からニュース取得）",
        macro_data="USD/JPY=155.2, VIX=18.5, 米10Y=4.35%",
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # リスクが "high" のときはデイトレ中止
    if ctx.risk_level == "high":
        logger.warning("リスク水準 HIGH のためデイトレセッションを中止します")
        return

    # 2. 候補銘柄スクリーニング
    candidates = daytrade_agent.screen_candidates(SAMPLE_UNIVERSE, ctx)
    logger.info(f"候補銘柄: {candidates}")
    if not candidates:
        logger.info("本日のデイトレ対象なし。終了します")
        return

    # 3. 各候補の取引提案 → クリティーク → 発注
    open_positions: dict[str, dict] = {}  # symbol -> {entry_price, side, qty, order_id}

    for symbol in candidates:
        try:
            # 板データ取得（本番）
            if not paper:
                board_data = broker.get_board(symbol)
            else:
                # ペーパー用のモックデータ
                board_data = {"CurrentPrice": 2000, "CalcPrice": 2000}

            # 提案生成
            proposal = daytrade_agent.generate_trade_proposal(
                symbol=symbol,
                symbol_name=next((u["name"] for u in SAMPLE_UNIVERSE if u["symbol"] == symbol), symbol),
                board_data=board_data,
                bars_5min=[],  # 本番では data/market.py が取得
                ctx=ctx,
            )
            if not proposal:
                continue

            # クリティーク審査
            verdict = critic.review(proposal, ctx, broker.get_wallet_margin() if not paper else {"MarginAccountWallet": 500000})
            if not verdict.approved:
                logger.info(f"{symbol}: クリティーク否決 - {verdict.suggestion}")
                continue

            # 発注（ペーパーは発注しない）
            if not paper:
                result = broker.send_margin_order(
                    symbol=proposal.symbol,
                    side="2" if proposal.side == "buy" else "1",
                    qty=proposal.qty,
                    price=proposal.price,
                )
                if result.success:
                    open_positions[symbol] = {
                        "entry_price": proposal.price or board_data["CurrentPrice"],
                        "side": proposal.side,
                        "qty": proposal.qty,
                        "order_id": result.order_id,
                        "stop_loss": proposal.stop_loss,
                        "take_profit": proposal.take_profit,
                    }
                    logger.info(f"発注成功: {symbol} order_id={result.order_id}")
            else:
                logger.info(f"[ペーパー] 発注シミュレート: {symbol} {proposal.side} x{proposal.qty}")

        except Exception as e:
            logger.error(f"{symbol} 処理中エラー: {e}", exc_info=True)

    # 4. 損切り監視ループ（引けまで）
    logger.info("損切り監視ループ開始...")
    while open_positions and not is_near_close():
        time.sleep(60)  # 1分ごとにチェック
        for symbol in list(open_positions.keys()):
            try:
                board = broker.get_board(symbol) if not paper else {"CurrentPrice": open_positions[symbol]["entry_price"] * 0.98}
                current_price = board.get("CurrentPrice", 0)
                pos = open_positions[symbol]

                if daytrade_agent.should_emergency_exit(pos, current_price):
                    if not paper:
                        # 逆方向で成行決済
                        close_side = "1" if pos["side"] == "buy" else "2"
                        broker.send_margin_order(symbol, close_side, pos["qty"], price=0)
                    logger.warning(f"損切り決済: {symbol} @ {current_price}")
                    del open_positions[symbol]
            except Exception as e:
                logger.error(f"監視ループエラー {symbol}: {e}")

    # 5. 引け前全決済
    for symbol, pos in open_positions.items():
        logger.info(f"引け前強制決済: {symbol}")
        if not paper:
            close_side = "1" if pos["side"] == "buy" else "2"
            broker.send_margin_order(symbol, close_side, pos["qty"], price=0)

    logger.info("=== デイトレセッション終了 ===")


# ──────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="マルチエージェント投資システム")
    parser.add_argument("--mode", choices=["daytrade", "paper"], default="paper")
    args = parser.parse_args()

    run_daytrade_session(paper=(args.mode == "paper"))
