"""
tests/test_moment_swing_structure.py
MomentSwingBase / MomentSwing_US / MomentSwing_JP の構造テスト。

統合第一段以降: screen_value は機械的Darvas判定（LLM呼び出しなし）のため、
  LLM モックを使ったテストではなく _load_bars / scan_entry_signal をモックする。
"""
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from agents.moment_swing import MomentSwingBase, MomentSwing_US, MomentSwing_JP
from agents.base import MarketContext, TradeProposal
from strategies.darvas import EntrySignal


def _make_ctx() -> MarketContext:
    return MarketContext(
        date="2026-06-18", sector_scores={}, macro_notes="",
        rotation_signal="", risk_level="low",
    )


def _make_dummy_df(n: int = 20) -> pd.DataFrame:
    """テスト用ダミー日足 DataFrame（n 本）。"""
    dates = pd.date_range("2026-01-01", periods=n, freq="B", tz="Asia/Tokyo")
    return pd.DataFrame({
        "ts_utc": dates,
        "open":  [100.0] * n,
        "high":  [105.0] * n,
        "low":   [95.0] * n,
        "close": [100.0] * n,
        "volume": [1000] * n,
    })


# ---------------------------------------------------------------------------
# 基本属性テスト
# ---------------------------------------------------------------------------

class TestMomentSwingAttributes(unittest.TestCase):

    def test_us_market(self):
        self.assertEqual(MomentSwing_US._market, "US")

    def test_us_name(self):
        self.assertEqual(MomentSwing_US.name, "MomentSwing_US")

    def test_us_is_subclass(self):
        self.assertTrue(issubclass(MomentSwing_US, MomentSwingBase))

    def test_jp_market(self):
        self.assertEqual(MomentSwing_JP._market, "JP")

    def test_jp_name(self):
        self.assertEqual(MomentSwing_JP.name, "MomentSwing_JP")

    def test_jp_is_subclass(self):
        self.assertTrue(issubclass(MomentSwing_JP, MomentSwingBase))

    def test_shared_methods(self):
        for method in ("screen_value", "check_exits", "revise_proposal"):
            self.assertTrue(hasattr(MomentSwingBase, method), f"MomentSwingBase missing {method}")

    def test_jp_and_us_share_same_base_screen_value(self):
        """JP と US が同じ screen_value 実装を使うこと（コピペ防止）。"""
        self.assertIs(MomentSwing_JP.screen_value, MomentSwingBase.screen_value)
        self.assertIs(MomentSwing_US.screen_value, MomentSwingBase.screen_value)

    def test_jp_and_us_share_same_check_exits(self):
        """JP と US が同じ check_exits 実装を使うこと。"""
        self.assertIs(MomentSwing_JP.check_exits, MomentSwingBase.check_exits)
        self.assertIs(MomentSwing_US.check_exits, MomentSwingBase.check_exits)


# ---------------------------------------------------------------------------
# strategy / market フィールドテスト
# ---------------------------------------------------------------------------

class TestScreenValueOutput(unittest.TestCase):
    """screen_value が返す TradeProposal の構造を確認する。"""

    def _make_jp_agent(self):
        agent = MomentSwing_JP.__new__(MomentSwing_JP)
        agent.logger = MagicMock()
        return agent

    def test_strategy_is_momentum_swing(self):
        """TradeProposal.strategy は "momentum_swing" でなければならない。"""
        agent = self._make_jp_agent()
        dummy_df = _make_dummy_df()
        signal = EntrySignal(has_signal=True, box_top=105.0, box_bottom=90.0)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984", "name": "ソフトバンクG"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].strategy, "momentum_swing")

    def test_proposal_market_is_jp(self):
        """MomentSwing_JP が返す TradeProposal.market == 'JP'。"""
        agent = self._make_jp_agent()
        dummy_df = _make_dummy_df()
        signal = EntrySignal(has_signal=True, box_top=105.0, box_bottom=90.0)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984", "name": "ソフトバンクG"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        self.assertEqual(proposals[0].market, "JP")

    def test_proposal_side_is_buy(self):
        agent = self._make_jp_agent()
        dummy_df = _make_dummy_df()
        signal = EntrySignal(has_signal=True, box_top=105.0, box_bottom=90.0)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        self.assertEqual(proposals[0].side, "buy")

    def test_no_signal_returns_empty(self):
        """シグナルなしのとき提案リストは空。"""
        agent = self._make_jp_agent()
        dummy_df = _make_dummy_df()
        no_signal = EntrySignal(has_signal=False, box_top=0.0, box_bottom=0.0)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=no_signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        self.assertEqual(proposals, [])

    def test_existing_symbol_skipped(self):
        """existing_symbols に含まれる銘柄はスキップ。"""
        agent = self._make_jp_agent()
        proposals = agent.screen_value(
            universe=[{"symbol": "9984"}],
            ctx=_make_ctx(),
            existing_symbols=["9984"],
            max_position=500_000,
            cash=500_000,
        )
        self.assertEqual(proposals, [])


# ---------------------------------------------------------------------------
# Darvas 固有フィールドテスト
# ---------------------------------------------------------------------------

class TestDarvasProposalFields(unittest.TestCase):
    """screen_value が返す提案が Darvas 判定に基づいていることを確認。"""

    def _make_jp_agent(self):
        agent = MomentSwing_JP.__new__(MomentSwing_JP)
        agent.logger = MagicMock()
        return agent

    def test_stop_loss_equals_darvas_floor(self):
        """proposal.stop_loss == Darvas 箱床（絶対価格）。"""
        agent = self._make_jp_agent()
        dummy_df = _make_dummy_df()
        floor = 90.0
        signal = EntrySignal(has_signal=True, box_top=105.0, box_bottom=floor)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        self.assertAlmostEqual(proposals[0].stop_loss, floor)

    def test_take_profit_is_none(self):
        """Darvas 戦略に固定 TP は存在しない。take_profit == None。"""
        agent = self._make_jp_agent()
        dummy_df = _make_dummy_df()
        signal = EntrySignal(has_signal=True, box_top=105.0, box_bottom=90.0)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        self.assertIsNone(proposals[0].take_profit)

    def test_extra_has_darvas_floor(self):
        """extra に darvas_floor が含まれる。"""
        agent = self._make_jp_agent()
        dummy_df = _make_dummy_df()
        floor = 90.0
        signal = EntrySignal(has_signal=True, box_top=105.0, box_bottom=floor)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        self.assertIn("darvas_floor", proposals[0].extra)
        self.assertAlmostEqual(proposals[0].extra["darvas_floor"], floor)

    def test_no_llm_call_in_screen_value(self):
        """screen_value は LLM を呼ばない（_ask_llm_json が呼ばれないこと）。"""
        agent = self._make_jp_agent()
        agent._ask_llm_json = MagicMock()
        dummy_df = _make_dummy_df()
        signal = EntrySignal(has_signal=True, box_top=105.0, box_bottom=90.0)

        with patch.object(agent, "_load_bars", return_value=dummy_df), \
             patch("agents.moment_swing.scan_entry_signal", return_value=signal), \
             patch("agents.moment_swing.build_weekly_filter", return_value={}):
            agent.screen_value(
                universe=[{"symbol": "9984"}],
                ctx=_make_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )

        agent._ask_llm_json.assert_not_called()


# ---------------------------------------------------------------------------
# check_exits テスト
# ---------------------------------------------------------------------------

class TestCheckExits(unittest.TestCase):
    """check_exits が Darvas 床割れ銘柄の売り提案を返すことを確認。"""

    def _make_jp_agent(self):
        agent = MomentSwing_JP.__new__(MomentSwing_JP)
        agent.logger = MagicMock()
        return agent

    def test_floor_breach_returns_sell(self):
        """終値が Darvas 床を下回るとき sell 提案を返す。"""
        agent = self._make_jp_agent()
        # 最終バーの終値 = 85（床 90 を下回る）
        df = _make_dummy_df()
        df.loc[df.index[-1], "close"] = 85.0

        entry_ts = pd.Timestamp("2026-01-01", tz="Asia/Tokyo")
        trailing_floor = 90.0

        with patch.object(agent, "_load_bars", return_value=df), \
             patch("agents.moment_swing.compute_trailing_stop", return_value=trailing_floor):
            proposals = agent.check_exits(positions=[{
                "symbol":       "9984",
                "qty":          100,
                "entry_date":   entry_ts,
                "darvas_floor": trailing_floor,
            }])

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].side, "sell")
        self.assertEqual(proposals[0].symbol, "9984")

    def test_no_breach_returns_empty(self):
        """終値が Darvas 床を上回るとき空リストを返す。"""
        agent = self._make_jp_agent()
        # 最終バーの終値 = 100（床 90 を上回る）
        df = _make_dummy_df()
        trailing_floor = 90.0

        with patch.object(agent, "_load_bars", return_value=df), \
             patch("agents.moment_swing.compute_trailing_stop", return_value=trailing_floor):
            proposals = agent.check_exits(positions=[{
                "symbol":       "9984",
                "qty":          100,
                "entry_date":   pd.Timestamp("2026-01-01", tz="Asia/Tokyo"),
                "darvas_floor": trailing_floor,
            }])

        self.assertEqual(proposals, [])

    def test_sell_proposal_market_is_jp(self):
        """sell 提案の market は "JP"。"""
        agent = self._make_jp_agent()
        df = _make_dummy_df()
        df.loc[df.index[-1], "close"] = 80.0

        with patch.object(agent, "_load_bars", return_value=df), \
             patch("agents.moment_swing.compute_trailing_stop", return_value=95.0):
            proposals = agent.check_exits(positions=[{
                "symbol":       "9984",
                "qty":          100,
                "entry_date":   pd.Timestamp("2026-01-01", tz="Asia/Tokyo"),
                "darvas_floor": 95.0,
            }])

        self.assertEqual(proposals[0].market, "JP")


# ---------------------------------------------------------------------------
# revise_proposal テスト
# ---------------------------------------------------------------------------

class TestReviseProposal(unittest.TestCase):

    def _make_jp_agent(self):
        agent = MomentSwing_JP.__new__(MomentSwing_JP)
        agent.logger = MagicMock()
        return agent

    def _make_proposal(self) -> TradeProposal:
        return TradeProposal(
            agent="MomentSwing_JP",
            symbol="9984",
            market="JP",
            side="buy",
            qty=100,
            price=0.0,
            strategy="momentum_swing",
            rationale="Darvasブレイク",
            stop_loss=90.0,
            take_profit=None,
            extra={"darvas_floor": 90.0},
        )

    def test_revise_returns_none_on_withdraw(self):
        """LLM が withdraw を返したとき None を返す。"""
        agent = self._make_jp_agent()
        proposal = self._make_proposal()

        with patch.object(agent, "_ask_llm_json", return_value={"action": "withdraw"}):
            result = agent.revise_proposal(
                proposal, ["momentum lost"], "withdraw", _make_ctx()
            )

        self.assertIsNone(result)

    def test_revise_updates_qty(self):
        """LLM が buy を返したとき qty が更新される。"""
        agent = self._make_jp_agent()
        proposal = self._make_proposal()

        with patch.object(agent, "_ask_llm_json",
                          return_value={"action": "buy", "qty": 50, "rationale": "reduced"}):
            result = agent.revise_proposal(
                proposal, ["size too large"], "reduce qty", _make_ctx()
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.qty, 50)


# ---------------------------------------------------------------------------
# system_prompt テスト
# ---------------------------------------------------------------------------

class TestSystemPrompts(unittest.TestCase):

    def test_us_no_value_keyword(self):
        self.assertNotIn("バリュー", MomentSwing_US.system_prompt)

    def test_us_has_darvas_keyword(self):
        self.assertIn("Darvas", MomentSwing_US.system_prompt)

    def test_jp_no_value_keyword(self):
        self.assertNotIn("バリュー", MomentSwing_JP.system_prompt)

    def test_jp_has_darvas_keyword(self):
        self.assertIn("Darvas", MomentSwing_JP.system_prompt)


# ---------------------------------------------------------------------------
# CIO 配分テスト（既存）
# ---------------------------------------------------------------------------

class TestCIOAllocCoversSwing(unittest.TestCase):

    def test_allocate_budgets_covers_moment_swing_jp(self):
        from agents.cio import CIOAgent

        cio = CIOAgent.__new__(CIOAgent)
        cio.logger = MagicMock()
        ctx = _make_ctx()
        ctx.sector_scores = {"tech": 0.8}

        with patch.object(cio, "logger"):
            cio.logger = MagicMock()
            allocs = cio.allocate_budgets(
                ctx, total_cash_jpy=1_000_000, cash_usd=0, usd_jpy_rate=155.0
            )

        self.assertIn("MomentSwing_JP", allocs)
        self.assertGreater(allocs["MomentSwing_JP"].budget_jpy, 0)


if __name__ == "__main__":
    unittest.main()
