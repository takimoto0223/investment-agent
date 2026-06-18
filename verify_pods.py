"""
verify_pods.py
5ポッド(ScalpDay_JP / ScalpDay_US / MomentSwing_JP / MomentSwing_US / FXRebalance)
+ CIO allocate_budgets の通し確認スクリプト。

LLM呼び出しは現実的なモックデータで代替し、ブローカー接続は不要。
目的: 各ポッドが正しい市場・通貨・戦略フィールドで提案を出せるかを確認する。
"""
import sys
import logging
from unittest.mock import MagicMock, patch

# Windows コンソールの文字化け対策
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# ── ロギング（コンソールのみ） ────────────────────────
logging.basicConfig(
    level=logging.WARNING,  # ノイズを抑制
    format="%(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# ── 共通コンテキスト ──────────────────────────────────
from agents.base import MarketContext
from agents.cio import CIOAgent

CTX = MarketContext(
    date="2026-06-19",
    sector_scores={"semiconductors": 0.85, "technology": 0.78, "financials": 0.62, "healthcare": 0.55},
    macro_notes="USD/JPY=155.2, VIX=17.8, 米10Y=4.35%",
    rotation_signal="risk_on",
    risk_level="medium",
)

TOTAL_JPY = 1_000_000
CASH_USD  = 10_000
USD_JPY   = 155.2

SEP = "-" * 60


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def warn(msg: str) -> None:
    print(f"  [!!] {msg}")


# ──────────────────────────────────────────────────────
# 0. CIO allocate_budgets（LLMなし・純ロジック）
# ──────────────────────────────────────────────────────
section("CIO allocate_budgets（risk=medium, JPY100万/USD1万）")

cio = CIOAgent.__new__(CIOAgent)
cio.logger = MagicMock()
allocs = cio.allocate_budgets(
    CTX,
    total_cash_jpy=TOTAL_JPY,
    cash_usd=CASH_USD,
    usd_jpy_rate=USD_JPY,
)

for pod, alloc in allocs.items():
    if alloc.budget_jpy > 0:
        print(f"  {pod:20s}  ¥{alloc.budget_jpy:>10,.0f}  sectors={alloc.active_sectors}  catalyst_slots={alloc.catalyst_slots}")
    if alloc.budget_usd > 0:
        print(f"  {pod:20s}  ${alloc.budget_usd:>10,.0f}  sectors={alloc.active_sectors}  catalyst_slots={alloc.catalyst_slots}")

print()
# 全ポッドが揃っているか確認
expected_pods = {"ScalpDay_JP", "ScalpDay_US", "MomentSwing_JP", "MomentSwing_US", "FXRebalance"}
for pod in expected_pods:
    if pod in allocs:
        ok(f"{pod} → 枠あり (gate通過確認: budget{'_jpy' if 'JP' in pod or pod == 'FXRebalance' else '_usd'} > 0: "
           f"{allocs[pod].budget_jpy > 0 or allocs[pod].budget_usd > 0})")
    else:
        warn(f"{pod} → allocs に存在しない!")


# ──────────────────────────────────────────────────────
# 1. ScalpDay_JP
# ──────────────────────────────────────────────────────
section("ScalpDay_JP（デイトレ日本株）")
from agents.scalp_day import ScalpDay_JP

jp_universe = [
    {"symbol": "9984", "name": "ソフトバンクG",  "volume_ratio": 2.1, "atr_pct": 2.3},
    {"symbol": "6857", "name": "アドバンテスト", "volume_ratio": 1.8, "atr_pct": 1.9},
    {"symbol": "4063", "name": "信越化学",        "volume_ratio": 0.9, "atr_pct": 0.8},
]

# 注意: max_daytrade_margin_jpy=300,000 / 100株単位 → price <= 3,000 でないと qty=0 になる
# 検証用に 1,500 円の銘柄を想定（例: 2330 フィックスターズ相当の低価格銘柄）
jp_board = {"CurrentPrice": 1_500, "BidPrice": 1_498, "AskPrice": 1_502}
jp_bars  = [{"close": 1_480 + i * 2, "volume": 18_000} for i in range(10)]

jp_screen_mock = ["9984", "6857"]
jp_proposal_mock = {
    "action": "buy", "qty": 200, "price": 1_502,
    "stop_loss": 1_472, "take_profit": 1_547,
    "rationale": "出来高2.1倍・VWAP上方・RSI58で買いシグナル確認",
}

agent_jp = ScalpDay_JP.__new__(ScalpDay_JP)
agent_jp.logger = MagicMock()

with patch.object(agent_jp, "_ask_llm_json", side_effect=[jp_screen_mock, jp_proposal_mock, jp_proposal_mock]):
    candidates = agent_jp.screen_candidates(jp_universe, CTX)
    print(f"  screen_candidates → {candidates}")
    proposals_jp = []
    for sym in candidates:
        name = next((u["name"] for u in jp_universe if u["symbol"] == sym), sym)
        p = agent_jp.generate_trade_proposal(sym, name, jp_board, jp_bars, CTX)
        if p:
            proposals_jp.append(p)

for p in proposals_jp:
    print(f"  {SEP}")
    print(f"  symbol={p.symbol}  market={p.market}  side={p.side}  qty={p.qty}")
    print(f"  price={p.price}  stop_loss={p.stop_loss}  take_profit={p.take_profit}")
    print(f"  strategy={p.strategy}  agent={p.agent}")
    assert p.market == "JP",    f"FAIL: market={p.market} (expect JP)"
    assert p.strategy == "scalpday", f"FAIL: strategy={p.strategy}"
    ok(f"market=JP ✓  strategy=scalpday ✓  qty={p.qty} (100株単位: {p.qty % 100 == 0})")


# ──────────────────────────────────────────────────────
# 2. ScalpDay_US
# ──────────────────────────────────────────────────────
section("ScalpDay_US（デイトレ米国株）")
from agents.scalp_day import ScalpDay_US

us_universe = [
    {"symbol": "NVDA",  "name": "NVIDIA",     "volume_ratio": 2.3, "atr_pct": 2.8},
    {"symbol": "TSLA",  "name": "Tesla",      "volume_ratio": 1.9, "atr_pct": 3.1},
    {"symbol": "AAPL",  "name": "Apple",      "volume_ratio": 1.1, "atr_pct": 1.2},
]

us_board = {"CurrentPrice": 1_080, "BidPrice": 1_079, "AskPrice": 1_081}
us_bars  = [{"close": 1_060 + i * 2, "volume": 8_000_000} for i in range(10)]

us_screen_mock = ["NVDA", "TSLA"]
us_proposal_mock = {
    "action": "buy", "qty": 2, "price": 1_082,
    "stop_loss": 1_060, "take_profit": 1_114,
    "rationale": "Volume surge 2.3x VWAP breakout RSI=61 momentum continuation",
}

agent_us = ScalpDay_US.__new__(ScalpDay_US)
agent_us.logger = MagicMock()

with patch.object(agent_us, "_ask_llm_json", side_effect=[us_screen_mock, us_proposal_mock, us_proposal_mock]):
    candidates_us = agent_us.screen_candidates(us_universe, CTX)
    print(f"  screen_candidates → {candidates_us}")
    proposals_us = []
    for sym in candidates_us:
        name = next((u["name"] for u in us_universe if u["symbol"] == sym), sym)
        p = agent_us.generate_trade_proposal(sym, name, us_board, us_bars, CTX)
        if p:
            proposals_us.append(p)

for p in proposals_us:
    print(f"  {SEP}")
    print(f"  symbol={p.symbol}  market={p.market}  side={p.side}  qty={p.qty}")
    print(f"  price={p.price}  stop_loss={p.stop_loss}  take_profit={p.take_profit}")
    print(f"  strategy={p.strategy}  agent={p.agent}")
    assert p.market == "US",    f"FAIL: market={p.market} (expect US)"
    assert p.strategy == "scalpday", f"FAIL: strategy={p.strategy}"
    ok(f"market=US ✓  strategy=scalpday ✓  qty={p.qty} (1株単位: qty={p.qty})")


# ──────────────────────────────────────────────────────
# 3. MomentSwing_JP
# ──────────────────────────────────────────────────────
section("MomentSwing_JP（スイング日本株）")
from agents.moment_swing import MomentSwing_JP

jp_swing_universe = [
    {"symbol": "6857", "name": "アドバンテスト", "sector": "semiconductors"},
    {"symbol": "9984", "name": "ソフトバンクG",  "sector": "technology"},
    {"symbol": "7203", "name": "トヨタ自動車",    "sector": "automotive"},
]

swing_jp_mock = [
    {
        "symbol": "6857", "name": "アドバンテスト", "qty": 200,
        "rationale": "半導体セクター強・出来高1.8倍・25日線上抜け",
        "stop_loss_pct": 0.06, "target_return_pct": 0.12,
    },
    {
        "symbol": "9984", "name": "ソフトバンクG", "qty": 100,
        "rationale": "テクノロジーセクター強・CIOローテーション一致",
        "stop_loss_pct": 0.07, "target_return_pct": 0.14,
    },
]

budget_jp = allocs["MomentSwing_JP"].budget_jpy
print(f"  CIO配分枠: ¥{budget_jp:,.0f}")

agent_ms_jp = MomentSwing_JP.__new__(MomentSwing_JP)
agent_ms_jp.logger = MagicMock()

with patch.object(agent_ms_jp, "_ask_llm_json", return_value=swing_jp_mock):
    proposals_ms_jp = agent_ms_jp.screen_value(
        universe=jp_swing_universe,
        ctx=CTX,
        existing_symbols=[],
        max_position=500_000,
        cash=budget_jp,
    )

for p in proposals_ms_jp:
    print(f"  {SEP}")
    print(f"  symbol={p.symbol}  market={p.market}  side={p.side}  qty={p.qty}")
    print(f"  strategy={p.strategy}  agent={p.agent}")
    print(f"  stop_loss_pct={p.extra.get('stop_loss_pct')}  target={p.extra.get('target_return_pct')}")
    print(f"  rationale={p.rationale}")
    assert p.market == "JP",              f"FAIL: market={p.market}"
    assert p.strategy == "momentum_swing", f"FAIL: strategy={p.strategy}"
    ok(f"market=JP ✓  strategy=momentum_swing ✓  SL={p.extra['stop_loss_pct']:.0%}  TP={p.extra['target_return_pct']:.0%}")


# ──────────────────────────────────────────────────────
# 4. MomentSwing_US
# ──────────────────────────────────────────────────────
section("MomentSwing_US（スイング米国株）")
from agents.moment_swing import MomentSwing_US

us_swing_universe = [
    {"symbol": "NVDA",  "name": "NVIDIA",    "sector": "semiconductors"},
    {"symbol": "AMD",   "name": "AMD",       "sector": "semiconductors"},
    {"symbol": "GOOGL", "name": "Alphabet",  "sector": "internet"},
]

swing_us_mock = [
    {
        "symbol": "NVDA", "name": "NVIDIA", "qty": 2,
        "rationale": "Semiconductor sector CIO-aligned, momentum breakout vol+2.3x",
        "stop_loss_pct": 0.06, "target_return_pct": 0.12,
    },
]

budget_us = allocs["MomentSwing_US"].budget_usd
print(f"  CIO配分枠: ${budget_us:,.0f}")

agent_ms_us = MomentSwing_US.__new__(MomentSwing_US)
agent_ms_us.logger = MagicMock()

with patch.object(agent_ms_us, "_ask_llm_json", return_value=swing_us_mock):
    proposals_ms_us = agent_ms_us.screen_value(
        universe=us_swing_universe,
        ctx=CTX,
        existing_symbols=[],
        max_position=3000,
        cash=budget_us,
    )

for p in proposals_ms_us:
    print(f"  {SEP}")
    print(f"  symbol={p.symbol}  market={p.market}  side={p.side}  qty={p.qty}")
    print(f"  strategy={p.strategy}  agent={p.agent}")
    print(f"  stop_loss_pct={p.extra.get('stop_loss_pct')}  target={p.extra.get('target_return_pct')}")
    print(f"  rationale={p.rationale}")
    assert p.market == "US",              f"FAIL: market={p.market}"
    assert p.strategy == "momentum_swing", f"FAIL: strategy={p.strategy}"
    ok(f"market=US ✓  strategy=momentum_swing ✓  SL={p.extra['stop_loss_pct']:.0%}  TP={p.extra['target_return_pct']:.0%}")


# ──────────────────────────────────────────────────────
# 5. FXRebalance（FXStrategyAgent）
# ──────────────────────────────────────────────────────
section("FXRebalance（FXStrategyAgent）")
from agents.fx_strategy import FXStrategyAgent

fx_mock = {
    "fx_signal": "buy_usd",
    "target_usd_ratio": 40.0,
    "current_usd_ratio": 35.0,
    "rationale": "USD/JPY上昇トレンド継続、ドル比率を5%引き上げ推奨",
    "us_weight_bias": "overweight",
    "usd_jpy_rate": 155.2,
}

budget_fx = allocs["FXRebalance"].budget_jpy
print(f"  CIO配分枠（最大取引額）: ¥{budget_fx:,.0f}")

fx_agent = FXStrategyAgent.__new__(FXStrategyAgent)
fx_agent.logger = MagicMock()

with patch.object(fx_agent, "_ask_llm_json", return_value=fx_mock):
    fx_signal = fx_agent.generate_signal("USD/JPY=155.2, VIX=17.8", 0.35, CTX)

print(f"  fx_signal       = {fx_signal['fx_signal']}")
print(f"  usd_jpy_rate    = {fx_signal['usd_jpy_rate']}")
print(f"  target_usd_ratio= {fx_signal['target_usd_ratio']}%")
print(f"  us_weight_bias  = {fx_signal['us_weight_bias']}")
print(f"  rationale       = {fx_signal['rationale']}")
assert "usd_jpy_rate" in fx_signal, "FAIL: usd_jpy_rateがない（allocate_budgetsに渡せない）"
ok("usd_jpy_rate ✓  シグナル生成 ✓")


# ──────────────────────────────────────────────────────
# 6. クロスチェック
# ──────────────────────────────────────────────────────
section("クロスチェック")

all_proposals = proposals_jp + proposals_us + proposals_ms_jp + proposals_ms_us

print(f"\n  生成提案一覧 (合計 {len(all_proposals)} 件):")
print(f"  {'agent':22s} {'market':6s} {'strategy':16s} {'symbol':6s} {'side':5s} {'qty':>5s}")
print(f"  {SEP}")
for p in all_proposals:
    print(f"  {p.agent:22s} {p.market:6s} {p.strategy:16s} {p.symbol:6s} {p.side:5s} {p.qty:>5}")

print()
# JP 提案に USD 記号がないか確認（通貨混在チェック）
jp_props = [p for p in all_proposals if p.market == "JP"]
us_props = [p for p in all_proposals if p.market == "US"]
ok(f"JP提案数={len(jp_props)}  US提案数={len(us_props)}  市場混在なし={'JP' not in [p.market for p in us_props] and 'US' not in [p.market for p in jp_props]}")

# scalpday と momentum_swing が混在しないか確認
scalp_strategies  = {p.strategy for p in proposals_jp + proposals_us}
swing_strategies  = {p.strategy for p in proposals_ms_jp + proposals_ms_us}
ok(f"ScalpDay戦略={scalp_strategies}  MomentSwing戦略={swing_strategies}")

print(f"\n{'=' * 60}")
print("  [DONE] 全ポッド確認完了")
print('=' * 60)
