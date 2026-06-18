"""
tests/test_moment_swing_structure.py
MomentSwingBase / MomentSwing_US / MomentSwing_JP の構造テスト＋モメンタムロジックテスト（LLM呼び出しなし）。
"""
import unittest
from agents.moment_swing import MomentSwingBase, MomentSwing_US, MomentSwing_JP


class TestMomentSwingAttributes(unittest.TestCase):
    def test_us_market(self):
        self.assertEqual(MomentSwing_US._market, "US")

    def test_us_name(self):
        self.assertEqual(MomentSwing_US.name, "MomentSwing_US")

    def test_us_is_subclass(self):
        self.assertTrue(issubclass(MomentSwing_US, MomentSwingBase))

    def test_shared_methods(self):
        for method in ("screen_value", "revise_proposal"):
            self.assertTrue(hasattr(MomentSwing_US, method), f"MomentSwing_US missing {method}")

    def test_proposal_market_field(self):
        """TradeProposalのmarket フィールドが _market を使うことを確認（単位テスト）。"""
        from unittest.mock import MagicMock, patch
        from agents.base import MarketContext
        agent = MomentSwing_US.__new__(MomentSwing_US)
        agent.logger = MagicMock()

        dummy_data = [
            {
                "symbol": "NVDA", "name": "NVIDIA", "qty": 5, "price": 0,
                "rationale": "test", "stop_loss_pct": 0.08, "target_return_pct": 0.15,
            }
        ]
        ctx = MarketContext(
            date="2026-06-18", sector_scores={}, macro_notes="",
            rotation_signal="", risk_level="low",
        )
        with patch.object(agent, "_ask_llm_json", return_value=dummy_data):
            proposals = agent.screen_value(
                universe=[{"symbol": "NVDA", "name": "NVIDIA"}],
                ctx=ctx,
                existing_symbols=[],
                max_position=3000,
                cash=5000,
            )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].market, "US")
        self.assertEqual(proposals[0].agent, "MomentSwing_US")


class TestMomentumLogic(unittest.TestCase):
    """Step3: バリュー→モメンタム化のロジック検証（LLM呼び出しなし）。"""

    def _make_agent(self):
        from unittest.mock import MagicMock
        agent = MomentSwing_US.__new__(MomentSwing_US)
        agent.logger = MagicMock()
        return agent

    def _base_ctx(self):
        from agents.base import MarketContext
        return MarketContext(
            date="2026-06-18", sector_scores={}, macro_notes="",
            rotation_signal="", risk_level="low",
        )

    def test_strategy_is_momentum_swing(self):
        """TradeProposalのstrategyフィールドは"momentum_swing"でなければならない（"value_hold"は不可）。"""
        from unittest.mock import patch
        agent = self._make_agent()
        dummy = [{"symbol": "NVDA", "name": "NVIDIA", "qty": 5}]
        with patch.object(agent, "_ask_llm_json", return_value=dummy):
            proposals = agent.screen_value(
                universe=[{"symbol": "NVDA"}], ctx=self._base_ctx(),
                existing_symbols=[], max_position=3000, cash=5000,
            )
        self.assertEqual(proposals[0].strategy, "momentum_swing")

    def test_stop_loss_default_is_006(self):
        """LLMがstop_loss_pctを返さない場合、フォールバックは0.06（モメンタム向き浅めSL）。"""
        from unittest.mock import patch
        agent = self._make_agent()
        dummy = [{"symbol": "AAPL", "name": "Apple", "qty": 3}]  # SL/TPキー省略
        with patch.object(agent, "_ask_llm_json", return_value=dummy):
            proposals = agent.screen_value(
                universe=[{"symbol": "AAPL"}], ctx=self._base_ctx(),
                existing_symbols=[], max_position=3000, cash=5000,
            )
        self.assertAlmostEqual(proposals[0].extra["stop_loss_pct"], 0.06)

    def test_target_return_default_is_012(self):
        """LLMがtarget_return_pctを返さない場合、フォールバックは0.12（R:R≥1.5を維持）。"""
        from unittest.mock import patch
        agent = self._make_agent()
        dummy = [{"symbol": "MSFT", "name": "Microsoft", "qty": 4}]
        with patch.object(agent, "_ask_llm_json", return_value=dummy):
            proposals = agent.screen_value(
                universe=[{"symbol": "MSFT"}], ctx=self._base_ctx(),
                existing_symbols=[], max_position=3000, cash=5000,
            )
        self.assertAlmostEqual(proposals[0].extra["target_return_pct"], 0.12)

    def test_default_rr_ratio_gte_15(self):
        """デフォルトSL/TPフォールバックのR:Rが1.5以上（0.12/0.06=2.0）。"""
        agent = self._make_agent()
        default_sl = 0.06
        default_tp = 0.12
        rr = default_tp / default_sl
        self.assertGreaterEqual(rr, 1.5)

    def test_system_prompt_no_value_keyword(self):
        """system_promptに"バリュー"が含まれていないこと（バリュー戦略の痕跡を消したか）。"""
        self.assertNotIn("バリュー", MomentSwing_US.system_prompt)

    def test_system_prompt_has_momentum_keyword(self):
        """system_promptに"モメンタム"が含まれていること。"""
        self.assertIn("モメンタム", MomentSwing_US.system_prompt)

    def test_revise_returns_none_on_withdraw(self):
        """LLMがwithdrawを返したとき、revise_proposalはNoneを返す。"""
        from unittest.mock import patch, MagicMock
        from agents.base import TradeProposal
        agent = self._make_agent()
        proposal = TradeProposal(
            agent="MomentSwing_US", symbol="NVDA", market="US",
            side="buy", qty=5, price=400.0, strategy="momentum_swing",
            rationale="test", stop_loss=376.0, take_profit=448.0,
            extra={"stop_loss_pct": 0.06, "target_return_pct": 0.12},
        )
        with patch.object(agent, "_ask_llm_json", return_value={"action": "withdraw"}):
            result = agent.revise_proposal(proposal, ["momentum lost"], "withdraw", self._base_ctx())
        self.assertIsNone(result)


class TestMomentSwingJP(unittest.TestCase):
    """PR②-b: MomentSwing_JP の構造テスト。"""

    def _make_jp(self):
        from unittest.mock import MagicMock
        agent = MomentSwing_JP.__new__(MomentSwing_JP)
        agent.logger = MagicMock()
        return agent

    def _base_ctx(self):
        from agents.base import MarketContext
        return MarketContext(
            date="2026-06-18", sector_scores={}, macro_notes="",
            rotation_signal="", risk_level="low",
        )

    def test_jp_market(self):
        self.assertEqual(MomentSwing_JP._market, "JP")

    def test_jp_name(self):
        self.assertEqual(MomentSwing_JP.name, "MomentSwing_JP")

    def test_jp_is_subclass_of_base(self):
        self.assertTrue(issubclass(MomentSwing_JP, MomentSwingBase))

    def test_jp_currency(self):
        self.assertEqual(MomentSwing_JP._currency, "JPY")
        self.assertEqual(MomentSwing_JP._currency_symbol, "¥")

    def test_jp_min_unit(self):
        self.assertEqual(MomentSwing_JP._min_unit, 100)

    def test_jp_system_prompt_has_momentum(self):
        self.assertIn("モメンタム", MomentSwing_JP.system_prompt)

    def test_jp_system_prompt_no_value(self):
        self.assertNotIn("バリュー", MomentSwing_JP.system_prompt)

    def test_jp_position_limit_text_mentions_jpy(self):
        agent = self._make_jp()
        text = agent._position_limit_text()
        self.assertIn("¥", text)
        self.assertIn("100株", text)

    def test_jp_shared_methods(self):
        """screen_value / revise_proposal は基底から継承（JP側に重複実装がない）。"""
        for method in ("screen_value", "revise_proposal"):
            self.assertIn(method, MomentSwingBase.__dict__, f"基底に{method}がない")
            self.assertNotIn(method, MomentSwing_JP.__dict__, f"JP側に{method}が重複している")

    def test_jp_proposal_market_field(self):
        """screen_value が返す TradeProposal.market == 'JP'。"""
        from unittest.mock import patch
        agent = self._make_jp()
        dummy = [{"symbol": "9984", "name": "ソフトバンクG", "qty": 100}]
        with patch.object(agent, "_ask_llm_json", return_value=dummy):
            proposals = agent.screen_value(
                universe=[{"symbol": "9984", "name": "ソフトバンクG"}],
                ctx=self._base_ctx(),
                existing_symbols=[],
                max_position=500_000,
                cash=500_000,
            )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].market, "JP")
        self.assertEqual(proposals[0].strategy, "momentum_swing")

    def test_jp_prompt_contains_jpy_currency(self):
        """スクリーニングプロンプトに通貨記号 ¥ と JPY が含まれる。"""
        from unittest.mock import patch
        agent = self._make_jp()
        captured_prompt = []

        def capture(prompt):
            captured_prompt.append(prompt)
            return []

        with patch.object(agent, "_ask_llm_json", side_effect=capture):
            agent.screen_value(
                universe=[{"symbol": "9984"}], ctx=self._base_ctx(),
                existing_symbols=[], max_position=500_000, cash=500_000,
            )
        self.assertTrue(captured_prompt, "プロンプトが呼ばれなかった")
        self.assertIn("¥", captured_prompt[0])
        self.assertIn("JPY", captured_prompt[0])
        self.assertNotIn("$", captured_prompt[0])

    def test_us_prompt_contains_usd_currency(self):
        """MomentSwing_US のプロンプトには $ と USD が含まれ ¥ は含まれない。"""
        from unittest.mock import patch, MagicMock
        agent = MomentSwing_US.__new__(MomentSwing_US)
        agent.logger = MagicMock()
        captured_prompt = []

        def capture(prompt):
            captured_prompt.append(prompt)
            return []

        with patch.object(agent, "_ask_llm_json", side_effect=capture):
            agent.screen_value(
                universe=[{"symbol": "NVDA"}], ctx=self._base_ctx(),
                existing_symbols=[], max_position=3000, cash=5000,
            )
        self.assertIn("$", captured_prompt[0])
        self.assertIn("USD", captured_prompt[0])
        self.assertNotIn("¥", captured_prompt[0])

    def test_jp_and_us_share_same_base_screen_value(self):
        """JP と US が同じ screen_value 実装を使っていること（基底の参照が同一）。"""
        self.assertIs(
            MomentSwing_JP.screen_value,
            MomentSwingBase.screen_value,
            "MomentSwing_JP.screen_value が基底と異なる（コピペの可能性）",
        )
        self.assertIs(
            MomentSwing_US.screen_value,
            MomentSwingBase.screen_value,
            "MomentSwing_US.screen_value が基底と異なる（コピペの可能性）",
        )

    def test_allocate_budgets_covers_moment_swing_jp(self):
        """allocate_budgets の jpy_pods に MomentSwing_JP が含まれていること。"""
        from unittest.mock import MagicMock, patch
        from agents.cio import CIOAgent
        from agents.base import MarketContext

        cio = CIOAgent.__new__(CIOAgent)
        cio.logger = MagicMock()
        ctx = MarketContext(
            date="2026-06-18", sector_scores={"tech": 0.8},
            macro_notes="", rotation_signal="", risk_level="low",
        )
        with patch.object(cio, "logger"):
            cio.logger = MagicMock()
            allocs = cio.allocate_budgets(
                ctx, total_cash_jpy=1_000_000, cash_usd=0, usd_jpy_rate=155.0
            )
        self.assertIn("MomentSwing_JP", allocs)
        self.assertGreater(allocs["MomentSwing_JP"].budget_jpy, 0)
