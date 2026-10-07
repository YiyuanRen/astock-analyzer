"""多级别联立(区间套)。

缠论"区间套"精髓: 日线定方向与买卖点类型, 低级别(30分/5分)定精确入场时机和价格,
把"买入区间"收窄为"买入点位"。

输入: 三个级别的 ChanAnalyzer.analyze 结果 + 实时价
输出: 综合操作建议字典(方向/介入价/止损/目标/赢赔比/仓位/支撑压力)

说明: chan.py 只给出缠论元素, 具体的介入/止损/目标价属于"策略层", 本模块基于缠论结构
(中枢上下沿、笔/线段极值、买卖点位置)做**规则化**推导, 属启发式, 仅供参考。
"""
from __future__ import annotations

# 买卖点类型 -> 仓位建议
_POSITION_ADVICE = {
    "一类买点": "一买风险较高，建议轻仓试探(10-20%)",
    "二类买点": "二买较稳，可加仓(20-40%)",
    "三类买点": "三买顺势，可重仓(30-50%)",
    "一类卖点": "一卖，建议减仓",
    "二类卖点": "二卖，建议继续减仓",
    "三类卖点": "三卖，趋势转弱，建议清仓",
}


def _fmt_rr(entry: float, stop: float, target: float) -> str | None:
    """赢赔比 1:x。"""
    risk = entry - stop
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return None
    return f"1:{round(reward / risk, 1)}"


def _safe(d: dict, key, default=None):
    return d.get(key, default) if isinstance(d, dict) else default


def multi_level_analyze(daily: dict, m30: dict, m5: dict,
                        realtime_price: float | None = None) -> dict:
    out: dict = {
        "daily": {
            "segment_direction": _safe(daily, "segment_direction"),
            "buy_sell_point": _safe(daily, "buy_sell_point"),
            "zhongshu_range": _safe(daily, "zhongshu_range"),
            "price_vs_zhongshu": _safe(daily, "price_vs_zhongshu"),
            "latest_bsp": _safe(daily, "latest_bsp"),
        },
        "operation_direction": "观望",
        "entry_zone": None,
        "stop_loss": None,
        "target_price": None,
        "rr_ratio": None,
        "position_advice": None,
        "support": None,
        "resistance": None,
        "multi_level_conclusion": "",
    }

    if _safe(daily, "error"):
        out["multi_level_conclusion"] = "日线数据不足，无法分析，建议观望。"
        return out

    cur = realtime_price or _safe(daily, "last_close")
    direction = _safe(daily, "segment_direction")

    # ---- 支撑/压力: 优先用日线中枢上下沿, 结合线段极值 ----
    zs = _safe(daily, "zhongshu_range")
    seg_low, seg_high = _safe(daily, "seg_low"), _safe(daily, "seg_high")
    support = zs[0] if zs else seg_low
    resistance = zs[1] if zs else seg_high
    # 若价格已在中枢上方, 用线段低点作为更贴近的支撑
    if zs and cur and cur > zs[1] and seg_low:
        support = max(zs[0], seg_low) if seg_low < cur else zs[1]
        resistance = seg_high or zs[1]
    out["support"] = support
    out["resistance"] = resistance

    daily_bsp = _safe(daily, "buy_sell_point")
    daily_latest = _safe(daily, "latest_bsp") or {}
    daily_is_buy = daily_latest.get("is_buy")

    # ---- 区间套判定 ----
    # 买入: 日线当前买点 + 低级别向上共振
    m30_up = _safe(m30, "bi_direction") == "向上" or (_safe(m30, "latest_bsp") or {}).get("is_buy")
    m30_down = _safe(m30, "bi_direction") == "向下" or (
        (_safe(m30, "latest_bsp") or {}).get("is_buy") is False
        and _safe(m30, "buy_sell_point") != "无信号")

    if daily_bsp and daily_bsp != "无信号" and daily_is_buy:
        confirmed = bool(m30_up)
        out["operation_direction"] = "买入" if confirmed else "观望"
        # 精确入场: 5分钟最近笔低点 ~ 当前价
        m5_low = _safe(m5, "bi_low")
        entry_low = m5_low if (m5_low and m5_low <= cur) else round(cur * 0.99, 2)
        entry_high = round(cur * 1.005, 2)
        # 止损: 入场低点下方 (跌破则买点证伪), 参考日线中枢下沿
        stop = round(min(entry_low, support or entry_low) * 0.985, 2)
        target = resistance or (seg_high if seg_high else round(cur * 1.1, 2))
        entry_mid = round((entry_low + entry_high) / 2, 2)
        out.update({
            "entry_zone": (entry_low, entry_high),
            "stop_loss": stop,
            "target_price": target,
            "rr_ratio": _fmt_rr(entry_mid, stop, target),
            "position_advice": _POSITION_ADVICE.get(daily_bsp),
        })
        out["m30"] = {"confirmed": confirmed}
        out["m5"] = {"precise_entry": entry_low, "stop_loss": stop}
        conf_txt = "30分钟向上共振确认" if confirmed else "但30分钟尚未向上共振，需等待"
        out["multi_level_conclusion"] = (
            f"日线出现{daily_bsp}，{conf_txt}；"
            f"5分钟精确入场参考 {entry_low}-{entry_high}。")

    elif daily_bsp and daily_bsp != "无信号" and daily_is_buy is False:
        out["operation_direction"] = "卖出"
        out["position_advice"] = _POSITION_ADVICE.get(daily_bsp)
        out["stop_loss"] = support  # 跌破支撑加速
        out["target_price"] = support
        out["multi_level_conclusion"] = (
            f"日线出现{daily_bsp}，趋势转弱，建议在 {resistance} 附近减仓，"
            f"关注支撑 {support}。")

    else:
        # 无当前买卖点: 观望, 给出关键观察位
        out["operation_direction"] = "观望"
        trend_txt = {"上升": "多头趋势中", "下降": "空头趋势中", "震荡": "震荡格局"}.get(direction, "")
        out["multi_level_conclusion"] = (
            f"日线当前无明确买卖点({trend_txt})；"
            f"关注支撑 {support} / 压力 {resistance}，等待买点出现再介入。")

    return out
