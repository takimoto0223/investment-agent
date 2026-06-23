"""
config/settings.py
全エージェント共通の設定・定数を管理する。
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


# kabuポート対応: 18080=本番(live) / 18081=検証(test)
_KABU_PORTS = {"live": 18080, "test": 18081}


def _kabu_base_url() -> str:
    # KABU_BASE_URL が明示されていれば最優先（個別上書き用）
    explicit = os.getenv("KABU_BASE_URL")
    if explicit:
        return explicit
    port = _KABU_PORTS.get(os.getenv("KABU_ENV", "test"), 18081)
    return f"http://localhost:{port}/kabusapi"


def _kabu_ws_url() -> str:
    port = _KABU_PORTS.get(os.getenv("KABU_ENV", "test"), 18081)
    return f"ws://localhost:{port}/kabusapi/websocket"


@dataclass
class RiskLimits:
    """リスク上限。エージェント全体で参照する唯一の真実源。"""
    max_position_jpy: int = int(os.getenv("MAX_POSITION_SIZE_JPY", 500_000))
    max_daytrade_margin_jpy: int = int(os.getenv("MAX_DAYTRADE_MARGIN_JPY", 300_000))
    max_loss_per_day_jpy: int = int(os.getenv("MAX_LOSS_PER_DAY_JPY", 50_000))
    max_us_position_usd: int = int(os.getenv("MAX_US_POSITION_USD", 3_000))
    max_concentration_pct: float = 0.20   # 1銘柄がポートフォリオの20%を超えない
    daytrade_stop_loss_pct: float = 0.02  # 建玉に対し2%逆行したら強制決済
    usd_jpy_rate: float = float(os.getenv("USD_JPY_RATE", 155.0))  # 大口判定の円換算レート


@dataclass
class KabuConfig:
    """kabu STATION API 設定。

    KABU_ENV で検証(test)/本番(live)を切り替える。
    デフォルトを test にする理由: 未設定・誤設定時の実弾発注事故を防ぐ。
    """
    # ポート: 18080=本番(live) / 18081=検証(test)
    env: str = field(default_factory=lambda: os.getenv("KABU_ENV", "test"))
    base_url: str = field(default_factory=_kabu_base_url)
    ws_url: str = field(default_factory=_kabu_ws_url)
    password: str = field(default_factory=lambda: os.getenv("KABU_API_PASSWORD", ""))
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
