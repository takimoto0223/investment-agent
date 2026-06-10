"""
prompts/loader.py

プロンプトテンプレートに config/settings.py の値を注入して返すユーティリティ。
ハードコードされた数値をプロンプトから排除し、settings.py を唯一の真実源にする。

使い方:
    from prompts.loader import get_prompt
    system_prompt = get_prompt("daytrade")
"""
from prompts.all_agents import PROMPTS
from config.settings import RISK


def get_prompt(agent_name: str) -> str:
    """
    エージェント名に対応するプロンプトを取得し、
    設定値のプレースホルダーを実際の数値に置換して返す。
    """
    template = PROMPTS.get(agent_name)
    if not template:
        raise ValueError(f"Unknown agent: {agent_name}. Available: {list(PROMPTS.keys())}")

    replacements = {
        "{MAX_CONCENTRATION_PCT}":    str(int(RISK.max_concentration_pct * 100)),
        "{MAX_DAYTRADE_MARGIN_JPY}":  f"{RISK.max_daytrade_margin_jpy:,}",
        "{MAX_LOSS_PER_DAY_JPY}":     f"{RISK.max_loss_per_day_jpy:,}",
        "{DAYTRADE_STOP_LOSS_PCT}":   str(int(RISK.daytrade_stop_loss_pct * 100)),
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result


def list_agents() -> list[str]:
    """利用可能なエージェント名の一覧を返す。"""
    return list(PROMPTS.keys())
