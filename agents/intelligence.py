"""
agents/intelligence.py
情報収集エージェント：arxiv・GitHub・HackerNews から
テック系グロースセクターの最新シグナルを収集し、LLM でスコアリングする。
"""
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from agents.base import BaseAgent, MarketContext
from prompts.loader import get_prompt


# セクターごとの検索キーワード
_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "AIインフラ":           ["gpu", "hbm", "llm", "inference", "ai infrastructure", "nvidia", "tpu", "accelerator", "data center"],
    "半導体サプライチェーン": ["semiconductor", "chip fabrication", "wafer", "lithography", "etching", "chiplet", "advanced packaging"],
    "次世代メモリ":          ["mram", "hbm", "cxl", "compute express link", "storage class memory", "persistent memory"],
    "量子コンピューター":     ["quantum computing", "qubit", "quantum error correction", "quantum hardware", "quantum software"],
    "宇宙インフラ":          ["satellite", "rocket", "launch vehicle", "low earth orbit", "starlink", "space station"],
    "フォトニクス":          ["silicon photonics", "optical interconnect", "photonic chip", "laser communication"],
    "エネルギーインフラ":     ["data center power", "cooling system", "nuclear fusion", "small modular reactor", "smr"],
    "バイオ×AI":            ["drug discovery", "protein structure", "genomics", "biotech ai", "clinical trial ai"],
}

# GitHub 検索キーワード
_GITHUB_KEYWORDS = [
    "quantum", "mram", "photonic", "neuromorphic",
    "space", "llm-infra", "chip-design",
]

# arxiv カテゴリクエリ
_ARXIV_QUERY = "cat:cs.ET+OR+cat:cs.AR+OR+cat:quant-ph+OR+cat:cs.NE+OR+cat:eess.SP"


def _http_get(url: str, timeout: int = 12) -> str | None:
    """GET リクエストを送り、レスポンス文字列を返す。失敗時は None。"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "investment-agent/1.0 (research bot)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _detect_sectors(text: str) -> list[str]:
    """テキストからキーワードを検出し、該当セクター名を返す。"""
    text_lower = text.lower()
    matched = [
        sector
        for sector, keywords in _SECTOR_KEYWORDS.items()
        if any(kw in text_lower for kw in keywords)
    ]
    return matched or ["不明"]


class IntelligenceAgent(BaseAgent):
    name = "IntelligenceAgent"
    system_prompt = get_prompt("intelligence")

    # ──────────────────────────────────────────────
    # データ取得
    # ──────────────────────────────────────────────

    def fetch_arxiv(self, max_results: int = 20) -> list[dict]:
        """arxiv API から最新論文を取得する（cs.ET / cs.AR / quant-ph / cs.NE / eess.SP）。"""
        url = (
            "http://export.arxiv.org/api/query"
            f"?search_query={_ARXIV_QUERY}"
            "&sortBy=submittedDate&sortOrder=descending"
            f"&max_results={max_results}"
        )
        raw = _http_get(url, timeout=20)
        if not raw:
            self.logger.warning("arxiv API: 取得失敗")
            return []

        results: list[dict] = []
        try:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(raw)
            for entry in root.findall("atom:entry", ns):
                title   = (entry.findtext("atom:title",   "", ns) or "").strip().replace("\n", " ")
                summary = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")[:300]
                id_el   = entry.find("atom:id", ns)
                url_val = id_el.text.strip() if id_el is not None else ""
                sectors = _detect_sectors(f"{title} {summary}")
                results.append({
                    "source":      "arxiv",
                    "title":       title,
                    "summary_raw": summary[:200],
                    "sectors":     sectors,
                    "url":         url_val,
                })
        except ET.ParseError as e:
            self.logger.warning(f"arxiv XML 解析エラー: {e}")

        self.logger.info(f"arxiv: {len(results)} 件取得")
        return results

    def fetch_github(self) -> list[dict]:
        """GitHub 検索 API から直近 7 日に作成された高スター数リポジトリを取得する。"""
        since = (date.today() - timedelta(days=7)).isoformat()
        results: list[dict] = []

        for keyword in _GITHUB_KEYWORDS:
            q = urllib.parse.quote(f"{keyword} created:>{since}")
            url = (
                "https://api.github.com/search/repositories"
                f"?q={q}&sort=stars&order=desc&per_page=3"
            )
            raw = _http_get(url, timeout=10)
            if not raw:
                continue
            try:
                data = json.loads(raw)
                for item in (data.get("items") or []):
                    name  = item.get("full_name", "")
                    desc  = item.get("description") or ""
                    stars = item.get("stargazers_count", 0)
                    sectors = _detect_sectors(f"{name} {desc} {keyword}")
                    results.append({
                        "source":      "github",
                        "title":       f"{name}（★{stars:,}）",
                        "summary_raw": desc[:200],
                        "sectors":     sectors,
                        "url":         item.get("html_url", ""),
                        "stars":       stars,
                    })
            except (json.JSONDecodeError, KeyError):
                pass
            time.sleep(0.5)  # GitHub API レート制限対策

        self.logger.info(f"GitHub: {len(results)} 件取得")
        return results

    def fetch_hackernews(self, top_n: int = 30) -> list[dict]:
        """HackerNews 上位記事をセクターキーワードでフィルタリングして返す。"""
        raw = _http_get("https://hacker-news.firebaseio.com/v1/topstories.json", timeout=10)
        if not raw:
            self.logger.warning("HackerNews API: 取得失敗")
            return []

        try:
            story_ids: list[int] = json.loads(raw)[:top_n]
        except json.JSONDecodeError:
            return []

        results: list[dict] = []
        for sid in story_ids:
            item_raw = _http_get(
                f"https://hacker-news.firebaseio.com/v1/item/{sid}.json",
                timeout=8,
            )
            if not item_raw:
                continue
            try:
                item  = json.loads(item_raw)
                title = item.get("title", "")
                url_val = item.get("url") or f"https://news.ycombinator.com/item?id={sid}"
                hn_score = item.get("score", 0)
                sectors = _detect_sectors(title)
                if sectors != ["不明"]:  # キーワードマッチしたものだけ保持
                    results.append({
                        "source":      "hackernews",
                        "title":       title,
                        "summary_raw": f"HN スコア: {hn_score}",
                        "sectors":     sectors,
                        "url":         url_val,
                        "hn_score":    hn_score,
                    })
            except (json.JSONDecodeError, KeyError):
                pass

        self.logger.info(f"HackerNews: {len(results)} 件（フィルタ後）取得")
        return results

    # ──────────────────────────────────────────────
    # LLM スコアリング
    # ──────────────────────────────────────────────

    def _score_signals(self, raw_items: list[dict]) -> list[dict]:
        """LLM を使って relevance_score・supply_chain_position・summary を付与する。"""
        if not raw_items:
            return []

        # 1 回の LLM 呼び出しで全件処理（最大 40 件）
        batch = raw_items[:40]
        prompt = f"""
以下のテック系記事・リポジトリについて、投資シグナルとして分析し JSON 配列で返してください。

## 収集データ
{json.dumps(batch, ensure_ascii=False, indent=2)}

## 出力形式（JSON 配列のみ）
[
  {{
    "source": "arxiv | github | hackernews",
    "title": "タイトル（変更不可）",
    "relevance_score": 0.0〜1.0,
    "sectors": ["セクター名"],
    "supply_chain_position": "上流 | 中流 | 下流 | 不明",
    "summary": "100 文字以内の投資観点サマリー",
    "url": "URL（変更不可）"
  }}
]

スコア基準：
- 1.0: 査読済み論文 または 公式企業発表
- 0.8: 著名研究者のプレプリント・大手テック公開情報
- 0.7: GitHub スター急増（技術実装の証拠あり）
- 0.6: HackerNews 上位（業界内話題）
- 0.5 以下: 不明・PR 色が強い

supply_chain_position:
- 上流: 装置・材料・EDA・基礎研究
- 中流: チップ設計・製造・ウエハ加工
- 下流: 完成品・ソフトウェア・サービス
"""
        data = self._ask_llm_json(prompt)
        if isinstance(data, list):
            return data

        self.logger.warning("スコアリング LLM 失敗。生データをそのまま返します")
        return [
            {
                **item,
                "relevance_score":       0.5,
                "supply_chain_position": "不明",
                "summary":               item.get("summary_raw", "")[:100],
            }
            for item in batch
        ]

    # ──────────────────────────────────────────────
    # メインエントリポイント
    # ──────────────────────────────────────────────

    def collect(self, ctx: MarketContext | None = None) -> dict:
        """
        全ソースからシグナルを収集し、{"signals": [...]} 形式で返す。
        relevance_score < 0.6 のシグナルはドロップ済み。
        """
        self.logger.info("情報収集開始: arxiv / GitHub / HackerNews")

        raw_items: list[dict] = []
        raw_items.extend(self.fetch_arxiv())
        raw_items.extend(self.fetch_github())
        raw_items.extend(self.fetch_hackernews())

        self.logger.info(f"収集総件数: {len(raw_items)} 件 → LLM スコアリング中...")
        signals = self._score_signals(raw_items)

        # relevance_score 0.6 未満をドロップ
        signals = [s for s in signals if s.get("relevance_score", 0) >= 0.6]
        # スコア降順ソート
        signals.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        self.logger.info(f"有効シグナル数: {len(signals)} 件（score >= 0.6）")
        return {"signals": signals}

    def evaluate(self, signal: dict, ctx: MarketContext) -> dict:
        """議論オーケストレーター用：シグナルのセクター波及を評価する。"""
        prompt = f"""
## 評価対象シグナル
{json.dumps(signal, ensure_ascii=False, indent=2)}

## 現在の市場コンテキスト
- リスク水準: {ctx.risk_level}
- セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}

このシグナルについて、セクタースコアへの影響とサプライチェーン波及を評価し、
JSON で返してください。

{{
  "opinion": "賛成 | 反対 | 保留",
  "rationale": "根拠 100 文字以内",
  "suggested_action": "具体的な提案"
}}
"""
        data = self._ask_llm_json(prompt)
        return {
            "agent":            self.name,
            "opinion":          data.get("opinion", "保留"),
            "rationale":        data.get("rationale", ""),
            "suggested_action": data.get("suggested_action", ""),
        }
