"""
tests/test_cio_allocate_budgets.py
PR④ リスクゲート土台の検証。

確認事項:
  1. 5ポッドへの資金枠配分（budget_jpy/usd, active_sectors, catalyst_slots）
  2. risk_level=low/medium/high で枠が 100%/50%/0% に変化すること
  3. JPY系合計 80% / USD系合計 80% の上限が超えないこと
  4. catalyst_slots = FXRebalance:0, 他:1
"""
import unittest
from unittest.mock import MagicMock
from agents.base import MarketContext
from agents.cio import CIOAgent, _POD_RISK_FACTORS

_TOTAL_JPY = 1_000_000
_CASH_USD  = 10_000
_RATE      = 152.0

_SECTOR_SCORES = {
    "AI半導体":             0.90,  # 活性（>= 0.6）
    "データセンターインフラ": 0.75,  # 活性（>= 0.6）
    "エネルギー":            0.40,  # 非活性（< 0.6）→ active_sectors に含まれない
}


def _cio() -> CIOAgent:
    cio = CIOAgent.__new__(CIOAgent)
    cio.logger = MagicMock()
    return cio


def _ctx(risk: str) -> MarketContext:
    return MarketContext(
        date="2026-06-18",
        sector_scores=_SECTOR_SCORES,
        macro_notes="test",
        rotation_signal="維持",
        risk_level=risk,
    )


def _allocs(risk: str):
    return _cio().allocate_budgets(
        _ctx(risk),
        total_cash_jpy=_TOTAL_JPY,
        cash_usd=_CASH_USD,
        usd_jpy_rate=_RATE,
    )


class TestRiskGateLevels(unittest.TestCase):
    """確認2: risk_level に応じた係数変化"""

    def test_low_full_budget(self):
        a = _allocs("low")
        # JPY 系: 1,000,000 × ratio × 1.0
        self.assertAlmostEqual(a["ScalpDay_JP"].budget_jpy,    300_000)
        self.assertAlmostEqual(a["MomentSwing_JP"].budget_jpy, 300_000)
        self.assertAlmostEqual(a["FXRebalance"].budget_jpy,    200_000)
        # USD 系: 10,000 × ratio × 1.0
        self.assertAlmostEqual(a["ScalpDay_US"].budget_usd,    5_000)
        self.assertAlmostEqual(a["MomentSwing_US"].budget_usd, 3_000)

    def test_medium_half_budget(self):
        a = _allocs("medium")
        self.assertAlmostEqual(a["ScalpDay_JP"].budget_jpy,    150_000)
        self.assertAlmostEqual(a["MomentSwing_JP"].budget_jpy, 150_000)
        self.assertAlmostEqual(a["FXRebalance"].budget_jpy,    100_000)
        self.assertAlmostEqual(a["ScalpDay_US"].budget_usd,    2_500)
        self.assertAlmostEqual(a["MomentSwing_US"].budget_usd, 1_500)

    def test_high_zero_budget(self):
        """risk=high: 全ポッドが budget=0（旧 RiskManagerAgent の拒否権を吸収）"""
        a = _allocs("high")
        for pod, alloc in a.items():
            self.assertEqual(alloc.budget_jpy, 0, f"{pod}.budget_jpy must be 0 on high risk")
            self.assertEqual(alloc.budget_usd, 0, f"{pod}.budget_usd must be 0 on high risk")


class TestExposureCap(unittest.TestCase):
    """確認3: JPY/USD 系合計が各 80% 上限を超えない"""

    def _check_caps(self, risk: str):
        a = _allocs(risk)
        jpy_total = sum(v.budget_jpy for v in a.values())
        usd_total = sum(v.budget_usd for v in a.values())
        self.assertLessEqual(
            jpy_total, _TOTAL_JPY * 0.80 + 0.01,
            f"risk={risk}: JPY合計 {jpy_total:,.0f} が上限 {_TOTAL_JPY * 0.80:,.0f} を超過"
        )
        self.assertLessEqual(
            usd_total, _CASH_USD * 0.80 + 0.01,
            f"risk={risk}: USD合計 {usd_total:,.2f} が上限 {_CASH_USD * 0.80:,.2f} を超過"
        )

    def test_cap_low(self):
        self._check_caps("low")

    def test_cap_medium(self):
        self._check_caps("medium")

    def test_cap_high(self):
        self._check_caps("high")

    def test_low_jpy_exactly_80pct(self):
        """low リスクの JPY 系合計がちょうど 80%（ポッド比率設計の確認）"""
        a = _allocs("low")
        jpy_total = sum(v.budget_jpy for v in a.values())
        self.assertAlmostEqual(jpy_total, _TOTAL_JPY * 0.80, places=2)

    def test_low_usd_exactly_80pct(self):
        """low リスクの USD 系合計がちょうど 80%"""
        a = _allocs("low")
        usd_total = sum(v.budget_usd for v in a.values())
        self.assertAlmostEqual(usd_total, _CASH_USD * 0.80, places=2)


class TestCatalystSlots(unittest.TestCase):
    """確認4: catalyst_slots の値"""

    def test_fx_rebalance_zero_slots(self):
        a = _allocs("low")
        self.assertEqual(a["FXRebalance"].catalyst_slots, 0)

    def test_stock_pods_one_slot(self):
        a = _allocs("low")
        for pod in ("ScalpDay_JP", "ScalpDay_US", "MomentSwing_JP", "MomentSwing_US"):
            self.assertEqual(a[pod].catalyst_slots, 1, f"{pod}.catalyst_slots should be 1")

    def test_slots_unchanged_by_risk_level(self):
        """catalyst_slots は risk_level によらず固定"""
        for risk in ("low", "medium", "high"):
            a = _allocs(risk)
            self.assertEqual(a["FXRebalance"].catalyst_slots, 0, f"risk={risk}")
            self.assertEqual(a["ScalpDay_JP"].catalyst_slots, 1, f"risk={risk}")


class TestActiveSectors(unittest.TestCase):
    """確認1(補足): active_sectors のフィルタリング"""

    def test_only_high_score_sectors(self):
        """スコア 0.6 未満のセクターは active_sectors に含まれない"""
        a = _allocs("low")
        for pod in ("ScalpDay_JP", "ScalpDay_US", "MomentSwing_JP", "MomentSwing_US"):
            self.assertNotIn("エネルギー", a[pod].active_sectors,
                             f"{pod} should exclude low-score sector")
            self.assertIn("AI半導体", a[pod].active_sectors)
            self.assertIn("データセンターインフラ", a[pod].active_sectors)

    def test_fx_rebalance_no_sectors(self):
        """FXRebalance の active_sectors は常に空"""
        for risk in ("low", "medium", "high"):
            a = _allocs(risk)
            self.assertEqual(a["FXRebalance"].active_sectors, [], f"risk={risk}")

    def test_active_sectors_max_3(self):
        """活性セクターは最大 3 件"""
        ctx_many = MarketContext(
            date="2026-06-18",
            sector_scores={f"sector_{i}": 0.9 - i * 0.05 for i in range(6)},
            macro_notes="", rotation_signal="", risk_level="low",
        )
        a = _cio().allocate_budgets(ctx_many, total_cash_jpy=1_000_000, cash_usd=0)
        self.assertLessEqual(len(a["ScalpDay_JP"].active_sectors), 3)


class TestPodRiskFactorsOverride(unittest.TestCase):
    """確認2(補足): pod_risk_factors の上書き"""

    def test_custom_factors(self):
        """medium で 0.8 に上書きした場合、ScalpDay_JP = 1,000,000 × 0.30 × 0.8 = 240,000"""
        custom = {k: {"low": 1.0, "medium": 0.8, "high": 0.0} for k in _POD_RISK_FACTORS}
        a = _cio().allocate_budgets(
            _ctx("medium"), total_cash_jpy=_TOTAL_JPY, cash_usd=0,
            usd_jpy_rate=_RATE, pod_risk_factors=custom
        )
        self.assertAlmostEqual(a["ScalpDay_JP"].budget_jpy, 240_000)

    def test_all_pods_present(self):
        """返却に全5ポッドが含まれる"""
        a = _allocs("low")
        expected_pods = {"ScalpDay_JP", "ScalpDay_US", "MomentSwing_JP", "MomentSwing_US", "FXRebalance"}
        self.assertEqual(set(a.keys()), expected_pods)


if __name__ == "__main__":
    unittest.main()
