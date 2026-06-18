"""
tests/test_critics.py
CriticBase.refine_and_review() の動作テスト（LLM呼び出しなし）。

テストパス一覧:
  1. 初回承認       → (proposal, verdict),  revise_proposal 呼ばれない
  2. 否決→修正→承認 → (revised_proposal, verdict)
  3. 上限到達全否決  → (None, last_verdict),  エラーなし
  4. fixable=False  → (None, verdict), revise_proposal 呼ばれない（即打ち切り）
  5. 提案者が修正断念 → (None, verdict)
  6. 提案リスト空    → ループがエラーなく完了
  ─────── 各クリティーク即時否決テスト ────────────────────────────
  7. ScalpDay_JP_Critic: stop_loss=None → 即否決
  8. ScalpDay_US_Critic: stop_loss なし → 即否決
  9. ScalpDay_US_Critic: 市場時間外     → 即否決(fixable=False)
 10. ScalpDay_US_Critic: FX underweight+buy → 即否決(fixable=False)
 11. MomentSwing_US_Critic: stop_loss_pct なし → 即否決(fixable=True)
 12. MomentSwing_US_Critic: FX underweight+buy → 即否決(fixable=False)
 13. MomentSwing_JP_Critic: stop_loss_pct なし → 即否決(fixable=True)
 14. FXRebalance_Critic: 変更幅>20% → 即否決(fixable=True)
  ─────── 構造テスト ──────────────────────────────────────────────
 15. 全クリティークが CriticBase のサブクラス
 16. 全クリティークが model="claude-opus-4-8"
"""
import unittest
from unittest.mock import MagicMock, patch, call
from agents.base import TradeProposal, CriticVerdict, MarketContext
from agents.critics import (
    CriticBase,
    ScalpDay_JP_Critic,
    ScalpDay_US_Critic,
    MomentSwing_US_Critic,
    MomentSwing_JP_Critic,
    FXRebalance_Critic,
    IntelCritic,
)

# ──────────────────────────────────────────────────────────────────
# テスト用ヘルパー
# ──────────────────────────────────────────────────────────────────

def _make_proposal(
    symbol="NVDA",
    market="US",
    side="buy",
    qty=5,
    price=400.0,
    stop_loss=380.0,
    take_profit=460.0,
    strategy="scalpday",
    extra=None,
) -> TradeProposal:
    return TradeProposal(
        agent="TestAgent", symbol=symbol, market=market,
        side=side, qty=qty, price=price,
        stop_loss=stop_loss, take_profit=take_profit,
        strategy=strategy, rationale="test",
        extra=extra or {},
    )


def _make_ctx(risk_level="low") -> MarketContext:
    return MarketContext(
        date="2026-06-19", sector_scores={}, macro_notes="",
        rotation_signal="", risk_level=risk_level,
    )


def _make_approve_verdict(**kw) -> CriticVerdict:
    return CriticVerdict(approved=True, score=0.9, issues=[], suggestion="", **kw)


def _make_reject_verdict(fixable=True, **kw) -> CriticVerdict:
    return CriticVerdict(
        approved=False, score=0.3, fixable=fixable,
        issues=["テスト否決"], suggestion="修正してください", **kw
    )


class _ConcreteCritic(CriticBase):
    """テスト用の具象クリティーク（review() を外からモック可能にする）。"""
    name = "ConcreteCritic"
    system_prompt = "テスト用"

    def review(self, proposal, ctx, **kw):
        raise NotImplementedError("mock してから使ってください")


def _make_critic() -> _ConcreteCritic:
    c = _ConcreteCritic.__new__(_ConcreteCritic)
    c.logger = MagicMock()
    return c


def _make_proposer(revised=None):
    """revise_proposal をモックした proposer。"""
    proposer = MagicMock()
    proposer.revise_proposal.return_value = revised
    return proposer


# ──────────────────────────────────────────────────────────────────
# refine_and_review ループの動作テスト
# ──────────────────────────────────────────────────────────────────

class TestRefineAndReviewLoop(unittest.TestCase):

    def test_approve_first_round_returns_proposal_and_verdict(self):
        """1. 初回承認: (proposal, verdict) を返し revise_proposal は呼ばれない。"""
        critic   = _make_critic()
        proposal = _make_proposal()
        ctx      = _make_ctx()
        verdict  = _make_approve_verdict()
        proposer = _make_proposer()

        critic.review = MagicMock(return_value=verdict)

        result_p, result_v = critic.refine_and_review(proposer, proposal, ctx)

        self.assertIs(result_p, proposal)
        self.assertIs(result_v, verdict)
        self.assertTrue(result_v.approved)
        proposer.revise_proposal.assert_not_called()
        self.assertEqual(critic.review.call_count, 1)

    def test_reject_then_approve_returns_revised_proposal(self):
        """2. 否決→修正→承認: (revised_proposal, approve_verdict) を返す。"""
        critic   = _make_critic()
        proposal = _make_proposal(symbol="AAPL")
        revised  = _make_proposal(symbol="AAPL", stop_loss=385.0)
        ctx      = _make_ctx()
        verdicts = [_make_reject_verdict(), _make_approve_verdict()]
        proposer = _make_proposer(revised=revised)

        critic.review = MagicMock(side_effect=verdicts)

        result_p, result_v = critic.refine_and_review(proposer, proposal, ctx)

        self.assertIs(result_p, revised)
        self.assertTrue(result_v.approved)
        self.assertEqual(critic.review.call_count, 2)
        proposer.revise_proposal.assert_called_once()

    def test_all_rounds_rejected_returns_none_no_error(self):
        """3. 上限到達全否決: (None, last_verdict), エラーなし。"""
        critic   = _make_critic()
        proposal = _make_proposal()
        ctx      = _make_ctx()
        # max_rounds=2 → review 3回, revise 2回
        reject   = _make_reject_verdict()
        proposer = _make_proposer(
            revised=_make_proposal(symbol="NVDA", stop_loss=390.0)
        )

        critic.review = MagicMock(return_value=reject)

        result_p, result_v = critic.refine_and_review(proposer, proposal, ctx, max_rounds=2)

        self.assertIsNone(result_p)
        self.assertFalse(result_v.approved)
        # 3回レビュー(0, 1, 2)
        self.assertEqual(critic.review.call_count, 3)
        # 2回修正(ラウンド0, 1)
        self.assertEqual(proposer.revise_proposal.call_count, 2)

    def test_fixable_false_returns_none_without_revise(self):
        """4. fixable=False: (None, verdict), revise_proposal は呼ばれない。"""
        critic   = _make_critic()
        proposal = _make_proposal()
        ctx      = _make_ctx()
        verdict  = _make_reject_verdict(fixable=False)
        proposer = _make_proposer()

        critic.review = MagicMock(return_value=verdict)

        result_p, result_v = critic.refine_and_review(proposer, proposal, ctx)

        self.assertIsNone(result_p)
        self.assertFalse(result_v.fixable)
        proposer.revise_proposal.assert_not_called()
        # 1回レビューで即打ち切り
        self.assertEqual(critic.review.call_count, 1)

    def test_proposer_abandons_revision_returns_none(self):
        """5. 提案者が修正断念(revise_proposal→None): (None, verdict)。"""
        critic   = _make_critic()
        proposal = _make_proposal()
        ctx      = _make_ctx()
        verdict  = _make_reject_verdict()
        proposer = _make_proposer(revised=None)  # 断念

        critic.review = MagicMock(return_value=verdict)

        result_p, result_v = critic.refine_and_review(proposer, proposal, ctx)

        self.assertIsNone(result_p)
        self.assertEqual(critic.review.call_count, 1)  # 修正前の1回のみ
        proposer.revise_proposal.assert_called_once()

    def test_empty_proposals_loop_no_error(self):
        """6. 提案リスト空でもループはエラーなく完了する。"""
        critic = _make_critic()
        ctx    = _make_ctx()

        results = []
        for proposal in []:
            result_p, result_v = critic.refine_and_review(MagicMock(), proposal, ctx)
            results.append(result_p)

        self.assertEqual(results, [])  # 空でも例外なし

    def test_max_rounds_zero_single_review(self):
        """max_rounds=0: レビューは1回のみ、修正なし。"""
        critic   = _make_critic()
        proposal = _make_proposal()
        ctx      = _make_ctx()
        verdict  = _make_reject_verdict()
        proposer = _make_proposer()

        critic.review = MagicMock(return_value=verdict)

        result_p, _ = critic.refine_and_review(proposer, proposal, ctx, max_rounds=0)

        self.assertIsNone(result_p)
        self.assertEqual(critic.review.call_count, 1)
        proposer.revise_proposal.assert_not_called()

    def test_second_round_approved_returns_second_revised(self):
        """修正2回目で承認: 正しい revised_proposal を返す。"""
        critic   = _make_critic()
        proposal = _make_proposal()
        revised1 = _make_proposal(symbol="NVDA", stop_loss=382.0)
        revised2 = _make_proposal(symbol="NVDA", stop_loss=384.0)
        ctx      = _make_ctx()

        # reject × 2, approve × 1
        critic.review = MagicMock(side_effect=[
            _make_reject_verdict(),
            _make_reject_verdict(),
            _make_approve_verdict(),
        ])
        proposer = MagicMock()
        proposer.revise_proposal.side_effect = [revised1, revised2]

        result_p, result_v = critic.refine_and_review(proposer, proposal, ctx, max_rounds=2)

        self.assertIs(result_p, revised2)
        self.assertTrue(result_v.approved)
        self.assertEqual(critic.review.call_count, 3)


# ──────────────────────────────────────────────────────────────────
# 各クリティークの即時否決テスト（pre-check ロジック）
# ──────────────────────────────────────────────────────────────────

class TestScalpDayJPCriticPrecheck(unittest.TestCase):
    def _make(self):
        c = ScalpDay_JP_Critic.__new__(ScalpDay_JP_Critic)
        c.logger = MagicMock()
        return c

    def test_stop_loss_none_immediate_reject(self):
        """7. stop_loss=None は即否決（LLM呼び出しなし）。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=None)
        ctx      = _make_ctx()

        with patch.object(critic, "_ask_llm_json") as mock_llm:
            verdict = critic.review(proposal, ctx, wallet={})

        self.assertFalse(verdict.approved)
        self.assertTrue(verdict.fixable)
        mock_llm.assert_not_called()

    def test_valid_proposal_calls_llm(self):
        """stop_loss あり → LLM 呼び出しへ進む。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=380.0)
        ctx      = _make_ctx()

        llm_data = {"approved": True, "score": 0.85, "issues": [], "suggestion": ""}
        with patch.object(critic, "_ask_llm_json", return_value=llm_data):
            verdict = critic.review(proposal, ctx, wallet={"MarginAccountWallet": 500_000})

        self.assertTrue(verdict.approved)


class TestScalpDayUSCriticPrecheck(unittest.TestCase):
    def _make(self):
        c = ScalpDay_US_Critic.__new__(ScalpDay_US_Critic)
        c.logger = MagicMock()
        return c

    def test_no_stop_loss_immediate_reject(self):
        """8. stop_loss=None かつ stop_loss_pct=0 → 即否決。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=None, extra={})
        ctx      = _make_ctx()

        with patch.object(critic, "_ask_llm_json") as mock_llm:
            verdict = critic.review(proposal, ctx)

        self.assertFalse(verdict.approved)
        self.assertTrue(verdict.fixable)
        mock_llm.assert_not_called()

    def test_out_of_market_hours_fixable_false(self):
        """9. 市場時間外 → fixable=False で即否決。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=380.0)
        ctx      = _make_ctx()

        with patch("agents.critics._is_us_market_hours", return_value=False):
            with patch.object(critic, "_ask_llm_json") as mock_llm:
                verdict = critic.review(proposal, ctx)

        self.assertFalse(verdict.approved)
        self.assertFalse(verdict.fixable)
        mock_llm.assert_not_called()

    def test_fx_underweight_buy_fixable_false(self):
        """10. FX underweight + buy → fixable=False で即否決。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=380.0, side="buy")
        ctx      = _make_ctx()
        fx       = {"us_weight_bias": "underweight"}

        with patch("agents.critics._is_us_market_hours", return_value=True):
            with patch.object(critic, "_ask_llm_json") as mock_llm:
                verdict = critic.review(proposal, ctx, fx_signal=fx)

        self.assertFalse(verdict.approved)
        self.assertFalse(verdict.fixable)
        mock_llm.assert_not_called()

    def test_valid_proposal_within_hours_calls_llm(self):
        """市場時間内・SL設定あり → LLM 呼び出しへ。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=380.0)
        ctx      = _make_ctx()

        llm_data = {"approved": True, "score": 0.85, "issues": [], "suggestion": ""}
        with patch("agents.critics._is_us_market_hours", return_value=True):
            with patch.object(critic, "_ask_llm_json", return_value=llm_data):
                verdict = critic.review(
                    proposal, ctx,
                    account={"equity": 100_000},
                    fx_signal={"us_weight_bias": "neutral"},
                )

        self.assertTrue(verdict.approved)


class TestMomentSwingUSCriticPrecheck(unittest.TestCase):
    def _make(self):
        c = MomentSwing_US_Critic.__new__(MomentSwing_US_Critic)
        c.logger = MagicMock()
        return c

    def test_no_stop_loss_pct_immediate_reject_fixable(self):
        """11. stop_loss_pct=0 → 即否決(fixable=True)。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=None, extra={"stop_loss_pct": 0})
        ctx      = _make_ctx()

        with patch.object(critic, "_ask_llm_json") as mock_llm:
            verdict = critic.review(proposal, ctx)

        self.assertFalse(verdict.approved)
        self.assertTrue(verdict.fixable)
        mock_llm.assert_not_called()

    def test_fx_underweight_buy_fixable_false(self):
        """12. FX underweight + buy → fixable=False で即否決（市場時間外でも可）。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=None, extra={"stop_loss_pct": 0.06}, side="buy")
        ctx      = _make_ctx()
        fx       = {"us_weight_bias": "underweight"}

        with patch.object(critic, "_ask_llm_json") as mock_llm:
            verdict = critic.review(proposal, ctx, fx_signal=fx)

        self.assertFalse(verdict.approved)
        self.assertFalse(verdict.fixable)
        mock_llm.assert_not_called()

    def test_stop_loss_pct_accepted(self):
        """stop_loss=None でも stop_loss_pct>0 なら LLM まで進む。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=None, extra={"stop_loss_pct": 0.06})
        ctx      = _make_ctx()

        llm_data = {"approved": True, "score": 0.9, "issues": [], "suggestion": ""}
        with patch.object(critic, "_ask_llm_json", return_value=llm_data):
            verdict = critic.review(proposal, ctx, fx_signal={"us_weight_bias": "neutral"})

        self.assertTrue(verdict.approved)


class TestMomentSwingJPCriticPrecheck(unittest.TestCase):
    def _make(self):
        c = MomentSwing_JP_Critic.__new__(MomentSwing_JP_Critic)
        c.logger = MagicMock()
        return c

    def test_no_stop_loss_pct_immediate_reject_fixable(self):
        """13. stop_loss_pct=0 → 即否決(fixable=True)。"""
        critic   = self._make()
        proposal = _make_proposal(stop_loss=None, market="JP", extra={})
        ctx      = _make_ctx()

        with patch.object(critic, "_ask_llm_json") as mock_llm:
            verdict = critic.review(proposal, ctx)

        self.assertFalse(verdict.approved)
        self.assertTrue(verdict.fixable)
        mock_llm.assert_not_called()

    def test_stop_loss_pct_accepted(self):
        """stop_loss=None でも stop_loss_pct>0 なら LLM まで進む。"""
        critic   = self._make()
        proposal = _make_proposal(
            stop_loss=None, market="JP",
            extra={"stop_loss_pct": 0.06, "target_return_pct": 0.12},
        )
        ctx      = _make_ctx()

        llm_data = {"approved": True, "score": 0.88, "issues": [], "suggestion": ""}
        with patch.object(critic, "_ask_llm_json", return_value=llm_data):
            verdict = critic.review(proposal, ctx, wallet={"MarginAccountWallet": 1_000_000})

        self.assertTrue(verdict.approved)


class TestFXRebalanceCriticPrecheck(unittest.TestCase):
    def _make(self):
        c = FXRebalance_Critic.__new__(FXRebalance_Critic)
        c.logger = MagicMock()
        return c

    def test_large_change_immediate_reject_fixable(self):
        """14. 変更幅 >20% → 即否決(fixable=True, LLM不要)。"""
        critic = self._make()
        ctx    = _make_ctx()
        fx     = {"current_usd_ratio": 30, "target_usd_ratio": 55}  # 25%変動

        with patch.object(critic, "_ask_llm_json") as mock_llm:
            verdict = critic.review_signal(fx, ctx)

        self.assertFalse(verdict.approved)
        self.assertTrue(verdict.fixable)
        mock_llm.assert_not_called()

    def test_small_change_calls_llm(self):
        """変更幅 ≤20% → LLM まで進む。"""
        critic = self._make()
        ctx    = _make_ctx()
        fx     = {"current_usd_ratio": 40, "target_usd_ratio": 55}  # 15%変動

        llm_data = {"approved": True, "score": 0.8, "issues": [], "suggestion": ""}
        with patch.object(critic, "_ask_llm_json", return_value=llm_data):
            verdict = critic.review_signal(fx, ctx)

        self.assertTrue(verdict.approved)


# ──────────────────────────────────────────────────────────────────
# 構造テスト
# ──────────────────────────────────────────────────────────────────

class TestCriticStructure(unittest.TestCase):
    _all_critics = [
        ScalpDay_JP_Critic, ScalpDay_US_Critic,
        MomentSwing_US_Critic, MomentSwing_JP_Critic,
        FXRebalance_Critic, IntelCritic,
    ]

    def test_all_critics_are_subclass_of_criticbase(self):
        """15. 全クリティークが CriticBase のサブクラスであること。"""
        for cls in self._all_critics:
            with self.subTest(cls=cls.__name__):
                self.assertTrue(
                    issubclass(cls, CriticBase),
                    f"{cls.__name__} は CriticBase を継承していない",
                )

    def test_all_critics_use_opus(self):
        """16. 全クリティークが model='claude-opus-4-8'（発注最終判断者はケチらない）。"""
        for cls in self._all_critics:
            with self.subTest(cls=cls.__name__):
                self.assertEqual(
                    cls.model, "claude-opus-4-8",
                    f"{cls.__name__}.model が claude-opus-4-8 ではない",
                )

    def test_criticbase_default_max_revision_rounds_is_2(self):
        """CriticBase._max_revision_rounds のデフォルトは 2。"""
        self.assertEqual(CriticBase._max_revision_rounds, 2)

    def test_no_proposer_with_revise_proposal_returns_none(self):
        """revise_proposal を持たない proposer → 否決後に None を返す。"""
        critic   = _make_critic()
        proposal = _make_proposal()
        ctx      = _make_ctx()
        proposer = MagicMock(spec=[])  # revise_proposal を持たない

        critic.review = MagicMock(return_value=_make_reject_verdict())

        result_p, _ = critic.refine_and_review(proposer, proposal, ctx)
        self.assertIsNone(result_p)


# ──────────────────────────────────────────────────────────────────
# IntelCritic テスト
# ──────────────────────────────────────────────────────────────────

class TestIntelCritic(unittest.TestCase):
    def _make(self):
        c = IntelCritic.__new__(IntelCritic)
        c.logger = MagicMock()
        return c

    def test_empty_signals_returns_empty(self):
        """シグナルが空なら空リストを返す（LLM 呼び出しなし）。"""
        critic = self._make()
        ctx    = _make_ctx()

        with patch.object(critic, "_ask_llm_json") as mock_llm:
            result = critic.review_signals([], ctx)

        self.assertEqual(result, [])
        mock_llm.assert_not_called()

    def test_low_relevance_filtered_out(self):
        """relevance_score < 0.6 は除外される。"""
        critic = self._make()
        ctx    = _make_ctx()

        llm_response = [
            {"relevance_score": 0.3, "approved": True, "title": "low"},
            {"relevance_score": 0.8, "approved": True, "title": "high"},
        ]
        with patch.object(critic, "_ask_llm_json", return_value=llm_response):
            result = critic.review_signals([{"title": "test"}], ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "high")

    def test_not_approved_filtered_out(self):
        """approved=False のシグナルは除外される。"""
        critic = self._make()
        ctx    = _make_ctx()

        llm_response = [
            {"relevance_score": 0.9, "approved": False, "title": "PR記事"},
            {"relevance_score": 0.9, "approved": True,  "title": "有効シグナル"},
        ]
        with patch.object(critic, "_ask_llm_json", return_value=llm_response):
            result = critic.review_signals([{}, {}], ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "有効シグナル")

    def test_llm_failure_returns_original_signals(self):
        """LLM 失敗時は元シグナルをそのまま返す（情報損失防止）。"""
        critic   = self._make()
        ctx      = _make_ctx()
        original = [{"title": "original"}]

        with patch.object(critic, "_ask_llm_json", return_value=None):
            result = critic.review_signals(original, ctx)

        self.assertEqual(result, original)


if __name__ == "__main__":
    unittest.main()
