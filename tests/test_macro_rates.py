"""
tests/test_macro_rates.py
data/macro_rates.get_vix() / get_us_10y() のテスト。

ケース:
  1. API 成功 → value / "6/17" 形式 / source="api" / キャッシュ書き込み
  2. API 失敗 → キャッシュ有効 (1h以内) → source="cache"
  3. API 失敗 → キャッシュ期限切れ (25h超) → source="fallback"
  4. API 失敗 → キャッシュなし → source="fallback"
  5. FRED_API_KEY 未設定 → クラッシュせず fallback を返す
  6. 最新観測値が "." → スキップして次の有効値を使う
"""
import json
import os
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── テスト用 FRED レスポンス ────────────────────────────────────────

def _make_fred_response(observations: list[dict]) -> bytes:
    return json.dumps({
        "observations": observations,
    }).encode()


def _obs(date: str, value: str) -> dict:
    return {"date": date, "value": value}


def _make_urlopen_cm(body: bytes) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


_VIX_RESPONSE = _make_fred_response([
    _obs("2026-06-17", "18.44"),
    _obs("2026-06-16", "16.41"),
])

_US10Y_RESPONSE = _make_fred_response([
    _obs("2026-06-17", "4.49"),
    _obs("2026-06-16", "4.43"),
])

# 最新が "." でその次が有効値
_VIX_DOT_RESPONSE = _make_fred_response([
    _obs("2026-06-20", "."),      # 週末 → 欠損
    _obs("2026-06-17", "18.44"),  # 有効
])


# ── 共通ヘルパー ────────────────────────────────────────────────────

class _MacroRatesTestBase(unittest.TestCase):
    """各テストケースでキャッシュを独立させる。"""

    def setUp(self):
        import data.macro_rates as m
        self.m = m
        self._orig_cache = m._CACHE_PATH
        m._CACHE_PATH = Path("logs/_test_macro_rates_cache.json")
        if m._CACHE_PATH.exists():
            m._CACHE_PATH.unlink()

    def tearDown(self):
        self.m._CACHE_PATH = self._orig_cache
        p = Path("logs/_test_macro_rates_cache.json")
        if p.exists():
            p.unlink()

    def _write_test_cache(self, key: str, value: float, date_str: str, age_hours: float):
        self.m._CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache: dict = {}
        if self.m._CACHE_PATH.exists():
            cache = json.loads(self.m._CACHE_PATH.read_text(encoding="utf-8"))
        cache[key] = {
            "value":     value,
            "date":      date_str,
            "cached_at": (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(),
        }
        self.m._CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")


# ── VIX テスト ──────────────────────────────────────────────────────

class TestGetVix(_MacroRatesTestBase):

    # ── ケース 1: API 成功 ──────────────────────────────────────────

    def test_api_success(self):
        with patch("urllib.request.urlopen",
                   return_value=_make_urlopen_cm(_VIX_RESPONSE)), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_vix()

        self.assertAlmostEqual(value, 18.44, places=2)
        self.assertEqual(fetched_at, "6/17")
        self.assertEqual(source, "api")

        cache = json.loads(self.m._CACHE_PATH.read_text(encoding="utf-8"))
        self.assertAlmostEqual(cache["vix"]["value"], 18.44, places=2)
        self.assertEqual(cache["vix"]["date"], "2026-06-17")

    # ── ケース 2: API 失敗 → キャッシュ有効 ───────────────────────

    def test_cache_hit(self):
        self._write_test_cache("vix", 17.50, "2026-06-16", age_hours=1)
        with patch("urllib.request.urlopen", side_effect=TimeoutError()), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_vix()

        self.assertAlmostEqual(value, 17.50, places=2)
        self.assertEqual(fetched_at, "6/16")
        self.assertEqual(source, "cache")

    # ── ケース 3: API 失敗 → キャッシュ期限切れ → fallback ────────

    def test_cache_expired_fallback(self):
        self._write_test_cache("vix", 17.50, "2026-06-15", age_hours=25)
        with patch("urllib.request.urlopen", side_effect=TimeoutError()), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_vix()

        self.assertAlmostEqual(value, self.m._FALLBACK_VIX, places=1)
        self.assertEqual(source, "fallback")

    # ── ケース 4: API 失敗 → キャッシュなし → fallback ────────────

    def test_no_cache_fallback(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("err")), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_vix()

        self.assertAlmostEqual(value, self.m._FALLBACK_VIX, places=1)
        self.assertEqual(source, "fallback")
        self.assertIn("/", fetched_at)  # "6/20" 形式

    # ── ケース 5: API キー未設定 → クラッシュせず fallback ─────────

    def test_missing_api_key_fallback(self):
        env = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            value, fetched_at, source = self.m.get_vix()

        self.assertAlmostEqual(value, self.m._FALLBACK_VIX, places=1)
        self.assertEqual(source, "fallback")

    # ── ケース 6: 最新観測値が "." → 次の有効値を使う ─────────────

    def test_dot_value_skipped(self):
        with patch("urllib.request.urlopen",
                   return_value=_make_urlopen_cm(_VIX_DOT_RESPONSE)), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_vix()

        self.assertAlmostEqual(value, 18.44, places=2)
        self.assertEqual(fetched_at, "6/17")  # "." の 6/20 ではなく有効な 6/17
        self.assertEqual(source, "api")


# ── 米10Y テスト ────────────────────────────────────────────────────

class TestGetUs10y(_MacroRatesTestBase):

    def test_api_success(self):
        with patch("urllib.request.urlopen",
                   return_value=_make_urlopen_cm(_US10Y_RESPONSE)), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_us_10y()

        self.assertAlmostEqual(value, 4.49, places=2)
        self.assertEqual(fetched_at, "6/17")
        self.assertEqual(source, "api")

        cache = json.loads(self.m._CACHE_PATH.read_text(encoding="utf-8"))
        self.assertAlmostEqual(cache["us_10y"]["value"], 4.49, places=2)

    def test_cache_hit(self):
        self._write_test_cache("us_10y", 4.30, "2026-06-16", age_hours=2)
        with patch("urllib.request.urlopen", side_effect=TimeoutError()), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_us_10y()

        self.assertAlmostEqual(value, 4.30, places=2)
        self.assertEqual(source, "cache")

    def test_fallback(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("err")), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_us_10y()

        self.assertAlmostEqual(value, self.m._FALLBACK_US10Y, places=2)
        self.assertEqual(source, "fallback")

    def test_missing_api_key_fallback(self):
        env = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            value, fetched_at, source = self.m.get_us_10y()

        self.assertAlmostEqual(value, self.m._FALLBACK_US10Y, places=2)
        self.assertEqual(source, "fallback")

    def test_dot_value_skipped(self):
        dot_resp = _make_fred_response([
            _obs("2026-06-20", "."),
            _obs("2026-06-17", "4.49"),
        ])
        with patch("urllib.request.urlopen",
                   return_value=_make_urlopen_cm(dot_resp)), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            value, fetched_at, source = self.m.get_us_10y()

        self.assertAlmostEqual(value, 4.49, places=2)
        self.assertEqual(fetched_at, "6/17")
        self.assertEqual(source, "api")


# ── キャッシュ独立性テスト ──────────────────────────────────────────

class TestCacheIndependence(_MacroRatesTestBase):
    """VIX と 10Y のキャッシュエントリが互いに干渉しないこと。"""

    def test_vix_and_10y_coexist_in_cache(self):
        self._write_test_cache("vix",   18.44, "2026-06-17", age_hours=1)
        self._write_test_cache("us_10y", 4.49, "2026-06-17", age_hours=1)

        with patch("urllib.request.urlopen", side_effect=TimeoutError()), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            vix_val,   _, vix_src   = self.m.get_vix()
            t10y_val,  _, t10y_src  = self.m.get_us_10y()

        self.assertAlmostEqual(vix_val, 18.44, places=2)
        self.assertEqual(vix_src, "cache")
        self.assertAlmostEqual(t10y_val, 4.49, places=2)
        self.assertEqual(t10y_src, "cache")

    def test_vix_expired_does_not_invalidate_10y(self):
        """VIX のキャッシュが期限切れでも、10Y の有効キャッシュは使われる。"""
        self._write_test_cache("vix",    18.44, "2026-06-17", age_hours=25)  # 期限切れ
        self._write_test_cache("us_10y",  4.49, "2026-06-17", age_hours=1)   # 有効

        with patch("urllib.request.urlopen", side_effect=TimeoutError()), \
             patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            _, _, vix_src  = self.m.get_vix()
            _, _, t10y_src = self.m.get_us_10y()

        self.assertEqual(vix_src,  "fallback")
        self.assertEqual(t10y_src, "cache")


if __name__ == "__main__":
    unittest.main()
