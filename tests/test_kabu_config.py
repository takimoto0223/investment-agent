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

    # ── パスワード選択 ────────────────────────────────────────────

    def test_test_port_selects_test_password(self):
        """解決後ポート18081(検証) → TEST用パスワードが選ばれる。"""
        cfg = self._cfg(
            KABU_ENV="test",
            KABU_API_PASSWORD_TEST="pw_test",
            KABU_API_PASSWORD_LIVE="pw_live",
        )
        self.assertEqual(cfg.password, "pw_test")

    def test_live_port_selects_live_password(self):
        """解決後ポート18080(本番) → LIVE用パスワードが選ばれる。"""
        cfg = self._cfg(
            KABU_ENV="live",
            KABU_API_PASSWORD_TEST="pw_test",
            KABU_API_PASSWORD_LIVE="pw_live",
        )
        self.assertEqual(cfg.password, "pw_live")

    def test_base_url_override_selects_password_by_port(self):
        """KABU_BASE_URL で本番ポートを直指定した場合も LIVE パスワードが選ばれる。"""
        cfg = self._cfg(
            KABU_ENV="test",
            KABU_BASE_URL="http://localhost:18080/kabusapi",
            KABU_API_PASSWORD_TEST="pw_test",
            KABU_API_PASSWORD_LIVE="pw_live",
        )
        self.assertEqual(cfg.password, "pw_live")

    def test_fallback_to_shared_password_on_test_port(self):
        """_TEST/_LIVE が未設定なら KABU_API_PASSWORD にフォールバック(検証)。"""
        cfg = self._cfg(KABU_ENV="test", KABU_API_PASSWORD="pw_shared")
        self.assertEqual(cfg.password, "pw_shared")

    def test_fallback_to_shared_password_on_live_port(self):
        """_TEST/_LIVE が未設定なら KABU_API_PASSWORD にフォールバック(本番)。"""
        cfg = self._cfg(KABU_ENV="live", KABU_API_PASSWORD="pw_shared")
        self.assertEqual(cfg.password, "pw_shared")


class TestKabuBrokerLiveGuard(unittest.TestCase):
    """本番環境での誤発注ガードを確認する。"""

    def setUp(self):
        self._saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("KABU_")}

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("KABU_"):
                del os.environ[k]
        os.environ.update(self._saved)

    def _broker(self, **kabu_env):
        """KABU_* 環境変数をクリアして kabu_env だけを設定した fresh な KabuConfig を注入する。
        シングルトン KABU に依存せず、解決後の base_url をテストで制御できる。
        """
        from brokers.kabu import KabuBroker
        from config.settings import KabuConfig
        for k in list(os.environ):
            if k.startswith("KABU_"):
                del os.environ[k]
        os.environ.update(kabu_env)
        return KabuBroker(config=KabuConfig())

    # ── 本番ポート(18080) + フラグなし → ブロック ────────────────

    def test_send_cash_order_blocked_on_live_port(self):
        with self.assertRaises(RuntimeError):
            self._broker(KABU_ENV="live").send_cash_order("1234", "2", 100)

    def test_send_margin_order_blocked_on_live_port(self):
        with self.assertRaises(RuntimeError):
            self._broker(KABU_ENV="live").send_margin_order("1234", "2", 100)

    def test_cancel_order_blocked_on_live_port(self):
        with self.assertRaises(RuntimeError):
            self._broker(KABU_ENV="live").cancel_order("ORDER001")

    # ── 本番ポート + KABU_ALLOW_LIVE_ORDER=1 → ガード通過 ───────

    def test_send_cash_order_allowed_with_flag(self):
        with patch("requests.post", side_effect=ConnectionError("kabu not running")):
            result = self._broker(KABU_ENV="live", KABU_ALLOW_LIVE_ORDER="1").send_cash_order("1234", "2", 100)
        self.assertFalse(result.success)

    # ── 検証ポート(18081) → ガード通過 ──────────────────────────

    def test_send_cash_order_not_blocked_on_test_port(self):
        with patch("requests.post", side_effect=ConnectionError("kabu not running")):
            result = self._broker(KABU_ENV="test").send_cash_order("1234", "2", 100)
        self.assertFalse(result.success)

    def test_default_env_not_blocked(self):
        """KABU_ENV 未設定(デフォルト=18081)でもガードを通過する。"""
        with patch("requests.post", side_effect=ConnectionError("kabu not running")):
            result = self._broker().send_cash_order("1234", "2", 100)
        self.assertFalse(result.success)

    # ── 宣言と実体が食い違うケース ───────────────────────────────

    def test_blocked_when_base_url_points_to_live_despite_test_env(self):
        """KABU_ENV=test でも KABU_BASE_URL で本番ポートを直指定したらガードが効く。"""
        with self.assertRaises(RuntimeError):
            self._broker(
                KABU_ENV="test",
                KABU_BASE_URL="http://localhost:18080/kabusapi",
            ).send_cash_order("1234", "2", 100)

    def test_not_blocked_when_base_url_points_to_test_despite_live_env(self):
        """KABU_ENV=live でも KABU_BASE_URL で検証ポートを直指定したらガードを通過する。"""
        with patch("requests.post", side_effect=ConnectionError("kabu not running")):
            result = self._broker(
                KABU_ENV="live",
                KABU_BASE_URL="http://localhost:18081/kabusapi",
            ).send_cash_order("1234", "2", 100)
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
