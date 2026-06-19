"""
agents/intelligence.py
情報収集エージェント：複数ソースからテック・マーケット系シグナルを収集し、
LLM でスコアリングする。

収集ソース:
  [論文・OSS]
    - arxiv          : CS/量子/神経工学系論文
    - bioRxiv        : バイオ×AI系論文
    - Semantic Scholar: 引用数ベースの重要論文
    - GitHub         : スター急増リポジトリ
  [マーケットニュース]
    - TechCrunch RSS : テック系M&A・製品発表
    - Reuters RSS    : 市場直結グローバルニュース
    - Google News RSS: 銘柄・セクター別最新ニュース
    - SEC EDGAR 8-K  : 米国上場企業の重要事実開示
  [センチメント・バズ]
    - StockTwits     : 株式専用SNSのBull/Bearセンチメント
    - Google Trends  : 検索量急増（バズ予兆）
    - Options Flow   : 異常オプション取引（yfinance）
"""
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from agents.base import BaseAgent, MarketContext
from prompts.loader import get_prompt


# ── セクターキーワード ──────────────────────────────────────────
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

# GitHub 検索クエリ（キーワード + stars:>50 で質を担保）
_GITHUB_QUERIES = [
    "llm inference accelerator stars:>50",
    "gpu memory bandwidth hbm stars:>30",
    "quantum computing hardware stars:>30",
    "silicon photonics chip stars:>20",
    "ai semiconductor chip design stars:>50",
    "data center cooling ai stars:>30",
    "small modular reactor smr stars:>20",
]

# デイトレ・バリュー対象ユニバースの主要銘柄（センチメント・ニュース収集用）
_WATCH_SYMBOLS = ["NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TSLA", "AMD", "AVGO", "ORCL", "JPM", "XOM"]

# arxiv カテゴリ
_ARXIV_QUERY = "cat:cs.ET+OR+cat:cs.AR+OR+cat:quant-ph+OR+cat:cs.NE+OR+cat:eess.SP"

# Google Trends 検索キーワード（銘柄名 + テック系）
_TRENDS_KEYWORDS = ["NVDA stock", "AI chip", "semiconductor shortage", "data center demand", "quantum computing"]


def _http_get(url: str, timeout: int = 12, headers: dict | None = None) -> str | None:
    """GET リクエストを送り、レスポンス文字列を返す。失敗時は None。"""
    try:
        h = {"User-Agent": "investment-agent/1.0 (research bot)"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
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


def _parse_rss(raw: str, source: str) -> list[dict]:
    """RSS/Atom XML をパースして共通フォーマットで返す。"""
    results = []
    try:
        root = ET.fromstring(raw)
        # RSS 2.0
        for item in root.findall(".//item"):
            title   = (item.findtext("title") or "").strip()
            summary = (item.findtext("description") or "").strip()[:300]
            url_val = (item.findtext("link") or "").strip()
            if title:
                results.append({
                    "source":      source,
                    "title":       title,
                    "summary_raw": summary,
                    "sectors":     _detect_sectors(f"{title} {summary}"),
                    "url":         url_val,
                })
        # Atom
        if not results:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("atom:entry", ns):
                title   = (entry.findtext("atom:title", "", ns) or "").strip()
                summary = (entry.findtext("atom:summary", "", ns) or "").strip()[:300]
                link_el = entry.find("atom:link", ns)
                url_val = link_el.get("href", "") if link_el is not None else ""
                if title:
                    results.append({
                        "source":      source,
                        "title":       title,
                        "summary_raw": summary,
                        "sectors":     _detect_sectors(f"{title} {summary}"),
                        "url":         url_val,
                    })
    except ET.ParseError:
        pass
    return results


class IntelligenceAgent(BaseAgent):
    name = "IntelligenceAgent"
    system_prompt = get_prompt("intelligence")

    # ══════════════════════════════════════════════
    # 論文・OSS ソース
    # ══════════════════════════════════════════════

    def fetch_arxiv(self, max_results: int = 20) -> list[dict]:
        """arxiv から最新論文を取得（cs.ET / cs.AR / quant-ph / cs.NE / eess.SP）。"""
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
                results.append({
                    "source":      "arxiv",
                    "title":       title,
                    "summary_raw": summary[:200],
                    "sectors":     _detect_sectors(f"{title} {summary}"),
                    "url":         url_val,
                })
        except ET.ParseError as e:
            self.logger.warning(f"arxiv XML 解析エラー: {e}")

        self.logger.info(f"arxiv: {len(results)} 件取得")
        return results

    def fetch_biorxiv(self, max_results: int = 10, since: "datetime | None" = None) -> list[dict]:
        """bioRxiv からバイオ×AI系プレプリントを取得。since 指定時はその日付以降のみ。"""
        since_date = since.date().isoformat() if since is not None else (date.today() - timedelta(days=7)).isoformat()
        until = date.today().isoformat()
        url = f"https://api.biorxiv.org/details/biorxiv/{since_date}/{until}/0/json"
        raw = _http_get(url, timeout=15)
        if not raw:
            self.logger.warning("bioRxiv API: 取得失敗")
            return []

        results = []
        try:
            data = json.loads(raw)
            for item in (data.get("collection") or [])[:max_results]:
                title   = item.get("title", "")
                abstract = item.get("abstract", "")[:300]
                doi     = item.get("doi", "")
                sectors = _detect_sectors(f"{title} {abstract}")
                if sectors != ["不明"]:  # キーワードマッチのみ
                    results.append({
                        "source":      "biorxiv",
                        "title":       title,
                        "summary_raw": abstract,
                        "sectors":     sectors,
                        "url":         f"https://doi.org/{doi}" if doi else "",
                    })
        except (json.JSONDecodeError, KeyError):
            pass

        self.logger.info(f"bioRxiv: {len(results)} 件取得")
        return results

    def fetch_semantic_scholar(self, max_results: int = 10) -> list[dict]:
        """Semantic Scholar から引用数の多い最新AI/半導体論文を取得。"""
        query = urllib.parse.quote("AI semiconductor LLM inference chip")
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={query}&limit={max_results}"
            "&fields=title,abstract,year,citationCount,externalIds"
            "&sort=citationCount"
        )
        raw = _http_get(url, timeout=15)
        if not raw:
            self.logger.warning("Semantic Scholar API: 取得失敗")
            return []

        results = []
        try:
            data = json.loads(raw)
            current_year = datetime.now().year
            for paper in (data.get("data") or []):
                title    = paper.get("title", "")
                abstract = (paper.get("abstract") or "")[:300]
                year     = paper.get("year") or 0
                cites    = paper.get("citationCount", 0)
                if year < current_year - 2:  # 直近2年以内
                    continue
                arxiv_id = (paper.get("externalIds") or {}).get("ArXiv", "")
                url_val  = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
                sectors  = _detect_sectors(f"{title} {abstract}")
                results.append({
                    "source":      "semantic_scholar",
                    "title":       f"{title}（引用{cites}件）",
                    "summary_raw": abstract,
                    "sectors":     sectors,
                    "url":         url_val,
                    "citations":   cites,
                })
        except (json.JSONDecodeError, KeyError):
            pass

        self.logger.info(f"Semantic Scholar: {len(results)} 件取得")
        return results

    def fetch_github(self) -> list[dict]:
        """GitHub 検索 API から直近 14 日に更新された高スターリポジトリを取得する。"""
        since = (date.today() - timedelta(days=14)).isoformat()
        results: list[dict] = []
        seen: set[str] = set()

        for query in _GITHUB_QUERIES:
            q = urllib.parse.quote(f"{query} pushed:>{since}")
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
                    if name in seen:
                        continue
                    seen.add(name)
                    desc  = item.get("description") or ""
                    stars = item.get("stargazers_count", 0)
                    results.append({
                        "source":      "github",
                        "title":       f"{name}（★{stars:,}）",
                        "summary_raw": desc[:200],
                        "sectors":     _detect_sectors(f"{name} {desc} {query}"),
                        "url":         item.get("html_url", ""),
                        "stars":       stars,
                    })
            except (json.JSONDecodeError, KeyError):
                pass
            time.sleep(0.5)

        self.logger.info(f"GitHub: {len(results)} 件取得")
        return results

    # ══════════════════════════════════════════════
    # マーケットニュース ソース
    # ══════════════════════════════════════════════

    def fetch_techcrunch_rss(self, max_items: int = 15) -> list[dict]:
        """TechCrunch RSS からテック系M&A・製品発表ニュースを取得。"""
        raw = _http_get("https://techcrunch.com/feed/", timeout=15)
        if not raw:
            self.logger.warning("TechCrunch RSS: 取得失敗")
            return []
        # ニュースソースはLLMに任せるためセクターフィルタなし
        results = _parse_rss(raw, "techcrunch")[:max_items]
        self.logger.info(f"TechCrunch: {len(results)} 件取得")
        return results

    def fetch_tech_news_rss(self, max_items: int = 15) -> list[dict]:
        """CNBC Tech / Ars Technica RSS からグローバルテックニュースを取得。"""
        sources = [
            ("https://www.cnbc.com/id/19854910/device/rss/rss.html", "cnbc_tech"),
            ("https://feeds.arstechnica.com/arstechnica/index",        "ars_technica"),
        ]
        results = []
        for url, source_name in sources:
            raw = _http_get(url, timeout=15)
            if not raw:
                self.logger.warning(f"{source_name} RSS: 取得失敗")
                continue
            items = _parse_rss(raw, source_name)[:max_items // 2]
            results.extend(items)
        self.logger.info(f"Tech News RSS (CNBC+ARS): {len(results)} 件取得")
        return results

    def fetch_google_news_rss(self) -> list[dict]:
        """Google News RSS から監視銘柄・セクターの最新ニュースを取得。"""
        queries = [
            "NVIDIA semiconductor AI chip",
            "semiconductor supply chain",
            "data center AI infrastructure",
        ]
        results = []
        for q in queries:
            encoded = urllib.parse.quote(q)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            raw = _http_get(url, timeout=15)
            if not raw:
                continue
            items = _parse_rss(raw, "google_news")[:5]
            results.extend(items)
            time.sleep(0.3)
        self.logger.info(f"Google News: {len(results)} 件取得")
        return results

    def fetch_sec_edgar(self, max_filings: int = 10, since: "datetime | None" = None) -> list[dict]:
        """SEC EDGAR から監視銘柄の8-K（重要事実開示）を取得。since 指定時はその日付以降のみ。"""
        _lookback_days = max((date.today() - since.date()).days + 1, 1) if since is not None else 7
        results = []
        for symbol in _WATCH_SYMBOLS[:6]:  # API負荷軽減のため上位6銘柄
            cik_url = f"https://data.sec.gov/submissions/CIK{_SYMBOL_TO_CIK.get(symbol, '')}.json"
            if not _SYMBOL_TO_CIK.get(symbol):
                continue
            raw = _http_get(cik_url, timeout=10)
            if not raw:
                continue
            try:
                data    = json.loads(raw)
                filings = data.get("filings", {}).get("recent", {})
                forms   = filings.get("form", [])
                dates   = filings.get("filingDate", [])
                descriptions = filings.get("primaryDocument", [])
                accessions   = filings.get("accessionNumber", [])
                for i, form in enumerate(forms):
                    if form != "8-K":
                        continue
                    filing_date = dates[i] if i < len(dates) else ""
                    try:
                        if (date.today() - date.fromisoformat(filing_date)).days > _lookback_days:
                            continue
                    except ValueError:
                        continue
                    acc = (accessions[i] if i < len(accessions) else "").replace("-", "")
                    url_val = f"https://www.sec.gov/Archives/edgar/data/{_SYMBOL_TO_CIK[symbol]}/{acc}/"
                    results.append({
                        "source":      "sec_edgar",
                        "title":       f"{symbol} 8-K ({filing_date}): 重要事実開示",
                        "summary_raw": f"{symbol}が{filing_date}にSEC 8-Kを提出。投資判断に直結する可能性あり。",
                        "sectors":     _detect_sectors(symbol),
                        "url":         url_val,
                        "symbol":      symbol,
                    })
                    if len(results) >= max_filings:
                        break
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
            time.sleep(0.3)

        self.logger.info(f"SEC EDGAR 8-K: {len(results)} 件取得")
        return results

    # ══════════════════════════════════════════════
    # センチメント・バズ ソース
    # ══════════════════════════════════════════════

    def fetch_stocktwits(self) -> list[dict]:
        """StockTwits の監視銘柄センチメント（Bull/Bear比率）を取得。"""
        results = []
        for symbol in _WATCH_SYMBOLS[:8]:  # API負荷軽減
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
            raw = _http_get(url, timeout=10)
            if not raw:
                continue
            try:
                data = json.loads(raw)
                messages = data.get("messages", [])
                if not messages:
                    continue
                bull = sum(1 for m in messages if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
                bear = sum(1 for m in messages if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")
                total = bull + bear
                if total == 0:
                    continue
                bull_pct = round(bull / total * 100)
                bear_pct = 100 - bull_pct
                # センチメント偏りが大きい場合のみシグナルとして追加
                if abs(bull_pct - 50) >= 15:
                    sentiment_label = "Bullish" if bull_pct > 50 else "Bearish"
                    results.append({
                        "source":      "stocktwits",
                        "title":       f"${symbol} {sentiment_label} {bull_pct}% / Bearish {bear_pct}% ({total}件)",
                        "summary_raw": f"{symbol}のStockTwitsセンチメント: Bull={bull_pct}% Bear={bear_pct}% (直近{total}件)",
                        "sectors":     _detect_sectors(symbol),
                        "url":         f"https://stocktwits.com/symbol/{symbol}",
                        "bull_pct":    bull_pct,
                        "bear_pct":    bear_pct,
                    })
            except (json.JSONDecodeError, KeyError):
                pass
            time.sleep(0.5)

        self.logger.info(f"StockTwits: {len(results)} 件取得")
        return results

    def fetch_google_trends(self) -> list[dict]:
        """Google Trends デイリートレンド RSS から急上昇ワードを取得。
        pytrends は urllib3 2.x と非互換のため RSS フィードを直接使用。"""
        raw = _http_get(
            "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US",
            timeout=15,
        )
        if not raw:
            self.logger.warning("Google Trends RSS: 取得失敗")
            return []

        results = []
        try:
            root = ET.fromstring(raw)
            for item in root.findall(".//item"):
                title       = (item.findtext("title") or "").strip()
                traffic_el  = item.find("{https://trends.google.com/trends/trendingsearches/daily}approx_traffic")
                traffic_str = traffic_el.text.strip() if traffic_el is not None else ""
                url_val     = (item.findtext("link") or "").strip()
                sectors     = _detect_sectors(title)
                # セクター関連 or 高トラフィック（100K+）のみ
                high_traffic = "100K" in traffic_str or "500K" in traffic_str or "1M" in traffic_str
                if sectors != ["不明"] or high_traffic:
                    results.append({
                        "source":      "google_trends",
                        "title":       f'急上昇ワード: "{title}"（{traffic_str}検索）',
                        "summary_raw": f"Googleトレンド急上昇: {title} トラフィック推定{traffic_str}",
                        "sectors":     sectors if sectors != ["不明"] else ["マーケット全般"],
                        "url":         url_val,
                        "traffic":     traffic_str,
                    })
        except ET.ParseError as e:
            self.logger.warning(f"Google Trends RSS 解析エラー: {e}")

        self.logger.info(f"Google Trends: {len(results)} 件取得")
        return results

    def fetch_options_flow(self) -> list[dict]:
        """yfinance で異常なオプション取引（大口の買い越し）を検出。"""
        try:
            import yfinance as yf
        except ImportError:
            self.logger.warning("options_flow: yfinance未インストール")
            return []

        results = []
        for symbol in _WATCH_SYMBOLS[:6]:
            try:
                ticker = yf.Ticker(symbol)
                expirations = ticker.options
                if not expirations:
                    continue
                # 直近の満期のみ確認
                opt = ticker.option_chain(expirations[0])
                calls = opt.calls
                puts  = opt.puts
                if calls.empty or puts.empty:
                    continue

                total_call_vol = int(calls["volume"].fillna(0).sum())
                total_put_vol  = int(puts["volume"].fillna(0).sum())
                if total_call_vol + total_put_vol == 0:
                    continue
                pc_ratio = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else 0

                # P/C比率が極端（コール偏重=強気 or プット偏重=弱気）な場合のみシグナル
                if pc_ratio < 0.5:
                    label = f"コール大幅偏重（P/C={pc_ratio}）→ 強気シグナル"
                elif pc_ratio > 2.0:
                    label = f"プット大幅偏重（P/C={pc_ratio}）→ 弱気/ヘッジシグナル"
                else:
                    continue

                results.append({
                    "source":      "options_flow",
                    "title":       f"{symbol} オプション異常: {label}",
                    "summary_raw": f"{symbol} call_vol={total_call_vol:,} put_vol={total_put_vol:,} P/C={pc_ratio}",
                    "sectors":     _detect_sectors(symbol),
                    "url":         f"https://finance.yahoo.com/quote/{symbol}/options",
                    "pc_ratio":    pc_ratio,
                })
            except Exception:
                pass
            time.sleep(0.3)

        self.logger.info(f"Options Flow: {len(results)} 件取得")
        return results

    # ══════════════════════════════════════════════
    # LLM スコアリング
    # ══════════════════════════════════════════════

    def _score_signals(self, raw_items: list[dict]) -> list[dict]:
        """LLM で relevance_score・supply_chain_position・summary を付与する。"""
        if not raw_items:
            return []

        batch = raw_items[:40]
        prompt = f"""
以下のテック系記事・リポジトリ・センチメント情報について、投資シグナルとして分析し JSON 配列で返してください。

## 収集データ
{json.dumps(batch, ensure_ascii=False, indent=2)}

## 出力形式（JSON 配列のみ）
[
  {{
    "source": "ソース名（変更不可）",
    "title": "タイトル（変更不可）",
    "relevance_score": 0.0〜1.0,
    "sectors": ["セクター名"],
    "supply_chain_position": "上流 | 中流 | 下流 | 不明",
    "summary": "100 文字以内の投資観点サマリー",
    "url": "URL（変更不可）"
  }}
]

スコア基準：
- 0.95: SEC 8-K（企業の重要事実開示）
- 0.85〜0.90: 査読済み論文・公式企業発表・Reuters
- 0.75〜0.80: 著名研究者プレプリント・TechCrunch M&A報道・Options Flow異常
- 0.65〜0.70: GitHub スター急増・Google Trends急増・StockTwits偏重センチメント
- 0.60: HackerNews相当の業界話題
- 0.5 以下: PR色強い・信頼性低い

supply_chain_position:
- 上流: 材料・装置・EDA・基礎研究
- 中流: チップ設計・製造・ウエハ加工
- 下流: 完成品・クラウド・SaaS・エンドユーザー
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

    # ══════════════════════════════════════════════
    # メインエントリポイント
    # ══════════════════════════════════════════════

    # ══════════════════════════════════════════════
    # 処理済みURLキャッシュ（重複LLMスコアリング防止）
    # ══════════════════════════════════════════════

    _SEEN_PATH = "logs/intel_seen.json"
    _SEEN_TTL_DAYS = 7  # 7日で期限切れ

    def _load_seen(self) -> dict[str, str]:
        """処理済みURL → 処理日付 のマップを読み込む。期限切れは除外。"""
        from pathlib import Path
        path = Path(self._SEEN_PATH)
        if not path.exists():
            return {}
        try:
            raw: dict = json.loads(path.read_text(encoding="utf-8"))
            cutoff = (date.today() - timedelta(days=self._SEEN_TTL_DAYS)).isoformat()
            return {url: d for url, d in raw.items() if d >= cutoff}
        except Exception:
            return {}

    def _save_seen(self, seen: dict[str, str], new_urls: set[str]) -> None:
        """新規URLを追加して保存（期限切れを自動プルーン）。"""
        from pathlib import Path
        today = date.today().isoformat()
        for url in new_urls:
            if url:
                seen[url] = today
        cutoff = (date.today() - timedelta(days=self._SEEN_TTL_DAYS)).isoformat()
        pruned = {url: d for url, d in seen.items() if d >= cutoff}
        path = Path(self._SEEN_PATH)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(pruned, ensure_ascii=False), encoding="utf-8")

    def collect(self, ctx: MarketContext | None = None, since: "datetime | None" = None) -> dict:
        """
        全ソースからシグナルを収集し、{"signals": [...]} 形式で返す。
        since: この時刻以降の差分のみ収集（None なら URL dedup のみで制御）。
        処理済みURLはキャッシュ（logs/intel_seen.json）でスキップし、新着のみLLMスコアリング。
        """
        self.logger.info(
            f"情報収集開始: 論文/OSS/ニュース/センチメント"
            + (f" (since={since.isoformat()})" if since else "")
        )

        raw_items: list[dict] = []

        # 論文・OSS
        raw_items.extend(self.fetch_arxiv())
        raw_items.extend(self.fetch_biorxiv(since=since))
        raw_items.extend(self.fetch_github())

        # マーケットニュース
        raw_items.extend(self.fetch_techcrunch_rss())
        raw_items.extend(self.fetch_tech_news_rss())
        raw_items.extend(self.fetch_google_news_rss())
        raw_items.extend(self.fetch_sec_edgar(since=since))

        # センチメント・バズ（リアルタイムデータ — 常に新規扱い）
        realtime_items: list[dict] = []
        realtime_items.extend(self.fetch_stocktwits())
        realtime_items.extend(self.fetch_options_flow())

        self.logger.info(f"収集総件数: {len(raw_items)} 件（＋リアルタイム {len(realtime_items)} 件）")

        # ── 重複排除：処理済みURLをスキップ ──
        seen = self._load_seen()
        new_items = [item for item in raw_items if item.get("url", "") not in seen]
        skipped = len(raw_items) - len(new_items)
        if skipped:
            self.logger.info(f"既処理スキップ: {skipped} 件 → 新着 {len(new_items)} 件のみスコアリング")

        # リアルタイム系は毎回スコアリング（センチメントは変動する）
        to_score = new_items + realtime_items
        if not to_score:
            self.logger.info("新着シグナルなし。スコアリングをスキップ")
            return {"signals": []}

        self.logger.info(f"LLM スコアリング対象: {len(to_score)} 件...")
        signals = self._score_signals(to_score)

        # 処理済みURLを保存（リアルタイム系は除外）
        new_urls = {item.get("url", "") for item in new_items}
        self._save_seen(seen, new_urls)

        signals = [s for s in signals if s.get("relevance_score", 0) >= 0.6]
        signals.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        self.logger.info(f"有効シグナル数: {len(signals)} 件（score >= 0.6）")
        return {"signals": signals}

    def generate_rollup(
        self,
        signals: list[dict],
        date_str: str,
        window: str = "",
        existing_windows: list[str] | None = None,
    ) -> dict:
        """
        収集シグナルから日次ダイジェストを LLM で生成する。
        signals: IntelCritic 承認済みシグナルリスト
        date_str: "2026-06-19"
        window: 現在の収集窓ラベル "08:05"
        existing_windows: 当日の過去収集窓（朝→夕の引き継ぎ用）
        """
        from zoneinfo import ZoneInfo
        now_str  = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()
        windows  = list(dict.fromkeys((existing_windows or []) + ([window] if window else [])))

        if not signals:
            return {
                "date":              date_str,
                "generated_at":      now_str,
                "windows":           windows,
                "signal_count":      0,
                "top_signals":       [],
                "sector_highlights": {},
                "macro_summary":     "収集シグナルなし",
                "digest_text":       "",
            }

        top5 = sorted(signals, key=lambda x: x.get("relevance_score", 0), reverse=True)[:5]
        top_signals_out = [
            {
                "title":   s.get("title", ""),
                "score":   round(s.get("relevance_score", 0), 2),
                "sectors": s.get("sectors", []),
                "summary": s.get("summary", ""),
            }
            for s in top5
        ]

        prompt = f"""
以下の承認済み投資シグナル {len(signals)} 件を要約し、当日のダイジェストを JSON で返してください。

## 入力シグナル（上位30件）
{json.dumps(signals[:30], ensure_ascii=False, indent=2)}

## 出力形式（JSON のみ）
{{
  "sector_highlights": {{
    "AI半導体": "動向サマリー（60文字以内）",
    "半導体装置・材料": "動向サマリー（60文字以内）"
  }},
  "macro_summary": "マクロ環境の要約（100文字以内）",
  "digest_text": "各ポッドのCIOコンテキストに渡す投資材料サマリー（200文字以内）"
}}

シグナルに関連するセクターのみ sector_highlights に含めてください。
"""
        data = self._ask_llm_json(prompt)
        if not data:
            self.logger.warning("generate_rollup: LLM 失敗。シグナルからフォールバック生成")
            sectors: dict[str, list[str]] = {}
            for s in signals:
                for sec in s.get("sectors", []):
                    sectors.setdefault(sec, []).append(s.get("title", "")[:30])
            sector_hl  = {sec: f"{len(ts)}件: " + "、".join(ts[:2]) for sec, ts in sectors.items()}
            digest_txt = f"シグナル{len(signals)}件。" + "、".join(
                f"{sec}{len(t)}件" for sec, t in list(sectors.items())[:3]
            )
            data = {
                "sector_highlights": sector_hl,
                "macro_summary":     "LLM 失敗によりマクロ分析不可",
                "digest_text":       digest_txt,
            }

        return {
            "date":              date_str,
            "generated_at":      now_str,
            "windows":           windows,
            "signal_count":      len(signals),
            "top_signals":       top_signals_out,
            "sector_highlights": data.get("sector_highlights", {}),
            "macro_summary":     data.get("macro_summary", ""),
            "digest_text":       data.get("digest_text", ""),
        }



# ── SEC EDGAR 銘柄→CIK マッピング ─────────────────────────────
_SYMBOL_TO_CIK: dict[str, str] = {
    "NVDA":  "0001045810",
    "AAPL":  "0000320193",
    "MSFT":  "0000789019",
    "META":  "0001326801",
    "AMZN":  "0001018724",
    "GOOGL": "0001652044",
    "TSLA":  "0001318605",
    "AMD":   "0000002488",
    "AVGO":  "0001730168",
    "ORCL":  "0001341439",
    "JPM":   "0000019617",
    "XOM":   "0000034088",
}
