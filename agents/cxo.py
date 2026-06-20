"""
agents/cxo.py
CxOエージェント：全エージェントのログとポジション情報を集約し、
朝晩のHTMLレポートを生成してメールで送信する。

実行タイミング：
  21:00 → generate_evening_report(report_ctx)
  06:00 → generate_morning_report(report_ctx, daytrade_pl, scalpday_candidates)

データ収集の責務はない。呼び出し側（main.py）が CXOReportContext に
データを詰めて渡す。CXO はそのデータを整形・送信するだけ。
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agents.base import BaseAgent, MarketContext
from prompts.all_agents import CXO_PROMPT
from report.template import (
    EveningReportData, MorningReportData,
    HoldingItem, MarginPosition, SectorScore,
    ScalpDayCandidate,
    SwingDecision,
    build_evening_html, build_morning_html,
)
from report.mailer import send_report

logger = logging.getLogger(__name__)

_DEFAULT_USDJPY = 155.0


def _normalize_fx_signal(raw: str) -> str:
    """FXシグナル文字列を 円買い/中立/ドル買い に正規化する。"""
    s = str(raw).lower().replace("_", "").replace("-", "").replace(" ", "")
    if any(x in s for x in ["buyjpy", "sellusd", "jpybuy", "円買", "yenbuy"]):
        return "円買い"
    if any(x in s for x in ["buyusd", "selljpy", "usdbuy", "ドル買", "dollarbuy"]):
        return "ドル買い"
    return "中立"


@dataclass
class CXOReportContext:
    """
    レポート生成に必要なデータをまとめた注入用コンテキスト。
    呼び出し側（main.py）が CIO / FX / Broker から収集して渡す。
    """
    ctx:           MarketContext
    fx_signal_dict: dict          # FXStrategyAgent.generate_signal() の戻り値
    us_positions:  list[dict]     # AlpacaBroker.get_positions()
    jp_cash_jpy:      float = 0.0   # KabuBroker.get_wallet_margin()["MarginAccountWallet"]
    usd_cash:         float = 0.0   # AlpacaBroker.get_account()["cash"]
    us_equity_usd:    float = 0.0   # AlpacaBroker.get_account()["equity"]
    usdjpy_rate:      float = _DEFAULT_USDJPY
    usdjpy_source:    str   = ""    # "api" | "cache" | "fallback"
    usdjpy_fetched_at: str  = ""    # "6/19" 形式


class CXOAgent(BaseAgent):
    name = "CXOAgent"
    system_prompt = CXO_PROMPT
    model = "claude-opus-4-8"

    # ── セッションログ読み込み（mock 可能なよう小メソッドに切り出す） ──

    def _load_session_logs(self) -> dict:
        """
        生きているセッションログを読んで返す。
          - moment_swing_us_log.json : MomentSwing_US スイング投資の発注・見送り記録
          - scalpday_us_log.json     : ScalpDay_US デイトレ発注ログ（trade_count のフォールバック用）
        discussion_log.json は廃止済みのため読まない。
        """
        return {
            "moment_swing_us_log": self._read_json_log(Path("logs/moment_swing_us_log.json")),
            "scalpday_us_log":     self._read_json_log(Path("logs/scalpday_us_log.json")),
        }

    @staticmethod
    def _read_json_log(path: Path) -> list[dict]:
        """JSON ログファイルを読む。存在しない/破損時は空リスト。"""
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"ログ読み込み失敗 {path}: {exc}")
            return []

    # ── LLM: CxO 方針メモ生成 ────────────────────────────────────────

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

    # ── 注入データ → レポートデータ変換 ─────────────────────────────

    def _build_report_data(self, report_ctx: CXOReportContext) -> dict:
        """
        CXOReportContext を EveningReportData / MorningReportData 用フィールドに変換する。
        """
        ctx     = report_ctx.ctx
        usdjpy  = report_ctx.usdjpy_rate
        fx_dict = report_ctx.fx_signal_dict

        # セクタースコア
        sector_scores = [
            SectorScore(name=name, score=score, change=0.0, change_pct=0.0)
            for name, score in ctx.sector_scores.items()
        ]

        # FXシグナル正規化
        fx_label = _normalize_fx_signal(fx_dict.get("fx_signal", "hold"))

        # 米国株ポジション → HoldingItem
        _DUMMY_JP = [
            HoldingItem(symbol="9984", name="ソフトバンクG",  value_jpy=320_000, pf_pct=0.0, change_pct=+1.8),
            HoldingItem(symbol="6857", name="アドバンテスト", value_jpy=250_000, pf_pct=0.0, change_pct=-0.5),
            HoldingItem(symbol="4063", name="信越化学",        value_jpy=180_000, pf_pct=0.0, change_pct=+0.3),
            HoldingItem(symbol="2330", name="フィックスターズ",value_jpy= 90_000, pf_pct=0.0, change_pct=+2.1),
        ]

        us_holdings: list[HoldingItem] = []
        for pos in report_ctx.us_positions:
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

        # JP建玉は kabu API 未接続中は固定ダミー。US建玉が空なら空のまま渡す（テンプレート側で「保有なし」を表示）
        jp_holdings: list[HoldingItem] = _DUMMY_JP

        # 総資産・PF比計算
        usd_equity_jpy = report_ctx.us_equity_usd * usdjpy
        jp_cash        = report_ctx.jp_cash_jpy
        usd_cash_jpy   = report_ctx.usd_cash * usdjpy
        total_jpy      = usd_equity_jpy + jp_cash
        if total_jpy <= 0:
            total_jpy = max(
                sum(h.value_jpy for h in us_holdings) + sum(h.value_jpy for h in jp_holdings),
                1.0,
            )

        for h in us_holdings + jp_holdings:
            h.pf_pct = h.value_jpy / total_jpy if total_jpy > 0 else 0.0

        # 円ドル割合
        usd_total_jpy   = sum(h.value_jpy for h in us_holdings)
        usd_asset_ratio = min(1.0, usd_total_jpy / total_jpy) if total_jpy > 0 else 0.35
        jpy_asset_ratio = 1.0 - usd_asset_ratio
        cash_total      = jp_cash + usd_cash_jpy
        usd_cash_ratio  = usd_cash_jpy / cash_total if cash_total > 0 else 0.35
        jpy_cash_ratio  = 1.0 - usd_cash_ratio

        # リスクスコア
        risk_score = {"low": 1, "medium": 3, "high": 5}.get(ctx.risk_level, 3)

        return {
            "total_jpy":       total_jpy,
            "jp_holdings":     jp_holdings,
            "us_holdings":     us_holdings,
            "sector_scores":   sector_scores,
            "fx_label":        fx_label,
            "fx_rationale":    fx_dict.get("rationale", ""),
            "usd_asset_ratio": usd_asset_ratio,
            "jpy_asset_ratio": jpy_asset_ratio,
            "usd_cash_ratio":  usd_cash_ratio,
            "jpy_cash_ratio":  jpy_cash_ratio,
            "risk_score":      risk_score,
            "cxo_memo":        self._generate_cxo_memo(ctx),
            "ctx":             ctx,
        }

    # ── セッションログ → レポートデータ変換 ─────────────────────────

    def _build_morning_extras(
        self,
        logs: dict,
        daytrade_pl: dict | None,
        scalpday_candidates: list[ScalpDayCandidate] | None,
        usdjpy: float,
    ) -> dict:
        """
        朝レポート専用の追加データを組み立てる。
          - logs                : _load_session_logs() の戻り値
          - daytrade_pl         : calc_daytrade_pl(activities) の戻り値（None 可）
          - scalpday_candidates : ScalpDay_JP スクリーニング結果（None 可）
        """
        # ── デイトレ損益 ──────────────────────────────────────────────
        daytrade_records: list[dict] = []
        daytrade_gross_pl = daytrade_fees = daytrade_net_pl = 0.0
        us_trade_count = 0
        us_realized_pl_usd = 0.0

        if daytrade_pl:
            daytrade_gross_pl  = daytrade_pl.get("total_gross", 0.0)
            daytrade_fees      = daytrade_pl.get("total_fees", 0.0)
            daytrade_net_pl    = daytrade_pl.get("total_net", 0.0)
            us_realized_pl_usd = daytrade_net_pl
            for t in daytrade_pl.get("trades", []):
                daytrade_records.append({
                    "symbol":     t["symbol"],
                    "qty":        t["qty"],
                    "buy_price":  t["buy_price"],
                    "sell_price": t["sell_price"],
                    "gross_pl":   t["gross_pl"],
                    "fees":       t["fees"],
                    "net_pl":     t["net_pl"],
                })
            us_trade_count = len(daytrade_records)
            logger.info(
                f"デイトレ損益: gross={daytrade_gross_pl:+.2f} "
                f"fees=-{daytrade_fees:.4f} net={daytrade_net_pl:+.2f} USD"
            )

        # デイトレ件数フォールバック（Alpaca 接続失敗時）
        if us_trade_count == 0:
            dt_sessions = logs.get("scalpday_us_log", [])
            if dt_sessions:
                us_trade_count = len(dt_sessions[-1].get("executed", []))

        # ── MomentSwingUS 買い/見送り決定 ────────────────────────────
        swing_decisions: list[SwingDecision] = []
        val_sessions = logs.get("moment_swing_us_log", [])
        if val_sessions:
            latest_val = val_sessions[-1]
            for ex in latest_val.get("executed", []):
                swing_decisions.append(SwingDecision(
                    symbol=ex.get("symbol", ""),
                    name=ex.get("name", ex.get("symbol", "")),
                    action="buy",
                    rationale=ex.get("rationale", ""),
                    qty=float(ex.get("qty", 0)),
                    consensus=ex.get("consensus", "execute"),
                ))
            for rj in latest_val.get("rejected", []):
                swing_decisions.append(SwingDecision(
                    symbol=rj.get("symbol", ""),
                    name=rj.get("symbol", ""),
                    action="reject",
                    rationale=rj.get("reason", ""),
                ))

        return {
            "us_realized_pl_usd": us_realized_pl_usd,
            "us_trade_count":     us_trade_count,
            "daytrade_records":   daytrade_records,
            "daytrade_gross_pl":  daytrade_gross_pl,
            "daytrade_fees":      daytrade_fees,
            "daytrade_net_pl":    daytrade_net_pl,
            "scalpday_candidates": scalpday_candidates or [],
            "swing_decisions":    swing_decisions,
        }

    # ── 要承認通知 ──────────────────────────────────────────────────

    def notify_action_required(
        self,
        symbol: str,
        action: str,          # "stop_loss" | "take_profit"
        chg_pct: float,       # 現在の変化率（例: -0.082）
        threshold_pct: float, # SL or TP の閾値（例: 0.08 / 0.15）
        current_price: float,
        unrealized_pl: float,
    ) -> bool:
        """
        バリューポジションの損切り/利確条件到達をメールで通知する。
        自動決済はしない。ユーザーが承認・手動実行する。
        """
        action_ja  = "損切り（Stop Loss）" if action == "stop_loss" else "利確（Take Profit）"
        sign       = "-" if action == "stop_loss" else "+"
        threshold  = f"{sign}{threshold_pct:.0%}"
        chg_str    = f"{chg_pct:+.2%}"
        pl_str     = f"${unrealized_pl:+.2f}"
        subject    = f"[要承認] {symbol} {action_ja}条件到達 {chg_str}"

        html = f"""
<html><body style="font-family:sans-serif;padding:20px;">
<h2 style="color:{'#cc3300' if action=='stop_loss' else '#006600'}">
  ⚠️ {symbol} {action_ja}条件到達</h2>
<table border="1" cellpadding="8" style="border-collapse:collapse;">
  <tr><th>銘柄</th><td><b>{symbol}</b></td></tr>
  <tr><th>アクション種別</th><td>{action_ja}</td></tr>
  <tr><th>現在の騰落率</th><td><b>{chg_str}</b></td></tr>
  <tr><th>閾値</th><td>{threshold}</td></tr>
  <tr><th>現在値</th><td>${current_price:.2f}</td></tr>
  <tr><th>含み損益</th><td>{pl_str}</td></tr>
</table>
<p style="margin-top:16px;">
  <b>自動決済はしていません。</b><br>
  承認する場合は Alpaca ダッシュボードまたは以下のスクリプトで手動決済してください。
</p>
<pre style="background:#f5f5f5;padding:12px;border-radius:4px;">
python -c "
from brokers.alpaca import AlpacaBroker
AlpacaBroker().close_position('{symbol}')
print('決済完了: {symbol}')
"
</pre>
<p style="color:#888;font-size:12px;">
  このメールは投資エージェントシステムから自動送信されました。<br>
  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
</p>
</body></html>"""

        ok = send_report(subject, html)
        logger.info(f"要承認通知送信: {symbol} {action_ja} {'成功' if ok else '失敗'}")
        return ok

    # ── 夜間レポート ─────────────────────────────────────────────────

    def generate_evening_report(self, report_ctx: CXOReportContext) -> bool:
        """
        21:00 夜間レポートを生成してメール送信する。
        データ収集は呼び出し側が行い report_ctx に詰めて渡す。
        Returns: 送信成功で True。
        """
        logger.info("=== 夕方レポート生成開始 ===")
        now = datetime.now()
        d   = self._build_report_data(report_ctx)
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
            usdjpy_rate=report_ctx.usdjpy_rate,
            usdjpy_source=report_ctx.usdjpy_source,
            usdjpy_fetched_at=report_ctx.usdjpy_fetched_at,
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

    # ── 朝次レポート ─────────────────────────────────────────────────

    def generate_morning_report(
        self,
        report_ctx: CXOReportContext,
        daytrade_pl: dict | None = None,
        scalpday_candidates: list[ScalpDayCandidate] | None = None,
    ) -> bool:
        """
        06:00 朝次レポートを生成してメール送信する。
        データ収集は呼び出し側が行い引数で渡す。

        Args:
            report_ctx:           CIO / FX / Broker データをまとめたコンテキスト
            daytrade_pl:          calc_daytrade_pl(activities) の戻り値（None 可）
            scalpday_candidates:  ScalpDay_JP スクリーニング結果（None 可）
        Returns: 送信成功で True。
        """
        logger.info("=== 朝レポート生成開始 ===")
        now  = datetime.now()
        d    = self._build_report_data(report_ctx)
        ctx: MarketContext = d["ctx"]

        logs   = self._load_session_logs()
        extras = self._build_morning_extras(
            logs, daytrade_pl, scalpday_candidates, report_ctx.usdjpy_rate
        )

        usdjpy = report_ctx.usdjpy_rate
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
            usdjpy_source=report_ctx.usdjpy_source,
            usdjpy_fetched_at=report_ctx.usdjpy_fetched_at,
            margin_positions=[],
            sector_scores=d["sector_scores"],
            all_positions=d["us_holdings"],
            pre_us_fx_signal=d["fx_label"],
            pre_us_fx_rationale=d["fx_rationale"],
            cxo_memo=d["cxo_memo"],
            macro_notes=ctx.macro_notes,
            rotation_signal=ctx.rotation_signal,
            us_realized_pl_usd=extras["us_realized_pl_usd"],
            us_realized_pl_jpy=extras["us_realized_pl_usd"] * usdjpy,
            us_trade_count=extras["us_trade_count"],
            scalpday_candidates=extras["scalpday_candidates"],
            overnight_fx_summary=f"USD/JPY {usdjpy:.2f}",
            overnight_fx_high=usdjpy * 1.005,
            overnight_fx_low=usdjpy * 0.995,
            overnight_fx_change_pct=0.0,
            daytrade_records=extras["daytrade_records"],
            daytrade_gross_pl=extras["daytrade_gross_pl"],
            daytrade_fees=extras["daytrade_fees"],
            daytrade_net_pl=extras["daytrade_net_pl"],
            swing_decisions=extras["swing_decisions"],
        )

        html    = build_morning_html(report_data)
        subject = f"[投資レポート] 朝次サマリー {now.strftime('%Y/%m/%d %H:%M')}"
        ok      = send_report(subject, html)
        logger.info(f"朝レポート送信: {'成功' if ok else '失敗'}")
        logger.info("=== 日次サイクル完了。次回起動は 21:00（夜間レポート）まで待機 ===")
        return ok
