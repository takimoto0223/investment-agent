"""
main.py
マルチエージェント投資システムのオーケストレーター。

実行フロー（デイトレセッション）：
  1. CIO → MarketContext 生成
  2. ScalpDay_JP → 候補銘柄スクリーニング → TradeProposal 生成
  3. ScalpDay_JP_Critic → 審査・修正往復 → CriticVerdict
  4. 承認済みのみ KabuBroker → 発注
  5. 損切り監視ループ（引けまで継続）
  6. 引け前に全ポジション強制決済

実行コマンド:
  python main.py --mode scalpday_jp       # 日本株デイトレセッション
  python main.py --mode moment_swing_jp   # 日本株スイング投資セッション（09:05 想定）
  python main.py --mode scalpday_us       # 米国株デイトレペーパートレード（Alpaca）
  python main.py --mode moment_swing_us   # 米国株スイング投資セッション（23:30 想定）
  python main.py --mode fx_rebalance      # FXリバランスセッション（JP/US どちらか開場時）
  python main.py --mode intel_scout       # IntelScout 収集セッション（08:00/17:00 JST 想定）
  python main.py --mode morning_report    # 朝次レポート生成＋メール送信（06:00 想定）
  python main.py --mode evening_report    # 夜間レポート生成＋メール送信（21:00 想定）
"""
import argparse
import ctypes
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from agents.cio import CIOAgent
from agents.scalp_day import ScalpDay_JP, ScalpDay_US
from agents.critics import (
    ScalpDay_JP_Critic,
    ScalpDay_US_Critic,
    MomentSwing_US_Critic,
    MomentSwing_JP_Critic,
    IntelCritic,
    FXRebalance_Critic,
)
from brokers.kabu import KabuBroker
from config.settings import RISK
from data import market as mkt
from data.intel_store import get_news_summary_for_cio, load_state, save_state, IntelState, write_intel_digest, read_intel_digest
from data.market_clock import is_jp_open, is_us_open
from data.universe import build_pod_universe

# ── Windowsスリープ防止 ────────────────────────────
_ES_CONTINUOUS      = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

def _prevent_sleep():
    """長時間セッション中にWindowsのスリープを防ぐ（S3スリープ対策）。"""
    ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    logging.getLogger("main").info("スリープ防止: 有効")

def _allow_sleep():
    """スリープ防止を解除する（セッション終了時に呼ぶ）。"""
    ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    logging.getLogger("main").info("スリープ防止: 解除")


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

# マクロデータ（本番は外部APIから取得予定）
_MACRO_DATA = "USD/JPY=155.2, VIX=18.5, 米10Y=4.35%"


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
    """当日の米国株セッション終了時刻（JST 06:00）を返す。翌日跨ぎに対応。"""
    now = datetime.now()
    end = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= end:
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
# ScalpDay_JP（日本株デイトレ）セッション
# ──────────────────────────────────────────────────


def run_scalpday_jp_session(paper: bool = True):
    """ScalpDay_JP セッションのメインループ。"""
    if not is_jp_open():
        logger.info("東証 閉場/時間外 → ScalpDay_JP セッションをスキップ")
        return

    logger.info(f"=== ScalpDay_JP セッション開始 {'[ペーパー]' if paper else '[本番]'} ===")

    cio = CIOAgent()
    scalpday_jp_agent = ScalpDay_JP()
    critic = ScalpDay_JP_Critic()
    broker = KabuBroker()

    # 1. MarketContext 生成
    logger.info("CIO: マーケットコンテキスト生成中...")
    _news, _obs = get_news_summary_for_cio()
    ctx = cio.generate_market_context(
        news_summary=_news,
        macro_data=_MACRO_DATA,
        obs_source=_obs,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # 2. 配分ゲート（旧 risk_level == "high" チェックを吸収）
    if not paper:
        try:
            _wallet = broker.get_wallet_margin()
            _total_jpy = float(_wallet.get("MarginAccountWallet", 500000))
        except Exception:
            logger.warning("ウォレット取得失敗。ペーパー相当の上限で代替")
            _wallet = {"MarginAccountWallet": 500000}
            _total_jpy = 500000
    else:
        _wallet = {"MarginAccountWallet": 500000}
        _total_jpy = 500000

    allocs = cio.allocate_budgets(ctx, total_cash_jpy=_total_jpy, cash_usd=0)
    if allocs["ScalpDay_JP"].budget_jpy == 0:
        logger.warning(f"配分ゲート: ScalpDay_JP 枠ゼロ (risk={ctx.risk_level}) → セッション中止")
        return

    # 3. ユニバース構築（CIO セクターフィルタ + カタリスト例外枠）
    logger.info("ユニバース構築中（板情報・日足データ取得）...")
    alloc = allocs["ScalpDay_JP"]
    universe = build_pod_universe("JP", alloc.active_sectors, alloc.catalyst_slots)
    if not mkt.check_kabu_connection():
        logger.info("モードモック: kabuステーション未接続のためモックデータを使用")

    # 3. 候補銘柄スクリーニング
    candidates = scalpday_jp_agent.screen_candidates(universe, ctx)
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
            proposal = scalpday_jp_agent.generate_trade_proposal(
                symbol=symbol,
                symbol_name=next((u["name"] for u in universe if u["symbol"] == symbol), symbol),
                board_data=board_data,
                bars_5min=bars_5min,
                ctx=ctx,
            )
            if not proposal:
                continue

            # クリティーク審査
            approved_proposal, verdict = critic.refine_and_review(
                scalpday_jp_agent, proposal, ctx, wallet=_wallet
            )
            if approved_proposal is None:
                logger.info(f"{symbol}: クリティーク否決 - {verdict.suggestion}")
                continue
            proposal = approved_proposal

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

                if scalpday_jp_agent.should_emergency_exit(pos, current_price):
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

    logger.info("=== ScalpDay_JP セッション終了 ===")


# ──────────────────────────────────────────────────
# MomentSwing_JP（日本株スイング）セッション
# ──────────────────────────────────────────────────


def _save_moment_swing_jp_log(
    executed: list[dict],
    total_jpy: float,
    risk_level: str,
    rejected: list[dict] | None = None,
) -> None:
    log_path = Path("logs/moment_swing_jp_log.json")
    log_path.parent.mkdir(exist_ok=True)
    existing: list[dict] = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.append({
        "session_date": datetime.now().isoformat(),
        "risk_level":   risk_level,
        "total_jpy":    total_jpy,
        "executed":     executed,
        "rejected":     rejected or [],
    })
    log_path.write_text(
        json.dumps(existing[-30:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"MomentSwingJPログ保存: {log_path}")


def run_moment_swing_jp_session(paper: bool = True):
    """
    MomentSwing_JP セッション（毎朝 09:05 JST 想定）。
    MomentSwing_JP でモメンタム候補を選定 → Critic審査 → kabu 発注。
    ポジション監視は引けまで行い SL/TP 到達時はメール通知（自動決済なし）。
    結果は logs/moment_swing_jp_log.json に保存する。
    """
    if not is_jp_open():
        logger.info("東証 閉場/時間外 → MomentSwing_JP セッションをスキップ")
        return

    from agents.moment_swing import MomentSwing_JP
    from agents.fx_strategy import FXStrategyAgent

    logger.info(f"=== MomentSwing_JP セッション開始 {'[ペーパー]' if paper else '[本番]'} ===")
    _prevent_sleep()

    broker = KabuBroker()

    # ウォレット取得
    if not paper:
        try:
            _wallet = broker.get_wallet_margin()
            total_jpy = float(_wallet.get("MarginAccountWallet", 500_000))
        except Exception:
            logger.warning("ウォレット取得失敗。ペーパー上限で代替")
            _wallet = {"MarginAccountWallet": 500_000}
            total_jpy = 500_000
    else:
        _wallet = {"MarginAccountWallet": 500_000}
        total_jpy = 500_000

    # MarketContext（CIOキャッシュがあれば再利用）
    cio = CIOAgent()
    _news, _obs = get_news_summary_for_cio()
    ctx = cio.generate_market_context(
        news_summary=_news,
        macro_data=_MACRO_DATA,
        obs_source=_obs,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # FXシグナル（allocate_budgets の usd_jpy_rate に必要）
    fx_agent = FXStrategyAgent()
    fx_signal = fx_agent.generate_signal(_MACRO_DATA, 0.35, ctx)
    usd_jpy_rate = float(fx_signal.get("usd_jpy_rate", 155.0))

    # CIO 配分ゲート
    allocs = cio.allocate_budgets(
        ctx, total_cash_jpy=total_jpy, cash_usd=0, usd_jpy_rate=usd_jpy_rate
    )
    if allocs["MomentSwing_JP"].budget_jpy == 0:
        logger.warning(f"配分ゲート: MomentSwing_JP 枠ゼロ (risk={ctx.risk_level}) → セッション終了")
        _save_moment_swing_jp_log([], total_jpy, ctx.risk_level)
        _allow_sleep()
        return

    budget_jpy = allocs["MomentSwing_JP"].budget_jpy
    max_pos_jpy = float(getattr(RISK, "max_position_jpy", 500_000))
    logger.info(f"MomentSwing_JP 配分枠: ¥{budget_jpy:,.0f} / 1銘柄上限 ¥{max_pos_jpy:,.0f}")

    # 既存ポジション
    try:
        existing_positions = broker.get_positions() if not paper else []
    except Exception:
        existing_positions = []
    existing_symbols = [str(p.get("symbol", "")) for p in existing_positions]

    # スクリーニング
    swing_agent = MomentSwing_JP()
    jp_swing_universe = build_pod_universe(
        "JP", allocs["MomentSwing_JP"].active_sectors, allocs["MomentSwing_JP"].catalyst_slots
    )
    proposals = swing_agent.screen_value(
        universe=jp_swing_universe,
        ctx=ctx,
        existing_symbols=existing_symbols,
        max_position=max_pos_jpy,
        cash=budget_jpy,
    )
    logger.info(f"MomentSwingJP 候補提案数: {len(proposals)}")

    if not proposals:
        logger.info("MomentSwingJP 候補なし。セッション終了")
        _save_moment_swing_jp_log([], total_jpy, ctx.risk_level)
        _allow_sleep()
        return

    # 価格補完（Criticが price=0 を即否決するため審査前に取得）
    for proposal in proposals:
        try:
            board = mkt.get_board(proposal.symbol)
            current_price = float(board.get("CurrentPrice", 0))
            if current_price > 0:
                sl_pct = proposal.extra.get("stop_loss_pct", 0.06)
                tp_pct = proposal.extra.get("target_return_pct", 0.12)
                proposal.price       = current_price
                proposal.qty         = int(max_pos_jpy / current_price / 100) * 100
                proposal.qty         = max(100, proposal.qty)
                proposal.stop_loss   = round(current_price * (1 - sl_pct), 0)
                proposal.take_profit = round(current_price * (1 + tp_pct), 0)
                logger.info(
                    f"{proposal.symbol}: 価格補完 ¥{current_price:.0f} "
                    f"SL=¥{proposal.stop_loss:.0f} TP=¥{proposal.take_profit:.0f} qty={proposal.qty}"
                )
        except Exception as e:
            logger.warning(f"{proposal.symbol}: 価格補完失敗 {e}")

    critic = MomentSwing_JP_Critic()
    executed: list[dict] = []
    rejected: list[dict] = []

    for proposal in proposals:
        logger.info(f"審議開始: {proposal.symbol} — {proposal.rationale}")
        approved_proposal, verdict = critic.refine_and_review(
            swing_agent, proposal, ctx, wallet=_wallet
        )
        if approved_proposal is None:
            logger.info(f"{proposal.symbol}: Critic否決 — {verdict.suggestion}")
            rejected.append({"symbol": proposal.symbol, "reason": verdict.suggestion})
            continue
        proposal = approved_proposal

        if not paper:
            result = broker.send_margin_order(
                symbol=proposal.symbol,
                side="2",  # 買い
                qty=proposal.qty,
                price=proposal.price,
            )
            if result.success:
                logger.info(f"[MomentSwingJP 買い] {proposal.symbol} x{proposal.qty} order_id={result.order_id}")
                executed.append({
                    "symbol":            proposal.symbol,
                    "name":              proposal.extra.get("name", proposal.symbol),
                    "qty":               proposal.qty,
                    "price":             proposal.price,
                    "rationale":         proposal.rationale,
                    "order_id":          result.order_id,
                    "stop_loss_pct":     proposal.extra.get("stop_loss_pct", 0.06),
                    "target_return_pct": proposal.extra.get("target_return_pct", 0.12),
                })
            else:
                logger.warning(f"{proposal.symbol}: 発注失敗 — {result.message}")
                rejected.append({"symbol": proposal.symbol, "reason": result.message})
        else:
            logger.info(f"[ペーパー] MomentSwingJP 発注シミュレート: {proposal.symbol} ¥{proposal.price:.0f} x{proposal.qty}")
            executed.append({
                "symbol":            proposal.symbol,
                "name":              proposal.extra.get("name", proposal.symbol),
                "qty":               proposal.qty,
                "price":             proposal.price,
                "rationale":         proposal.rationale,
                "order_id":          "paper",
                "stop_loss_pct":     proposal.extra.get("stop_loss_pct", 0.06),
                "target_return_pct": proposal.extra.get("target_return_pct", 0.12),
            })

    _save_moment_swing_jp_log(executed, total_jpy, ctx.risk_level, rejected)

    # SL/TP 監視（引けまで 15 分ごとチェック、自動決済せず CxO 経由で通知）
    if executed:
        stop_loss_map   = {e["symbol"]: e["stop_loss_pct"]     for e in executed}
        take_profit_map = {e["symbol"]: e["target_return_pct"] for e in executed}
        entry_map       = {e["symbol"]: e["price"]             for e in executed}

        from agents.cxo import CXOAgent
        cxo = CXOAgent()
        pending_approval: set[str] = set()

        logger.info(f"MomentSwingJP 監視開始（引けまで）: {list(stop_loss_map.keys())}")
        while is_trading_hours():
            time.sleep(900)  # 15分ごとチェック
            try:
                for symbol in list(stop_loss_map.keys()):
                    board = mkt.get_board(symbol) if paper else broker.get_board(symbol)
                    current = float(board.get("CurrentPrice", 0))
                    if current <= 0:
                        continue
                    entry = entry_map.get(symbol, current)
                    chg   = (current - entry) / entry if entry > 0 else 0.0
                    sl    = stop_loss_map[symbol]
                    tp    = take_profit_map[symbol]
                    logger.info(f"  {symbol}: {chg:+.2%} (SL:{sl:.0%}/TP:{tp:.0%})")
                    if symbol not in pending_approval:
                        if chg <= -sl:
                            logger.warning(f"MomentSwingJP SL到達: {symbol} {chg:+.2%}")
                            cxo.notify_action_required(symbol, "stop_loss", chg, sl, current, (current - entry) * executed[0]["qty"])
                            pending_approval.add(symbol)
                        elif chg >= tp:
                            logger.info(f"MomentSwingJP TP到達: {symbol} {chg:+.2%}")
                            cxo.notify_action_required(symbol, "take_profit", chg, tp, current, (current - entry) * executed[0]["qty"])
                            pending_approval.add(symbol)
            except Exception as e:
                logger.error(f"MomentSwingJP 監視エラー: {e}")

    _allow_sleep()
    logger.info(f"=== MomentSwing_JP セッション終了: 発注{len(executed)}件 ===")


# ──────────────────────────────────────────────────
# MomentSwing_US（米国株スイング）セッション
# ──────────────────────────────────────────────────


def run_moment_swing_us_session():
    """
    MomentSwing_US セッション（毎日 23:30 JST 想定）。
    MomentSwing_US で中長期保有候補を選定 → Critic審査 → Alpaca発注。
    結果は logs/moment_swing_us_log.json に保存する。
    """
    if not is_us_open():
        logger.info("NYSE 閉場/時間外 → MomentSwing_US セッションをスキップ")
        return

    from brokers.alpaca import AlpacaBroker
    from agents.moment_swing import MomentSwing_US
    from agents.fx_strategy import FXStrategyAgent

    logger.info("=== MomentSwing_US セッション開始 ===")
    _prevent_sleep()

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
    _news, _obs = get_news_summary_for_cio()
    ctx = cio.generate_market_context(
        news_summary=_news,
        macro_data=_MACRO_DATA,
        obs_source=_obs,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # FXシグナル（配分ゲートで usd_jpy_rate を使うため先に取得）
    fx_agent = FXStrategyAgent()
    fx_signal = fx_agent.generate_signal(_MACRO_DATA, 0.35, ctx)
    usd_jpy_rate = float(fx_signal.get("usd_jpy_rate", 155.0))

    # 配分ゲート（usd_jpy_rate は FXStrategyAgent 由来）
    allocs = cio.allocate_budgets(ctx, total_cash_jpy=0, cash_usd=cash_usd, usd_jpy_rate=usd_jpy_rate)
    if allocs["MomentSwing_US"].budget_usd == 0:
        logger.warning(f"配分ゲート: MomentSwing_US 枠ゼロ (risk={ctx.risk_level}) → セッション終了")
        _save_moment_swing_us_log([], cash_usd, ctx.risk_level)
        _allow_sleep()
        return

    # 既存ポジション
    existing_positions = broker.get_positions()
    existing_symbols = [p.get("symbol", "") for p in existing_positions]
    logger.info(f"既存ポジション: {existing_symbols}")

    # スイング候補スクリーニング
    max_pos_usd = float(getattr(RISK, "max_us_position_usd", 3000))
    moment_swing_us_agent = MomentSwing_US()
    moment_swing_us_universe = build_pod_universe(
        "US", allocs["MomentSwing_US"].active_sectors, allocs["MomentSwing_US"].catalyst_slots
    )
    proposals = moment_swing_us_agent.screen_value(
        universe=moment_swing_us_universe,
        ctx=ctx,
        existing_symbols=existing_symbols,
        max_position=max_pos_usd,
        cash=cash_usd,
    )
    logger.info(f"MomentSwingUS 候補提案数: {len(proposals)}")

    if not proposals:
        logger.info("MomentSwingUS 候補なし。セッション終了")
        _save_moment_swing_us_log([], cash_usd, ctx.risk_level)
        return

    # ── 価格補完（CriticUS が price=0 を即否決するため、審査前に現在値を取得） ──
    from data import us_market as us_mkt
    for proposal in proposals:
        try:
            quote = us_mkt.get_quote_us(proposal.symbol)
            current_price = float(quote.get("CurrentPrice", 0))
            if current_price > 0:
                sl_pct  = proposal.extra.get("stop_loss_pct",    0.08)
                tp_pct  = proposal.extra.get("target_return_pct", 0.15)
                proposal.price       = current_price
                proposal.qty         = max(1, int(max_pos_usd / current_price))
                proposal.stop_loss   = round(current_price * (1 - sl_pct), 4)
                proposal.take_profit = round(current_price * (1 + tp_pct), 4)
                logger.info(
                    f"{proposal.symbol}: 価格補完 ${current_price:.2f} "
                    f"SL=${proposal.stop_loss:.2f} TP=${proposal.take_profit:.2f} qty={proposal.qty}"
                )
        except Exception as e:
            logger.warning(f"{proposal.symbol}: 価格補完失敗 {e}")

    # ── 準備フェーズ完了。市場オープン（ET 9:30 = JST 22:30）まで待機 ──
    wait_until("22:30")

    critic = MomentSwing_US_Critic()
    executed: list[dict] = []
    rejected: list[dict] = []

    for proposal in proposals:
        logger.info(f"審議開始: {proposal.symbol} — {proposal.rationale}")

        # CriticUS審査（否決なら moment_swing_us_agent に修正依頼して再審査）
        approved_proposal, critic_verdict = critic.refine_and_review(
            moment_swing_us_agent, proposal, ctx, account=acct, fx_signal=fx_signal
        )
        if approved_proposal is None:
            logger.info(f"{proposal.symbol}: CriticUS最終否決 — {critic_verdict.suggestion}")
            rejected.append({"symbol": proposal.symbol, "reason": critic_verdict.suggestion})
            continue
        proposal = approved_proposal

        # 成行発注
        result = broker.send_market_order(proposal.symbol, proposal.qty, "buy")
        if result.success:
            logger.info(
                f"[MomentSwingUS 買い] {proposal.symbol} x{proposal.qty} "
                f"order_id={result.order_id}"
            )
            executed.append({
                "symbol":     proposal.symbol,
                "name":       proposal.extra.get("name", proposal.symbol),
                "qty":        proposal.qty,
                "rationale":  proposal.rationale,
                "order_id":   result.order_id,
                "stop_loss_pct":       proposal.extra.get("stop_loss_pct", 0.08),
                "target_return_pct":   proposal.extra.get("target_return_pct", 0.15),
            })
        else:
            logger.warning(f"{proposal.symbol}: 発注失敗 — {result.message}")
            rejected.append({"symbol": proposal.symbol, "reason": result.message})

    _save_moment_swing_us_log(executed, cash_usd, ctx.risk_level, rejected)

    # ── MomentSwingUS ポジション監視ループ（6時まで、ポジションは閉じない） ──
    if not executed:
        logger.info("=== MomentSwing_US セッション終了（発注なし） ===")
        return

    # 当日発注分の SL/TP マップ
    stop_loss_map   = {e["symbol"]: e.get("stop_loss_pct",    0.08) for e in executed}
    take_profit_map = {e["symbol"]: e.get("target_return_pct", 0.15) for e in executed}

    # 前日以前から保有中のポジションを moment_swing_us_log から復元（翌日監視対応）
    _log = Path("logs/moment_swing_us_log.json")
    if _log.exists():
        try:
            all_sessions = json.loads(_log.read_text(encoding="utf-8"))
            historical: dict[str, dict] = {}
            for sess in all_sessions:
                for entry in sess.get("executed", []):
                    historical[entry["symbol"]] = entry
            held_symbols = {p["symbol"] for p in existing_positions}
            for sym in held_symbols:
                if sym not in stop_loss_map and sym in historical:
                    h = historical[sym]
                    stop_loss_map[sym]   = h.get("stop_loss_pct",    0.08)
                    take_profit_map[sym] = h.get("target_return_pct", 0.15)
                    logger.info(
                        f"前日ポジション監視追加: {sym} "
                        f"SL={stop_loss_map[sym]:.0%} TP={take_profit_map[sym]:.0%}"
                    )
        except Exception as e:
            logger.warning(f"MomentSwingUSログ読み込み失敗（前日ポジション復元スキップ）: {e}")

    from agents.cxo import CXOAgent
    cxo = CXOAgent()
    pending_approval: set[str] = set()  # 通知済みで承認待ちの銘柄（重複通知防止）

    session_end = _us_session_end()
    logger.info(f"MomentSwingUS 監視開始（{session_end.strftime('%H:%M')}まで）: {list(stop_loss_map.keys())}")

    while datetime.now() < session_end:
        time.sleep(1800)  # 30分ごとチェック
        try:
            positions = broker.get_positions()
            pos_map   = {p["symbol"]: p for p in positions}
            for symbol in list(stop_loss_map.keys()):
                pos = pos_map.get(symbol)
                if not pos:
                    # ポジションが消えた（ユーザーが手動決済）→ 監視解除
                    stop_loss_map.pop(symbol, None)
                    take_profit_map.pop(symbol, None)
                    pending_approval.discard(symbol)
                    continue
                entry   = float(pos["avg_entry_price"])
                current = float(pos["current_price"])
                chg     = (current - entry) / entry if entry > 0 else 0.0
                unpl    = float(pos["unrealized_pl"])
                sl_pct  = stop_loss_map[symbol]
                tp_pct  = take_profit_map.get(symbol, 0.15)
                logger.info(f"  {symbol}: {chg:+.2%} 含み損益 ${unpl:+.2f} (SL:{sl_pct:.0%}/TP:{tp_pct:.0%})")

                # SL/TP 条件到達 → CxO 経由でメール通知（自動決済しない）
                if symbol not in pending_approval:
                    if chg <= -sl_pct:
                        logger.warning(f"MomentSwingUS SL条件到達: {symbol} {chg:+.2%} — 要承認メール送信")
                        cxo.notify_action_required(
                            symbol, "stop_loss", chg, sl_pct, current, unpl
                        )
                        pending_approval.add(symbol)
                    elif chg >= tp_pct:
                        logger.info(f"MomentSwingUS TP条件到達: {symbol} {chg:+.2%} — 要承認メール送信")
                        cxo.notify_action_required(
                            symbol, "take_profit", chg, tp_pct, current, unpl
                        )
                        pending_approval.add(symbol)
        except Exception as e:
            logger.error(f"MomentSwingUS 監視エラー: {e}")

    _allow_sleep()
    logger.info(f"=== MomentSwing_US セッション終了: 発注{len(executed)}件 ===")


def _save_moment_swing_us_log(
    executed: list[dict],
    cash_usd: float,
    risk_level: str,
    rejected: list[dict] | None = None,
) -> None:
    log_path = Path("logs/moment_swing_us_log.json")
    log_path.parent.mkdir(exist_ok=True)
    existing: list[dict] = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.append({
        "session_date": datetime.now().isoformat(),
        "risk_level":   risk_level,
        "cash_usd":     cash_usd,
        "executed":     executed,
        "rejected":     rejected or [],
    })
    log_path.write_text(
        json.dumps(existing[-30:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"MomentSwingUSログ保存: {log_path}")


def _generate_session_report(
    executed: list[dict],
    broker,
    session_start: "datetime",
    fx_signal: dict,
    ctx,
) -> None:
    """10分セッション終了後のサマリーレポートを生成・出力する。"""
    from brokers.alpaca import calc_daytrade_pl

    elapsed_min = (datetime.now() - session_start).total_seconds() / 60

    activities = []
    try:
        activities = broker.get_activities(since_hours=1)
    except Exception as e:
        logger.warning(f"約定履歴取得失敗: {e}")

    pl = calc_daytrade_pl(activities)

    lines = [
        "",
        "══════════════════════════════════════════════",
        "  ScalpDay_US セッションレポート",
        f"  開始: {session_start.strftime('%Y-%m-%d %H:%M')} "
        f"  終了: {datetime.now().strftime('%H:%M')}  ({elapsed_min:.0f}分)",
        "══════════════════════════════════════════════",
        f"  リスク水準: {ctx.risk_level}  ローテーション: {ctx.rotation_signal}",
        f"  FXシグナル: {fx_signal.get('fx_signal', 'N/A')}"
        f"  US配分バイアス: {fx_signal.get('us_weight_bias', 'N/A')}",
        f"  発注件数: {len(executed)} 件",
        "",
        "── デイトレ損益 ────────────────────────────",
        f"  グロス損益: ${pl['total_gross']:+.2f}",
        f"  手数料:     ${pl['total_fees']:.4f}",
        f"  ネット損益: ${pl['total_net']:+.2f}",
    ]

    if pl["trades"]:
        lines += ["", "── トレード詳細 ─────────────────────────────"]
        for t in pl["trades"]:
            lines.append(
                f"  {t['symbol']}: 買${t['buy_price']:.2f} → 売${t['sell_price']:.2f}"
                f"  x{t['qty']}株  純損益 ${t['net_pl']:+.2f}"
            )

    # 未決済残ポジション
    try:
        positions = broker.get_positions()
        if positions:
            lines += ["", "── 未決済ポジション（決済済みのはず） ─────────"]
            for p in positions:
                lines.append(
                    f"  {p['symbol']}: 含み損益 ${float(p['unrealized_pl']):+.2f}"
                    f"  現在 ${float(p['current_price']):.2f}"
                    f"  取得単価 ${float(p['avg_entry_price']):.2f}"
                )
    except Exception:
        pass

    # 発注詳細
    if executed:
        lines += ["", "── 発注ログ ─────────────────────────────────"]
        for e in executed:
            lines.append(
                f"  {e['symbol']} {e['side']} x{e['qty']}"
                f"  order_id={e['order_id']}"
            )
            lines.append(f"    → {e['rationale'][:80]}")

    lines.append("══════════════════════════════════════════════")

    report_text = "\n".join(lines)
    logger.info(report_text)

    # ファイル保存
    report_path = Path(f"logs/session_report_{session_start.strftime('%Y%m%d_%H%M')}.txt")
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    logger.info(f"レポート保存: {report_path}")


# ──────────────────────────────────────────────────
# ScalpDay_US（米国株デイトレ）セッション
# ──────────────────────────────────────────────────


def run_scalpday_us_session(duration_minutes: int | None = None):
    """
    ScalpDay_US セッション（毎日 23:35 JST 想定）。
    Alpaca paper-API に接続し、エージェントの提案を実際にペーパー発注する。
    duration_minutes: 指定時間（分）が経過したら全決済してレポートを出す。
    結果は logs/scalpday_us_log.json に保存する。
    """
    if not is_us_open():
        logger.info("NYSE 閉場/時間外 → ScalpDay_US セッションをスキップ")
        return

    from brokers.alpaca import AlpacaBroker
    from data import us_market as us_mkt
    from agents.fx_strategy import FXStrategyAgent

    label = f"[{duration_minutes}分限定]" if duration_minutes else ""
    logger.info(f"=== ScalpDay_US セッション開始 {label} ===")
    _prevent_sleep()

    cio              = CIOAgent()
    scalpday_us_agent = ScalpDay_US()
    critic            = ScalpDay_US_Critic()
    fx_agent          = FXStrategyAgent()
    broker            = AlpacaBroker()

    # 1. 口座確認
    acct = broker.get_account()
    logger.info(
        f"Alpaca口座: equity=${float(acct.get('equity',0)):,.2f} "
        f"cash=${float(acct.get('cash',0)):,.2f} "
        f"buying_power=${float(acct.get('buying_power',0)):,.2f}"
    )

    # 2. MarketContext 生成
    logger.info("CIO: マーケットコンテキスト生成中...")
    _news, _obs = get_news_summary_for_cio()
    ctx = cio.generate_market_context(
        news_summary=_news,
        macro_data=_MACRO_DATA,
        obs_source=_obs,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # 3. FX シグナル（配分ゲートで usd_jpy_rate を使うため先に取得）
    logger.info("FX戦略シグナル取得中...")
    fx_signal = fx_agent.generate_signal(_MACRO_DATA, 0.35, ctx)
    logger.info(
        f"FXシグナル: {fx_signal.get('fx_signal')} "
        f"us_weight_bias={fx_signal.get('us_weight_bias')}"
    )
    usd_jpy_rate = float(fx_signal.get("usd_jpy_rate", 155.0))

    # 配分ゲート（usd_jpy_rate は FXStrategyAgent 由来）
    allocs = cio.allocate_budgets(ctx, total_cash_jpy=0, cash_usd=float(acct.get("cash", 0)), usd_jpy_rate=usd_jpy_rate)
    if allocs["ScalpDay_US"].budget_usd == 0:
        logger.warning(f"配分ゲート: ScalpDay_US 枠ゼロ (risk={ctx.risk_level}) → セッション中止")
        _save_scalpday_us_log([], ctx.risk_level, "gate_abort")
        return

    # 4. MomentSwingUS ポジション銘柄を除外（デイトレ対象から外す）
    value_positions = {p.get("symbol") for p in broker.get_positions()}

    # 5. ユニバース構築・スクリーニング
    logger.info("米国株ユニバース構築中...")
    _us_alloc = allocs["ScalpDay_US"]
    universe = build_pod_universe("US", _us_alloc.active_sectors, _us_alloc.catalyst_slots)
    candidates = [c for c in scalpday_us_agent.screen_candidates(universe, ctx)
                  if c not in value_positions]
    logger.info(f"ScalpDayUS 候補銘柄: {candidates}")
    if not candidates:
        logger.info("本日の ScalpDayUS 対象なし。終了します")
        _save_scalpday_us_log([], ctx.risk_level, "no_candidates")
        return

    # ── 準備フェーズ完了。市場オープン（ET 9:30 = JST 22:30）まで待機 ──
    wait_until("22:30")
    session_start = datetime.now()
    session_end_time = (
        session_start + timedelta(minutes=duration_minutes) if duration_minutes else None
    )
    if session_end_time:
        logger.info(f"タイムリミット: {session_end_time.strftime('%H:%M')} に全決済")

    # 6. 各候補: 提案 → CriticUS → ペーパー発注
    executed: list[dict] = []
    rejected: list[dict] = []

    for symbol in candidates:
        try:
            quote     = us_mkt.get_quote_us(symbol)
            bars_5min = us_mkt.get_bars_5min_us(symbol)
            sym_name  = next((u["name"] for u in universe if u["symbol"] == symbol), symbol)

            proposal = scalpday_us_agent.generate_trade_proposal(
                symbol=symbol,
                symbol_name=sym_name,
                board_data=quote,
                bars_5min=bars_5min,
                ctx=ctx,
            )
            if not proposal:
                continue

            # USD建て株数を max_us_position_usd 上限で再計算
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

            # CriticUS審査（否決なら scalpday_us_agent に修正依頼して再審査）
            approved_proposal, verdict = critic.refine_and_review(
                scalpday_us_agent, proposal, ctx, account=acct, fx_signal=fx_signal
            )
            if approved_proposal is None:
                logger.info(f"{symbol}: クリティーク最終否決 — {verdict.suggestion}")
                rejected.append({"symbol": symbol, "reason": verdict.suggestion})
                continue
            proposal = approved_proposal

            # ペーパー発注
            if proposal.price and proposal.price > 0:
                result = broker.send_limit_order(symbol, proposal.qty, proposal.side, proposal.price)
            else:
                result = broker.send_market_order(symbol, proposal.qty, proposal.side)

            if result.success:
                logger.info(
                    f"[ScalpDayUS 発注] {symbol} {proposal.side} x{proposal.qty} "
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

    _save_scalpday_us_log(executed, ctx.risk_level, "orders_sent", rejected)

    # ── ポジション管理 + 再スクリーニングループ ──────────────────────────
    _STOP_LOSS_PCT   = -0.015  # -1.5% で損切り
    _TAKE_PROFIT_PCT =  0.025  # +2.5% で利確
    _RESCAN_SEC      = 300     # 5分ごとに再スクリーニング
    poll_sec = 30 if duration_minutes else 120

    # 発注済み銘柄の管理マップ（symbol → 発注情報）
    active_positions: dict[str, dict] = {
        e["symbol"]: {"side": e["side"], "qty": e["qty"], "order_id": e["order_id"]}
        for e in executed
    }
    all_executed = list(executed)   # 全発注履歴（最終レポート用）
    last_rescan  = session_start

    logger.info(
        f"管理ループ開始: 初期ポジション={list(active_positions.keys())}"
        + (f" （{duration_minutes}分後に全決済）" if duration_minutes else " （引けまで継続）")
    )

    while not is_near_us_close():
        # タイムリミット到達チェック
        if session_end_time and datetime.now() >= session_end_time:
            logger.info("タイムリミット到達 → 全決済フェーズへ")
            break

        time.sleep(poll_sec)

        # ── 1. 既存ポジションの SL/TP チェック ────────────────────────
        just_closed: list[str] = []
        try:
            positions = broker.get_positions()
            pos_map   = {p["symbol"]: p for p in positions}
            for symbol in list(active_positions.keys()):
                pos = pos_map.get(symbol)
                if not pos:
                    active_positions.pop(symbol, None)
                    continue
                entry   = float(pos["avg_entry_price"])
                current = float(pos["current_price"])
                chg     = (current - entry) / entry if entry > 0 else 0.0
                logger.info(f"  {symbol}: {chg:+.2%}  現在${current:.2f}")
                if chg <= _STOP_LOSS_PCT:
                    logger.warning(f"損切り: {symbol} {chg:+.2%} → 決済")
                    broker.close_position(symbol)
                    active_positions.pop(symbol, None)
                    just_closed.append(symbol)
                elif chg >= _TAKE_PROFIT_PCT:
                    logger.info(f"利確: {symbol} {chg:+.2%} → 決済")
                    broker.close_position(symbol)
                    active_positions.pop(symbol, None)
                    just_closed.append(symbol)
        except Exception as e:
            logger.error(f"監視ループエラー: {e}")

        # ── 2. 5分ごと or 決済直後の再スクリーニング ──────────────────
        now = datetime.now()
        do_rescan = (now - last_rescan).total_seconds() >= _RESCAN_SEC or bool(just_closed)
        if not do_rescan:
            continue

        last_rescan = now
        logger.info(f"再スクリーニング: {'決済直後' if just_closed else '定期'}")
        try:
            new_universe = build_pod_universe("US", _us_alloc.active_sectors, _us_alloc.catalyst_slots)
            # 現在Alpacaで保有中の全銘柄を除外（デイトレ・スイング問わず）
            held = {p.get("symbol") for p in broker.get_positions()}
            new_candidates = [
                c for c in scalpday_us_agent.screen_candidates(new_universe, ctx)
                if c not in held
            ]
            # 決済直後の銘柄を先頭に（再エントリーを優先試行）
            priority = [s for s in just_closed if s in new_candidates]
            others   = [c for c in new_candidates if c not in priority]

            for symbol in priority + others:
                if symbol in active_positions:
                    continue
                sym_name = next((u["name"] for u in new_universe if u["symbol"] == symbol), symbol)
                label = "再エントリー" if symbol in just_closed else "新規エントリー"
                logger.info(f"[{label}] {symbol} 試行...")
                try:
                    quote     = us_mkt.get_quote_us(symbol)
                    bars_5min = us_mkt.get_bars_5min_us(symbol)
                    proposal  = scalpday_us_agent.generate_trade_proposal(
                        symbol=symbol, symbol_name=sym_name,
                        board_data=quote, bars_5min=bars_5min, ctx=ctx,
                    )
                    if not proposal:
                        continue

                    cur_price = quote.get("CurrentPrice", 0)
                    max_pos   = float(getattr(RISK, "max_us_position_usd", 3000))
                    if cur_price > 0:
                        proposal.qty = max(1, int(max_pos / cur_price))
                    if proposal.stop_loss is None and cur_price > 0:
                        uni_item       = next((u for u in new_universe if u["symbol"] == symbol), {})
                        atr_pct        = float(uni_item.get("atr_pct", 2.0))
                        proposal.stop_loss = round(cur_price * (1 - 1.5 * atr_pct / 100), 4)

                    approved_proposal, verdict = critic.refine_and_review(
                        scalpday_us_agent, proposal, ctx, account=acct, fx_signal=fx_signal
                    )
                    if approved_proposal is None:
                        logger.info(f"[{label}否決] {symbol}: {verdict.suggestion}")
                        rejected.append({"symbol": symbol, "reason": verdict.suggestion})
                        continue
                    proposal = approved_proposal

                    if proposal.price and proposal.price > 0:
                        result = broker.send_limit_order(symbol, proposal.qty, proposal.side, proposal.price)
                    else:
                        result = broker.send_market_order(symbol, proposal.qty, proposal.side)

                    if result.success:
                        logger.info(
                            f"[{label}発注] {symbol} {proposal.side} x{proposal.qty} "
                            f"order_id={result.order_id}"
                        )
                        active_positions[symbol] = {
                            "side": proposal.side, "qty": proposal.qty, "order_id": result.order_id,
                        }
                        all_executed.append({
                            "symbol":    symbol,
                            "name":      sym_name,
                            "side":      proposal.side,
                            "qty":       proposal.qty,
                            "rationale": proposal.rationale,
                            "order_id":  result.order_id,
                        })
                    else:
                        logger.warning(f"[{label}発注失敗] {symbol}: {result.message}")
                except Exception as e:
                    logger.error(f"{label} {symbol} エラー: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"再スクリーニングエラー: {e}")

    # ── 強制決済フェーズ ──────────────────────────────────────────
    close_reason = "タイムリミット" if (session_end_time and datetime.now() >= session_end_time) else "引け前"
    logger.info(f"{close_reason}強制決済フェーズ")
    for symbol in list(active_positions.keys()):
        try:
            positions = broker.get_positions()
            if any(p["symbol"] == symbol for p in positions):
                logger.info(f"強制決済: {symbol}")
                broker.close_position(symbol)
        except Exception as e:
            logger.error(f"決済エラー {symbol}: {e}")

    _save_scalpday_us_log(all_executed, ctx.risk_level, "completed", rejected)

    if duration_minutes:
        _generate_session_report(all_executed, broker, session_start, fx_signal, ctx)

    _allow_sleep()
    logger.info(f"=== ScalpDay_US セッション終了: 総発注{len(all_executed)}件 ===")


def _save_scalpday_us_log(
    executed: list[dict],
    risk_level: str,
    status: str,
    rejected: list[dict] | None = None,
) -> None:
    log_path = Path("logs/scalpday_us_log.json")
    log_path.parent.mkdir(exist_ok=True)
    existing: list[dict] = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.append({
        "session_date": datetime.now().isoformat(),
        "risk_level":   risk_level,
        "status":       status,
        "executed":     executed,
        "rejected":     rejected or [],
    })
    log_path.write_text(
        json.dumps(existing[-30:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"ScalpDayUSログ保存: {log_path}")


# ──────────────────────────────────────────────────────────────────
# FX リバランスセッション
# ──────────────────────────────────────────────────────────────────


def run_fx_rebalance_session():
    """
    FX リバランスセッション（JP/US どちらかの市場が通常立会時間のとき実行）。

    マクロデータを読み、FX シグナルを生成・審査後にポジション調整意図をログ出力する。
    情報収集は IntelScout の役割。本セッションはマクロデータを読むだけで収集はしない。
    両市場とも閉場なら発注・建玉操作を一切行わず早期リターンする。
    """
    if not (is_jp_open() or is_us_open()):
        logger.info("両市場閉場 → FXリバランス見送り")
        return

    from agents.fx_strategy import FXStrategyAgent

    logger.info("=== FXリバランスセッション開始 ===")
    _prevent_sleep()

    cio      = CIOAgent()
    fx_agent = FXStrategyAgent()
    critic   = FXRebalance_Critic()

    # 1. MarketContext 生成（マクロデータは _MACRO_DATA のみ参照、収集はしない）
    logger.info("CIO: マーケットコンテキスト生成中...")
    _news, _obs = get_news_summary_for_cio()
    ctx = cio.generate_market_context(
        news_summary=_news,
        macro_data=_MACRO_DATA,
        obs_source=_obs,
    )
    logger.info(f"コンテキスト: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")

    # 2. FX シグナル生成
    logger.info("FXStrategyAgent: シグナル生成中...")
    fx_signal = fx_agent.generate_signal(_MACRO_DATA, 0.35, ctx)
    logger.info(
        f"FXシグナル: {fx_signal.get('fx_signal')} "
        f"目標ドル比率={fx_signal.get('target_usd_ratio')}%"
    )

    # 3. FXRebalance_Critic 審査
    logger.info("FXRebalance_Critic: 審査中...")
    verdict = critic.review_signal(fx_signal, ctx)
    logger.info(f"審査結果: approved={verdict.approved} score={verdict.score:.2f}")

    if not verdict.approved:
        issues_str = "; ".join(verdict.issues or [])
        logger.info(f"FXリバランス否決: {issues_str}")
        _allow_sleep()
        return

    # 4. リバランス実行（発注意図をログ出力。実 FX 発注はブローカー接続後に実装）
    target  = float(fx_signal.get("target_usd_ratio",  50))
    current = float(fx_signal.get("current_usd_ratio", 50))
    diff    = target - current

    if abs(diff) < 1.0:
        logger.info("ドル比率の変更不要（差分 < 1%）。リバランス見送り")
    else:
        direction = "ドル増加" if diff > 0 else "ドル削減"
        logger.info(
            f"FXリバランス実行: {direction} "
            f"現在={current:.1f}% → 目標={target:.1f}%（差分={diff:+.1f}%）"
        )

    _allow_sleep()
    logger.info("=== FXリバランスセッション終了 ===")


# ──────────────────────────────────────────────────────────────────
# IntelScout 収集セッション（1日2回: 08:00/17:00 JST）
# ──────────────────────────────────────────────────────────────────


def run_intel_scout_session():
    """
    IntelScout 収集セッション（08:00/17:00 JST 想定）。
    市場ガードなし（7日稼働、情報蓄積を止めない）。
    前回収集時刻からの差分を収集し、ロールアップを生成・保存する。
    完了後に CIO キャッシュを無効化して、以降のセッションが
    ダイジェスト付きコンテキストを取得できるようにする。
    """
    from agents.intelligence import IntelligenceAgent
    from zoneinfo import ZoneInfo

    logger.info("=== IntelScout 収集セッション開始 ===")
    _prevent_sleep()

    _JST_ZONE  = ZoneInfo("Asia/Tokyo")
    now        = datetime.now(_JST_ZONE)
    date_str   = now.strftime("%Y-%m-%d")
    window_lbl = now.strftime("%H:%M")

    # 1. 前回収集時刻を読み込み、since を決定
    state = load_state()
    last  = state.last_collected_at

    _FALLBACK_H     = 24
    _MAX_LOOKBACK_H = 48

    if last is None:
        logger.info(f"初回起動 → {_FALLBACK_H}h 前からフォールバック収集")
        since = now - timedelta(hours=_FALLBACK_H)
    else:
        age_h = (now - last).total_seconds() / 3600
        if age_h > _MAX_LOOKBACK_H:
            logger.warning(
                f"最終収集から {age_h:.0f}h 経過（上限 {_MAX_LOOKBACK_H}h）。"
                f"{_FALLBACK_H}h フォールバックを使用"
            )
            since = now - timedelta(hours=_FALLBACK_H)
        else:
            logger.info(f"前回収集: {last.isoformat()} ({age_h:.1f}h 前)")
            since = last

    # 2. シグナル収集（差分）
    intel_agent = IntelligenceAgent()
    critic      = IntelCritic()

    logger.info(f"差分収集: {since.isoformat()} 以降...")
    raw_result  = intel_agent.collect(since=since)
    raw_signals = raw_result.get("signals", [])
    logger.info(f"収集シグナル数: {len(raw_signals)} 件")

    # 3. IntelCritic 審査（ctx 不要）
    approved: list[dict] = []
    if raw_signals:
        logger.info("IntelCritic: 審査中...")
        approved = critic.review_signals(raw_signals)
        logger.info(f"承認シグナル数: {len(approved)} 件")

    # 4. 既存ダイジェストから同日の収集窓を引き継ぐ
    existing_digest  = read_intel_digest()
    existing_windows = (
        existing_digest.get("windows", [])
        if existing_digest.get("date") == date_str
        else []
    )

    # 5. ロールアップ生成
    logger.info("ロールアップ生成中...")
    rollup = intel_agent.generate_rollup(
        approved,
        date_str=date_str,
        window=window_lbl,
        existing_windows=existing_windows,
    )

    # 6. ダイジェスト保存（JSON + Markdown）
    write_intel_digest(rollup)
    logger.info(
        f"ダイジェスト保存: logs/intel_digest.json + "
        f"logs/digests/{date_str}.md  "
        f"(signal_count={rollup['signal_count']})"
    )

    # 7. CIO キャッシュを無効化（以降のセッションがダイジェスト付きコンテキストを取得するため）
    _cio_cache = Path("logs/market_context_cache.json")
    if _cio_cache.exists():
        _cio_cache.unlink()
        logger.info("CIO キャッシュ無効化: 次回セッションでダイジェストが反映されます")

    # 8. 収集時刻を更新
    state.last_collected_at = now
    save_state(state)
    logger.info(f"状態更新: last_collected_at = {now.isoformat()}")

    _allow_sleep()
    logger.info("=== IntelScout 収集セッション終了 ===")


# ──────────────────────────────────────────────────

def _collect_report_data() -> "tuple":
    """
    朝・夜レポート共通のデータ収集。
    各ブローカー/エージェントを初期化してデータを取得し返す。
    接続失敗時はデフォルト値で継続（システムを止めない）。

    Returns:
        (ctx, fx_signal, jp_cash_jpy, usd_cash, us_equity_usd,
         us_positions, usdjpy_rate, usdjpy_source, usdjpy_fetched_at)
    """
    from agents.cio import CIOAgent
    from agents.fx_strategy import FXStrategyAgent
    from agents.base import MarketContext
    from data.fx_rate import get_usd_jpy
    from datetime import date

    # 実勢 USD/JPY レート取得（タイムアウト4秒 → キャッシュ → 暫定値155.0）
    usdjpy_rate, usdjpy_fetched_at, usdjpy_source = get_usd_jpy()
    macro_data = f"USD/JPY={usdjpy_rate:.2f}, VIX=18.5, 米10Y=4.35%"

    # MarketContext（CIO）
    ctx: MarketContext
    try:
        _news, _obs = get_news_summary_for_cio()
        ctx = CIOAgent().generate_market_context(
            news_summary=_news,
            macro_data=macro_data,
            obs_source=_obs,
        )
    except Exception as exc:
        logger.warning(f"CIOコンテキスト取得失敗: {exc}")
        from agents.base import MarketContext
        ctx = MarketContext(
            date=date.today().isoformat(),
            sector_scores={},
            macro_notes="データ取得失敗",
            rotation_signal="維持",
            risk_level="medium",
        )

    # FXシグナル
    fx_signal: dict = {}
    try:
        fx_signal = FXStrategyAgent().generate_signal(macro_data, 0.35, ctx)
    except Exception as exc:
        logger.warning(f"FXシグナル取得失敗: {exc}")

    # kabu（日本株）現金残高
    jp_cash_jpy = 0.0
    try:
        from brokers.kabu import KabuBroker
        wallet = KabuBroker().get_wallet_margin()
        jp_cash_jpy = float(wallet.get("MarginAccountWallet", 0))
    except Exception as exc:
        logger.warning(f"kabu接続失敗: {exc}")

    # Alpaca（米国株）口座・ポジション
    usd_cash = us_equity_usd = 0.0
    us_positions: list[dict] = []
    try:
        from brokers.alpaca import AlpacaBroker
        alpaca  = AlpacaBroker()
        acct    = alpaca.get_account()
        usd_cash      = float(acct.get("cash", 0))
        us_equity_usd = float(acct.get("equity", 0))
        us_positions  = alpaca.get_positions()
    except Exception as exc:
        logger.warning(f"Alpaca接続失敗: {exc}")

    return (
        ctx, fx_signal, jp_cash_jpy, usd_cash, us_equity_usd,
        us_positions, usdjpy_rate, usdjpy_source, usdjpy_fetched_at,
    )


def run_morning_report() -> str:
    """
    朝 6 時レポート：データを収集し CxOAgent に渡してHTMLレポートをメール送信する。
    CIO / FX / Broker / ScalpDay_JP の初期化はここで行い CXO には注入する。
    """
    from agents.cxo import CXOAgent, CXOReportContext
    from agents.scalp_day import ScalpDay_JP
    from brokers.alpaca import AlpacaBroker, calc_daytrade_pl
    from report.template import ScalpDayCandidate

    logger.info(f"=== 朝次レポート生成開始 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 共通データ収集
    (ctx, fx_signal, jp_cash_jpy, usd_cash, us_equity_usd,
     us_positions, usdjpy_rate, usdjpy_source, usdjpy_fetched_at) = _collect_report_data()

    # Alpaca 約定履歴（デイトレ損益計算）
    daytrade_pl: dict | None = None
    try:
        activities  = AlpacaBroker().get_activities(since_hours=24)
        daytrade_pl = calc_daytrade_pl(activities)
    except Exception as exc:
        logger.warning(f"デイトレ損益計算失敗: {exc}")

    # ScalpDay_JP 候補スクリーニング
    scalpday_candidates: list[ScalpDayCandidate] = []
    try:
        from agents.cio import CIOAgent
        cio    = CIOAgent()
        allocs = cio.allocate_budgets(ctx, total_cash_jpy=jp_cash_jpy or 500_000, cash_usd=0)
        alloc  = allocs["ScalpDay_JP"]
        universe = build_pod_universe("JP", alloc.active_sectors, alloc.catalyst_slots)
        raw_cands = ScalpDay_JP().screen_candidates(universe, ctx)
        for sym in raw_cands[:5]:
            sym_name = next((u["name"] for u in universe if u["symbol"] == sym), sym)
            scalpday_candidates.append(ScalpDayCandidate(
                symbol=sym, name=sym_name, signal="buy", rationale="スクリーニング通過",
            ))
    except Exception as exc:
        logger.warning(f"デイトレ候補取得失敗: {exc}")

    report_ctx = CXOReportContext(
        ctx=ctx, fx_signal_dict=fx_signal, us_positions=us_positions,
        jp_cash_jpy=jp_cash_jpy, usd_cash=usd_cash, us_equity_usd=us_equity_usd,
        usdjpy_rate=usdjpy_rate,
        usdjpy_source=usdjpy_source,
        usdjpy_fetched_at=usdjpy_fetched_at,
    )
    try:
        CXOAgent().generate_morning_report(
            report_ctx,
            daytrade_pl=daytrade_pl,
            scalpday_candidates=scalpday_candidates,
        )
    except Exception as exc:
        logger.error(f"朝次HTMLレポート送信失敗: {exc}", exc_info=True)

    return f"=== 朝次レポート {datetime.now().strftime('%Y-%m-%d %H:%M')} 完了 ==="


def run_evening_report() -> None:
    """
    夜 21 時レポート：データを収集し CxOAgent に渡してHTMLレポートをメール送信する。
    CIO / FX / Broker の初期化はここで行い CXO には注入する。
    """
    from agents.cxo import CXOAgent, CXOReportContext

    logger.info("=== 夜間レポート生成開始 ===")

    (ctx, fx_signal, jp_cash_jpy, usd_cash, us_equity_usd,
     us_positions, usdjpy_rate, usdjpy_source, usdjpy_fetched_at) = _collect_report_data()
    report_ctx = CXOReportContext(
        ctx=ctx, fx_signal_dict=fx_signal, us_positions=us_positions,
        jp_cash_jpy=jp_cash_jpy, usd_cash=usd_cash, us_equity_usd=us_equity_usd,
        usdjpy_rate=usdjpy_rate,
        usdjpy_source=usdjpy_source,
        usdjpy_fetched_at=usdjpy_fetched_at,
    )
    try:
        CXOAgent().generate_evening_report(report_ctx)
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
            "scalpday_jp",
            "moment_swing_jp",
            "scalpday_us",
            "moment_swing_us",
            "fx_rebalance",
            "intel_scout",
            "morning_report",
            "evening_report",
        ],
        default="scalpday_jp",
        help=(
            "実行モード: "
            "scalpday_jp=日本株デイトレ, "
            "moment_swing_jp=日本株スイング, "
            "scalpday_us=米国株デイトレ(Alpaca), "
            "moment_swing_us=米国株スイング中長期, "
            "fx_rebalance=FXリバランス(JP/USどちらか開場時), "
            "intel_scout=IntelScout収集(08:00/17:00 JST 市場ガードなし), "
            "morning_report=朝次レポート(06:00), "
            "evening_report=夜間レポート(21:00)"
        ),
    )
    parser.add_argument(
        "--schedule",
        metavar="HH:MM",
        help="指定時刻まで待機してから実行する（例: --schedule 22:30）",
    )
    parser.add_argument(
        "--duration",
        metavar="MINUTES",
        type=int,
        default=None,
        help="scalpday_us モード: 指定分後に全決済してレポートを出す（例: --duration 10）",
    )
    args = parser.parse_args()

    if args.schedule:
        wait_until(args.schedule)

    if args.mode == "scalpday_jp":
        run_scalpday_jp_session(paper=True)
    elif args.mode == "moment_swing_jp":
        run_moment_swing_jp_session(paper=True)
    elif args.mode == "moment_swing_us":
        run_moment_swing_us_session()
    elif args.mode == "scalpday_us":
        run_scalpday_us_session(duration_minutes=args.duration)
    elif args.mode == "fx_rebalance":
        run_fx_rebalance_session()
    elif args.mode == "intel_scout":
        run_intel_scout_session()
    elif args.mode == "morning_report":
        run_morning_report()
    elif args.mode == "evening_report":
        run_evening_report()
