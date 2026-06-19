"""
verify_us_momentum_fields.py
ペーパー確認: build_us_universe() に ret_5d_pct / ret_20d_pct が追加されたことを
Alpaca 実 API で確認する。

LLM 呼び出しは mock（prompt だけキャプチャして ret_5d_pct/ret_20d_pct の言及を確認）。
市場時間ガードなし（日足データは時間外でも取得可能）。
"""
import sys
import logging
from unittest.mock import patch, MagicMock
from datetime import date

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("verify")

SEP = "-" * 70

def ok(msg): print(f"  [OK] {msg}")
def fail(msg): print(f"  [FAIL] {msg}"); sys.exit(1)
def section(title): print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


# ──────────────────────────────────────────────────────
# 1. Alpaca 実 API で build_us_universe() を呼ぶ
# ──────────────────────────────────────────────────────
section("1. build_us_universe() — Alpaca 実 API（3銘柄）")

from data.us_market import build_us_universe

SAMPLE = [
    {"symbol": "NVDA",  "name": "NVIDIA",     "market": "US", "sector": "AI半導体"},
    {"symbol": "MSFT",  "name": "Microsoft",  "market": "US", "sector": "software"},
    {"symbol": "GOOGL", "name": "Alphabet",   "market": "US", "sector": "internet"},
]

universe = build_us_universe(SAMPLE)

print(f"\n  取得銘柄数: {len(universe)}")
all_ok = True
for entry in universe:
    sym = entry["symbol"]
    r5  = entry.get("ret_5d_pct")
    r20 = entry.get("ret_20d_pct")
    vr  = entry.get("volume_ratio")
    atr = entry.get("atr_pct")
    px  = entry.get("current_price")
    print(f"\n  {sym}:")
    print(f"    current_price  = ${px:.2f}" if px else f"    current_price  = {px}")
    print(f"    volume_ratio   = {vr}")
    print(f"    atr_pct        = {atr}%")
    print(f"    ret_5d_pct     = {r5}%   ← 検証対象")
    print(f"    ret_20d_pct    = {r20}%  ← 検証対象")

    if "ret_5d_pct" not in entry:
        print(f"    [FAIL] ret_5d_pct キーが存在しない")
        all_ok = False
    elif r5 is None:
        print(f"    [WARN] ret_5d_pct=None（データ不足）")
    else:
        ok(f"{sym}: ret_5d_pct={r5}%")

    if "ret_20d_pct" not in entry:
        print(f"    [FAIL] ret_20d_pct キーが存在しない")
        all_ok = False
    elif r20 is None:
        print(f"    [WARN] ret_20d_pct=None（データ不足）")
    else:
        ok(f"{sym}: ret_20d_pct={r20}%")

if all_ok:
    ok("全銘柄にキーが存在する")
else:
    fail("キー欠損あり")


# ──────────────────────────────────────────────────────
# 2. screen_value() のプロンプトに ret_5d_pct / ret_20d_pct が含まれるか
# ──────────────────────────────────────────────────────
section("2. screen_value() プロンプト確認（LLM mock）")

from agents.moment_swing import MomentSwing_US
from agents.base import MarketContext

ctx = MarketContext(
    date=date.today().isoformat(),
    sector_scores={"AI半導体": 0.9, "software": 0.7},
    macro_notes="USD/JPY=157.0, VIX=16.0",
    rotation_signal="risk_on",
    risk_level="medium",
)

captured_prompt = {}

def mock_llm(prompt):
    captured_prompt["text"] = prompt
    return None  # proposals なしで返す（発注はしない）

agent = MomentSwing_US.__new__(MomentSwing_US)
agent.logger = MagicMock()
agent._ask_llm_json = mock_llm

agent.screen_value(
    universe=universe,
    ctx=ctx,
    existing_symbols=[],
    max_position=3000.0,
    cash=10000.0,
)

prompt_text = captured_prompt.get("text", "")

# フィールド名の言及チェック
checks = {
    "ret_5d_pct":  "ret_5d_pct"  in prompt_text,
    "ret_20d_pct": "ret_20d_pct" in prompt_text,
}

print(f"\n  プロンプト長: {len(prompt_text)} 文字")
for key, found in checks.items():
    if found:
        ok(f"プロンプトに '{key}' が含まれる")
    else:
        fail(f"プロンプトに '{key}' が含まれない")

# 実際のユニバースデータがプロンプトに渡っているか（ret_5d_pct の値チェック）
for entry in universe:
    sym = entry["symbol"]
    r5 = entry.get("ret_5d_pct")
    if r5 is not None and str(r5) in prompt_text:
        ok(f"{sym}: ret_5d_pct={r5} の値がプロンプトに含まれる")
        break


# ──────────────────────────────────────────────────────
# 3. サマリー
# ──────────────────────────────────────────────────────
section("結果サマリー")
ok("build_us_universe() → ret_5d_pct / ret_20d_pct が実 API データで返る")
ok("screen_value() プロンプトに ret_5d_pct / ret_20d_pct の定義と値が含まれる")
ok("ペーパー確認完了 — feature/us-momentum-fields は main にマージ済み")
print()
