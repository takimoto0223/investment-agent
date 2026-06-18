"""
tests/test_cxo_report.py
PR⑥ CXOAgent リファクタリングのテスト。

確認事項:
  1. _build_report_data() がデータを正しく整形・変換する
  2. generate_evening_report() が send_report を呼ぶ
  3. generate_morning_report() が daytrade_pl / daytrade_candidates を組み込む
  4. daytrade_pl=None / daytrade_candidates=None でも安全に動く
  5. _load_session_logs() が読んでいるのは us_value_log / us_daytrade_log のみ
"""
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import date

from agents.base import MarketContext
from agents.cxo import CXOAgent, CXOReportContext, _normalize_fx_signal
from report.template import DaytradeCandidate, DaytradeRecord


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

    def test_empty_positions_uses_dummy(self):
        ctx = _make_report_ctx(us_positions=[])
        d = self.cxo._build_report_data(ctx)
        # ダミーUSホールディングが入る
        self.assertGreater(len(d["us_holdings"]), 0)

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
        candidates = [DaytradeCandidate("9984", "ソフトバンクG", "buy", "スクリーニング通過")]
        self.cxo.generate_morning_report(
            self.report_ctx, daytrade_candidates=candidates
        )
        self.assertEqual(len(captured["data"].daytrade_candidates), 1)
        self.assertEqual(captured["data"].daytrade_candidates[0].symbol, "9984")

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
        self.cxo.generate_morning_report(self.report_ctx, daytrade_candidates=None)
        self.assertEqual(captured["data"].daytrade_candidates, [])

    @patch("agents.cxo.send_report", return_value=True)
    @patch("agents.cxo.build_morning_html")
    def test_discussion_items_always_empty(self, mock_html, mock_send):
        """discussion_log は廃止済みのため常に空リスト。"""
        captured = {}

        def capture(data):
            captured["data"] = data
            return "<html></html>"

        mock_html.side_effect = capture
        self.cxo.generate_morning_report(self.report_ctx)
        self.assertEqual(captured["data"].discussion_items, [])
        self.assertEqual(captured["data"].discussion_session_date, "")

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

    def test_does_not_read_discussion_log(self):
        """discussion_log.json は廃止済みのため読まない。"""
        cxo = _make_cxo()
        read_paths = []

        def capture_read(path):
            read_paths.append(str(path))
            return []

        with patch.object(CXOAgent, "_read_json_log", side_effect=capture_read):
            cxo._load_session_logs()

        self.assertFalse(
            any("discussion" in p for p in read_paths),
            f"discussion_log が読まれている: {read_paths}",
        )

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
        self.assertEqual(len(extras["value_decisions"]), 1)
        self.assertEqual(extras["value_decisions"][0].symbol, "NVDA")
        self.assertEqual(extras["value_decisions"][0].action, "buy")


# ──────────────────────────────────────────────────
# 6. CXOReportContext: データクラス構造
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
