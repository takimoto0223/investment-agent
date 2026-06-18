"""
tests/test_universe.py
PR⑤ data/universe.py の filter_universe() テスト。

確認事項:
  (a) 通常: active_sectors 内の銘柄のみ返る
  (b) カタリスト: 異常銘柄が catalyst_slots 数まで追加される
  (c) FX (slots=0): catalyst_slots=0 の場合は例外を一切拾わない
  (d) slots 上限: 異常銘柄が複数あっても catalyst_slots を超えない
"""
import unittest
from data.universe import filter_universe, _CATALYST_VOLUME_RATIO_MIN, _CATALYST_PRICE_CHANGE_MIN


def _make_stock(symbol: str, sector: str, volume_ratio: float = 1.0, price_change_pct: float = 0.0) -> dict:
    return {
        "symbol":          symbol,
        "sector":          sector,
        "volume_ratio":    volume_ratio,
        "price_change_pct": price_change_pct,
        "current_price":   1000.0,
        "atr_pct":         1.5,
    }


# ──────────────────────────────────────────────────
# (a) 通常: セクター内のみ
# ──────────────────────────────────────────────────

class TestFilterUniverse_NormalSectorOnly(unittest.TestCase):
    """(a) 通常状態: active_sectors に含まれる銘柄だけ返る"""

    def setUp(self):
        self.universe = [
            _make_stock("AAA", "AI半導体"),
            _make_stock("BBB", "データセンターインフラ"),
            _make_stock("CCC", "エネルギー"),        # 非活性セクター
        ]
        self.active = ["AI半導体", "データセンターインフラ"]

    def test_only_active_sector_stocks_returned(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=1)
        symbols = {r["symbol"] for r in result}
        self.assertIn("AAA", symbols)
        self.assertIn("BBB", symbols)
        self.assertNotIn("CCC", symbols)

    def test_count_matches_active_sector_stocks(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=1)
        # "CCC" は非活性 + 異常なし → 追加されない
        self.assertEqual(len(result), 2)

    def test_normal_stock_outside_sector_not_added(self):
        # volume_ratio < 2.0 の場合はカタリスト候補にならない
        universe = [
            _make_stock("AAA", "AI半導体"),
            _make_stock("OUT", "エネルギー", volume_ratio=1.5, price_change_pct=0.05),
        ]
        result = filter_universe(universe, ["AI半導体"], catalyst_slots=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "AAA")


# ──────────────────────────────────────────────────
# (b) カタリスト: 異常銘柄が slots 数まで追加
# ──────────────────────────────────────────────────

class TestFilterUniverse_CatalystException(unittest.TestCase):
    """(b) 異常な銘柄があれば catalyst_slots 数まで例外として採用される"""

    def setUp(self):
        # 異常閾値を超えるカタリスト銘柄
        self.catalyst_vol = _CATALYST_VOLUME_RATIO_MIN
        self.catalyst_chg = _CATALYST_PRICE_CHANGE_MIN
        self.universe = [
            _make_stock("IN_SECTOR",    "AI半導体"),                                    # セクター内
            _make_stock("CATALYST",     "エネルギー",                                   # セクター外
                        volume_ratio=self.catalyst_vol, price_change_pct=self.catalyst_chg),
        ]
        self.active = ["AI半導体"]

    def test_catalyst_stock_is_added(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=1)
        symbols = {r["symbol"] for r in result}
        self.assertIn("IN_SECTOR", symbols)
        self.assertIn("CATALYST", symbols)

    def test_both_conditions_required(self):
        # volume_ratio は閾値以上だが price_change_pct が不足 → 採用されない
        universe = [
            _make_stock("IN",  "AI半導体"),
            _make_stock("OUT", "エネルギー", volume_ratio=self.catalyst_vol, price_change_pct=0.01),
        ]
        result = filter_universe(universe, ["AI半導体"], catalyst_slots=1)
        self.assertEqual(len(result), 1)

    def test_negative_price_change_also_qualifies(self):
        # 下落方向の異常も採用される（abs でチェック）
        universe = [
            _make_stock("IN",    "AI半導体"),
            _make_stock("CRASH", "金融",
                        volume_ratio=self.catalyst_vol * 2,
                        price_change_pct=-self.catalyst_chg * 2),
        ]
        result = filter_universe(universe, ["AI半導体"], catalyst_slots=1)
        symbols = {r["symbol"] for r in result}
        self.assertIn("CRASH", symbols)

    def test_already_in_sector_not_double_counted(self):
        # active_sectors 内の銘柄が異常値を示していても重複追加しない
        universe = [
            _make_stock("IN", "AI半導体",
                        volume_ratio=self.catalyst_vol * 3,
                        price_change_pct=self.catalyst_chg * 3),
        ]
        result = filter_universe(universe, ["AI半導体"], catalyst_slots=1)
        self.assertEqual(len(result), 1)


# ──────────────────────────────────────────────────
# (c) FX: catalyst_slots=0 なら例外なし
# ──────────────────────────────────────────────────

class TestFilterUniverse_FXZeroSlots(unittest.TestCase):
    """(c) catalyst_slots=0 のとき、どんな異常銘柄もセクター外から拾わない"""

    def setUp(self):
        self.universe = [
            _make_stock("IN",      "AI半導体"),
            # 極端に異常な銘柄でも slots=0 なら追加されない
            _make_stock("EXTREME", "エネルギー",
                        volume_ratio=10.0, price_change_pct=0.20),
        ]
        self.active = ["AI半導体"]

    def test_no_catalyst_when_slots_zero(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=0)
        symbols = {r["symbol"] for r in result}
        self.assertNotIn("EXTREME", symbols)

    def test_base_sector_still_included(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "IN")

    def test_empty_active_sectors_with_zero_slots(self):
        result = filter_universe(self.universe, [], catalyst_slots=0)
        self.assertEqual(result, [])


# ──────────────────────────────────────────────────
# (d) slots 上限: catalyst_slots を超えて採用しない
# ──────────────────────────────────────────────────

class TestFilterUniverse_SlotsLimit(unittest.TestCase):
    """(d) 異常銘柄が多数あっても catalyst_slots を超えて採用しない"""

    def setUp(self):
        vol = _CATALYST_VOLUME_RATIO_MIN
        chg = _CATALYST_PRICE_CHANGE_MIN
        self.universe = [
            _make_stock("BASE", "AI半導体"),
            # セクター外の異常銘柄を 4 銘柄用意
            _make_stock("C1", "エネルギー", volume_ratio=vol * 4, price_change_pct=chg * 4),
            _make_stock("C2", "金融",       volume_ratio=vol * 3, price_change_pct=chg * 3),
            _make_stock("C3", "ディフェンシブ", volume_ratio=vol * 2, price_change_pct=chg * 2),
            _make_stock("C4", "半導体装置・材料", volume_ratio=vol, price_change_pct=chg),
        ]
        self.active = ["AI半導体"]

    def test_slots_1_adds_at_most_one(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=1)
        # BASE + exactly 1 catalyst
        self.assertEqual(len(result), 2)

    def test_slots_2_adds_at_most_two(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=2)
        self.assertEqual(len(result), 3)

    def test_highest_volume_ratio_selected_first(self):
        result = filter_universe(self.universe, self.active, catalyst_slots=1)
        catalyst = [r for r in result if r["symbol"] != "BASE"]
        self.assertEqual(len(catalyst), 1)
        # C1 が volume_ratio 最大 → 採用されるはず
        self.assertEqual(catalyst[0]["symbol"], "C1")

    def test_never_exceeds_slots(self):
        # slots をずらして常に超えないことを確認
        for slots in range(0, 5):
            result = filter_universe(self.universe, self.active, catalyst_slots=slots)
            base_count = 1  # "BASE" のみ
            extra_count = len(result) - base_count
            self.assertLessEqual(extra_count, slots,
                                 f"slots={slots} のとき extra={extra_count} が上限超過")


if __name__ == "__main__":
    unittest.main()
