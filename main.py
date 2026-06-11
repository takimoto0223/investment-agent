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

# モード別ログファイル（並列起動時の競合回避）
_mode_for_log = next(
    (sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--mode" and i + 1 < len(sys.argv)),
    "session",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/{_mode_for_log}.log", encoding="utf-8"),
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
    """日本株引け30分前かどうか。"""
    now = datetime.now()
    return now.hour == 15 and now.minute >= 0


# ── 米国株セッション時刻ユーティリティ ─────────────────────────────

def _us_session_end() -> datetime:
    """
    当日の米国株セッション終了時刻（JST 06:00）を返す。
    06:00 を過ぎていたら翌日の 06:00 を返す。
    """
    now = datetime.now()
    end = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= end:
        from datetime import timedelta
        end += timedelta(days=1)
    return end

def is_near_us_close() -> bool:
    """米国株引け前 30 分以内かどうか（JST 05:30 以降）。"""
    now = datetime.now()
    h, m = now.hour, now.minute
    return (h == 5 and m >= 30) or h == 6

def is_us_session_active() -> bool:
    """米国株の取引セッション中かどうか（JST 22:00〜06:00）。"""
    now = datetime.now()
    h = now.hour
    return h >= 22 or h < 6


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

# デイトレ対象ユニバース（モメンタム・出来高重視）
_US_BASE_SYMBOLS = [
    {"symbol": "NVDA",  "name": "エヌビディア"},
    {"symbol": "AAPL",  "name": "アップル"},
    {"symbol": "MSFT",  "name": "マイクロソフト"},
    {"symbol": "TSLA",  "name": "テスラ"},
    {"symbol": "META",  "name": "メタ"},
    {"symbol": "AMZN",  "name": "アマゾン"},
    {"symbol": "GOOGL", "name": "アルファベット"},
]

# バリュー投資ユニバース（中長期保有候補、セクター分散）
_US_VALUE_UNIVERSE = [
    {"symbol": "NVDA",  "name": "NVIDIA",           "sector": "semiconductors"},
    {"symbol": "MSFT",  "name": "Microsoft",        "sector": "software"},
    {"symbol": "AAPL",  "name": "Apple",            "sector": "consumer_tech"},
    {"symbol": "GOOGL", "name": "Alphabet",         "sector": "internet"},
    {"symbol": "META",  "name": "Meta",             "sector": "internet"},
    {"symbol": "AMZN",  "name": "Amazon",           "sector": "ecommerce_cloud"},
    {"symbol": "AMD",   "name": "AMD",              "sector": "semiconductors"},
    {"symbol": "AVGO",  "name": "Broadcom",         "sector": "semiconductors"},
    {"symbol": "ORCL",  "name": "Oracle",           "sector": "software"},
    {"symbol": "CRM",   "name": "Salesforce",       "sector": "software"},
    {"symbol": "JPM",   "name": "JPMorgan Chase",   "sector": "financials"},
    {"symbol": "UNH",   "name": "UnitedHealth",     "sector": "healthcare"},
    {"symbol": "PG",    "name": "Procter & Gamble", "sector": "consumer_staples"},
    {"symbol": "KO",    "name": "Coca-Cola",        "sector": "consumer_staples"},
    {"symbol": "XOM",   "name": "ExxonMobil",       "sector": "energy"},
]


def _refine_and_review(
    proposer,
    proposal,
    critic,
    ctx,
    acct: dict,
    fx_signal: dict,
    max_rounds: int = 2,
) -> tuple:
    """
    提案 → CriticUS審査 → 否決なら提案者に修正依頼 → 再審査 のループ。
    (最終proposal, 最終verdict) を返す。

    - 市場時間外など fixable=False の否決は即打ち切り
    - proposer が revise_proposal() を持たない場合もそのまま返す
    """
    from agents.base import CriticVerdict

    for round_n in range(1, max_rounds + 1):
        verdict = critic.review(proposal, ctx, acct, fx_signal)
        if verdict.approved:
            if round_n > 1:
                logger.info(f"[{proposal.symbol}] Round {round_n}: 修正後に承認")
            return proposal, verdict

        logger.info(
            f"[{proposal.symbol}] Round {round_n} 否決 — "
            f"{', '.join(verdict.issues[:2])}"
        )

        if not verdict.fixable:
            logger.info(f"[{proposal.symbol}] 修正不能（fixable=False）→ 終了")
            return proposal, verdict

        if not hasattr(proposer, "revise_proposal"):
            return proposal, verdict

        revised = proposer.revise_proposal(
            proposal, verdict.issues, verdict.suggestion, ctx
        )
        if revised is None:
            logger.info(f"[{proposal.symbol}] 提案者が修正断念 → 否決確定")
            return proposal, verdict

        proposal = revised

    # max_rounds 消化後の最終審査
    verdict = critic.review(proposal, ctx, acct, fx_signal)
    return proposal, verdict


def run_us_value_session():
    """
    米国株バリュー投資セッション（毎日22:00 JST想定）。
    USEquityAgent で中長期保有候補を選定 → パネル議論 → CriticUS審査 → Alpaca発注。
    結果は logs/us_value_log.json に保存する。
    """
    import json as _json
    from pathlib import Path
    from datetime import datetime as _dt
    from brokers.alpaca import AlpacaBroker
    from agents.us_equity import USEquityAgent
    from agents.critic_us import CriticUSAgent
    from agents.fx_strategy import FXStrategyAgent
    from config.settings import RISK

    logger.info("=== 米国株バリュー投資セッション開始 ===")

    broker = AlpacaBroker()
    acct = broker.get_account()
    cash_usd = float(acct.get("cash", 0))
    logger.info(
        f"Alpaca口座: equity=${float(acct.get('equity',0)):,.2f} "
        f"cash=${cash_usd:,.2f}"
    )

    if cash_usd < 200:
        logger.info("買い付け余力不足（$200未満）。セッション終了")
        return

    # MarketContext
    cio = CIOAgent()
    macro_data = "USD/JPY=155.2, VIX=18.5, 米10Y=4.35%"
    ctx = cio.generate_market_context(
        news_summary="（バリュー投資セッション）",
        macro_data=macro_data,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # FXシグナル
    fx_agent = FXStrategyAgent()
    fx_signal = fx_agent.generate_signal(macro_data, 0.35, ctx)

    # 既存ポジション
    existing_positions = broker.get_positions()
    existing_symbols = [p.get("symbol", "") for p in existing_positions]
    logger.info(f"既存ポジション: {existing_symbols}")

    # バリュー候補スクリーニング
    max_pos_usd = float(getattr(RISK, "max_us_position_usd", 3000))
    us_equity = USEquityAgent()
    proposals = us_equity.screen_value(
        universe=_US_VALUE_UNIVERSE,
        ctx=ctx,
        existing_symbols=existing_symbols,
        max_position_usd=max_pos_usd,
        cash_usd=cash_usd,
    )
    logger.info(f"バリュー候補提案数: {len(proposals)}")

    if not proposals:
        logger.info("バリュー投資候補なし。セッション終了")
        _save_us_value_log([], cash_usd, ctx.risk_level)
        return

    critic = CriticUSAgent()
    executed: list[dict] = []
    rejected: list[dict] = []

    for proposal in proposals:
        logger.info(f"審議開始: {proposal.symbol} — {proposal.rationale}")

        # パネル議論（CIO・FX・リスク視点）
        risk_notes = f"既存ポジション数: {len(existing_symbols)}, 現金: ${cash_usd:,.0f}"
        panel = us_equity.panel_review(proposal, ctx, fx_signal, risk_notes)
        consensus = panel.get("consensus", "defer")
        logger.info(
            f"  CIO:{panel['cio_opinion']} FX:{panel['fx_opinion']} "
            f"Risk:{panel['risk_opinion']} → {consensus}"
        )
        logger.info(f"  議論結論: {panel.get('consensus_reason', '')}")

        if consensus == "reject":
            logger.info(f"{proposal.symbol}: パネル議論で否決")
            rejected.append({"symbol": proposal.symbol, "reason": panel.get("consensus_reason", "")})
            continue

        # CriticUS最終審査（否決なら us_equity に修正依頼して再審査）
        proposal, critic_verdict = _refine_and_review(
            us_equity, proposal, critic, ctx, acct, fx_signal
        )
        if not critic_verdict.approved:
            logger.info(f"{proposal.symbol}: CriticUS最終否決 — {critic_verdict.suggestion}")
            rejected.append({"symbol": proposal.symbol, "reason": critic_verdict.suggestion})
            continue

        # 成行発注
        result = broker.send_market_order(proposal.symbol, proposal.qty, "buy")
        if result.success:
            logger.info(
                f"[バリュー買い] {proposal.symbol} x{proposal.qty} "
                f"order_id={result.order_id}"
            )
            executed.append({
                "symbol":     proposal.symbol,
                "name":       proposal.extra.get("name", proposal.symbol),
                "qty":        proposal.qty,
                "rationale":  proposal.rationale,
                "order_id":   result.order_id,
                "consensus":  consensus,
                "stop_loss_pct":       proposal.extra.get("stop_loss_pct", 0.08),
                "target_return_pct":   proposal.extra.get("target_return_pct", 0.15),
            })
        else:
            logger.warning(f"{proposal.symbol}: 発注失敗 — {result.message}")
            rejected.append({"symbol": proposal.symbol, "reason": result.message})

    _save_us_value_log(executed, cash_usd, ctx.risk_level, rejected)

    # ── バリューポジション監視ループ（6時まで、ポジションは閉じない） ──
    if not executed:
        logger.info("=== 米国株バリュー投資セッション終了（発注なし） ===")
        return

    # 銘柄ごとのストップロス率を記録
    stop_loss_map = {e["symbol"]: e.get("stop_loss_pct", 0.08) for e in executed}
    session_end   = _us_session_end()
    logger.info(f"バリューポジション監視開始（{session_end.strftime('%H:%M')}まで）: {list(stop_loss_map.keys())}")

    while datetime.now() < session_end:
        time.sleep(1800)  # 30分ごとチェック
        try:
            positions = broker.get_positions()
            pos_map   = {p["symbol"]: p for p in positions}
            for symbol, sl_pct in list(stop_loss_map.items()):
                pos = pos_map.get(symbol)
                if not pos:
                    continue
                entry   = float(pos["avg_entry_price"])
                current = float(pos["current_price"])
                chg     = (current - entry) / entry if entry > 0 else 0.0
                unpl    = float(pos["unrealized_pl"])
                logger.info(f"  {symbol}: {chg:+.2%} 含み損益 ${unpl:+.2f}")
                if chg <= -sl_pct:
                    logger.warning(f"バリューSL発動: {symbol} {chg:+.2%}（設定SL:{sl_pct:.0%}）→ 売却")
                    broker.close_position(symbol)
                    del stop_loss_map[symbol]
        except Exception as e:
            logger.error(f"バリュー監視エラー: {e}")

    logger.info(f"=== 米国株バリュー投資セッション終了: 発注{len(executed)}件 ===")


def _save_us_value_log(
    executed: list[dict],
    cash_usd: float,
    risk_level: str,
    rejected: list[dict] | None = None,
) -> None:
    import json as _json
    from pathlib import Path
    from datetime import datetime as _dt

    log_path = Path("logs/us_value_log.json")
    log_path.parent.mkdir(exist_ok=True)
    existing: list[dict] = []
    if log_path.exists():
        try:
            existing = _json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.append({
        "session_date": _dt.now().isoformat(),
        "risk_level":   risk_level,
        "cash_usd":     cash_usd,
        "executed":     executed,
        "rejected":     rejected or [],
    })
    log_path.write_text(
        _json.dumps(existing[-30:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"バリューログ保存: {log_path}")


def run_us_paper_session():
    """
    米国株デイトレペーパーセッション（毎日22:30 JST想定）。
    Alpaca paper-API に接続し、エージェントの提案を実際にペーパー発注する。
    結果は logs/us_daytrade_log.json に保存する。
    """
    import json as _json
    from pathlib import Path
    from datetime import datetime as _dt
    from brokers.alpaca import AlpacaBroker
    from data import us_market as us_mkt
    from agents.critic_us import CriticUSAgent
    from agents.fx_strategy import FXStrategyAgent

    logger.info("=== 米国株デイトレセッション開始 ===")

    cio            = CIOAgent()
    daytrade_agent = DaytradeAgent()
    critic         = CriticUSAgent()
    fx_agent       = FXStrategyAgent()
    broker         = AlpacaBroker()

    # 1. 口座確認
    acct = broker.get_account()
    logger.info(
        f"Alpaca口座: equity=${float(acct.get('equity',0)):,.2f} "
        f"cash=${float(acct.get('cash',0)):,.2f} "
        f"buying_power=${float(acct.get('buying_power',0)):,.2f}"
    )

    # 2. MarketContext 生成
    logger.info("CIO: マーケットコンテキスト生成中...")
    macro_data = "USD/JPY=155.2, VIX=18.5, 米10Y=4.35%, S&P500先物=+0.3%"
    ctx = cio.generate_market_context(
        news_summary="（米国株デイトレセッション）",
        macro_data=macro_data,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    if ctx.risk_level == "high":
        logger.warning("リスク水準 HIGH のためセッションを中止します")
        _save_us_daytrade_log([], ctx.risk_level, "high_risk_abort")
        return

    # 3. FX シグナル
    logger.info("FX戦略シグナル取得中...")
    fx_signal = fx_agent.generate_signal(macro_data, 0.35, ctx)
    logger.info(
        f"FXシグナル: {fx_signal.get('fx_signal')} "
        f"us_weight_bias={fx_signal.get('us_weight_bias')}"
    )

    # 4. バリューポジション銘柄を除外（デイトレ対象から外す）
    value_positions = {p.get("symbol") for p in broker.get_positions()}

    # 5. ユニバース構築・スクリーニング
    logger.info("米国株ユニバース構築中...")
    universe = us_mkt.build_us_universe(_US_BASE_SYMBOLS)
    candidates = [c for c in daytrade_agent.screen_candidates(universe, ctx)
                  if c not in value_positions]
    logger.info(f"デイトレ候補銘柄: {candidates}")
    if not candidates:
        logger.info("本日のデイトレ対象なし。終了します")
        _save_us_daytrade_log([], ctx.risk_level, "no_candidates")
        return

    # 6. 各候補: 提案 → CriticUS → ペーパー発注
    executed: list[dict] = []
    rejected: list[dict] = []

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

            # USD建て株数を max_us_position_usd 上限で再計算
            # DaytradeAgent の qty は JPY 前提なので USD 銘柄には使えない
            max_pos_usd = float(getattr(RISK, "max_us_position_usd", 3000))
            current_price_usd = quote.get("CurrentPrice", 0)
            if current_price_usd > 0:
                proposal.qty = max(1, int(max_pos_usd / current_price_usd))

            # stop_loss が未設定なら ATR × 1.5 で補完
            if proposal.stop_loss is None and current_price_usd > 0:
                uni_item = next((u for u in universe if u["symbol"] == symbol), {})
                atr_pct = float(uni_item.get("atr_pct", 2.0))
                sl_price = round(current_price_usd * (1 - 1.5 * atr_pct / 100), 4)
                proposal.stop_loss = sl_price
                logger.info(f"{symbol}: ATRベースstop_loss={sl_price:.4f} (ATR={atr_pct:.2f}%)")

            # CriticUS審査（否決なら daytrade_agent に修正依頼して再審査）
            proposal, verdict = _refine_and_review(
                daytrade_agent, proposal, critic, ctx, acct, fx_signal
            )
            if not verdict.approved:
                logger.info(f"{symbol}: クリティーク最終否決 — {verdict.suggestion}")
                rejected.append({"symbol": symbol, "reason": verdict.suggestion})
                continue

            # ペーパー発注
            if proposal.price and proposal.price > 0:
                result = broker.send_limit_order(symbol, proposal.qty, proposal.side, proposal.price)
            else:
                result = broker.send_market_order(symbol, proposal.qty, proposal.side)

            if result.success:
                logger.info(
                    f"[デイトレ発注] {symbol} {proposal.side} x{proposal.qty} "
                    f"order_id={result.order_id}"
                )
                executed.append({
                    "symbol":    symbol,
                    "name":      sym_name,
                    "side":      proposal.side,
                    "qty":       proposal.qty,
                    "rationale": proposal.rationale,
                    "order_id":  result.order_id,
                })
            else:
                logger.warning(f"{symbol}: 発注失敗 — {result.message}")
                rejected.append({"symbol": symbol, "reason": result.message})

        except Exception as e:
            logger.error(f"{symbol} 処理中エラー: {e}", exc_info=True)

    _save_us_daytrade_log(executed, ctx.risk_level, "orders_sent", rejected)

    # ── ポジション監視ループ（引けまで） ──────────────────────────
    daytrade_symbols: set[str] = {e["symbol"] for e in executed}
    _STOP_LOSS_PCT  = -0.015   # -1.5% で損切り
    _TAKE_PROFIT_PCT = 0.025   # +2.5% で利確

    logger.info(f"監視ループ開始: {daytrade_symbols} （引けまで継続）")
    while daytrade_symbols and not is_near_us_close():
        time.sleep(120)  # 2分ごとチェック
        try:
            positions = broker.get_positions()
            pos_map   = {p["symbol"]: p for p in positions}
            for symbol in list(daytrade_symbols):
                pos = pos_map.get(symbol)
                if not pos:
                    daytrade_symbols.discard(symbol)
                    continue
                entry   = float(pos["avg_entry_price"])
                current = float(pos["current_price"])
                chg     = (current - entry) / entry if entry > 0 else 0.0
                if chg <= _STOP_LOSS_PCT:
                    logger.warning(f"損切り: {symbol} {chg:+.2%} → 全決済")
                    broker.close_position(symbol)
                    daytrade_symbols.discard(symbol)
                elif chg >= _TAKE_PROFIT_PCT:
                    logger.info(f"利確: {symbol} {chg:+.2%} → 全決済")
                    broker.close_position(symbol)
                    daytrade_symbols.discard(symbol)
        except Exception as e:
            logger.error(f"監視ループエラー: {e}")

    # 引け前強制決済（デイトレポジションのみ）
    logger.info("引け前強制決済フェーズ")
    for symbol in list(daytrade_symbols):
        try:
            positions = broker.get_positions()
            if any(p["symbol"] == symbol for p in positions):
                logger.info(f"引け強制決済: {symbol}")
                broker.close_position(symbol)
        except Exception as e:
            logger.error(f"引け決済エラー {symbol}: {e}")

    _save_us_daytrade_log(executed, ctx.risk_level, "completed", rejected)
    logger.info(f"=== 米国株デイトレセッション終了: 発注{len(executed)}件 ===")


def _save_us_daytrade_log(
    executed: list[dict],
    risk_level: str,
    status: str,
    rejected: list[dict] | None = None,
) -> None:
    import json as _json
    from pathlib import Path
    from datetime import datetime as _dt

    log_path = Path("logs/us_daytrade_log.json")
    log_path.parent.mkdir(exist_ok=True)
    existing: list[dict] = []
    if log_path.exists():
        try:
            existing = _json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.append({
        "session_date": _dt.now().isoformat(),
        "risk_level":   risk_level,
        "status":       status,
        "executed":     executed,
        "rejected":     rejected or [],
    })
    log_path.write_text(
        _json.dumps(existing[-30:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"デイトレログ保存: {log_path}")


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

    logger.info("=== インテリジェンスセッション（初回）終了 ===")

    # 2時間ごとに再収集（6時まで）
    session_end = _us_session_end()
    while datetime.now() < session_end:
        wait_sec = min(7200, int((session_end - datetime.now()).total_seconds()))
        if wait_sec < 600:
            break
        logger.info(f"次回インテリジェンス収集まで {wait_sec // 60} 分待機...")
        time.sleep(wait_sec if wait_sec <= 7200 else 7200)
        if datetime.now() >= session_end:
            break
        logger.info("=== インテリジェンス再収集 ===")
        try:
            raw_result  = intel_agent.collect(ctx)
            raw_signals = raw_result.get("signals", [])
            logger.info(f"再収集シグナル数: {len(raw_signals)} 件")
            approved    = critic_intel.review(raw_signals, ctx)
            high_score  = [s for s in approved if s.get("relevance_score", 0) >= 0.75]
            for signal in high_score:
                result = discussion.run(signal, ctx, cio_agent=cio,
                                        equity_agent=equity, fx_agent=fx, risk_agent=risk)
                discussion_results.append(result)
            # ログ追記保存（既存セッションを更新）
            log_path = Path("logs/discussion_log.json")
            existing = []
            if log_path.exists():
                try:
                    existing = _json.loads(log_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            if existing:
                existing[-1]["discussions"].extend(discussion_results[-len(high_score):])
                existing[-1]["signals_found"] += len(raw_signals)
            log_path.write_text(_json.dumps(existing[-30:], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"インテリジェンス再収集エラー: {e}")

    logger.info("=== インテリジェンスセッション終了（全サイクル完了） ===")


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
            "daytrade", "paper", "us_paper", "us_value",
            "intelligence", "morning_report", "evening_report",
        ],
        default="paper",
        help=(
            "実行モード: daytrade=日本株本番, paper=日本株ペーパー, "
            "us_paper=米国株デイトレ, us_value=米国株バリュー中長期, "
            "intelligence=情報収集・議論, "
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

    if args.mode == "us_value":
        run_us_value_session()
    elif args.mode == "us_paper":
        run_us_paper_session()
    elif args.mode == "intelligence":
        run_intelligence_session()
    elif args.mode == "morning_report":
        run_morning_report()
    elif args.mode == "evening_report":
        run_evening_report()
    else:
        run_daytrade_session(paper=(args.mode == "paper"))
