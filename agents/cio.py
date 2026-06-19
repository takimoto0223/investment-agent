"""
agents/cio.py
CIOエージェント：マクロ環境とセクタートレンドを分析し、
全エージェントに共有する MarketContext を生成する。

実行タイミング：毎朝 8:00（日本株市場開始前）、週次レビュー時
"""
import json
import time
from datetime import date
from pathlib import Path
from agents.base import BaseAgent, MarketContext, Allocation
from config.settings import RISK

_CTX_CACHE_PATH = Path("logs/market_context_cache.json")
_CTX_CACHE_TTL  = 3 * 3600  # 3時間（秒）

# ポッド別リスク係数テーブル。将来はCIOがコンテキスト次第で動的に変えることも想定。
# 一律値だが "高ボラポッドは medium でも絞る" 等の個別調整がここで可能。
_POD_RISK_FACTORS: dict[str, dict[str, float]] = {
    "ScalpDay_JP":    {"low": 1.0, "medium": 0.5, "high": 0.0},
    "ScalpDay_US":    {"low": 1.0, "medium": 0.5, "high": 0.0},
    "MomentSwing_JP": {"low": 1.0, "medium": 0.5, "high": 0.0},
    "MomentSwing_US": {"low": 1.0, "medium": 0.5, "high": 0.0},
    "FXRebalance":    {"low": 1.0, "medium": 0.5, "high": 0.0},
}


SYSTEM_PROMPT = """
あなたはプロの機関投資家・CIO（最高投資責任者）です。
以下の専門領域に深い知見を持ちます：
- AI・データセンターインフラ（GPU半導体・HBM・電力）
- 宇宙インフラ（衛星通信・ロケット・宇宙サービス）
- 半導体サプライチェーン全体（前工程・後工程・装置・材料）
- 日米のマクロ経済（金利・為替・景気サイクル）
- セクターローテーション（グロース⇔バリュー、景気敏感⇔ディフェンシブ）

あなたの役割：
1. 現在の市場環境からセクターごとの強弱スコア（0.0〜1.0）を算出する
2. セクターローテーションのシグナルを日本語で簡潔に示す
3. リスク水準を low / medium / high で評価する
4. 下位エージェント（現物・デイトレ）が参照するマクロノートを作成する

制約：
- 根拠のない楽観論は禁止。不確実な場合は "medium" リスクを維持する
- セクタースコアは毎日変動させず、週次ベースで更新する
"""


class CIOAgent(BaseAgent):
    name = "CIOAgent"
    system_prompt = SYSTEM_PROMPT
    model = "claude-opus-4-8"  # 全エージェントの羅針盤となる上位職

    # ── MarketContext ファイルキャッシュ（3時間 TTL） ──────────────────
    def _load_ctx_cache(self) -> MarketContext | None:
        """当日分のキャッシュが3時間以内に生成されていれば返す。"""
        try:
            if not _CTX_CACHE_PATH.exists():
                return None
            raw = json.loads(_CTX_CACHE_PATH.read_text(encoding="utf-8"))
            if raw.get("date") != date.today().isoformat():
                return None
            if time.time() - raw.get("saved_at", 0) > _CTX_CACHE_TTL:
                return None
            self.logger.info("MarketContext キャッシュヒット（3時間以内）")
            return MarketContext(
                date=raw["date"],
                sector_scores=raw["sector_scores"],
                macro_notes=raw["macro_notes"],
                rotation_signal=raw["rotation_signal"],
                risk_level=raw["risk_level"],
                obs_generated_by=raw.get("obs_generated_by", "CIO"),
            )
        except Exception:
            return None

    def _save_ctx_cache(self, ctx: MarketContext) -> None:
        """MarketContext をファイルキャッシュに保存する。"""
        try:
            _CTX_CACHE_PATH.parent.mkdir(exist_ok=True)
            payload = {
                "date":              ctx.date,
                "sector_scores":     ctx.sector_scores,
                "macro_notes":       ctx.macro_notes,
                "rotation_signal":   ctx.rotation_signal,
                "risk_level":        ctx.risk_level,
                "obs_generated_by":  ctx.obs_generated_by,
                "saved_at":          time.time(),
            }
            _CTX_CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            self.logger.warning(f"MarketContext キャッシュ保存失敗: {e}")

    def generate_market_context(self, news_summary: str = "", macro_data: str = "", obs_source: str = "CIO") -> MarketContext:
        """
        ニュースサマリーとマクロデータを入力に MarketContext を生成する。
        3時間以内に同日付のキャッシュがあれば LLM 呼び出しをスキップする。
        news_summary: 当日の主要ニュース要約（data/market.py が生成）
        macro_data:   米金利・為替・VIX などの数値サマリー
        """
        cached = self._load_ctx_cache()
        if cached is not None:
            return cached

        prompt = f"""
本日 {date.today().isoformat()} の投資環境を分析し、以下の JSON を出力してください。

## 入力情報
### ニュースサマリー
{news_summary or "（未取得）"}

### マクロデータ
{macro_data or "（未取得）"}

## 出力形式（JSONのみ）
{{
  "sector_scores": {{
    "AI半導体": 0.0〜1.0,
    "データセンターインフラ": 0.0〜1.0,
    "宇宙インフラ": 0.0〜1.0,
    "半導体装置・材料": 0.0〜1.0,
    "エネルギー": 0.0〜1.0,
    "金融": 0.0〜1.0,
    "ディフェンシブ": 0.0〜1.0
  }},
  "macro_notes": "100文字以内のマクロ環境要約",
  "rotation_signal": "例：AI半導体→エネルギーへ資金移動の兆候あり",
  "risk_level": "low | medium | high"
}}
"""
        data = self._ask_llm_json(prompt)
        if not data:
            # LLM失敗時はデフォルト値で継続（システムを止めない）
            self.logger.warning("CIO: LLM応答失敗。デフォルトコンテキストを使用")
            data = {
                "sector_scores": {s: 0.5 for s in ["AI半導体", "データセンターインフラ", "宇宙インフラ", "半導体装置・材料"]},
                "macro_notes": "データ取得失敗のためデフォルト値",
                "rotation_signal": "維持",
                "risk_level": "medium",
            }

        ctx = MarketContext(
            date=date.today().isoformat(),
            sector_scores=data.get("sector_scores", {}),
            macro_notes=data.get("macro_notes", ""),
            rotation_signal=data.get("rotation_signal", "維持"),
            risk_level=data.get("risk_level", "medium"),
            obs_generated_by=obs_source,
        )
        self.logger.info(f"MarketContext生成完了: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")
        self._save_ctx_cache(ctx)
        return ctx

    def allocate_budgets(
        self,
        ctx: MarketContext,
        total_cash_jpy: float = 0.0,
        cash_usd: float = 0.0,
        usd_jpy_rate: float = 155.0,
        # ↑ 必ず FXStrategyAgent.generate_signal() の戻り値から渡すこと。
        #   CIO は独自レートを計算しない（FX判断の一元化）。
        #   JP のみのセッション（cash_usd=0）ではレートは結果に影響しないため省略可。
        pod_risk_factors: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, Allocation]:
        """
        各ポッドへの資金枠・活性セクター・カタリスト例外枠を算出して返す。

        risk_level に応じた係数（_POD_RISK_FACTORS でポッド別に管理）:
          "low"    → 100%（制限なし）
          "medium" → 50%（半分に縮小）
          "high"   → 0%（ゲート遮断。旧 RiskManagerAgent の拒否権を吸収）

        全体エクスポージャ上限:
          - JPY 系ポッド合計 = total_cash_jpy × 80%（ポッド比率の合計値で担保）
          - USD 系ポッド合計 = cash_usd × 80%（同上）
          - 円ドル換算は usd_jpy_rate で行うが、ゲート判定（budget == 0 チェック）には
            レートの精度は影響しない。レートは将来の合計露出チェック拡張用に保持。

        カタリスト例外枠（catalyst_slots）:
          活性セクター外でも出来高・値動きが異常な銘柄をこの枠数だけ許可する。
          実行エージェント側がこの値を参照してユニバースフィルタを緩める。
        """
        risk    = ctx.risk_level
        factors = pod_risk_factors or _POD_RISK_FACTORS

        # スコア 0.6 以上のセクターを活性セクターとして上位 3 件抽出
        active_sectors = [
            s for s, sc in sorted(ctx.sector_scores.items(), key=lambda x: -x[1])
            if sc >= 0.6
        ][:3]

        # ポッド別割当比率（JPY 系合計 80%、USD 系合計 80% ＝ 総量エクスポージャ上限）
        jpy_pods = {"ScalpDay_JP": 0.30, "MomentSwing_JP": 0.30, "FXRebalance": 0.20}
        usd_pods = {"ScalpDay_US": 0.50, "MomentSwing_US": 0.30}

        allocs: dict[str, Allocation] = {}
        for pod, ratio in jpy_pods.items():
            f = factors.get(pod, {"low": 1.0, "medium": 0.5, "high": 0.0}).get(risk, 0.5)
            allocs[pod] = Allocation(
                budget_jpy=total_cash_jpy * ratio * f,
                budget_usd=0.0,
                active_sectors=active_sectors if pod != "FXRebalance" else [],
                catalyst_slots=1 if pod != "FXRebalance" else 0,
            )
        for pod, ratio in usd_pods.items():
            f = factors.get(pod, {"low": 1.0, "medium": 0.5, "high": 0.0}).get(risk, 0.5)
            allocs[pod] = Allocation(
                budget_jpy=0.0,
                budget_usd=cash_usd * ratio * f,
                active_sectors=active_sectors,
                catalyst_slots=1,
            )

        self.logger.info(
            f"allocate_budgets: risk={risk} usd_jpy_rate={usd_jpy_rate} "
            f"active={active_sectors} | "
            + ", ".join(
                f"{k}=¥{v.budget_jpy:,.0f}/${v.budget_usd:,.0f}(catalyst={v.catalyst_slots})"
                for k, v in allocs.items()
            )
        )
        return allocs
