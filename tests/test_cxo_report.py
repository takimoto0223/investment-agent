"""
tests/test_cxo_report.py
PR⑥ CXOAgent リファクタリングのテスト。

確認事項:
  1. _build_report_data() がデータを正しく整形・変換する
  2. generate_evening_report() が send_report を呼ぶ
  3. generate_morning_report() が daytrade_pl / scalpday_candidates を組み込む
  4. daytrade_pl=None / scalpday_candidates=None でも安全に動く
  5. _load_session_logs() が読んでいるのは moment_swing_us_log / scalpday_us_log のみ
"""
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import date

from agents.base import MarketContext
from agents.cxo import CXOAgent, CXOReportContext, _normalize_fx_signal
from report.template import ScalpDayCandidate, DaytradeRecord


# ──────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────

def _make_ctx(risk: str = "medium") -> MarketContext:
    return MarketContext(
        date=date.today().isoformat(),
        sector_scores={"AI半導体": 0.85, "金融": 0.42},
        macro_notes="テスト用コンテキスト",
        rotation_signal="維持",
        risk_level=risk,
    )


def _make_report_ctx(**kwargs) -> CXOReportContext:
    defaults = dict(
        ctx=_make_ctx(),
        fx_signal_dict={"fx_signal": "buy_usd", "rationale": "ドル強い", "usd_jpy_rate": 155.0},
        us_positions=[
            {"symbol": "NVDA", "qty": "2", "avg_entry_price": "800.0",
             "current_price": "840.0", "market_value": "1680.0"},
        ],
        jp_cash_jpy=500_000.0,
        usd_cash=1_000.0,
        us_equity_usd=2_000.0,
        usdjpy_rate=155.0,
    )
    defaults.update(kwargs)
    return CXOReportContext(**defaults)


def _make_cxo() -> CXOAgent:
    """LLM 呼び出しをスタブした CXOAgent を返す。"""
    cxo = CXOAgent.__new__(CXOAgent)
    cxo.logger = MagicMock()
    cxo._generate_cxo_memo = MagicMock(return_value="テスト方針メモ")
    return cxo


# ──────────────────────────────────────────────────
# 1. _normalize_fx_signal
# ──────────────────────────────────────────────────

class TestNormalizeFxSignal(unittest.TestCase):

    def test_buy_usd(self):
        self.assertEqual(_normalize_fx_signal("buy_usd"), "ドル買い")

    def test_buy_jpy(self):
        self.assertEqual(_normalize_fx_signal("buy_jpy"), "円買い")

    def test_hold_is_neutral(self):
        self.assertEqual(_normalize_fx_signal("hold"), "中立")

    def test_unknown_is_neutral(self):
        self.assertEqual(_normalize_fx_signal("unknown"), "中立")


# ──────────────────────────────────────────────────
# 2. _build_report_data: データ変換
# ──────────────────────────────────────────────────

class TestBuildReportData(unittest.TestCase):

    def setUp(self):
        self.cxo = _make_cxo()
        self.report_ctx = _make_report_ctx()

    def test_sector_scores_mapped(self):
        d = self.cxo._build_report_data(self.report_ctx)
        names = {s.name for s in d["sector_scores"]}
        self.assertIn("AI半導体", names)
        self.assertIn("金融", names)

    def test_fx_label_normalized(self):
        d = self.cxo._build_report_data(self.report_ctx)
        self.assertEqual(d["fx_label"], "ドル買い")

    def test_us_positions_converted_to_holdings(self):
        d = self.cxo._build_report_data(self.report_ctx)
        syms = {h.symbol for h in d["us_holdings"]}
        self.assertIn("NVDA", syms)

    def test_change_pct_calculated(self):
        d = self.cxo._build_report_data(self.report_ctx)
        nvda = next(h for h in d["us_holdings"] if h.symbol == "NVDA")
        # (840 - 800) / 800 * 100 = 5.0
        self.assertAlmostEqual(nvda.change_pct, 5.0, places=1)

    def test_risk_score_low(self):
        ctx = _make_report_ctx(ctx=_make_ctx("low"))
        d = self.cxo._build_report_data(ctx)
        self.assertEqual(d["risk_score"], 1)

    def test_risk_score_high(self):
        ctx = _make_report_ctx(ctx=_make_ctx("high"))
        d = self.cxo._build_report_data(ctx)
        self.assertEqual(d["risk_score"], 5)

    def test_total_jpy_positive(self):
        d = self.cxo._build_report_data(self.report_ctx)
        self.assertGreater(d["total_jpy"], 0)

    def test_pf_pct_sums_nonzero(self):
        d = self.cxo._build_report_data(self.report_ctx)
        total_pf = sum(h.pf_pct for h in d["us_holdings"] + d["jp_holdings"])
        self.assertGreater(total_pf, 0)

    def test_empty_positions_yields_empty_us_holdings(self):
        """us_positions=[] のとき us_holdings は空リストになる（ダミー補完なし）。"""
        ctx = _make_report_ctx(us_positions=[])
        d = self.cxo._build_report_data(ctx)
        self.assertEqual(d["us_holdings"], [])

    def test_empty_positions_no_dummy_symbols(self):
        """us_positions=[] のとき NVDA/MSFT/AAPL の固定ダミーが混入しない（回帰防止）。"""
        ctx = _make_report_ctx(us_positions=[])
        d = self.cxo._build_report_data(ctx)
        symbols = {h.symbol for h in d["us_holdings"]}
        self.assertNotIn("NVDA", symbols)
        self.assertNotIn("MSFT", symbols)
        self.assertNotIn("AAPL", symbols)

    def test_cxo_memo_generated(self):
        d = self.cxo._build_report_data(self.report_ctx)
        self.assertEqual(d["cxo_memo"], "テスト方針メモ")


# ──────────────────────────────────────────────────
# 3. generate_evening_report: send_report が呼ばれる
# ──────────────────────────────────────────────────

class TestGenerateEveningReport(unittest.TestCase):

    def setUp(self):
        self.cxo = _make_cxo()
        self.report_ctx = _make_report_ctx()

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_evening_html", return_value="<html>test</html>")
    def test_send_report_called_once(self, mock_html, mock_send):
        self.cxo.generate_evening_report(self.report_ctx)
        mock_send.assert_called_once()

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_evening_html", return_value="<html>test</html>")
    def test_returns_true_on_success(self, mock_html, mock_send):
        result = self.cxo.generate_evening_report(self.report_ctx)
        self.assertTrue(result)

    @patch("agents.cxo.send_report", return_value=False)
    @patch("agents.cxo.build_evening_html", return_value="<html>test</html>")
    def test_returns_false_on_send_failure(self, mock_html, mock_send):
        result = self.cxo.generate_evening_report(self.report_ctx)
        self.assertFalse(result)

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_evening_html", return_value="<html>test</html>")
    def test_subject_contains_date(self, mock_html, mock_send):
        self.cxo.generate_evening_report(self.report_ctx)
        subject = mock_send.call_args[0][0]
        self.assertIn("夜間サマリー", subject)


# ──────────────────────────────────────────────────
# 4. generate_morning_report: daytrade_pl / candidates を組み込む
# ──────────────────────────────────────────────────

_EMPTY_LOGS = {"moment_swing_us_log": [], "scalpday_us_log": []}

_SAMPLE_PL = {
    "total_gross": 50.0,
    "total_fees":  0.25,
    "total_net":   49.75,
    "trades": [
        {
            "symbol": "NVDA", "qty": 1,
            "buy_price": 800.0, "sell_price": 850.0,
            "gross_pl": 50.0, "fees": 0.25, "net_pl": 49.75,
        }
    ],
}


class TestGenerateMorningReport(unittest.TestCase):

    def setUp(self):
        self.cxo = _make_cxo()
        self.cxo._load_session_logs = MagicMock(return_value=_EMPTY_LOGS)
        self.report_ctx = _make_report_ctx()

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_morning_html", return_value="<html>morning</html>")
    def test_send_report_called(self, mock_html, mock_send):
        self.cxo.generate_morning_report(self.report_ctx)
        mock_send.assert_called_once()

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_morning_html")
    def test_daytrade_pl_included_in_report_data(self, mock_html, mock_send):
        captured = {}

        def capture(data):
            captured["data"] = data
            return "<html></html>"

        mock_html.side_effect = capture
        self.cxo.generate_morning_report(self.report_ctx, daytrade_pl=_SAMPLE_PL)
        self.assertAlmostEqual(captured["data"].daytrade_gross_pl, 50.0)
        self.assertAlmostEqual(captured["data"].daytrade_net_pl, 49.75)
        self.assertEqual(captured["data"].us_trade_count, 1)

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_morning_html")
    def test_daytrade_candidates_included(self, mock_html, mock_send):
        captured = {}

        def capture(data):
            captured["data"] = data
            return "<html></html>"

        mock_html.side_effect = capture
        candidates = [ScalpDayCandidate("9984", "ソフトバンクG", "buy", "スクリーニング通過")]
        self.cxo.generate_morning_report(
            self.report_ctx, scalpday_candidates=candidates
        )
        self.assertEqual(len(captured["data"].scalpday_candidates), 1)
        self.assertEqual(captured["data"].scalpday_candidates[0].symbol, "9984")

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_morning_html")
    def test_none_daytrade_pl_is_safe(self, mock_html, mock_send):
        """daytrade_pl=None でもエラーにならない。"""
        mock_html.return_value = "<html></html>"
        result = self.cxo.generate_morning_report(self.report_ctx, daytrade_pl=None)
        self.assertTrue(result)

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_morning_html")
    def test_none_candidates_gives_empty_list(self, mock_html, mock_send):
        captured = {}

        def capture(data):
            captured["data"] = data
            return "<html></html>"

        mock_html.side_effect = capture
        self.cxo.generate_morning_report(self.report_ctx, scalpday_candidates=None)
        self.assertEqual(captured["data"].scalpday_candidates, [])

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_morning_html")
    def test_subject_contains_morning(self, mock_html, mock_send):
        mock_html.return_value = "<html></html>"
        self.cxo.generate_morning_report(self.report_ctx)
        subject = mock_send.call_args[0][0]
        self.assertIn("朝次サマリー", subject)


# ──────────────────────────────────────────────────
# 5. _load_session_logs: 読むのは moment_swing_us / scalpday_us のみ
# ──────────────────────────────────────────────────

class TestLoadSessionLogs(unittest.TestCase):

    def test_returns_dict_with_expected_keys(self):
        cxo = _make_cxo()
        with patch.object(CXOAgent, "_read_json_log", return_value=[]):
            logs = cxo._load_session_logs()
        self.assertIn("moment_swing_us_log", logs)
        self.assertIn("scalpday_us_log", logs)

    def test_reads_moment_swing_us_log(self):
        cxo = _make_cxo()
        read_paths = []

        def capture_read(path):
            read_paths.append(str(path))
            return []

        with patch.object(CXOAgent, "_read_json_log", side_effect=capture_read):
            cxo._load_session_logs()

        self.assertTrue(any("moment_swing_us_log" in p for p in read_paths))

    def test_reads_scalpday_us_log(self):
        cxo = _make_cxo()
        read_paths = []

        def capture_read(path):
            read_paths.append(str(path))
            return []

        with patch.object(CXOAgent, "_read_json_log", side_effect=capture_read):
            cxo._load_session_logs()

        self.assertTrue(any("scalpday_us_log" in p for p in read_paths))

    def test_moment_swing_us_log_data_becomes_value_decisions(self):
        cxo = _make_cxo()
        cxo._generate_cxo_memo = MagicMock(return_value="memo")
        logs = {
            "moment_swing_us_log": [{"executed": [{"symbol": "NVDA", "name": "NVIDIA",
                                                    "qty": 1, "rationale": "買い理由",
                                                    "order_id": "x"}],
                                     "rejected": []}],
            "scalpday_us_log": [],
        }
        extras = cxo._build_morning_extras(logs, None, None, 155.0)
        self.assertEqual(len(extras["swing_decisions"]), 1)
        self.assertEqual(extras["swing_decisions"][0].symbol, "NVDA")
        self.assertEqual(extras["swing_decisions"][0].action, "buy")


# ──────────────────────────────────────────────────
# 6. US建玉ゼロ時のHTML表示（回帰防止）
# ──────────────────────────────────────────────────

class TestUsHoldingsEmptyHtml(unittest.TestCase):
    """
    us_positions=[] のとき:
      - HTML に「保有なし」が出る
      - ダミー銘柄 NVDA/MSFT/AAPL が HTML に出ない
    このクラスは build_evening_html を実際に呼ぶ（matplotlib 使用）。
    """

    def _build_html(self, us_positions: list) -> str:
        from unittest.mock import patch as p
        from agents.cxo import CXOAgent, CXOReportContext
        from report.template import build_evening_html

        cxo = _make_cxo()
        ctx = _make_report_ctx(us_positions=us_positions, us_equity_usd=0.0)
        with p("agents.cxo.send_report", return_value=True) as mock_send:
            d = cxo._build_report_data(ctx)

        from report.template import EveningReportData, SectorScore
        from datetime import datetime
        data = EveningReportData(
            generated_at=datetime.now(),
            total_assets_jpy=d["total_jpy"],
            total_assets_change_pct=0.0,
            jp_holdings=d["jp_holdings"],
            us_holdings=d["us_holdings"],
            risk_score=d["risk_score"],
            risk_level=ctx.ctx.risk_level,
            jpy_asset_ratio=d["jpy_asset_ratio"],
            usd_asset_ratio=d["usd_asset_ratio"],
            jpy_cash_ratio=d["jpy_cash_ratio"],
            usd_cash_ratio=d["usd_cash_ratio"],
            fx_signal=d["fx_label"],
            fx_rationale=d["fx_rationale"],
            usdjpy_rate=ctx.usdjpy_rate,
            margin_positions=[],
            sector_scores=d["sector_scores"],
            all_positions=d["us_holdings"],
            pre_us_fx_signal=d["fx_label"],
            pre_us_fx_rationale=d["fx_rationale"],
            cxo_memo=d["cxo_memo"],
            macro_notes=ctx.ctx.macro_notes,
            rotation_signal=ctx.ctx.rotation_signal,
        )
        return build_evening_html(data)

    def test_empty_us_shows_hohon_nashi(self):
        """保有ゼロのとき HTML に「保有なし」が含まれる。"""
        html = self._build_html(us_positions=[])
        self.assertIn("保有なし", html)

    def test_empty_us_no_dummy_nvda_in_html(self):
        """保有ゼロのとき HTML に NVDA ダミー行が出ない（回帰防止）。"""
        html = self._build_html(us_positions=[])
        # 凡例行として "NVDA" が出ないことを確認
        # ※ テスト用 MarketContext は "NVDA" を含まないので、グラフ系からも出ない
        self.assertNotIn("NVDA", html)

    def test_with_us_positions_no_hohon_nashi(self):
        """保有ありのとき「保有なし」は出ない。"""
        positions = [
            {"symbol": "NVDA", "qty": "1", "avg_entry_price": "800.0",
             "current_price": "840.0", "market_value": "840.0"},
        ]
        html = self._build_html(us_positions=positions)
        self.assertNotIn("保有なし", html)
        self.assertIn("NVDA", html)


# ──────────────────────────────────────────────────
# 8. CXOReportContext: データクラス構造
# ──────────────────────────────────────────────────

class TestCXOReportContext(unittest.TestCase):

    def test_required_fields(self):
        ctx = _make_report_ctx()
        self.assertIsInstance(ctx.ctx, MarketContext)
        self.assertIsInstance(ctx.fx_signal_dict, dict)
        self.assertIsInstance(ctx.us_positions, list)

    def test_defaults(self):
        ctx = CXOReportContext(
            ctx=_make_ctx(),
            fx_signal_dict={},
            us_positions=[],
        )
        self.assertEqual(ctx.jp_cash_jpy, 0.0)
        self.assertEqual(ctx.usd_cash, 0.0)
        self.assertEqual(ctx.us_equity_usd, 0.0)
        self.assertAlmostEqual(ctx.usdjpy_rate, 155.0)


if __name__ == "__main__":
    unittest.main()
