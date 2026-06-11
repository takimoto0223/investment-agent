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
  python main.py --mode daytrade         # 日本株デイトレセッション
  python main.py --mode paper            # ペーパートレード（発注しない）
  python main.py --mode us_paper         # 米国株ペーパートレード（Alpaca）
  python main.py --mode intelligence     # 情報収集・議論セッション（毎日 23:00 想定）
  python main.py --mode morning_report   # 朝次レポート生成＋メール送信（06:00 想定）
  python main.py --mode evening_report   # 夜間レポート生成＋メール送信（21:00 想定）
"""
import argparse
import logging
import sys
import time
from datetime import datetime

from agents.cio import CIOAgent
from agents.daytrade import DaytradeAgent
from agents.critic_day import CriticDayAgent
from brokers.kabu import KabuBroker
from config.settings import RISK
from data import market as mkt

# ── ログ設定 ──────────────────────────────────────
# Windowsコンソールの文字化け対策
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
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

# ベース銘柄リスト（volume_ratio・atr_pct は data/market.py が動的に計算して付加する）
_BASE_SYMBOLS = [
    {"symbol": "9984", "name": "ソフトバンクG"},
    {"symbol": "6857", "name": "アドバンテスト"},
    {"symbol": "4063", "name": "信越化学"},
    {"symbol": "2330", "name": "フィックスターズ"},
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
        news_summary="（本番では外部ニュースAPIから取得）",
        macro_data="USD/JPY=155.2, VIX=18.5, 米10Y=4.35%",
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # リスクが "high" のときはデイトレ中止
    if ctx.risk_level == "high":
        logger.warning("リスク水準 HIGH のためデイトレセッションを中止します")
        return

    # 2. ユニバース構築（volume_ratio・atr_pct を動的に計算）
    logger.info("ユニバース構築中（板情報・日足データ取得）...")
    universe = mkt.build_universe(_BASE_SYMBOLS)
    mock_flag = any(mkt.check_kabu_connection() is False for _ in [None])
    if not mkt.check_kabu_connection():
        logger.info("モードモック: kabuステーション未接続のためモックデータを使用")

    # 3. 候補銘柄スクリーニング
    candidates = daytrade_agent.screen_candidates(universe, ctx)
    logger.info(f"候補銘柄: {candidates}")
    if not candidates:
        logger.info("本日のデイトレ対象なし。終了します")
        return

    # 4. 各候補の取引提案 → クリティーク → 発注
    open_positions: dict[str, dict] = {}  # symbol -> {entry_price, side, qty, order_id}

    for symbol in candidates:
        try:
            # 板データ・5分足データを取得（モック自動切替あり）
            board_data = mkt.get_board(symbol) if paper else broker.get_board(symbol)
            bars_5min  = mkt.get_bars_5min(symbol)

            # 提案生成
            proposal = daytrade_agent.generate_trade_proposal(
                symbol=symbol,
                symbol_name=next((u["name"] for u in universe if u["symbol"] == symbol), symbol),
                board_data=board_data,
                bars_5min=bars_5min,
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
# 米国株ペーパートレードセッション
# ──────────────────────────────────────────────────

# テスト対象の米国株ユニバース（AI・半導体・テック中心）
_US_BASE_SYMBOLS = [
    {"symbol": "NVDA",  "name": "エヌビディア"},
    {"symbol": "AAPL",  "name": "アップル"},
    {"symbol": "MSFT",  "name": "マイクロソフト"},
    {"symbol": "TSLA",  "name": "テスラ"},
    {"symbol": "META",  "name": "メタ"},
    {"symbol": "AMZN",  "name": "アマゾン"},
    {"symbol": "GOOGL", "name": "アルファベット"},
]


def run_us_paper_session():
    """
    米国株ペーパートレードセッション。
    Alpaca paper-API に接続し、エージェントの提案を実際にペーパー発注する。
    """
    from brokers.alpaca import AlpacaBroker
    from data import us_market as us_mkt
    from agents.critic_us import CriticUSAgent
    from agents.fx_strategy import FXStrategyAgent

    logger.info("=== 米国株ペーパートレードセッション開始 ===")

    cio            = CIOAgent()
    daytrade_agent = DaytradeAgent()
    critic         = CriticUSAgent()
    fx_agent       = FXStrategyAgent()
    broker         = AlpacaBroker()

    # 1. 口座確認
    acct = broker.get_account()
    logger.info(
        f"Alpaca口座: equity=${acct['equity']:,.2f} "
        f"cash=${acct['cash']:,.2f} "
        f"buying_power=${acct['buying_power']:,.2f}"
    )

    # 2. MarketContext 生成
    logger.info("CIO: マーケットコンテキスト生成中...")
    macro_data = "USD/JPY=155.2, VIX=18.5, 米10Y=4.35%, S&P500先物=+0.3%"
    ctx = cio.generate_market_context(
        news_summary="（米国株市場オープン時のコンテキスト生成）",
        macro_data=macro_data,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    if ctx.risk_level == "high":
        logger.warning("リスク水準 HIGH のためセッションを中止します")
        return

    # 3. FX シグナル取得（クリティークに渡す）
    logger.info("FX戦略シグナル取得中...")
    current_usd_ratio = acct["equity"] / (acct["equity"] * 155.0 + 1) * 100  # 概算
    fx_signal = fx_agent.generate_signal(macro_data, current_usd_ratio / 100, ctx)
    logger.info(
        f"FXシグナル: {fx_signal.get('fx_signal')} "
        f"us_weight_bias={fx_signal.get('us_weight_bias')}"
    )

    # 4. 米国株ユニバース構築
    logger.info("米国株ユニバース構築中（Alpaca データ取得）...")
    universe = us_mkt.build_us_universe(_US_BASE_SYMBOLS)

    # 5. 候補銘柄スクリーニング
    candidates = daytrade_agent.screen_candidates(universe, ctx)
    logger.info(f"候補銘柄: {candidates}")
    if not candidates:
        logger.info("本日の米国株デイトレ対象なし。終了します")
        return

    # 6. 各候補の取引提案 → CriticUSAgent → ペーパー発注
    for symbol in candidates:
        try:
            quote     = us_mkt.get_quote_us(symbol)
            bars_5min = us_mkt.get_bars_5min_us(symbol)
            sym_name  = next((u["name"] for u in universe if u["symbol"] == symbol), symbol)

            proposal = daytrade_agent.generate_trade_proposal(
                symbol=symbol,
                symbol_name=sym_name,
                board_data=quote,
                bars_5min=bars_5min,
                ctx=ctx,
            )
            if not proposal:
                continue

            # CriticUSAgent で審査（FXシグナル・ドル建て大口判定・市場時間チェック含む）
            verdict = critic.review(proposal, ctx, acct, fx_signal)
            if not verdict.approved:
                logger.info(f"{symbol}: クリティーク否決 - {verdict.suggestion}")
                continue

            # ペーパー発注（Alpaca paper-API に実際に送信）
            price = proposal.price
            if price and price > 0:
                result = broker.send_limit_order(symbol, proposal.qty, proposal.side, price)
            else:
                result = broker.send_market_order(symbol, proposal.qty, proposal.side)

            if result.success:
                logger.info(
                    f"[ペーパー発注] {symbol} {proposal.side} x{proposal.qty} "
                    f"order_id={result.order_id}"
                )
            else:
                logger.warning(f"{symbol}: 発注失敗 - {result.message}")

        except Exception as e:
            logger.error(f"{symbol} 処理中エラー: {e}", exc_info=True)

    logger.info("=== 米国株ペーパートレードセッション終了 ===")


# ──────────────────────────────────────────────────────────────────
# 情報収集・議論セッション（毎日 23:00）
# ──────────────────────────────────────────────────────────────────

def run_intelligence_session():
    """
    毎日 23:00 に実行するインテリジェンス収集・議論セッション。
    1. IntelligenceAgent でシグナル収集（arxiv / GitHub / HackerNews）
    2. CriticIntelligenceAgent で審査
    3. relevance_score >= 0.75 のシグナルについてエージェント議論
    4. 結果を logs/discussion_log.json に追記保存
    """
    import json as _json
    from pathlib import Path
    from datetime import datetime as _dt
    from agents.intelligence import IntelligenceAgent
    from agents.critic_intelligence import CriticIntelligenceAgent
    from agents.discussion import DiscussionOrchestratorAgent
    from agents.equity import EquityAgent
    from agents.fx_strategy import FXStrategyAgent
    from agents.risk_manager import RiskManagerAgent

    logger.info("=== インテリジェンスセッション開始 ===")

    intel_agent  = IntelligenceAgent()
    critic_intel = CriticIntelligenceAgent()
    discussion   = DiscussionOrchestratorAgent()
    cio          = CIOAgent()
    equity       = EquityAgent()
    fx           = FXStrategyAgent()
    risk         = RiskManagerAgent()

    # 1. MarketContext 生成
    logger.info("CIO: マーケットコンテキスト生成中...")
    macro_data = "USD/JPY=155.2, VIX=18.5, 米10Y=4.35%, S&P500先物=フラット"
    ctx = cio.generate_market_context(
        news_summary="（インテリジェンスセッション用コンテキスト）",
        macro_data=macro_data,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # 2. シグナル収集
    logger.info("情報収集中（arxiv / GitHub / HackerNews）...")
    raw_result   = intel_agent.collect(ctx)
    raw_signals  = raw_result.get("signals", [])
    logger.info(f"収集シグナル数: {len(raw_signals)} 件")

    if not raw_signals:
        logger.info("シグナルなし。セッション終了")
        return

    # 3. クリティーク審査
    logger.info("CriticIntelligence: シグナル審査中...")
    approved_signals = critic_intel.review(raw_signals, ctx)
    logger.info(f"承認シグナル数: {len(approved_signals)} 件")

    # 4. 高スコアシグナルの議論
    high_score = [s for s in approved_signals if s.get("relevance_score", 0) >= 0.75]
    logger.info(f"議論対象シグナル数: {len(high_score)} 件（score >= 0.75）")

    discussion_results: list[dict] = []
    for signal in high_score:
        logger.info(
            f"議論開始: [{signal.get('relevance_score', 0):.2f}] "
            f"{signal.get('title', '')[:60]}"
        )
        result = discussion.run(
            signal, ctx,
            cio_agent=cio,
            equity_agent=equity,
            fx_agent=fx,
            risk_agent=risk,
        )
        discussion_results.append(result)

    # 5. logs/discussion_log.json に追記保存
    log_path = Path("logs/discussion_log.json")
    log_path.parent.mkdir(exist_ok=True)

    session_record = {
        "session_date":      _dt.now().isoformat(),
        "signals_found":     len(raw_signals),
        "signals_approved":  len(approved_signals),
        "signals_discussed": len(high_score),
        "approved_signals":  approved_signals,
        "discussions":       discussion_results,
    }

    existing: list[dict] = []
    if log_path.exists():
        try:
            existing = _json.loads(log_path.read_text(encoding="utf-8"))
        except (_json.JSONDecodeError, OSError):
            existing = []

    existing.append(session_record)
    existing = existing[-30:]  # 最新 30 セッションのみ保持
    log_path.write_text(
        _json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"議論ログ保存: {log_path}")

    # 6. セッションサマリー出力
    execute_count = sum(1 for r in discussion_results if r.get("verdict") == "execute")
    defer_count   = sum(1 for r in discussion_results if r.get("verdict") == "defer")
    reject_count  = sum(1 for r in discussion_results if r.get("verdict") == "reject")
    logger.info(
        f"議論サマリー: execute={execute_count} / defer={defer_count} / reject={reject_count}"
    )
    for result in discussion_results:
        logger.info(
            f"  [{result.get('verdict', '?')}] {result.get('signal_title', '')[:60]}"
        )

    logger.info("=== インテリジェンスセッション終了 ===")


def run_morning_report() -> str:
    """
    朝 6 時レポート：
    1. テキストサマリーをログ出力
    2. CxOAgent でHTMLレポートを生成してメール送信
    """
    import json as _json
    from pathlib import Path
    from agents.cxo import CXOAgent

    lines = [f"=== 朝次レポート {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]

    log_path = Path("logs/discussion_log.json")
    if log_path.exists():
        try:
            sessions = _json.loads(log_path.read_text(encoding="utf-8"))
            if sessions:
                latest = sessions[-1]
                lines.append(f"\n【インテリジェンス議論サマリー（{latest['session_date'][:10]}）】")
                lines.append(f"  収集シグナル: {latest.get('signals_found', 0)} 件")
                lines.append(f"  承認シグナル: {latest.get('signals_approved', 0)} 件")
                for disc in latest.get("discussions", []):
                    verdict = disc.get("verdict", "?")
                    title   = disc.get("signal_title", "")[:50]
                    score   = disc.get("signal_score", 0)
                    lines.append(f"  [{verdict}] ({score:.2f}) {title}")
        except (_json.JSONDecodeError, OSError, KeyError):
            lines.append("  議論ログ読み込み失敗")
    else:
        lines.append("  議論ログなし（インテリジェンスセッション未実行）")

    report = "\n".join(lines)
    logger.info(report)

    # HTMLレポートをメール送信
    try:
        CXOAgent().generate_morning_report()
    except Exception as exc:
        logger.error(f"朝次HTMLレポート送信失敗: {exc}", exc_info=True)

    return report


def run_evening_report() -> None:
    """
    夜 21 時レポート：CxOAgent でHTMLレポートを生成してメール送信する。
    """
    from agents.cxo import CXOAgent
    logger.info("=== 夜間レポート生成開始 ===")
    try:
        CXOAgent().generate_evening_report()
    except Exception as exc:
        logger.error(f"夜間HTMLレポート送信失敗: {exc}", exc_info=True)


# ──────────────────────────────────────────────────
# スケジューラー（指定時刻まで待機して実行）
# ──────────────────────────────────────────────────

def wait_until(hhmm: str) -> None:
    """
    指定した時刻（HH:MM、当日の日本時間）まで待機する。
    例: wait_until("22:30")
    """
    h, m = int(hhmm.split(":")[0]), int(hhmm.split(":")[1])
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        logger.info(f"指定時刻 {hhmm} はすでに過ぎています。即時実行します")
        return
    wait_sec = (target - now).seconds
    logger.info(f"指定時刻 {hhmm} まで {wait_sec // 60} 分 {wait_sec % 60} 秒待機します...")
    time.sleep(wait_sec)
    logger.info(f"待機完了。{hhmm} になりました")


# ──────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="マルチエージェント投資システム")
    parser.add_argument(
        "--mode",
        choices=[
            "daytrade", "paper", "us_paper",
            "intelligence", "morning_report", "evening_report",
        ],
        default="paper",
        help=(
            "実行モード: daytrade=日本株本番, paper=日本株ペーパー, "
            "us_paper=米国株ペーパー, intelligence=情報収集・議論, "
            "morning_report=朝次レポート(06:00), evening_report=夜間レポート(21:00)"
        ),
    )
    parser.add_argument(
        "--schedule",
        metavar="HH:MM",
        help="指定時刻まで待機してから実行する（例: --schedule 22:30）",
    )
    args = parser.parse_args()

    if args.schedule:
        wait_until(args.schedule)

    if args.mode == "us_paper":
        run_us_paper_session()
    elif args.mode == "intelligence":
        run_intelligence_session()
    elif args.mode == "morning_report":
        run_morning_report()
    elif args.mode == "evening_report":
        run_evening_report()
    else:
        run_daytrade_session(paper=(args.mode == "paper"))
