"""
agents/cio.py
CIOエージェント：マクロ環境とセクタートレンドを分析し、
全エージェントに共有する MarketContext を生成する。

実行タイミング：毎朝 8:00（日本株市場開始前）、週次レビュー時
"""
import json
from datetime import date
from agents.base import BaseAgent, MarketContext
from config.settings import RISK


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

    def generate_market_context(self, news_summary: str = "", macro_data: str = "") -> MarketContext:
        """
        ニュースサマリーとマクロデータを入力に MarketContext を生成する。
        news_summary: 当日の主要ニュース要約（data/market.py が生成）
        macro_data:   米金利・為替・VIX などの数値サマリー
        """
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
        )
        self.logger.info(f"MarketContext生成完了: risk={ctx.risk_level}, rotation={ctx.rotation_signal}")
        return ctx

    def evaluate(self, signal: dict, ctx: MarketContext) -> dict:
        """
        議論オーケストレーター用：シグナルがセクタースコアに与える影響を評価する。
        """
        prompt = f"""
## 評価対象シグナル
{json.dumps(signal, ensure_ascii=False, indent=2)}

## 現在のセクタースコア
{json.dumps(ctx.sector_scores, ensure_ascii=False)}

## 市場コンテキスト
- ローテーション: {ctx.rotation_signal}
- リスク水準: {ctx.risk_level}

CIO の視点からこのシグナルを評価してください。
セクタースコアへの影響・サプライチェーン連動・ローテーション方向性を考慮し JSON で返してください。

{{
  "opinion": "賛成 | 反対 | 保留",
  "rationale": "根拠 100 文字以内（セクタースコア・サプライチェーン波及を含める）",
  "suggested_action": "具体的な提案（スコア調整・セクター優先度変更等）"
}}
"""
        data = self._ask_llm_json(prompt)
        return {
            "agent":            self.name,
            "opinion":          data.get("opinion", "保留"),
            "rationale":        data.get("rationale", ""),
            "suggested_action": data.get("suggested_action", ""),
        }

    def evaluate_portfolio_rotation(self, current_holdings: list[dict], ctx: MarketContext) -> str:
        """
        現在の保有銘柄とコンテキストを照らし合わせ、リバランス提案を自然言語で返す。
        current_holdings: [{"symbol": "7203", "sector": "自動車", "weight": 0.15}, ...]
        """
        prompt = f"""
## 現在のポートフォリオ
{json.dumps(current_holdings, ensure_ascii=False, indent=2)}

## 市場コンテキスト
- セクタースコア: {json.dumps(ctx.sector_scores, ensure_ascii=False)}
- ローテーションシグナル: {ctx.rotation_signal}
- リスク水準: {ctx.risk_level}

## リスク制約
- 1銘柄の最大比率: {RISK.max_concentration_pct * 100:.0f}%

上記を踏まえ、ポートフォリオのリバランス提案を箇条書きで3点以内で述べてください。
具体的な銘柄コードか銘柄名を含めてください。
"""
        return self._ask_llm(prompt)
