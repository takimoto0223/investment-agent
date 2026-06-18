"""
tests/test_market_clock.py
data/market_clock.py の市場開閉ガードのテスト。

確認事項:
  (a) 開場日の通常時間は True を返す
  (b) 週末/祝日 は False を返す
  (c) 時間外（開場前・昼休み・引け後）は False を返す
  (d) JP/US の暦が独立に機能する
"""
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from data.market_clock import (
    is_jp_open,
    is_us_open,
    _is_year_end_new_year,
)

_JST = ZoneInfo("Asia/Tokyo")
_ET  = ZoneInfo("America/New_York")


def _jp(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    """JST の datetime を生成するヘルパー。"""
    return datetime(y, m, d, h, mi, tzinfo=_JST)


# ──────────────────────────────────────────────
# JP: 開場時間 (a)
# ──────────────────────────────────────────────

class TestJPOpen(unittest.TestCase):
    """(a) 平日・通常立会時間は True。"""

    def _call(self, dt: datetime) -> bool:
        with patch("data.market_clock._is_jp_holiday", return_value=False):
            return is_jp_open(dt)

    def test_am_session(self):
        """前場 10:00 JST → True"""
        self.assertTrue(self._call(_jp(2026, 6, 15, 10, 0)))   # 月曜

    def test_am_session_boundary_open(self):
        """前場 9:00 丁度 → True（境界値）"""
        self.assertTrue(self._call(_jp(2026, 6, 15, 9, 0)))

    def test_am_session_boundary_close(self):
        """前場 11:30 → False（閉場境界）"""
        self.assertFalse(self._call(_jp(2026, 6, 15, 11, 30)))

    def test_pm_session(self):
        """後場 14:00 JST → True"""
        self.assertTrue(self._call(_jp(2026, 6, 15, 14, 0)))

    def test_pm_session_boundary_open(self):
        """後場 12:30 丁度 → True"""
        self.assertTrue(self._call(_jp(2026, 6, 15, 12, 30)))

    def test_pm_session_boundary_close(self):
        """後場 15:30 → False（閉場境界）"""
        self.assertFalse(self._call(_jp(2026, 6, 15, 15, 30)))


# ──────────────────────────────────────────────
# JP: 閉場パターン (b)(c)
# ──────────────────────────────────────────────

class TestJPClosed(unittest.TestCase):
    """(b)(c) 週末/祝日/時間外は False。"""

    def _call(self, dt: datetime, holiday: bool = False) -> bool:
        with patch("data.market_clock._is_jp_holiday", return_value=holiday):
            return is_jp_open(dt)

    def test_saturday(self):
        """土曜 10:00 → False"""
        self.assertFalse(self._call(_jp(2026, 6, 13, 10, 0)))   # 土曜

    def test_sunday(self):
        """日曜 10:00 → False"""
        self.assertFalse(self._call(_jp(2026, 6, 14, 10, 0)))   # 日曜

    def test_holiday(self):
        """平日・jpholiday=True → False"""
        self.assertFalse(self._call(_jp(2026, 7, 20, 10, 0), holiday=True))

    def test_year_end_dec31(self):
        """12/31（年末休場）→ False"""
        self.assertFalse(self._call(_jp(2025, 12, 31, 10, 0)))

    def test_new_year_jan1(self):
        """1/1（元日）→ False"""
        self.assertFalse(self._call(_jp(2026, 1, 1, 10, 0)))

    def test_new_year_jan3(self):
        """1/3（年始休場末日）→ False"""
        self.assertFalse(self._call(_jp(2026, 1, 3, 10, 0)))

    def test_before_open(self):
        """開場前 8:59 JST → False"""
        self.assertFalse(self._call(_jp(2026, 6, 15, 8, 59)))

    def test_lunch_break(self):
        """昼休み 12:00 JST → False"""
        self.assertFalse(self._call(_jp(2026, 6, 15, 12, 0)))

    def test_after_close(self):
        """引け後 15:30 JST → False"""
        self.assertFalse(self._call(_jp(2026, 6, 15, 15, 30)))

    def test_late_evening(self):
        """夜間 20:00 JST → False"""
        self.assertFalse(self._call(_jp(2026, 6, 15, 20, 0)))


# ──────────────────────────────────────────────
# JP: _is_year_end_new_year ユーティリティ単体
# ──────────────────────────────────────────────

class TestYearEndNewYear(unittest.TestCase):

    def test_dec31_is_true(self):
        from datetime import date
        self.assertTrue(_is_year_end_new_year(date(2025, 12, 31)))

    def test_jan1_is_true(self):
        from datetime import date
        self.assertTrue(_is_year_end_new_year(date(2026, 1, 1)))

    def test_jan3_is_true(self):
        from datetime import date
        self.assertTrue(_is_year_end_new_year(date(2026, 1, 3)))

    def test_jan4_is_false(self):
        from datetime import date
        self.assertFalse(_is_year_end_new_year(date(2026, 1, 4)))

    def test_dec30_is_false(self):
        from datetime import date
        self.assertFalse(_is_year_end_new_year(date(2025, 12, 30)))


# ──────────────────────────────────────────────
# US: Alpaca clock API 経由 (a)(b)(c)
# ──────────────────────────────────────────────

class TestUSOpen(unittest.TestCase):
    """(a) clock.is_open=True → True, (b)(c) False → False。"""

    def test_open_when_clock_true(self):
        """Alpaca が is_open=True を返す → True"""
        with patch("data.market_clock._fetch_alpaca_clock_is_open", return_value=True):
            self.assertTrue(is_us_open())

    def test_closed_when_clock_false(self):
        """Alpaca が is_open=False を返す（時間外/祝日）→ False"""
        with patch("data.market_clock._fetch_alpaca_clock_is_open", return_value=False):
            self.assertFalse(is_us_open())

    def test_safe_fallback_on_api_error(self):
        """Alpaca API 例外 → 安全側 False"""
        with patch(
            "data.market_clock._fetch_alpaca_clock_is_open",
            side_effect=Exception("接続失敗"),
        ):
            self.assertFalse(is_us_open())


# ──────────────────────────────────────────────
# 独立性: JP/US の暦は互いに影響しない (d)
# ──────────────────────────────────────────────

class TestMarketClockIndependence(unittest.TestCase):
    """(d) JP が閉場でも US の判定は独立、逆も同様。"""

    def test_us_open_when_jp_closed_holiday(self):
        """JP が祝日でも US clock=True なら US は開場。"""
        jp_time = _jp(2026, 7, 20, 10, 0)   # 海の日（祝日）

        with patch("data.market_clock._is_jp_holiday", return_value=True):
            jp_result = is_jp_open(jp_time)

        with patch("data.market_clock._fetch_alpaca_clock_is_open", return_value=True):
            us_result = is_us_open()

        self.assertFalse(jp_result)   # JP は閉場
        self.assertTrue(us_result)    # US は関係なく開場

    def test_jp_open_when_us_closed(self):
        """US が閉場（Alpaca=False）でも JP 通常時間なら JP は開場。"""
        jp_time = _jp(2026, 6, 15, 10, 0)   # 平日 10:00 JST

        with patch("data.market_clock._is_jp_holiday", return_value=False):
            jp_result = is_jp_open(jp_time)

        with patch("data.market_clock._fetch_alpaca_clock_is_open", return_value=False):
            us_result = is_us_open()

        self.assertTrue(jp_result)    # JP は開場
        self.assertFalse(us_result)   # US は閉場（互いに干渉しない）

    def test_jp_weekend_does_not_affect_us(self):
        """JP が週末でも US clock=True なら US は開場。"""
        jp_time = _jp(2026, 6, 13, 10, 0)   # 土曜

        with patch("data.market_clock._is_jp_holiday", return_value=False):
            jp_result = is_jp_open(jp_time)

        with patch("data.market_clock._fetch_alpaca_clock_is_open", return_value=True):
            us_result = is_us_open()

        self.assertFalse(jp_result)
        self.assertTrue(us_result)


# ──────────────────────────────────────────────
# セッションガード: main.py の早期 return を確認
# ──────────────────────────────────────────────

class TestSessionGuard(unittest.TestCase):
    """is_jp_open/is_us_open=False のとき各セッションが early return する。"""

    def test_scalpday_jp_session_skips_when_jp_closed(self):
        """run_scalpday_jp_session: JP 閉場 → 発注処理に到達しない。"""
        import main
        with patch("main.is_jp_open", return_value=False), \
             patch("main.KabuBroker") as mock_broker:
            main.run_scalpday_jp_session(paper=True)
            mock_broker.assert_not_called()

    def test_moment_swing_jp_session_skips_when_jp_closed(self):
        """run_moment_swing_jp_session: JP 閉場 → KabuBroker が生成されない。"""
        import main
        with patch("main.is_jp_open", return_value=False), \
             patch("main.KabuBroker") as mock_broker:
            main.run_moment_swing_jp_session(paper=True)
            mock_broker.assert_not_called()

    def test_moment_swing_us_session_skips_when_us_closed(self):
        """run_moment_swing_us_session: US 閉場 → 内部処理に到達しない。"""
        import main
        called = []
        with patch("main.is_us_open", return_value=False), \
             patch("main.CIOAgent", side_effect=lambda: called.append(1)):
            main.run_moment_swing_us_session()
            self.assertEqual(called, [])

    def test_scalpday_us_session_skips_when_us_closed(self):
        """run_scalpday_us_session: US 閉場 → 内部処理に到達しない。"""
        import main
        called = []
        with patch("main.is_us_open", return_value=False), \
             patch("main.CIOAgent", side_effect=lambda: called.append(1)):
            main.run_scalpday_us_session()
            self.assertEqual(called, [])


# ──────────────────────────────────────────────────
# FXRebalance セッションガード (a)(b)(c)(d)
# ──────────────────────────────────────────────────

class TestFXRebalanceGuard(unittest.TestCase):
    """
    run_fx_rebalance_session() の市場開閉ガードテスト。
    JP/US どちらかが開場なら実行、両方閉場なら early return。
    """

    def _make_full_mocks(self):
        """CIO/FX/Critic をまとめてスタブするヘルパー。"""
        from agents.base import MarketContext
        from agents.critics import CriticVerdict
        from datetime import date
        from unittest.mock import MagicMock

        mock_ctx = MarketContext(
            date=date.today().isoformat(),
            sector_scores={"AI半導体": 0.8},
            macro_notes="test",
            rotation_signal="維持",
            risk_level="medium",
        )
        mock_cio = MagicMock()
        mock_cio.generate_market_context.return_value = mock_ctx

        mock_fx = MagicMock()
        mock_fx.generate_signal.return_value = {
            "fx_signal": "hold",
            "target_usd_ratio": 35.0,
            "current_usd_ratio": 35.0,
        }

        mock_verdict = CriticVerdict(
            approved=True, score=0.9, fixable=False, issues=[], suggestion=""
        )
        mock_critic = MagicMock()
        mock_critic.review_signal.return_value = mock_verdict

        return mock_cio, mock_fx, mock_critic

    # ── (a) JP 開場のみ → 実行される ──────────────────────────────

    def test_jp_only_open_executes(self):
        """(a) JP 開場・US 閉場 → ガード通過、CIOAgent が呼ばれる。"""
        import main
        mock_cio, mock_fx, mock_critic = self._make_full_mocks()
        with patch("main.is_jp_open", return_value=True), \
             patch("main.is_us_open", return_value=False), \
             patch("main.CIOAgent", return_value=mock_cio), \
             patch("main.FXRebalance_Critic", return_value=mock_critic), \
             patch("agents.fx_strategy.FXStrategyAgent", return_value=mock_fx):
            main.run_fx_rebalance_session()
            mock_cio.generate_market_context.assert_called_once()

    # ── (b) US 開場のみ → 実行される ──────────────────────────────

    def test_us_only_open_executes(self):
        """(b) JP 閉場・US 開場 → ガード通過、CIOAgent が呼ばれる。"""
        import main
        mock_cio, mock_fx, mock_critic = self._make_full_mocks()
        with patch("main.is_jp_open", return_value=False), \
             patch("main.is_us_open", return_value=True), \
             patch("main.CIOAgent", return_value=mock_cio), \
             patch("main.FXRebalance_Critic", return_value=mock_critic), \
             patch("agents.fx_strategy.FXStrategyAgent", return_value=mock_fx):
            main.run_fx_rebalance_session()
            mock_cio.generate_market_context.assert_called_once()

    # ── 両方開場でも実行される（ガード条件の確認）────────────────

    def test_both_open_executes(self):
        """JP/US 両方開場 → ガード通過、実行される。"""
        import main
        mock_cio, mock_fx, mock_critic = self._make_full_mocks()
        with patch("main.is_jp_open", return_value=True), \
             patch("main.is_us_open", return_value=True), \
             patch("main.CIOAgent", return_value=mock_cio), \
             patch("main.FXRebalance_Critic", return_value=mock_critic), \
             patch("agents.fx_strategy.FXStrategyAgent", return_value=mock_fx):
            main.run_fx_rebalance_session()
            mock_cio.generate_market_context.assert_called_once()

    # ── (c) 両方閉場 → スキップ ────────────────────────────────

    def test_both_closed_skips(self):
        """(c) JP/US 両方閉場 → CIOAgent が呼ばれない（early return）。"""
        import main
        called = []
        with patch("main.is_jp_open", return_value=False), \
             patch("main.is_us_open", return_value=False), \
             patch("main.CIOAgent", side_effect=lambda: called.append(1)):
            main.run_fx_rebalance_session()
            self.assertEqual(called, [], "両市場閉場なのに CIOAgent が呼ばれた")

    # ── (d) スキップ時もエラーなし・ログが残る ──────────────────

    def test_both_closed_no_exception(self):
        """(d) 両方閉場でも例外を送出しない。"""
        import main
        with patch("main.is_jp_open", return_value=False), \
             patch("main.is_us_open", return_value=False):
            try:
                main.run_fx_rebalance_session()
            except Exception as e:
                self.fail(f"両市場閉場で例外が発生: {e}")

    def test_both_closed_log_message(self):
        """(d) 両方閉場のとき「両市場閉場 → FXリバランス見送り」をログに残す。"""
        import main
        import logging
        with patch("main.is_jp_open", return_value=False), \
             patch("main.is_us_open", return_value=False), \
             self.assertLogs("main", level=logging.INFO) as cm:
            main.run_fx_rebalance_session()
            self.assertTrue(
                any("両市場閉場" in line for line in cm.output),
                f"期待するログが見つからない: {cm.output}",
            )

    # ── Intel Scout は FX ガードと無関係 ─────────────────────────

    def test_intel_scout_session_not_affected_by_fx_guard(self):
        """
        両市場閉場でも run_intel_scout_session は FX ガードで止まらない。
        （収集側を発注ガードに巻き込まない要件の確認）
        """
        import main
        import inspect
        # 両市場閉場をセット
        with patch("main.is_jp_open", return_value=False), \
             patch("main.is_us_open", return_value=False):
            # FX リバランスはスキップ
            called_fx = []
            with patch("main.CIOAgent", side_effect=lambda: called_fx.append(1)):
                main.run_fx_rebalance_session()
            self.assertEqual(called_fx, [])

            # IntelScout はガードを持たない → ガード関数を呼ばないことを確認
            src = inspect.getsource(main.run_intel_scout_session)
            self.assertNotIn("is_jp_open", src)
            self.assertNotIn("is_us_open", src)


if __name__ == "__main__":
    unittest.main()
