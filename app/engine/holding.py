"""S 任务(持仓)的止损/目标位与盈亏计算 —— 全部为纯函数, 便于单测。

规则(均为规则化启发式, 非 chan.py 原生输出):
- 结构止损: 日线结构支撑(中枢下沿 / 线段低点 / 最近买点价)中, 低于现价至少 min_stop_gap 的最近一档,
            再下浮 structural_buffer; 找不到则 现价×(1-fallback_stop_pct)
- 成本保护: 若提供成本价, 与 成本×(1-cost_stop_pct) 取较高者(更保守)
- 目标位:   日线结构压力(多级别联立的压力位 / 线段高点 / 中枢上沿)中, 高于现价的最近一档;
            找不到则 现价×(1+fallback_target_pct)
- 止损位只上移不下移(ratchet): 每日收盘重算, 新值更高才上调; 重发 S 指令则重置
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULTS = {
    "structural_buffer": 0.015,
    "min_stop_gap": 0.02,
    "cost_stop_pct": 0.08,
    "fallback_stop_pct": 0.07,
    "fallback_target_pct": 0.10,
}


def _p(params: dict | None) -> dict:
    return {**DEFAULTS, **(params or {})}


@dataclass
class Levels:
    stop_loss: float
    target_price: float
    stop_source: str            # "结构止损" | "成本保护" | "兜底(-7%)"
    structural_stop: float
    personal_stop: float | None


def _supports(daily: dict) -> list[float]:
    cands: list[float] = []
    zs = daily.get("zhongshu_range")
    if zs:
        cands.append(float(zs[0]))
    if daily.get("seg_low"):
        cands.append(float(daily["seg_low"]))
    bsp = daily.get("latest_bsp") or {}
    if bsp.get("is_buy") and bsp.get("price"):
        cands.append(float(bsp["price"]))
    return cands


def _resistances(daily: dict, ml: dict | None) -> list[float]:
    cands: list[float] = []
    if ml and ml.get("resistance"):
        cands.append(float(ml["resistance"]))
    if daily.get("seg_high"):
        cands.append(float(daily["seg_high"]))
    zs = daily.get("zhongshu_range")
    if zs:
        cands.append(float(zs[1]))
    return cands


def structural_stop(daily: dict, price: float, params: dict | None = None) -> tuple[float, bool]:
    """返回 (结构止损价, 是否使用了兜底)。"""
    p = _p(params)
    ok = [c for c in _supports(daily) if c <= price * (1 - p["min_stop_gap"])]
    if ok:
        return round(max(ok) * (1 - p["structural_buffer"]), 2), False
    return round(price * (1 - p["fallback_stop_pct"]), 2), True


def compute_target(daily: dict, ml: dict | None, price: float, params: dict | None = None) -> float:
    p = _p(params)
    above = [c for c in _resistances(daily, ml) if c >= price * 1.005]
    if above:
        return round(min(above), 2)
    return round(price * (1 + p["fallback_target_pct"]), 2)


def compute_levels(daily: dict, ml: dict | None, price: float, cost: float | None = None,
                   params: dict | None = None) -> Levels:
    p = _p(params)
    s_stop, fallback = structural_stop(daily, price, p)
    personal = round(cost * (1 - p["cost_stop_pct"]), 2) if cost else None
    if personal is not None and personal > s_stop:
        stop, source = personal, "成本保护"
    else:
        stop, source = s_stop, ("兜底(-%d%%)" % round(p["fallback_stop_pct"] * 100) if fallback else "结构止损")
    return Levels(stop, compute_target(daily, ml, price, p), source, s_stop, personal)


def ratchet(old_stop: float | None, new_stop: float) -> tuple[float, bool]:
    """只上移不下移。返回 (生效止损, 是否上移)。"""
    if old_stop is None:
        return new_stop, False
    if new_stop > old_stop:
        return new_stop, True
    return old_stop, False


def pnl(cost: float | None, qty: int | None, price: float) -> dict:
    out = {"pnl_pct": None, "pnl_amount": None, "market_value": None}
    if cost:
        out["pnl_pct"] = round((price / cost - 1) * 100, 2)
        if qty:
            out["pnl_amount"] = round((price - cost) * qty, 2)
    if qty:
        out["market_value"] = round(price * qty, 2)
    return out


def dist_to_stop_pct(price: float, stop: float) -> float:
    """现价距止损位的百分比(正数=还有多少空间; 负数=已跌破)。"""
    return round((price - stop) / price * 100, 2)


def holding_header_md(title: str, price: float, cost: float | None, qty: int | None,
                      stop: float, stop_source: str, target: float, note: str = "") -> str:
    """持仓头部区块(确定性生成, 不经 LLM)。"""
    lines = [f"**{title}**"]
    if cost:
        d = pnl(cost, qty, price)
        sign = "+" if d["pnl_pct"] >= 0 else ""
        row = f"- 成本：{cost:g}" + (f" × {qty} 股" if qty else "") + f"｜现价：{price:g}｜浮盈：**{sign}{d['pnl_pct']}%**"
        if d["pnl_amount"] is not None:
            row += f"（{'+' if d['pnl_amount'] >= 0 else ''}{d['pnl_amount']:,.2f}）"
        lines.append(row)
        if d["market_value"] is not None:
            lines.append(f"- 持仓市值：{d['market_value']:,.2f}")
    else:
        lines.append(f"- 现价：{price:g}（未提供成本价，仅按结构位监控）")
    dist = dist_to_stop_pct(price, stop)
    warn = "  ⚠️ **当前价已低于止损位，将触发止损提醒**" if dist <= 0 else ""
    lines.append(f"- 🛑 止损：**{stop:g}**（{stop_source}，距现价 {dist:+.2f}%）{warn}")
    lines.append(f"- 🎯 目标：**{target:g}**")
    if note:
        lines.append(f"- {note}")
    return "\n".join(lines)
