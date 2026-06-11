"""
agents/cxo.py
CxOエージェント：全エージェントのログとポジション情報を集約し、
朝晩のHTMLレポートを生成してメールで送信する。

実行タイミング：
  21:00 → generate_evening_report()
  06:00 → generate_morning_report()
"""
import json
import logging
from datetime import date, datetime
from pathlib import Path

from agents.base import BaseAgent, MarketContext
from prompts.all_agents import CXO_PROMPT
from report.template import (
    EveningReportData, MorningReportData,
    HoldingItem, MarginPosition, SectorScore,
    DaytradeCandidate, DiscussionItem,
    DaytradeRecord, ValueDecision,
    build_evening_html, build_morning_html,
)
from report.mailer import send_report

logger = logging.getLogger(__name__)

_DEFAULT_USDJPY = 155.0

_JP_SYMBOLS = [
    {"symbol": "9984", "name": "ソフトバンクG"},
    {"symbol": "6857", "name": "アドバンテスト"},
    {"symbol": "4063", "name": "信越化学"},
    {"symbol": "2330", "name": "フィックスターズ"},
]


def _normalize_fx_signal(raw: str) -> str:
    """FXシグナル文字列を 円買い/中立/ドル買い に正規化する。"""
    s = str(raw).lower().replace("_", "").replace("-", "").replace(" ", "")
    if any(x in s for x in ["buyjpy", "sellusd", "jpybuy", "円買", "yenbuy"]):
        return "円買い"
    if any(x in s for x in ["buyusd", "selljpy", "usdbuy", "ドル買", "dollarbuy"]):
        return "ドル買い"
    return "中立"


class CXOAgent(BaseAgent):
    name = "CXOAgent"
    system_prompt = CXO_PROMPT
    model = "claude-opus-4-8"

    # ── データ収集 ───────────────────────────────────────────────

    def _collect_common_data(self) -> dict:
        """
        夜・朝レポート共通のデータを収集する。
        ブローカー接続失敗時はデフォルト値で継続（システムを止めない）。
        """
        result = {
            "ctx": None,
            "fx_signal_dict": {},
            "us_positions": [],
            "jp_cash_jpy": 0.0,
            "usd_cash": 0.0,
            "us_equity_usd": 0.0,
            "usdjpy_rate": _DEFAULT_USDJPY,
        }

        # 1. MarketContext（CIO）
        try:
            from agents.cio import CIOAgent
            ctx = CIOAgent().generate_market_context(
                news_summary="（レポート生成時コンテキスト）",
                macro_data=f"USD/JPY={_DEFAULT_USDJPY}, VIX=18.5, 米10Y=4.35%",
            )
            result["ctx"] = ctx
        except Exception as exc:
            logger.warning(f"CIOコンテキスト取得失敗: {exc}")
            result["ctx"] = MarketContext(
                date=date.today().isoformat(),
                sector_scores={},
                macro_notes="データ取得失敗",
                rotation_signal="維持",
                risk_level="medium",
            )

        # 2. FXシグナル
        try:
            from agents.fx_strategy import FXStrategyAgent
            result["fx_signal_dict"] = FXStrategyAgent().generate_signal(
                macro_data=f"USD/JPY={_DEFAULT_USDJPY}, VIX=18.5",
                current_usd_ratio=0.35,
                ctx=result["ctx"],
            )
        except Exception as exc:
            logger.warning(f"FXシグナル取得失敗: {exc}")

        # 3. kabu（日本株）現金残高
        try:
            from brokers.kabu import KabuBroker
            wallet = KabuBroker().get_wallet_margin()
            result["jp_cash_jpy"] = float(wallet.get("MarginAccountWallet", 0))
        except Exception as exc:
            logger.warning(f"kabu接続失敗: {exc}")

        # 4. Alpaca（米国株）ポジション・口座情報
        try:
            from brokers.alpaca import AlpacaBroker
            alpaca = AlpacaBroker()
            acct   = alpaca.get_account()
            result["usd_cash"]      = float(acct.get("cash", 0))
            result["us_equity_usd"] = float(acct.get("equity", 0))
            result["us_positions"]  = alpaca.get_positions()
        except Exception as exc:
            logger.warning(f"Alpaca接続失敗: {exc}")

        return result

    # ── LLM: CxO方針メモ生成 ────────────────────────────────────

    def _generate_cxo_memo(self, ctx: MarketContext) -> str:
        prompt = f"""
本日の投資システム CxO 方針メモを100文字以内で記述してください。

市場コンテキスト:
- リスク水準: {ctx.risk_level}
- ローテーションシグナル: {ctx.rotation_signal}
- マクロノート: {ctx.macro_notes}

方針テキストのみを返してください（JSON不要）。
"""
        try:
            return self._ask_llm(prompt).strip()[:200]
        except Exception as exc:
            logger.warning(f"CxOメモ生成失敗: {exc}")
            return "通常運転。リスク管理方針に従い自律運転継続。"

    # ── 共通データ → レポートデータ変換 ─────────────────────────

    def _build_report_data(self, raw: dict) -> dict:
        """
        _collect_common_data() の結果を EveningReportData 用フィールドに変換する。
        """
        ctx: MarketContext = raw["ctx"]
        usdjpy = raw["usdjpy_rate"]
        fx_dict = raw["fx_signal_dict"]

        # セクタースコア
        sector_scores = [
            SectorScore(name=name, score=score, change=0.0, change_pct=0.0)
            for name, score in ctx.sector_scores.items()
        ]

        # FXシグナル正規化
        fx_label = _normalize_fx_signal(fx_dict.get("fx_signal", "hold"))

        # 米国株ポジション → HoldingItem
        _DUMMY_JP = [
            HoldingItem(symbol="9984", name="ソフトバンクG", value_jpy=320_000, pf_pct=0.0, change_pct=+1.8),
            HoldingItem(symbol="6857", name="アドバンテスト", value_jpy=250_000, pf_pct=0.0, change_pct=-0.5),
            HoldingItem(symbol="4063", name="信越化学", value_jpy=180_000, pf_pct=0.0, change_pct=+0.3),
            HoldingItem(symbol="2330", name="フィックスターズ", value_jpy=90_000, pf_pct=0.0, change_pct=+2.1),
        ]
        _DUMMY_US = [
            HoldingItem(symbol="NVDA", name="NVIDIA", value_jpy=0, pf_pct=0.0, change_pct=+3.2),
            HoldingItem(symbol="MSFT", name="Microsoft", value_jpy=0, pf_pct=0.0, change_pct=+0.7),
            HoldingItem(symbol="AAPL", name="Apple", value_jpy=0, pf_pct=0.0, change_pct=-0.4),
        ]

        us_holdings: list[HoldingItem] = []
        for pos in raw["us_positions"]:
            try:
                qty   = float(pos.get("qty", 0))
                entry = float(pos.get("avg_entry_price", 0))
                curr  = float(pos.get("current_price", 0))
                mv    = float(pos.get("market_value", qty * curr))
                chg   = (curr - entry) / entry * 100 if entry > 0 else 0.0
                us_holdings.append(HoldingItem(
                    symbol=pos.get("symbol", ""),
                    name=pos.get("symbol", ""),
                    value_jpy=mv * usdjpy,
                    pf_pct=0.0,
                    change_pct=chg,
                ))
            except Exception:
                continue

        # ブローカー未接続時はダミーデータで円グラフを描画
        jp_holdings: list[HoldingItem] = _DUMMY_JP
        if not us_holdings:
            for d in _DUMMY_US:
                d.value_jpy = d.value_jpy or (200_000 * usdjpy / 155.0)
            us_holdings = [
                HoldingItem(symbol="NVDA", name="NVIDIA", value_jpy=580_000, pf_pct=0.0, change_pct=+3.2),
                HoldingItem(symbol="MSFT", name="Microsoft", value_jpy=430_000, pf_pct=0.0, change_pct=+0.7),
                HoldingItem(symbol="AAPL", name="Apple", value_jpy=310_000, pf_pct=0.0, change_pct=-0.4),
            ]

        # 総資産・PF比計算
        usd_equity_jpy = raw["us_equity_usd"] * usdjpy
        jp_cash        = raw["jp_cash_jpy"]
        usd_cash_jpy   = raw["usd_cash"] * usdjpy
        total_jpy      = usd_equity_jpy + jp_cash
        if total_jpy <= 0:
            total_jpy = max(
                sum(h.value_jpy for h in us_holdings) + sum(h.value_jpy for h in jp_holdings),
                1.0,
            )

        for h in us_holdings + jp_holdings:
            h.pf_pct = h.value_jpy / total_jpy if total_jpy > 0 else 0.0

        # 円ドル割合
        usd_total_jpy  = sum(h.value_jpy for h in us_holdings)
        usd_asset_ratio = min(1.0, usd_total_jpy / total_jpy) if total_jpy > 0 else 0.35
        jpy_asset_ratio = 1.0 - usd_asset_ratio
        cash_total      = jp_cash + usd_cash_jpy
        usd_cash_ratio  = usd_cash_jpy / cash_total if cash_total > 0 else 0.35
        jpy_cash_ratio  = 1.0 - usd_cash_ratio

        # リスクスコア
        risk_score = {"low": 1, "medium": 3, "high": 5}.get(ctx.risk_level, 3)

        return {
            "total_jpy":        total_jpy,
            "jp_holdings":      jp_holdings,
            "us_holdings":      us_holdings,
            "sector_scores":    sector_scores,
            "fx_label":         fx_label,
            "fx_rationale":     fx_dict.get("rationale", ""),
            "usd_asset_ratio":  usd_asset_ratio,
            "jpy_asset_ratio":  jpy_asset_ratio,
            "usd_cash_ratio":   usd_cash_ratio,
            "jpy_cash_ratio":   jpy_cash_ratio,
            "risk_score":       risk_score,
            "cxo_memo":         self._generate_cxo_memo(ctx),
            "ctx":              ctx,
        }

    # ── 夜間レポート ─────────────────────────────────────────────

    def generate_evening_report(self) -> bool:
        """
        21:00 夜間レポートを生成してメール送信する。
        Returns: 送信成功で True。
        """
        logger.info("=== 夕方レポート生成開始 ===")
        now = datetime.now()
        raw = self._collect_common_data()
        d   = self._build_report_data(raw)
        ctx: MarketContext = d["ctx"]

        report_data = EveningReportData(
            generated_at=now,
            total_assets_jpy=d["total_jpy"],
            total_assets_change_pct=0.0,
            jp_holdings=d["jp_holdings"],
            us_holdings=d["us_holdings"],
            risk_score=d["risk_score"],
            risk_level=ctx.risk_level,
            jpy_asset_ratio=d["jpy_asset_ratio"],
            usd_asset_ratio=d["usd_asset_ratio"],
            jpy_cash_ratio=d["jpy_cash_ratio"],
            usd_cash_ratio=d["usd_cash_ratio"],
            fx_signal=d["fx_label"],
            fx_rationale=d["fx_rationale"],
            usdjpy_rate=raw["usdjpy_rate"],
            margin_positions=[],
            sector_scores=d["sector_scores"],
            all_positions=d["us_holdings"],
            pre_us_fx_signal=d["fx_label"],
            pre_us_fx_rationale=d["fx_rationale"],
            cxo_memo=d["cxo_memo"],
            macro_notes=ctx.macro_notes,
            rotation_signal=ctx.rotation_signal,
        )

        html    = build_evening_html(report_data)
        subject = f"[投資レポート] 夜間サマリー {now.strftime('%Y/%m/%d %H:%M')}"
        ok      = send_report(subject, html)
        logger.info(f"夕方レポート送信: {'成功' if ok else '失敗'}")
        return ok

    # ── 朝次レポート ─────────────────────────────────────────────

    def generate_morning_report(self) -> bool:
        """
        06:00 朝次レポートを生成してメール送信する。
        Returns: 送信成功で True。
        """
        logger.info("=== 朝レポート生成開始 ===")
        now = datetime.now()
        raw = self._collect_common_data()
        d   = self._build_report_data(raw)
        ctx: MarketContext = d["ctx"]

        # 米国株デイトレ損益（約定履歴から手数料込みで計算）
        us_realized_pl_usd = 0.0
        us_trade_count     = 0
        daytrade_records: list[DaytradeRecord] = []
        daytrade_gross_pl = daytrade_fees = daytrade_net_pl = 0.0
        try:
            from brokers.alpaca import AlpacaBroker, calc_daytrade_pl
            alpaca = AlpacaBroker()
            activities = alpaca.get_activities(since_hours=24)
            pl_result  = calc_daytrade_pl(activities)
            daytrade_gross_pl = pl_result["total_gross"]
            daytrade_fees     = pl_result["total_fees"]
            daytrade_net_pl   = pl_result["total_net"]
            us_trade_count    = len(pl_result["trades"])
            us_realized_pl_usd = daytrade_net_pl
            for t in pl_result["trades"]:
                daytrade_records.append(DaytradeRecord(
                    symbol=t["symbol"],
                    side="sell",
                    qty=t["qty"],
                    buy_price=t["buy_price"],
                    sell_price=t["sell_price"],
                    gross_pl=t["gross_pl"],
                    fees=t["fees"],
                    net_pl=t["net_pl"],
                ))
            logger.info(
                f"デイトレ損益: gross={daytrade_gross_pl:+.2f} "
                f"fees=-{daytrade_fees:.4f} net={daytrade_net_pl:+.2f} USD"
            )
        except Exception as exc:
            logger.warning(f"デイトレ損益計算失敗: {exc}")

        # バリュー投資 昨日の買い/見送り決定
        value_decisions: list[ValueDecision] = []
        try:
            val_log = Path("logs/us_value_log.json")
            if val_log.exists():
                sessions = json.loads(val_log.read_text(encoding="utf-8"))
                if sessions:
                    latest_val = sessions[-1]
                    for ex in latest_val.get("executed", []):
                        value_decisions.append(ValueDecision(
                            symbol=ex.get("symbol", ""),
                            name=ex.get("name", ex.get("symbol", "")),
                            action="buy",
                            rationale=ex.get("rationale", ""),
                            qty=float(ex.get("qty", 0)),
                            consensus=ex.get("consensus", "execute"),
                        ))
                    for rj in latest_val.get("rejected", []):
                        value_decisions.append(ValueDecision(
                            symbol=rj.get("symbol", ""),
                            name=rj.get("symbol", ""),
                            action="reject",
                            rationale=rj.get("reason", ""),
                        ))
        except Exception as exc:
            logger.warning(f"バリューログ読み込み失敗: {exc}")

        # 前日デイトレログからus_trade_countをフォールバック補完
        if us_trade_count == 0:
            try:
                dt_log = Path("logs/us_daytrade_log.json")
                if dt_log.exists():
                    sessions = json.loads(dt_log.read_text(encoding="utf-8"))
                    if sessions:
                        us_trade_count = len(sessions[-1].get("executed", []))
            except Exception as exc:
                logger.warning(f"デイトレログ読み込み失敗: {exc}")

        # 日本株デイトレ候補
        daytrade_candidates: list[DaytradeCandidate] = []
        try:
            from agents.daytrade import DaytradeAgent
            from data import market as mkt
            universe = mkt.build_universe(_JP_SYMBOLS)
            raw_cands = DaytradeAgent().screen_candidates(universe, ctx)
            for sym in raw_cands[:5]:
                sym_name = next(
                    (u["name"] for u in _JP_SYMBOLS if u["symbol"] == sym), sym
                )
                daytrade_candidates.append(DaytradeCandidate(
                    symbol=sym,
                    name=sym_name,
                    signal="buy",
                    rationale="スクリーニング通過",
                ))
        except Exception as exc:
            logger.warning(f"デイトレ候補取得失敗: {exc}")

        # インテリジェンス議論サマリー
        discussion_items: list[DiscussionItem] = []
        discussion_session_date = ""
        log_path = Path("logs/discussion_log.json")
        if log_path.exists():
            try:
                sessions = json.loads(log_path.read_text(encoding="utf-8"))
                if sessions:
                    latest = sessions[-1]
                    discussion_session_date = latest.get("session_date", "")[:10]
                    for disc in latest.get("discussions", []):
                        discussion_items.append(DiscussionItem(
                            verdict=disc.get("verdict", "?"),
                            score=float(disc.get("signal_score", 0.0)),
                            title=disc.get("signal_title", "")[:60],
                            summary=disc.get("summary", ""),
                        ))
            except Exception as exc:
                logger.warning(f"議論ログ読み込み失敗: {exc}")

        usdjpy = raw["usdjpy_rate"]
        report_data = MorningReportData(
            generated_at=now,
            total_assets_jpy=d["total_jpy"],
            total_assets_change_pct=0.0,
            jp_holdings=d["jp_holdings"],
            us_holdings=d["us_holdings"],
            risk_score=d["risk_score"],
            risk_level=ctx.risk_level,
            jpy_asset_ratio=d["jpy_asset_ratio"],
            usd_asset_ratio=d["usd_asset_ratio"],
            jpy_cash_ratio=d["jpy_cash_ratio"],
            usd_cash_ratio=d["usd_cash_ratio"],
            fx_signal=d["fx_label"],
            fx_rationale=d["fx_rationale"],
            usdjpy_rate=usdjpy,
            margin_positions=[],
            sector_scores=d["sector_scores"],
            all_positions=d["us_holdings"],
            pre_us_fx_signal=d["fx_label"],
            pre_us_fx_rationale=d["fx_rationale"],
            cxo_memo=d["cxo_memo"],
            macro_notes=ctx.macro_notes,
            rotation_signal=ctx.rotation_signal,
            us_realized_pl_usd=us_realized_pl_usd,
            us_realized_pl_jpy=us_realized_pl_usd * usdjpy,
            us_trade_count=us_trade_count,
            daytrade_candidates=daytrade_candidates,
            overnight_fx_summary=f"USD/JPY {usdjpy:.2f}",
            overnight_fx_high=usdjpy * 1.005,
            overnight_fx_low=usdjpy * 0.995,
            overnight_fx_change_pct=0.0,
            discussion_items=discussion_items,
            discussion_session_date=discussion_session_date,
            daytrade_records=daytrade_records,
            daytrade_gross_pl=daytrade_gross_pl,
            daytrade_fees=daytrade_fees,
            daytrade_net_pl=daytrade_net_pl,
            value_decisions=value_decisions,
        )

        html    = build_morning_html(report_data)
        subject = f"[投資レポート] 朝次サマリー {now.strftime('%Y/%m/%d %H:%M')}"
        ok      = send_report(subject, html)
        logger.info(f"朝レポート送信: {'成功' if ok else '失敗'}")
        logger.info("=== 日次サイクル完了。次回起動は 21:00（夜間レポート）まで待機 ===")
        return ok
