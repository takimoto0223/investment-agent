"""
verify_critics.py
PR③ 完了後の通し確認スクリプト（LLM呼び出しをモックして実挙動を検証）。

シナリオ:
  1. ScalpDay_JP    + ScalpDay_JP_Critic    — 初回承認 (1ラウンド)
  2. MomentSwing_JP + MomentSwing_JP_Critic — pre-check否決→修正→承認 (2ラウンド)
  3. ScalpDay_US    + ScalpDay_US_Critic    — 全否決 max_rounds=2 → 提案ゼロ (3ラウンド)
  4. MomentSwing_US + MomentSwing_US_Critic — FX underweight → fixable=False → 即ゼロ
  5. FXRebalance_Critic                     — シグナル承認
  6. IntelCritic                            — 3件→1件(PR記事・低relevance除外)
  7. 混在ポッド                              — 承認1件+否決1件(提案者断念) → エラーなし

確認項目:
  - 修正ループが max_rounds で止まっているか(延々往復しない)
  - 否決が続いたポッドが提案ゼロで正常終了するか(エラーなし)
  - 承認/否決/修正の verdict が各クリティークから実際に出ているか
"""
import sys
import logging
import traceback
from unittest.mock import patch, MagicMock

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("verify_critics")

from agents.base import MarketContext, TradeProposal
from agents.scalp_day import ScalpDay_JP, ScalpDay_US
from agents.moment_swing import MomentSwing_JP, MomentSwing_US
from agents.critics import (
    ScalpDay_JP_Critic,
    ScalpDay_US_Critic,
    MomentSwing_JP_Critic,
    MomentSwing_US_Critic,
    FXRebalance_Critic,
    IntelCritic,
)


# ──────────────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────────────

CTX = MarketContext(
    date="2026-06-19",
    sector_scores={"AI半導体": 0.85, "ソフトウェア": 0.72, "自動車": 0.60},
    macro_notes="VIX=18, 米10Y=4.3%, USD/JPY=155",
    rotation_signal="AI半導体リード継続",
    risk_level="medium",
)

def _make(cls):
    """LLMを呼ばずにエージェントインスタンスを生成する。"""
    agent = cls.__new__(cls)
    agent.logger = logging.getLogger(cls.name)
    return agent

def _sep(title):
    logger.info("=" * 65)
    logger.info(f"  {title}")
    logger.info("=" * 65)

def _ok(msg):    logger.info(f"  [OK]   {msg}")
def _info(msg):  logger.info(f"  {msg}")
def _result():   logger.info("  ---- 結果 ----")


# ──────────────────────────────────────────────────────────────────
# Scenario 1: ScalpDay_JP + ScalpDay_JP_Critic — 初回承認
# ──────────────────────────────────────────────────────────────────

def run_s1():
    _sep("S1: ScalpDay_JP + ScalpDay_JP_Critic — 初回承認")

    proposer = _make(ScalpDay_JP)
    critic   = _make(ScalpDay_JP_Critic)

    proposal = TradeProposal(
        agent="ScalpDay_JP", symbol="9984", market="JP",
        side="buy", qty=200, price=1500.0, strategy="scalpday",
        rationale="VWAP上抜け, 出来高2.1倍。モメンタム継続期待。",
        stop_loss=1470.0, take_profit=1560.0,
    )
    _info(f"提案: {proposal.symbol} {proposal.side} x{proposal.qty} "
          f"@ ¥{proposal.price:.0f}  SL=¥{proposal.stop_loss:.0f}  TP=¥{proposal.take_profit:.0f}")

    # critic LLM → 1回目で承認
    llm_approve = {"approved": True, "score": 0.92,
                   "issues": [], "suggestion": "SL/TP・建玉上限・R:R すべてクリア", "fixable": True}
    revise_call_count = [0]
    def count_revise(_):
        revise_call_count[0] += 1
        return {}

    with patch.object(critic, "_ask_llm_json", return_value=llm_approve), \
         patch.object(proposer, "_ask_llm_json", side_effect=count_revise):
        result_p, result_v = critic.refine_and_review(
            proposer, proposal, CTX, wallet={"MarginAccountWallet": 500_000}
        )

    _result()
    _info(f"approved_proposal: {'有り (' + result_p.symbol + ')' if result_p else 'None'}")
    _info(f"verdict.approved={result_v.approved}  score={result_v.score}  "
          f"suggestion='{result_v.suggestion}'")
    _info(f"revise_proposal 呼び出し回数: {revise_call_count[0]}")

    assert result_p is not None and result_v.approved
    assert revise_call_count[0] == 0, "初回承認なので revise は不要"
    _ok("初回1ラウンドで承認。revise_proposal 未呼出。")
    return True


# ──────────────────────────────────────────────────────────────────
# Scenario 2: MomentSwing_JP + MomentSwing_JP_Critic
#             pre-check否決(SL未設定) → 修正 → 承認
# ──────────────────────────────────────────────────────────────────

def run_s2():
    _sep("S2: MomentSwing_JP + MomentSwing_JP_Critic — pre-check否決→修正→承認")

    proposer = _make(MomentSwing_JP)
    critic   = _make(MomentSwing_JP_Critic)

    # stop_loss_pct=0 → pre-check で即否決
    proposal = TradeProposal(
        agent="MomentSwing_JP", symbol="6857", market="JP",
        side="buy", qty=100, price=3200.0, strategy="momentum_swing",
        rationale="半導体セクターリード。RSI上昇トレンド、出来高1.8倍。",
        stop_loss=None, take_profit=None,
        extra={"stop_loss_pct": 0.0, "target_return_pct": 0.12, "name": "アドバンテスト"},
    )
    _info(f"提案: {proposal.symbol} {proposal.side} x{proposal.qty} "
          f"@ ¥{proposal.price:.0f}  stop_loss_pct={proposal.extra['stop_loss_pct']}")
    _info("Round 1: pre-check失敗(stop_loss未設定) → LLM不使用, fixable=True → revise")

    # proposer revise LLM: stop_loss_pct=0.06 に修正
    revise_resp = {
        "action": "buy", "qty": 100,
        "stop_loss_pct": 0.06, "target_return_pct": 0.12,
        "rationale": "SLを6%に設定して再提案。R:R=2.0で基準クリア。",
    }
    # critic LLM (2回目のみ到達): 承認
    critic_approve = {
        "approved": True, "score": 0.87,
        "issues": [], "suggestion": "stop_loss_pct=6%、R:R=2.0。基準クリア。", "fixable": True,
    }
    proposer_call_count = [0]
    critic_call_count   = [0]

    def proposer_llm(_):
        proposer_call_count[0] += 1
        _info(f"  → proposer LLM #{proposer_call_count[0]}: revise_proposal 返答")
        return revise_resp

    def critic_llm(_):
        critic_call_count[0] += 1
        _info(f"  → critic LLM #{critic_call_count[0]}: 審査(2ラウンド目)")
        return critic_approve

    with patch.object(proposer, "_ask_llm_json", side_effect=proposer_llm), \
         patch.object(critic,   "_ask_llm_json", side_effect=critic_llm):
        result_p, result_v = critic.refine_and_review(
            proposer, proposal, CTX, wallet={"MarginAccountWallet": 1_000_000}
        )

    _result()
    _info(f"approved_proposal: {'有り (' + result_p.symbol + ')' if result_p else 'None'}")
    _info(f"verdict.approved={result_v.approved}  score={result_v.score}")
    _info(f"修正後 stop_loss_pct={result_p.extra.get('stop_loss_pct') if result_p else 'N/A'}")
    _info(f"proposer LLM呼出: {proposer_call_count[0]}回  critic LLM呼出: {critic_call_count[0]}回")

    assert result_p is not None and result_v.approved
    assert proposer_call_count[0] == 1, "revise は1回のみ"
    assert critic_call_count[0] == 1,   "critic LLM は2ラウンド目のみ(1回)"
    _ok("1回修正後に承認。ループ2ラウンドで停止。pre-checkはLLM不使用。")
    return True


# ──────────────────────────────────────────────────────────────────
# Scenario 3: ScalpDay_US + ScalpDay_US_Critic
#             全否決 max_rounds=2 → 3レビュー後に提案ゼロ
# ──────────────────────────────────────────────────────────────────

def run_s3():
    _sep("S3: ScalpDay_US + ScalpDay_US_Critic — 全否決 (max_rounds=2) → 提案ゼロ")

    proposer = _make(ScalpDay_US)
    critic   = _make(ScalpDay_US_Critic)

    proposal = TradeProposal(
        agent="ScalpDay_US", symbol="TSLA", market="US",
        side="buy", qty=2, price=250.0, strategy="scalpday",
        rationale="RSI上昇、ブレイクアウトシグナル。",
        stop_loss=245.0, take_profit=262.0,
    )
    _info(f"提案: {proposal.symbol} {proposal.side} x{proposal.qty} "
          f"@ ${proposal.price:.2f}  SL=${proposal.stop_loss:.2f}")

    # critic LLM: 3回とも否決
    critic_seq = [
        {"approved": False, "score": 0.35, "fixable": True,
         "issues": ["SLが株価変動幅に対して浅すぎる(2%未満)"],
         "suggestion": "SLを現値から2.5%以上離してください"},
        {"approved": False, "score": 0.28, "fixable": True,
         "issues": ["SL修正が不十分", "直近出来高がATR要件未達"],
         "suggestion": "セットアップを再評価してください"},
        {"approved": False, "score": 0.22, "fixable": True,
         "issues": ["セットアップが全体的に弱い"],
         "suggestion": "本日のTSLAはスキップを推奨"},
    ]
    # proposer LLM: 2回修正回答 (3回目のレビューは修正なしで終了)
    revise_seq = [
        {"action": "buy", "qty": 2, "price": 250.0,
         "stop_loss": 243.75, "take_profit": 265.0, "rationale": "SLをATR×1.5で修正"},
        {"action": "buy", "qty": 2, "price": 250.0,
         "stop_loss": 241.0, "take_profit": 268.0, "rationale": "さらにSLを拡大"},
    ]
    critic_iter = iter(critic_seq)
    revise_iter = iter(revise_seq)
    critic_call_count = [0]
    revise_call_count = [0]

    def critic_llm(prompt):
        resp = next(critic_iter)
        critic_call_count[0] += 1
        _info(f"  → critic LLM #{critic_call_count[0]}: score={resp['score']} approved={resp['approved']}")
        _info(f"     issues={resp['issues']}")
        return resp

    def revise_llm(prompt):
        resp = next(revise_iter)
        revise_call_count[0] += 1
        _info(f"  → proposer LLM #{revise_call_count[0]}: revise SL→${resp['stop_loss']:.2f}")
        return resp

    # 市場時間内・FX中立 としてモック
    with patch("agents.critics._is_us_market_hours", return_value=True), \
         patch.object(critic,   "_ask_llm_json", side_effect=critic_llm), \
         patch.object(proposer, "_ask_llm_json", side_effect=revise_llm):
        result_p, result_v = critic.refine_and_review(
            proposer, proposal, CTX,
            account={"equity": 50_000},
            fx_signal={"us_weight_bias": "neutral"},
        )

    _result()
    _info(f"approved_proposal: {'有り' if result_p else 'None（提案ゼロ）'}")
    _info(f"last verdict: approved={result_v.approved}  score={result_v.score}")
    _info(f"last issues: {result_v.issues}")
    _info(f"critic LLM呼出: {critic_call_count[0]}回 (期待=3)  "
          f"revise呼出: {revise_call_count[0]}回 (期待=2)")

    assert result_p is None,              "全否決なので None を返すべき"
    assert not result_v.approved
    assert critic_call_count[0] == 3,    f"reviewは3回: got {critic_call_count[0]}"
    assert revise_call_count[0] == 2,    f"reviseは2回: got {revise_call_count[0]}"
    _ok("max_rounds=2 で停止 (review×3, revise×2)。提案ゼロで正常終了。エラーなし。")
    return True


# ──────────────────────────────────────────────────────────────────
# Scenario 4: MomentSwing_US + MomentSwing_US_Critic
#             FX underweight + buy → fixable=False → 即ゼロ
# ──────────────────────────────────────────────────────────────────

def run_s4():
    _sep("S4: MomentSwing_US + MomentSwing_US_Critic — FX underweight → fixable=False")

    proposer = _make(MomentSwing_US)
    critic   = _make(MomentSwing_US_Critic)

    proposal = TradeProposal(
        agent="MomentSwing_US", symbol="AAPL", market="US",
        side="buy", qty=5, price=200.0, strategy="momentum_swing",
        rationale="AI製品サイクル強化、機関投資家の買い継続。",
        stop_loss=None, take_profit=None,
        extra={"stop_loss_pct": 0.06, "target_return_pct": 0.12, "name": "Apple"},
    )
    fx_signal = {"us_weight_bias": "underweight", "fx_signal": "ドル売り推奨",
                 "usd_jpy_rate": 155.2, "target_usd_ratio": 25.0}
    _info(f"提案: {proposal.symbol} {proposal.side} x{proposal.qty} "
          f"@ ${proposal.price:.2f}  stop_loss_pct={proposal.extra['stop_loss_pct']}")
    _info(f"FXシグナル: us_weight_bias={fx_signal['us_weight_bias']} → buy は即否決対象")

    critic_call_count  = [0]
    revise_call_count  = [0]
    def critic_llm(_):  critic_call_count[0]  += 1; return {}
    def revise_llm(_):  revise_call_count[0]  += 1; return {}

    with patch.object(critic,   "_ask_llm_json", side_effect=critic_llm), \
         patch.object(proposer, "_ask_llm_json", side_effect=revise_llm):
        result_p, result_v = critic.refine_and_review(
            proposer, proposal, CTX,
            account={"equity": 80_000}, fx_signal=fx_signal,
        )

    _result()
    _info(f"approved_proposal: {'有り' if result_p else 'None（提案ゼロ）'}")
    _info(f"verdict.approved={result_v.approved}  fixable={result_v.fixable}")
    _info(f"issues={result_v.issues}")
    _info(f"critic LLM呼出: {critic_call_count[0]}回 (期待=0)  "
          f"revise呼出: {revise_call_count[0]}回 (期待=0)")

    assert result_p is None
    assert not result_v.fixable,           "fixable=False でないと再試行される"
    assert critic_call_count[0] == 0,     "pre-checkで弾くのでLLM不使用"
    assert revise_call_count[0] == 0,     "fixable=False なので修正なし"
    _ok("FX underweight pre-check → fixable=False で即打ち切り。LLM未使用、revise未呼出。")
    return True


# ──────────────────────────────────────────────────────────────────
# Scenario 5: FXRebalance_Critic — シグナル承認
# ──────────────────────────────────────────────────────────────────

def run_s5():
    _sep("S5: FXRebalance_Critic — 変更幅12% → pre-check通過 → LLM → 承認")

    critic = _make(FXRebalance_Critic)

    fx_signal = {
        "us_weight_bias": "overweight",
        "fx_signal": "ドル比率を小幅調整",
        "current_usd_ratio": 40.0,
        "target_usd_ratio": 52.0,   # 変動幅12% < 20% → pre-check通過
        "usd_jpy_rate": 155.2,
    }
    _info(f"FXシグナル: current={fx_signal['current_usd_ratio']}% → target={fx_signal['target_usd_ratio']}% "
          f"(変動{abs(fx_signal['target_usd_ratio']-fx_signal['current_usd_ratio']):.0f}%)")

    llm_resp = {
        "approved": True, "score": 0.83,
        "issues": [], "suggestion": "12%変更は段階的変更の範囲内。方向性も妥当。", "fixable": True,
    }
    with patch.object(critic, "_ask_llm_json", return_value=llm_resp):
        verdict = critic.review_signal(fx_signal, CTX)

    _result()
    _info(f"verdict.approved={verdict.approved}  score={verdict.score}")
    _info(f"suggestion='{verdict.suggestion}'")

    assert verdict.approved
    _ok("FXシグナル承認。変動幅チェック(pre-check)通過→LLM審査。")
    return True


# ──────────────────────────────────────────────────────────────────
# Scenario 6: IntelCritic — 3件入力 → PR記事・低relevance除外 → 1件出力
# ──────────────────────────────────────────────────────────────────

def run_s6():
    _sep("S6: IntelCritic — 3件入力 → フィルタ後1件")

    critic = _make(IntelCritic)

    raw = [
        {"source": "arxiv",      "title": "LLMとロボティクスの融合研究",   "url": "https://arxiv.org/abs/xxx"},
        {"source": "hackernews", "title": "ABC社 新製品発表 (PR記事)",      "url": "https://abc.com/news"},
        {"source": "github",     "title": "NVIDIA CUDA 13.0 リリース",     "url": "https://github.com/nvidia"},
    ]
    _info(f"入力: {len(raw)}件")
    for s in raw:
        _info(f"  [{s['source']}] {s['title']}")

    # LLM応答: relevance_score < 0.6 は除外, approved=False は除外
    llm_resp = [
        {"source": "arxiv",      "title": "LLMとロボティクスの融合研究",  "relevance_score": 0.82,
         "approved": True,  "classification": "研究成果", "summary": "AI×ロボ融合。半導体セクター中長期影響あり"},
        {"source": "hackernews", "title": "ABC社 新製品発表 (PR記事)",    "relevance_score": 0.71,
         "approved": False, "classification": "PR記事",  "summary": "PR記事のため除外"},
        {"source": "github",     "title": "NVIDIA CUDA 13.0 リリース",   "relevance_score": 0.45,
         "approved": True,  "classification": "公式発表","summary": "relevance_score<0.6のため除外"},
    ]
    with patch.object(critic, "_ask_llm_json", return_value=llm_resp):
        result = critic.review_signals(raw, CTX)

    _result()
    _info(f"入力: {len(raw)}件 → 出力: {len(result)}件")
    for s in result:
        _info(f"  [承認] ({s['relevance_score']:.2f}) {s['title']}")
    _info("  除外:")
    _info("    [PR記事]   ABC社 新製品発表 → approved=False")
    _info("    [低rel]    NVIDIA CUDA 13.0 → relevance_score=0.45 < 0.6")

    assert len(result) == 1 and result[0]["source"] == "arxiv"
    _ok("PR記事・低relevance除外。研究成果1件のみ承認。")
    return True


# ──────────────────────────────────────────────────────────────────
# Scenario 7: 混在ポッド — 一部承認 + 一部(提案者断念)否決 → エラーなし
# ──────────────────────────────────────────────────────────────────

def run_s7():
    _sep("S7: 混在ポッド — 承認1件 + 否決(提案者断念)1件 → エラーなし")

    proposer = _make(MomentSwing_JP)
    critic   = _make(MomentSwing_JP_Critic)

    proposals = [
        # 9984: stop_loss_pct=0.06 → pre-check通過 → critic LLM → 承認
        TradeProposal(
            agent="MomentSwing_JP", symbol="9984", market="JP",
            side="buy", qty=100, price=2000.0, strategy="momentum_swing",
            rationale="テックセクターリード継続",
            stop_loss=None, take_profit=None,
            extra={"stop_loss_pct": 0.06, "target_return_pct": 0.12, "name": "SBG"},
        ),
        # 7203: stop_loss_pct=0.0 → pre-check否決 → revise → 提案者が断念(withdraw) → None
        TradeProposal(
            agent="MomentSwing_JP", symbol="7203", market="JP",
            side="buy", qty=100, price=3000.0, strategy="momentum_swing",
            rationale="自動車は様子見が続く",
            stop_loss=None, take_profit=None,
            extra={"stop_loss_pct": 0.0, "target_return_pct": 0.12, "name": "トヨタ"},
        ),
    ]

    executed, rejected = [], []

    # ── 9984: critic承認, proposer不使用 ─────────────────────────
    _info("--- [9984] pre-check通過(SL_pct=0.06) → critic LLM → 承認 ---")
    with patch.object(critic, "_ask_llm_json",
                      return_value={"approved": True, "score": 0.90,
                                    "issues": [], "suggestion": "R:R=2.0 基準クリア", "fixable": True}), \
         patch.object(proposer, "_ask_llm_json",
                      side_effect=lambda _: (_ for _ in ()).throw(AssertionError("revise不要なのに呼ばれた"))):
        rp, rv = critic.refine_and_review(proposer, proposals[0], CTX,
                                          wallet={"MarginAccountWallet": 1_000_000})
    if rp is not None:
        executed.append(rp.symbol)
        _info(f"  → 承認 executed に追加: {rp.symbol}")
    else:
        rejected.append(proposals[0].symbol)

    # ── 7203: pre-check否決 → revise → withdraw → None ──────────
    _info("--- [7203] pre-check否決(SL_pct=0.0) → revise → proposer断念(withdraw) → None ---")
    with patch.object(critic, "_ask_llm_json",
                      side_effect=lambda _: (_ for _ in ()).throw(AssertionError("2ラウンド目は来ないはず"))), \
         patch.object(proposer, "_ask_llm_json",
                      return_value={"action": "withdraw"}):
        rp, rv = critic.refine_and_review(proposer, proposals[1], CTX,
                                          wallet={"MarginAccountWallet": 1_000_000})
    if rp is None:
        rejected.append(proposals[1].symbol)
        _info(f"  → 否決 rejected に追加: {proposals[1].symbol}  reason={rv.issues}")

    _result()
    _info(f"executed: {executed}")
    _info(f"rejected: {rejected}")

    assert "9984" in executed,  "9984 は承認されるべき"
    assert "7203" in rejected,  "7203 は否決されるべき"
    assert len(executed) == 1
    assert len(rejected) == 1
    _ok("一部承認・一部否決(提案者断念)。エラーなし。empty-proposal相当の正常終了確認。")
    return True


# ──────────────────────────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────────────────────────

SCENARIOS = [
    ("S1: ScalpDay_JP   初回承認",               run_s1),
    ("S2: MomentSwing_JP 否決→修正→承認",        run_s2),
    ("S3: ScalpDay_US   全否決→提案ゼロ",         run_s3),
    ("S4: MomentSwing_US FX否決→即ゼロ",         run_s4),
    ("S5: FXRebalance_Critic 承認",              run_s5),
    ("S6: IntelCritic フィルタ",                 run_s6),
    ("S7: 混在ポッド エラーなし",                 run_s7),
]

if __name__ == "__main__":
    results: dict[str, str] = {}

    for name, fn in SCENARIOS:
        try:
            fn()
            results[name] = "OK"
        except AssertionError as e:
            results[name] = f"FAIL(assertion): {e}"
            traceback.print_exc()
        except Exception as e:
            results[name] = f"FAIL(error): {e}"
            traceback.print_exc()

    logger.info("")
    logger.info("=" * 65)
    logger.info("  最終サマリー")
    logger.info("=" * 65)
    all_ok = True
    for name, status in results.items():
        icon = "[OK]  " if status == "OK" else "[FAIL]"
        logger.info(f"  {icon} {name}: {status}")
        if status != "OK":
            all_ok = False
    logger.info("=" * 65)
    if all_ok:
        logger.info("  全シナリオ通過。PR⑤ に進んで問題なし。")
    else:
        logger.info("  一部シナリオ失敗。上記ログを確認してください。")
    logger.info("=" * 65)
    sys.exit(0 if all_ok else 1)
