"""
tests/test_scalp_day_structure.py
PR②-a Step1: ScalpDayBase / ScalpDay_JP / ScalpDay_US の構造テスト（LLM呼び出しなし）。
"""
import unittest
from unittest.mock import MagicMock
from agents.scalp_day import ScalpDayBase, ScalpDay_JP, ScalpDay_US
from config.settings import RISK


def _make(cls):
    a = cls.__new__(cls)
    a.logger = MagicMock()
    return a


class TestScalpDayAttributes(unittest.TestCase):
    """市場固有クラス属性の確認"""

    def test_jp_market(self):
        self.assertEqual(ScalpDay_JP._market, "JP")

    def test_us_market(self):
        self.assertEqual(ScalpDay_US._market, "US")

    def test_jp_min_unit(self):
        self.assertEqual(ScalpDay_JP._min_unit, 100)

    def test_us_min_unit(self):
        self.assertEqual(ScalpDay_US._min_unit, 1)

    def test_jp_name(self):
        self.assertEqual(ScalpDay_JP.name, "ScalpDay_JP")

    def test_us_name(self):
        self.assertEqual(ScalpDay_US.name, "ScalpDay_US")


class TestCalcMaxQtyJP(unittest.TestCase):
    """ScalpDay_JP._calc_max_qty(): JPY上限・100株単位"""

    def setUp(self):
        self.agent = _make(ScalpDay_JP)

    def test_rounds_to_100(self):
        # max_daytrade_margin_jpy=300,000 / price=2,100 = 142.8 → floor to 100株単位 = 100
        qty = self.agent._calc_max_qty(2100)
        self.assertEqual(qty % 100, 0, "100株単位でなければならない")

    def test_normal_price(self):
        # price=1,000 → 300,000 / 1,000 = 300 → 300株
        qty = self.agent._calc_max_qty(1000)
        expected = int(RISK.max_daytrade_margin_jpy / 1000 / 100) * 100
        self.assertEqual(qty, expected)

    def test_very_high_price(self):
        # price > max_daytrade_margin_jpy → qty=0（<min_unit で弾かれる想定）
        qty = self.agent._calc_max_qty(RISK.max_daytrade_margin_jpy + 1)
        self.assertEqual(qty, 0)

    def test_position_limit_text_mentions_jpy(self):
        text = self.agent._position_limit_text()
        self.assertIn("JPY", text)
        self.assertIn("100株", text)


class TestCalcMaxQtyUS(unittest.TestCase):
    """ScalpDay_US._calc_max_qty(): USD上限・最小1株"""

    def setUp(self):
        self.agent = _make(ScalpDay_US)

    def test_minimum_one(self):
        # price >> max_us_position_usd → still returns 1
        qty = self.agent._calc_max_qty(RISK.max_us_position_usd * 10)
        self.assertGreaterEqual(qty, 1)

    def test_normal_price(self):
        # price=100 → 3,000/100 = 30
        qty = self.agent._calc_max_qty(100)
        expected = max(1, int(RISK.max_us_position_usd / 100))
        self.assertEqual(qty, expected)

    def test_no_unit_rounding(self):
        # US: 端数は切り捨て（100単位ではない）
        qty = self.agent._calc_max_qty(200)
        self.assertEqual(qty, max(1, int(RISK.max_us_position_usd / 200)))

    def test_position_limit_text_mentions_usd(self):
        text = self.agent._position_limit_text()
        self.assertIn("USD", text)


class TestShouldEmergencyExit(unittest.TestCase):
    """should_emergency_exit: 基底クラスで統一ロジック"""

    def _check(self, cls):
        agent = _make(cls)
        # -2% 以上の損失 → True（daytrade_stop_loss_pct=0.02）
        pos = {"entry_price": 1000, "side": "buy"}
        self.assertTrue(agent.should_emergency_exit(pos, 979))   # -2.1%
        self.assertFalse(agent.should_emergency_exit(pos, 981))  # -1.9%

    def test_jp_emergency_exit(self):
        self._check(ScalpDay_JP)

    def test_us_emergency_exit(self):
        self._check(ScalpDay_US)

    def test_sell_side_exit(self):
        agent = _make(ScalpDay_JP)
        pos = {"entry_price": 1000, "side": "sell"}
        self.assertTrue(agent.should_emergency_exit(pos, 1021))   # +2.1%
        self.assertFalse(agent.should_emergency_exit(pos, 1019))  # +1.9%


class TestBaseRaisesNotImplemented(unittest.TestCase):
    """基底クラスの抽象メソッドは NotImplementedError を発生させる"""

    def test_calc_max_qty_raises(self):
        base = _make(ScalpDayBase)
        with self.assertRaises(NotImplementedError):
            base._calc_max_qty(1000)

    def test_position_limit_text_raises(self):
        base = _make(ScalpDayBase)
        with self.assertRaises(NotImplementedError):
            base._position_limit_text()


class TestInheritance(unittest.TestCase):
    """継承関係の確認"""

    def test_jp_is_subclass(self):
        self.assertTrue(issubclass(ScalpDay_JP, ScalpDayBase))

    def test_us_is_subclass(self):
        self.assertTrue(issubclass(ScalpDay_US, ScalpDayBase))

    def test_shared_method_names(self):
        for method in ("screen_candidates", "generate_trade_proposal",
                       "revise_proposal", "should_emergency_exit"):
            self.assertTrue(hasattr(ScalpDay_JP, method), f"ScalpDay_JP missing {method}")
            self.assertTrue(hasattr(ScalpDay_US, method), f"ScalpDay_US missing {method}")
