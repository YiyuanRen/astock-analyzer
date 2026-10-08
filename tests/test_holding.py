import pytest

from app.engine import holding as h

DAILY = {
    "zhongshu_range": [10.0, 10.6],
    "seg_low": 10.2,
    "seg_high": 12.0,
    "latest_bsp": {"is_buy": True, "price": 10.3},
}
ML = {"resistance": 11.8}


def test_structural_stop_picks_nearest_support_below_gap():
    # 现价 11.5, 候选 10.0/10.2/10.3 都低于 11.5*0.98=11.27 -> 取最近 10.3, 再下浮 1.5%
    s, fb = h.structural_stop(DAILY, 11.5)
    assert s == round(10.3 * 0.985, 2) and fb is False


def test_structural_stop_skips_too_close_supports():
    # 现价 10.4: 10.3 距现价不足2%(<=10.19才行) -> 跳过; 10.2 也不行(>10.192); 10.0 可
    s, _ = h.structural_stop(DAILY, 10.4)
    assert s == round(10.0 * 0.985, 2)


def test_structural_stop_fallback_when_no_support():
    s, fb = h.structural_stop({}, 10.0)
    assert (s, fb) == (9.3, True)


def test_cost_protection_takes_higher_of_two():
    # 成本 11.5 -> 个人止损 10.58; 结构止损 10.15 -> 取 10.58
    lv = h.compute_levels(DAILY, ML, 11.5, cost=11.5)
    assert lv.personal_stop == 10.58 and lv.stop_loss == 10.58 and lv.stop_source == "成本保护"
    # 成本很低(8) -> 个人止损 7.36 < 结构 -> 结构
    lv = h.compute_levels(DAILY, ML, 11.5, cost=8.0)
    assert lv.stop_loss == lv.structural_stop and lv.stop_source == "结构止损"


def test_no_cost_uses_structural_only():
    lv = h.compute_levels(DAILY, ML, 11.5)
    assert lv.personal_stop is None and lv.stop_source == "结构止损"


def test_fallback_source_label():
    lv = h.compute_levels({}, None, 10.0)
    assert lv.stop_loss == 9.3 and lv.stop_source.startswith("兜底")


def test_target_nearest_resistance_above_price():
    assert h.compute_target(DAILY, ML, 11.5) == 11.8        # 11.8 / 12.0 / 10.6 -> 最近的高于现价者
    assert h.compute_target(DAILY, ML, 11.9) == 12.0
    assert h.compute_target({}, None, 10.0) == 11.0         # 兜底 +10%


def test_ratchet_only_moves_up():
    assert h.ratchet(None, 9.5) == (9.5, False)
    assert h.ratchet(9.5, 10.0) == (10.0, True)
    assert h.ratchet(10.0, 9.2) == (10.0, False)            # 绝不下调
    assert h.ratchet(10.0, 10.0) == (10.0, False)


def test_pnl():
    d = h.pnl(10.45, 100, 11.57)
    assert d == {"pnl_pct": 10.72, "pnl_amount": 112.0, "market_value": 1157.0}
    assert h.pnl(None, 100, 11.57) == {"pnl_pct": None, "pnl_amount": None, "market_value": 1157.0}
    assert h.pnl(10.0, None, 11.0)["pnl_amount"] is None


def test_dist_to_stop():
    assert h.dist_to_stop_pct(10.0, 9.5) == 5.0
    assert h.dist_to_stop_pct(10.0, 10.5) == -5.0


def test_header_full_and_warning():
    md = h.holding_header_md("💼 已建立持仓跟踪", 11.57, 10.45, 100, 9.6, "结构止损", 11.83)
    assert "成本：10.45 × 100 股" in md and "+10.72%" in md and "1,157.00" in md and "9.6" in md
    assert "⚠️" not in md
    md = h.holding_header_md("x", 9.0, 10.45, None, 9.6, "成本保护", 11.0)
    assert "⚠️" in md and "未提供" not in md


def test_header_without_cost():
    md = h.holding_header_md("x", 11.57, None, None, 10.5, "结构止损", 12.0)
    assert "未提供成本价" in md
