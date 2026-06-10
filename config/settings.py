"""
config/settings.py
全エージェント共通の設定・定数を管理する。
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class RiskLimits:
    """リスク上限。エージェント全体で参照する唯一の真実源。"""
    max_position_jpy: int = int(os.getenv("MAX_POSITION_SIZE_JPY", 500_000))
    max_daytrade_margin_jpy: int = int(os.getenv("MAX_DAYTRADE_MARGIN_JPY", 300_000))
    max_loss_per_day_jpy: int = int(os.getenv("MAX_LOSS_PER_DAY_JPY", 50_000))
    max_us_position_usd: int = int(os.getenv("MAX_US_POSITION_USD", 3_000))
    max_concentration_pct: float = 0.20   # 1銘柄がポートフォリオの20%を超えない
    daytrade_stop_loss_pct: float = 0.02  # 建玉に対し2%逆行したら強制決済


@dataclass
class KabuConfig:
    """kabu STATION API 設定。"""
    base_url: str = os.getenv("KABU_BASE_URL", "http://localhost:18080/kabusapi")
    password: str = os.getenv("KABU_API_PASSWORD", "")
    # kabuステーションはローカル起動前提のため、接続タイムアウトを短めに
    timeout_sec: int = 5


@dataclass
class AlpacaConfig:
    """Alpaca API 設定。"""
    api_key: str = os.getenv("ALPACA_API_KEY", "")
    secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    base_url: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")


@dataclass
class LLMConfig:
    """Claude API 設定。"""
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 2048


# シングルトン的に使うグローバルインスタンス
RISK = RiskLimits()
KABU = KabuConfig()
ALPACA = AlpacaConfig()
LLM = LLMConfig()
