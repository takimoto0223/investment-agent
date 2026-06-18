"""
tests/test_intel_scout.py
IntelScout の差分収集・ロールアップ・状態管理・ダイジェスト注入のテスト。
"""
import json
import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).parent.parent))

_JST = timezone(timedelta(hours=9))


# ──────────────────────────────────────────────────────────────────
# 1. 差分収集: since パラメータが fetch_biorxiv / fetch_sec_edgar に伝わる
# ──────────────────────────────────────────────────────────────────

class TestIncrementalCollect(unittest.TestCase):
    def _make_agent(self):
        from agents.intelligence import IntelligenceAgent
        agent = IntelligenceAgent.__new__(IntelligenceAgent)
        agent.logger = MagicMock()
        agent._ask_llm_json = MagicMock(return_value=[])
        agent._load_seen    = MagicMock(return_value={})
        agent._save_seen    = MagicMock()
        return agent

    def test_since_passed_to_biorxiv(self):
        """6h 前の since が fetch_biorxiv に渡る。"""
        agent = self._make_agent()
        since = datetime.now(_JST) - timedelta(hours=6)
        with patch.object(agent, "fetch_biorxiv",        return_value=[]) as m_bio, \
             patch.object(agent, "fetch_arxiv",          return_value=[]), \
             patch.object(agent, "fetch_github",         return_value=[]), \
             patch.object(agent, "fetch_techcrunch_rss", return_value=[]), \
             patch.object(agent, "fetch_tech_news_rss",  return_value=[]), \
             patch.object(agent, "fetch_google_news_rss",return_value=[]), \
             patch.object(agent, "fetch_sec_edgar",      return_value=[]) as m_sec, \
             patch.object(agent, "fetch_stocktwits",     return_value=[]), \
             patch.object(agent, "fetch_options_flow",   return_value=[]):
            agent.collect(since=since)
        m_bio.assert_called_once_with(since=since)
        m_sec.assert_called_once_with(since=since)

    def test_no_since_uses_defaults(self):
        """since=None のとき fetch_biorxiv に since=None が渡る（既存動作）。"""
        agent = self._make_agent()
        with patch.object(agent, "fetch_biorxiv",        return_value=[]) as m_bio, \
             patch.object(agent, "fetch_arxiv",          return_value=[]), \
             patch.object(agent, "fetch_github",         return_value=[]), \
             patch.object(agent, "fetch_techcrunch_rss", return_value=[]), \
             patch.object(agent, "fetch_tech_news_rss",  return_value=[]), \
             patch.object(agent, "fetch_google_news_rss",return_value=[]), \
             patch.object(agent, "fetch_sec_edgar",      return_value=[]) as m_sec, \
             patch.object(agent, "fetch_stocktwits",     return_value=[]), \
             patch.object(agent, "fetch_options_flow",   return_value=[]):
            agent.collect(since=None)
        m_bio.assert_called_once_with(since=None)
        m_sec.assert_called_once_with(since=None)


# ──────────────────────────────────────────────────────────────────
# 2. ロールアップ生成: 出力キーが揃う
# ──────────────────────────────────────────────────────────────────

class TestGenerateRollup(unittest.TestCase):
    def _make_agent(self):
        from agents.intelligence import IntelligenceAgent
        agent = IntelligenceAgent.__new__(IntelligenceAgent)
        agent.logger = MagicMock()
        return agent

    def test_rollup_structure_with_signals(self):
        """シグナルあり → 必要なキーが全部揃っている。"""
        agent = self._make_agent()
        signals = [
            {"title": "NVDA 8-K開示", "relevance_score": 0.95, "sectors": ["AI半導体"], "summary": "GPU需要増"},
            {"title": "arxiv論文",     "relevance_score": 0.80, "sectors": ["AIインフラ"], "summary": "新手法"},
        ]
        llm_out = {
            "sector_highlights": {"AI半導体": "NVDA好調"},
            "macro_summary":     "リスクオン継続",
            "digest_text":       "NVDA 8-K開示。GPU需要増加。",
        }
        agent._ask_llm_json = MagicMock(return_value=llm_out)
        result = agent.generate_rollup(signals, date_str="2026-06-19", window="08:05")

        for key in ["date", "generated_at", "windows", "signal_count", "top_signals",
                    "sector_highlights", "macro_summary", "digest_text"]:
            self.assertIn(key, result, f"キー '{key}' が欠落")
        self.assertEqual(result["date"],         "2026-06-19")
        self.assertEqual(result["signal_count"], 2)
        self.assertIn("08:05", result["windows"])
        self.assertEqual(result["sector_highlights"], {"AI半導体": "NVDA好調"})
        self.assertEqual(result["digest_text"],  "NVDA 8-K開示。GPU需要増加。")

    def test_rollup_empty_signals(self):
        """シグナルなし → LLM 呼び出しなしで空ロールアップを返す。"""
        agent = self._make_agent()
        agent._ask_llm_json = MagicMock()
        result = agent.generate_rollup([], date_str="2026-06-19", window="08:05")

        self.assertEqual(result["signal_count"], 0)
        self.assertEqual(result["top_signals"],  [])
        self.assertEqual(result["digest_text"],  "")
        agent._ask_llm_json.assert_not_called()

    def test_rollup_merges_existing_windows(self):
        """朝窓の結果に夕窓を追記するとき、既存窓が保持される。"""
        agent = self._make_agent()
        agent._ask_llm_json = MagicMock(return_value={
            "sector_highlights": {}, "macro_summary": "", "digest_text": "夕窓テスト",
        })
        result = agent.generate_rollup(
            [{"title": "test", "relevance_score": 0.7, "sectors": [], "summary": ""}],
            date_str="2026-06-19",
            window="17:01",
            existing_windows=["08:05"],
        )
        self.assertIn("08:05", result["windows"])
        self.assertIn("17:01", result["windows"])

    def test_rollup_llm_failure_fallback(self):
        """LLM 失敗時はシグナルから基本ロールアップを生成する（クラッシュしない）。"""
        agent = self._make_agent()
        agent._ask_llm_json = MagicMock(return_value=None)
        signals = [{"title": "test", "relevance_score": 0.7, "sectors": ["AI半導体"], "summary": "test"}]
        result = agent.generate_rollup(signals, date_str="2026-06-19")
        self.assertEqual(result["signal_count"], 1)
        self.assertIn("digest_text", result)


# ──────────────────────────────────────────────────────────────────
# 3. read_intel_digest: 空 dict フォールバック
# ──────────────────────────────────────────────────────────────────

class TestReadIntelDigest(unittest.TestCase):
    def test_returns_empty_dict_when_file_missing(self):
        """ファイル不在 → 空 dict（正常パス）。"""
        from data.intel_store import read_intel_digest
        with patch("data.intel_store._DIGEST_PATH", Path("/nonexistent/path.json")):
            result = read_intel_digest()
        self.assertEqual(result, {})

    def test_returns_empty_dict_on_parse_error(self):
        """JSON 破損 → 空 dict（クラッシュしない）。"""
        from data.intel_store import read_intel_digest
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "NOT_JSON{"
        with patch("data.intel_store._DIGEST_PATH", mock_path):
            result = read_intel_digest()
        self.assertEqual(result, {})

    def test_returns_digest_when_valid(self):
        """正常な JSON → dict を返す。"""
        from data.intel_store import read_intel_digest
        content = {"date": "2026-06-19", "digest_text": "テスト", "generated_at": "2026-06-19T08:00:00+09:00"}
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(content)
        with patch("data.intel_store._DIGEST_PATH", mock_path):
            result = read_intel_digest()
        self.assertEqual(result["digest_text"], "テスト")


# ──────────────────────────────────────────────────────────────────
# 4. 鮮度チェック: generated_at > 48h → 空フォールバック + WARNING
# ──────────────────────────────────────────────────────────────────

class TestFreshnessCheck(unittest.TestCase):
    def _make_digest(self, age_hours: float, text: str = "最新ニュース") -> dict:
        dt = datetime.now(_JST) - timedelta(hours=age_hours)
        return {
            "generated_at": dt.isoformat(),
            "digest_text":  text,
            "date":         dt.strftime("%Y-%m-%d"),
        }

    def test_fresh_digest_returns_text(self):
        """24h 以内 → digest_text と "IntelScout" を返す。"""
        from data.intel_store import get_news_summary_for_cio
        digest = self._make_digest(age_hours=24)
        with patch("data.intel_store.read_intel_digest", return_value=digest):
            text, obs_by = get_news_summary_for_cio()
        self.assertEqual(text, "最新ニュース")
        self.assertEqual(obs_by, "IntelScout")

    def test_stale_digest_returns_empty_and_warns(self):
        """49h 経過 → 空文字列 + "CIO" + WARNING ログ。"""
        from data.intel_store import get_news_summary_for_cio
        digest = self._make_digest(age_hours=49)
        with patch("data.intel_store.read_intel_digest", return_value=digest), \
             self.assertLogs("data.intel_store", level="WARNING") as cm:
            text, obs_by = get_news_summary_for_cio()
        self.assertEqual(text, "")
        self.assertEqual(obs_by, "CIO")
        self.assertTrue(any("IntelScout" in line for line in cm.output))

    def test_empty_digest_returns_empty(self):
        """空 dict → ("", "CIO")（初回/未収集の正常パス）。"""
        from data.intel_store import get_news_summary_for_cio
        with patch("data.intel_store.read_intel_digest", return_value={}):
            text, obs_by = get_news_summary_for_cio()
        self.assertEqual(text, "")
        self.assertEqual(obs_by, "CIO")

    def test_empty_digest_text_returns_empty(self):
        """digest_text が空文字 → ("", "CIO")。"""
        from data.intel_store import get_news_summary_for_cio
        digest = self._make_digest(age_hours=1, text="")
        with patch("data.intel_store.read_intel_digest", return_value=digest):
            text, obs_by = get_news_summary_for_cio()
        self.assertEqual(text, "")
        self.assertEqual(obs_by, "CIO")


# ──────────────────────────────────────────────────────────────────
# 5. セッションが空 digest でクラッシュしない
# ──────────────────────────────────────────────────────────────────

class TestSessionFallback(unittest.TestCase):
    def _make_cio(self):
        from agents.cio import CIOAgent
        cio = CIOAgent.__new__(CIOAgent)
        cio.logger = MagicMock()
        cio._load_ctx_cache = MagicMock(return_value=None)
        cio._save_ctx_cache = MagicMock()
        cio._ask_llm_json   = MagicMock(return_value={
            "sector_scores": {"AI半導体": 0.8},
            "macro_notes":    "テスト",
            "rotation_signal": "維持",
            "risk_level":      "medium",
        })
        return cio

    def test_cio_accepts_empty_news_summary(self):
        """CIO は news_summary="", obs_source="CIO" でも MarketContext を返す。"""
        from agents.base import MarketContext
        ctx = self._make_cio().generate_market_context(
            news_summary="", macro_data="USD/JPY=155", obs_source="CIO"
        )
        self.assertIsInstance(ctx, MarketContext)
        self.assertEqual(ctx.obs_generated_by, "CIO")

    def test_cio_sets_intel_scout_via_obs_source(self):
        """obs_source="IntelScout" → obs_generated_by = "IntelScout"（文字数によらない）。"""
        ctx = self._make_cio().generate_market_context(
            news_summary="OK",  # 2文字の短いダイジェスト
            macro_data="",
            obs_source="IntelScout",
        )
        self.assertEqual(ctx.obs_generated_by, "IntelScout")

    def test_cio_default_obs_source_is_cio(self):
        """obs_source 省略 → obs_generated_by = "CIO"（既存呼び出しの後方互換）。"""
        ctx = self._make_cio().generate_market_context(news_summary="", macro_data="")
        self.assertEqual(ctx.obs_generated_by, "CIO")


# ──────────────────────────────────────────────────────────────────
# 6. パイプライン通し: get_news_summary_for_cio → CIO → MarketContext.obs_generated_by
# ──────────────────────────────────────────────────────────────────

class TestObsGeneratedByPipeline(unittest.TestCase):
    """
    IntelScout → CIO → MarketContext.obs_generated_by の経路を通しで検証する。
    CIO 内で obs_source を再判定していないこと（= read_intel_digest の結果が
    そのまま MarketContext に記録されること）を確認するための回帰テスト。
    """

    def _make_fresh_digest(self, text: str) -> dict:
        """48h 以内の新鮮なダイジェストを作る。"""
        return {
            "generated_at": datetime.now(_JST).isoformat(),
            "digest_text":  text,
            "date":         datetime.now(_JST).strftime("%Y-%m-%d"),
        }

    def _run_pipeline(self, digest: dict) -> str:
        """
        read_intel_digest → get_news_summary_for_cio → generate_market_context
        を通しで実行し、MarketContext.obs_generated_by を返す。
        """
        from agents.cio import CIOAgent
        from data.intel_store import get_news_summary_for_cio

        cio = CIOAgent.__new__(CIOAgent)
        cio.logger = MagicMock()
        cio._load_ctx_cache = MagicMock(return_value=None)
        cio._save_ctx_cache = MagicMock()
        cio._ask_llm_json   = MagicMock(return_value={
            "sector_scores":   {"AI半導体": 0.8},
            "macro_notes":     "テスト",
            "rotation_signal": "維持",
            "risk_level":      "medium",
        })

        with patch("data.intel_store.read_intel_digest", return_value=digest):
            _news, _obs = get_news_summary_for_cio()
            ctx = cio.generate_market_context(
                news_summary=_news,
                macro_data="",
                obs_source=_obs,
            )
        return ctx.obs_generated_by

    def test_empty_digest_gives_cio(self):
        """材料なし(空dict) → obs_generated_by == "CIO"。"""
        result = self._run_pipeline({})
        self.assertEqual(result, "CIO")

    def test_fresh_digest_gives_intel_scout(self):
        """新鮮なダイジェストあり → obs_generated_by == "IntelScout"。"""
        result = self._run_pipeline(self._make_fresh_digest("NVDA 8-K 開示。GPU 需要急増。"))
        self.assertEqual(result, "IntelScout")

    def test_short_digest_still_gives_intel_scout(self):
        """
        短い正規ダイジェスト「本日材料なし」(8文字) でも obs_generated_by == "IntelScout"。
        旧・文字数判定(> 20文字)では "CIO" と誤記録されていた回帰ケース。
        """
        result = self._run_pipeline(self._make_fresh_digest("本日材料なし"))
        self.assertEqual(result, "IntelScout")

    def test_stale_digest_falls_back_to_cio(self):
        """49h 前のダイジェスト → 鮮度切れ → obs_generated_by == "CIO"。"""
        old_dt = datetime.now(_JST) - timedelta(hours=49)
        stale_digest = {
            "generated_at": old_dt.isoformat(),
            "digest_text":  "古いダイジェスト",
            "date":         old_dt.strftime("%Y-%m-%d"),
        }
        with self.assertLogs("data.intel_store", level="WARNING"):
            result = self._run_pipeline(stale_digest)
        self.assertEqual(result, "CIO")


# ──────────────────────────────────────────────────────────────────
# 8. load_state / save_state: 往復テスト
# ──────────────────────────────────────────────────────────────────

class TestIntelState(unittest.TestCase):
    def test_roundtrip(self):
        """save → load で same datetime が返る。"""
        import tempfile, os
        from data.intel_store import IntelState, load_state, save_state
        now = datetime(2026, 6, 19, 8, 5, 0, tzinfo=_JST)
        with tempfile.TemporaryDirectory() as td:
            fake_path = Path(td) / "state.json"
            with patch("data.intel_store._STATE_PATH", fake_path):
                save_state(IntelState(last_collected_at=now))
                loaded = load_state()
        self.assertIsNotNone(loaded.last_collected_at)
        self.assertEqual(loaded.last_collected_at.isoformat(), now.isoformat())

    def test_load_missing_file_returns_empty(self):
        """ファイル不在 → IntelState(last_collected_at=None)。"""
        from data.intel_store import IntelState, load_state
        with patch("data.intel_store._STATE_PATH", Path("/nonexistent/state.json")):
            state = load_state()
        self.assertIsNone(state.last_collected_at)


# ──────────────────────────────────────────────────────────────────
# 9. write_intel_digest: JSON + Markdown 両方書かれる
# ──────────────────────────────────────────────────────────────────

class TestWriteIntelDigest(unittest.TestCase):
    def test_writes_json_and_markdown(self):
        """write_intel_digest が JSON と .md を両方作成する。"""
        import tempfile
        from data.intel_store import write_intel_digest
        rollup = {
            "date":              "2026-06-19",
            "generated_at":      "2026-06-19T08:05:00+09:00",
            "windows":           ["08:05"],
            "signal_count":      3,
            "top_signals":       [],
            "sector_highlights": {"AI半導体": "好調"},
            "macro_summary":     "リスクオン",
            "digest_text":       "テストダイジェスト",
        }
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            fake_json = td_path / "intel_digest.json"
            fake_dir  = td_path / "digests"
            with patch("data.intel_store._DIGEST_PATH", fake_json), \
                 patch("data.intel_store._DIGESTS_DIR", fake_dir):
                write_intel_digest(rollup)

            self.assertTrue(fake_json.exists(), "intel_digest.json が作成されない")
            saved = json.loads(fake_json.read_text(encoding="utf-8"))
            self.assertEqual(saved["digest_text"], "テストダイジェスト")

            md_path = fake_dir / "2026-06-19.md"
            self.assertTrue(md_path.exists(), "Markdown ファイルが作成されない")
            md_content = md_path.read_text(encoding="utf-8")
            self.assertIn("AI半導体", md_content)
            self.assertIn("テストダイジェスト", md_content)
            self.assertIn("08:05", md_content)


if __name__ == "__main__":
    unittest.main()
