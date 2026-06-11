"""
agents/base.py
全エージェントが継承する基底クラスと、エージェント間で受け渡す
共通メッセージ・スキーマを定義する。

設計方針:
  - エージェントは「入力→判断→出力」を行う純粋な関数的ユニット
  - 判断はすべて Claude API 経由（LLM）
  - 副作用（発注）はブローカー層に委譲し、エージェント自身は出力しない
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from anthropic import Anthropic
from config.settings import LLM

logger = logging.getLogger(__name__)
_client = Anthropic(api_key=LLM.api_key)


# ──────────────────────────────────────────────
# 共通データスキーマ
# ──────────────────────────────────────────────

@dataclass
class MarketContext:
    """CIOエージェントが生成し、下位エージェントに渡す市場コンテキスト。"""
    date: str                          # "2026-06-10"
    sector_scores: dict[str, float]    # {"AI半導体": 0.85, "宇宙インフラ": 0.72, ...}
    macro_notes: str                   # マクロ環境の要約テキスト
    rotation_signal: str               # "AI半導体→エネルギー", "維持" など
    risk_level: str                    # "low" | "medium" | "high"


@dataclass
class TradeProposal:
    """執行エージェントが生成し、クリティークに渡す取引提案。"""
    agent: str                         # 提案元エージェント名
    symbol: str
    market: str                        # "JP" | "US"
    side: str                          # "buy" | "sell"
    qty: int | float
    price: float                       # 0=成行
    strategy: str                      # "daytrade" | "swing" | "value"
    rationale: str                     # 判断根拠（LLMが生成した自然言語）
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    extra: dict = field(default_factory=dict)


@dataclass
class CriticVerdict:
    """クリティークエージェントの審査結果。"""
    approved: bool
    score: float                       # 0.0〜1.0 (確信度)
    issues: list[str]                  # 否決・修正が必要な理由
    suggestion: str                    # 修正案または「問題なし」
    fixable: bool = True               # False=修正しても無意味（市場時間外など）


# ──────────────────────────────────────────────
# 基底エージェントクラス
# ──────────────────────────────────────────────

class BaseAgent:
    """
    全エージェントの親クラス。
    LLMへの問い合わせ・ログ・JSON抽出を共通化する。
    """
    name: str = "BaseAgent"
    system_prompt: str = "あなたはプロの投資家です。"
    model: str | None = None  # None = LLM.model を使用。上位職はサブクラスで "claude-opus-4-8" 等に上書き

    def __init__(self):
        self.logger = logging.getLogger(self.name)

    def _ask_llm(self, user_message: str, extra_system: str = "") -> str:
        """
        Claude に問い合わせ、テキスト応答を返す。
        extra_system: システムプロンプトに追記する文字列（エージェント固有ルール）
        """
        system = self.system_prompt
        if extra_system:
            system = f"{system}\n\n{extra_system}"

        response = _client.messages.create(
            model=self.model or LLM.model,
            max_tokens=LLM.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        result = response.content[0].text
        self.logger.debug(f"LLM応答:\n{result[:300]}...")
        return result

    def _ask_llm_json(self, user_message: str, extra_system: str = "") -> dict | list:
        """
        JSONのみを返すよう指示してLLMに問い合わせ、パースして返す。
        LLMが reasoning など前置きJSONを出力した場合もリカバリーする。
        """
        json_instruction = "\n\n必ずJSON形式のみで回答してください。前置き・説明文・マークダウンのコードブロックは不要です。"
        raw = self._ask_llm(user_message, extra_system + json_instruction)
        # ```json ... ``` ブロックが混入した場合の除去
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        # まず完全文字列をパース
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # LLMが複数のJSON値を返した場合（例: reasoning dict + 結果 list）のリカバリー
        # 最初の完全なJSON値を取り出し、その後に続くJSON値も試みる
        decoder = json.JSONDecoder()
        try:
            first, end_pos = decoder.raw_decode(raw)
            remainder = raw[end_pos:].strip()
            # 最初がdictで残りに別のJSON値がある場合は残りを優先（期待値が後にある場合）
            if remainder and isinstance(first, dict):
                try:
                    return json.loads(remainder)
                except json.JSONDecodeError:
                    pass
            return first
        except json.JSONDecodeError:
            pass

        self.logger.error(f"JSON解析失敗（リカバリー不能）\n原文: {raw[:200]}")
        return {}

    def _log_proposal(self, proposal: TradeProposal):
        self.logger.info(
            f"[提案] {proposal.symbol} {proposal.side} x{proposal.qty} "
            f"@ {proposal.price or '成行'} | 理由: {proposal.rationale[:80]}"
        )

    def _log_verdict(self, verdict: CriticVerdict):
        status = "✅承認" if verdict.approved else "❌否決"
        self.logger.info(f"[審査] {status} score={verdict.score:.2f} | {verdict.suggestion}")
