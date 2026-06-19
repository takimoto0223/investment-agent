"""
tests/test_fx_rate.py
data/fx_rate.get_usd_jpy() の4ケーステスト。

ケース:
  1. API 成功 → rate / "6/19" 形式 / source="api" を返す
  2. API タイムアウト → キャッシュ有効 (24h以内) → source="cache"
  3. API タイムアウト → キャッシュ期限切れ (24h超) → source="fallback", rate=155.0
  4. API タイムアウト → キャッシュなし → source="fallback", rate=155.0
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error


# ── テスト用 API レスポンス ──────────────────────────────────────

_API_RESPONSE = json.dumps({
    "date": "2026-06-19",
    "rates": {"JPY": 157.42},
}).encode()


def _make_urlopen_response(body: bytes) -> MagicMock:
    """urllib.request.urlopen のコンテキストマネージャを模倣する。"""
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ── テストクラス ─────────────────────────────────────────────────

class TestGetUsdJpy(unittest.TestCase):

    def setUp(self):
        import data.fx_rate as m
        self.m = m
        # テストごとにキャッシュパスを一時パスに差し替える
        self._orig_cache = m._CACHE_PATH
        m._CACHE_PATH = Path("logs/_test_fx_cache.json")
        # キャッシュを消去してクリーンな状態にする
        if m._CACHE_PATH.exists():
            m._CACHE_PATH.unlink()

    def tearDown(self):
        self.m._CACHE_PATH = self._orig_cache
        test_cache = Path("logs/_test_fx_cache.json")
        if test_cache.exists():
            test_cache.unlink()

    # ── ケース1: API 成功 ─────────────────────────────────────────

    def test_api_success(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_API_RESPONSE)):
            rate, fetched_at, source = self.m.get_usd_jpy()

        self.assertAlmostEqual(rate, 157.42, places=2)
        self.assertEqual(fetched_at, "6/19")
        self.assertEqual(source, "api")

        # キャッシュが書き込まれていること
        cache = json.loads(self.m._CACHE_PATH.read_text(encoding="utf-8"))
        self.assertAlmostEqual(cache["rate"], 157.42, places=2)
        self.assertEqual(cache["date"], "2026-06-19")

    # ── ケース2: API タイムアウト → キャッシュ有効（24h以内） ────

    def test_timeout_then_cache_hit(self):
        # 1時間前のキャッシュを作成
        self.m._CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.m._CACHE_PATH.write_text(json.dumps({
            "rate": 156.00,
            "date": "2026-06-19",
            "cached_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }), encoding="utf-8")

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            rate, fetched_at, source = self.m.get_usd_jpy()

        self.assertAlmostEqual(rate, 156.00, places=2)
        self.assertEqual(fetched_at, "6/19")
        self.assertEqual(source, "cache")

    # ── ケース3: API タイムアウト → キャッシュ期限切れ（24h超） ──

    def test_timeout_then_cache_expired(self):
        # 25時間前のキャッシュ（期限切れ）を作成
        self.m._CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.m._CACHE_PATH.write_text(json.dumps({
            "rate": 154.00,
            "date": "2026-06-18",
            "cached_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        }), encoding="utf-8")

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            rate, fetched_at, source = self.m.get_usd_jpy()

        self.assertAlmostEqual(rate, 155.0, places=1)
        self.assertEqual(source, "fallback")

    # ── ケース4: API タイムアウト → キャッシュなし → 最終フォールバック ──

    def test_timeout_no_cache(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            rate, fetched_at, source = self.m.get_usd_jpy()

        self.assertAlmostEqual(rate, 155.0, places=1)
        self.assertEqual(source, "fallback")
        self.assertIsInstance(fetched_at, str)
        self.assertIn("/", fetched_at)  # "6/19" 形式であること


if __name__ == "__main__":
    unittest.main()
