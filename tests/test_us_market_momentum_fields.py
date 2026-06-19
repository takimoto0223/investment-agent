"""
tests/test_us_market_momentum_fields.py
build_us_universe() への ret_5d_pct / ret_20d_pct 追加のテスト。
LLM・外部 API 呼び出しなし。
"""
import unittest
from unittest.mock import patch, MagicMock


def _make_daily(n: int, start_close: float = 100.0, step: float = 1.0) -> list[dict]:
    """n 本の日足ダミーデータ（単調増加）。"""
    bars = []
    c = start_close
    for i in range(n):
        bars.append({
            "date":   f"2025-01-{i+1:02d}",
            "open":   c - 0.5,
            "high":   c + 1.0,
            "low":    c - 1.0,
            "close":  c,
            "volume": 1_000_000,
        })
        c += step
    return bars


def _make_quote(price: float = 110.0) -> dict:
    return {"CurrentPrice": price, "AskPrice": price + 0.1, "BidPrice": price - 0.1}


class TestBuildUsUniverseMomentumFields(unittest.TestCase):

    def _build(self, daily_bars, quote=None, symbol="NVDA"):
        """build_us_universe を 1 銘柄で呼ぶヘルパー。"""
        from data.us_market import build_us_universe
        q = quote or _make_quote(daily_bars[-1]["close"] if daily_bars else 100.0)
        with patch("data.us_market.get_daily_bars_us", return_value=daily_bars), \
             patch("data.us_market.get_quote_us", return_value=q):
            return build_us_universe([{"symbol": symbol, "name": "Test", "market": "US", "sector": "AI半導体"}])

    # ─── フィールド存在 ───────────────────────────────────────────────────────

    def test_ret_5d_pct_key_present(self):
        daily = _make_daily(22)
        result = self._build(daily)
        self.assertIn("ret_5d_pct", result[0])

    def test_ret_20d_pct_key_present(self):
        daily = _make_daily(22)
        result = self._build(daily)
        self.assertIn("ret_20d_pct", result[0])

    # ─── 正常値 ──────────────────────────────────────────────────────────────

    def test_ret_5d_pct_value_correct(self):
        """22本の単調増加データで 5 日リターンを確認。"""
        daily = _make_daily(22, start_close=100.0, step=1.0)
        # closes: 100, 101, ..., 121  (22 bars, indices 0-21)
        # ret_5d = closes[-1] / closes[-6] - 1 = 121 / 116 - 1
        expected = round((121.0 / 116.0 - 1) * 100, 2)
        result = self._build(daily)
        self.assertAlmostEqual(result[0]["ret_5d_pct"], expected, places=1)

    def test_ret_20d_pct_value_correct(self):
        """22本の単調増加データで 20 日リターンを確認。"""
        daily = _make_daily(22, start_close=100.0, step=1.0)
        # closes[-1] = 121, closes[-21] = 101
        expected = round((121.0 / 101.0 - 1) * 100, 2)
        result = self._build(daily)
        self.assertAlmostEqual(result[0]["ret_20d_pct"], expected, places=1)

    # ─── データ不足時は None ─────────────────────────────────────────────────

    def test_ret_5d_pct_none_when_insufficient_bars(self):
        """5 本未満なら ret_5d_pct は None。"""
        daily = _make_daily(4)
        result = self._build(daily)
        self.assertIsNone(result[0]["ret_5d_pct"])

    def test_ret_20d_pct_none_when_insufficient_bars(self):
        """20 本未満なら ret_20d_pct は None。"""
        daily = _make_daily(15)
        result = self._build(daily)
        self.assertIsNone(result[0]["ret_20d_pct"])

    def test_ret_5d_pct_available_at_6_bars(self):
        """6 本以上あれば ret_5d_pct は計算される。"""
        daily = _make_daily(6)
        result = self._build(daily)
        self.assertIsNotNone(result[0]["ret_5d_pct"])

    def test_ret_20d_pct_available_at_21_bars(self):
        """21 本以上あれば ret_20d_pct は計算される。"""
        daily = _make_daily(21)
        result = self._build(daily)
        self.assertIsNotNone(result[0]["ret_20d_pct"])

    # ─── 既存フィールドへのリグレッション ────────────────────────────────────

    def test_existing_fields_not_broken(self):
        """既存の volume_ratio / atr_pct / current_price は引き続き存在する。"""
        daily = _make_daily(22)
        result = self._build(daily)
        for key in ("volume_ratio", "atr_pct", "current_price", "price_change_pct"):
            self.assertIn(key, result[0], f"existing field '{key}' is missing")

    def test_exception_path_includes_momentum_fields(self):
        """API 失敗時のフォールバックにも ret_5d_pct / ret_20d_pct が含まれる。"""
        from data.us_market import build_us_universe
        with patch("data.us_market.get_daily_bars_us", side_effect=Exception("network error")), \
             patch("data.us_market.get_quote_us", side_effect=Exception("network error")):
            result = build_us_universe([{"symbol": "ERR", "name": "fail", "market": "US", "sector": "test"}])
        self.assertIn("ret_5d_pct",  result[0])
        self.assertIn("ret_20d_pct", result[0])


class TestMomentSwingPromptIncludesMomentumFields(unittest.TestCase):
    """screen_value プロンプトに ret_5d_pct / ret_20d_pct への言及があることを確認。"""

    def setUp(self):
        from agents.moment_swing import MomentSwing_US
        from agents.base import MarketContext
        from unittest.mock import patch, MagicMock
        from datetime import date

        self.agent = MomentSwing_US.__new__(MomentSwing_US)
        self.agent.logger = MagicMock()
        self.ctx = MarketContext(
            date=date.today().isoformat(),
            sector_scores={"AI半導体": 0.9},
            macro_notes="test",
            rotation_signal="維持",
            risk_level="low",
        )

    def test_prompt_mentions_ret_5d_pct(self):
        captured = {}

        def fake_llm(prompt):
            captured["prompt"] = prompt
            return None

        self.agent._ask_llm_json = fake_llm
        self.agent.screen_value(
            universe=[{"symbol": "NVDA", "name": "NVIDIA", "ret_5d_pct": 4.5, "ret_20d_pct": 8.2}],
            ctx=self.ctx,
            existing_symbols=[],
            max_position=3000.0,
            cash=30000.0,
        )
        self.assertIn("ret_5d_pct", captured.get("prompt", ""),
                      "prompt should mention ret_5d_pct field name")

    def test_prompt_mentions_ret_20d_pct(self):
        captured = {}

        def fake_llm(prompt):
            captured["prompt"] = prompt
            return None

        self.agent._ask_llm_json = fake_llm
        self.agent.screen_value(
            universe=[{"symbol": "NVDA", "name": "NVIDIA", "ret_5d_pct": 4.5, "ret_20d_pct": 8.2}],
            ctx=self.ctx,
            existing_symbols=[],
            max_position=3000.0,
            cash=30000.0,
        )
        self.assertIn("ret_20d_pct", captured.get("prompt", ""),
                      "prompt should mention ret_20d_pct field name")


if __name__ == "__main__":
    unittest.main()
