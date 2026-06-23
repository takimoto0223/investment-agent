"""
tests/test_kabu_config.py
KabuConfig の環境切り替えと KabuBroker の本番発注ガードのテスト。
実 API は一切叩かない（設定値の検証のみ）。
"""
import os
import unittest
from unittest.mock import patch


class TestKabuConfig(unittest.TestCase):
    """KABU_ENV による base_url / ws_url の切り替えを確認する。"""

    def setUp(self):
        # テスト間の干渉を防ぐため KABU_* 環境変数を退避・除去
        self._saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("KABU_")}

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("KABU_"):
                del os.environ[k]
        os.environ.update(self._saved)

    def _cfg(self, **env):
        # load_dotenv() が初回 import 時に .env の KABU_* を復活させる場合があるため
        # import 後に改めてクリアし、テスト指定の値だけを設定する
        from config.settings import KabuConfig
        for k in list(os.environ):
            if k.startswith("KABU_"):
                del os.environ[k]
        os.environ.update(env)
        return KabuConfig()

    def test_default_uses_test_port(self):
        """KABU_ENV 未設定のデフォルトは検証環境(18081)。"""
        cfg = self._cfg()
        self.assertIn(":18081/", cfg.base_url)
        self.assertIn(":18081/", cfg.ws_url)
        self.assertEqual(cfg.env, "test")

    def test_explicit_test_uses_test_port(self):
        """KABU_ENV=test は検証ポート(18081)。"""
        cfg = self._cfg(KABU_ENV="test")
        self.assertIn(":18081/", cfg.base_url)
        self.assertIn(":18081/", cfg.ws_url)

    def test_live_uses_live_port(self):
        """KABU_ENV=live は本番ポート(18080)。"""
        cfg = self._cfg(KABU_ENV="live")
        self.assertIn(":18080/", cfg.base_url)
        self.assertIn(":18080/", cfg.ws_url)
        self.assertEqual(cfg.env, "live")

    def test_explicit_base_url_overrides_env(self):
        """KABU_BASE_URL の明示設定は KABU_ENV より優先される。"""
        cfg = self._cfg(KABU_ENV="live", KABU_BASE_URL="http://custom:9999/api")
        self.assertEqual(cfg.base_url, "http://custom:9999/api")

    def test_ws_url_not_affected_by_explicit_base_url(self):
        """KABU_BASE_URL を上書きしても ws_url は KABU_ENV に従う。"""
        cfg = self._cfg(KABU_ENV="live", KABU_BASE_URL="http://custom:9999/api")
        self.assertIn(":18080/", cfg.ws_url)

    def test_unknown_env_falls_back_to_test_port(self):
        """未知の KABU_ENV 値はデフォルト(18081)にフォールバック。"""
        cfg = self._cfg(KABU_ENV="staging")
        self.assertIn(":18081/", cfg.base_url)


class TestKabuBrokerLiveGuard(unittest.TestCase):
    """本番環境での誤発注ガードを確認する。"""

    def setUp(self):
        self._saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("KABU_")}

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("KABU_"):
                del os.environ[k]
        os.environ.update(self._saved)

    def _broker(self):
        from brokers.kabu import KabuBroker
        return KabuBroker()

    # ── live + フラグなし → ブロック ──────────────────────────────

    def test_send_cash_order_blocked_in_live(self):
        os.environ["KABU_ENV"] = "live"
        with self.assertRaises(RuntimeError):
            self._broker().send_cash_order("1234", "2", 100)

    def test_send_margin_order_blocked_in_live(self):
        os.environ["KABU_ENV"] = "live"
        with self.assertRaises(RuntimeError):
            self._broker().send_margin_order("1234", "2", 100)

    def test_cancel_order_blocked_in_live(self):
        os.environ["KABU_ENV"] = "live"
        with self.assertRaises(RuntimeError):
            self._broker().cancel_order("ORDER001")

    # ── live + KABU_ALLOW_LIVE_ORDER=1 → ガード通過 ─────────────

    def test_send_cash_order_allowed_with_flag(self):
        os.environ["KABU_ENV"] = "live"
        os.environ["KABU_ALLOW_LIVE_ORDER"] = "1"
        with patch("requests.post", side_effect=ConnectionError("kabu not running")):
            result = self._broker().send_cash_order("1234", "2", 100)
        # RuntimeError ではなく OrderResult が返ること
        self.assertFalse(result.success)

    # ── test 環境 → ガード通過 ───────────────────────────────────

    def test_send_cash_order_not_blocked_in_test(self):
        os.environ["KABU_ENV"] = "test"
        with patch("requests.post", side_effect=ConnectionError("kabu not running")):
            result = self._broker().send_cash_order("1234", "2", 100)
        # ガードを通過し、接続エラーが OrderResult で返ること
        self.assertFalse(result.success)

    def test_default_env_not_blocked(self):
        """KABU_ENV 未設定(デフォルト=test)でもガードを通過する。"""
        with patch("requests.post", side_effect=ConnectionError("kabu not running")):
            result = self._broker().send_cash_order("1234", "2", 100)
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
