"""报告组装: 把多级别缠论结果 + 实时行情 组装成标准报告字典(对应产品规格字段),
并提供降级用的纯文本渲染(不含无意义的"暂无"占位行)。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.config import get_config
from app.data.market import currency_of, display_code, market_label


def _volume_status(daily_df: pd.DataFrame, lookback: int = 5) -> str:
    """今日成交量 vs 前N日均量: 放量/缩量/平量。"""
    try:
        vols = daily_df["volume"].astype(float)
        if len(vols) < lookback + 1:
            return "未知"
        today = vols.iloc[-1]
        avg = vols.iloc[-lookback - 1:-1].mean()
        if avg <= 0:
            return "未知"
        ratio = today / avg
        if ratio >= 1.3:
            return "放量"
        if ratio <= 0.7:
            return "缩量"
        return "平量"
    except Exception:
        return "未知"


def build_report(quote: dict, daily: dict, m30: dict, m5: dict, ml: dict,
                 daily_df: pd.DataFrame | None = None) -> dict:
    cfg = get_config()
    disclaimer = cfg.risk_disclaimer

    name = quote.get("name") or "-"
    code = quote.get("code") or "-"
    price = quote.get("price")
    pct = quote.get("change_pct")

    zs = daily.get("zhongshu_range")
    zs_pos = daily.get("price_vs_zhongshu")
    zs_text = None
    if zs:
        zs_text = f"价格在中枢{zs_pos}（中枢 {zs[0]}-{zs[1]}）" if zs_pos else f"中枢 {zs[0]}-{zs[1]}"

    report = {
        "basic": {
            "name": name,
            "code": display_code(code) if code != "-" else "-",
            "market": market_label(code) if code != "-" else "A股",
            "currency": currency_of(code) if code != "-" else "CNY",
            "price": price,
            "change_pct": pct,
            "period": "多级别（日线/30分/5分）",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "chan_signal": {
            "bi_direction": daily.get("bi_direction"),
            "segment_direction": daily.get("segment_direction"),
            "zhongshu": zs_text,
            "buy_sell_point": daily.get("buy_sell_point"),
            "macd_divergence": daily.get("macd_divergence"),
            "bi_bars": daily.get("bi_bars"),
            "multi_level_conclusion": ml.get("multi_level_conclusion"),
        },
        "operation": {
            "direction": ml.get("operation_direction"),
            "entry_zone": ml.get("entry_zone"),
            "target_price": ml.get("target_price"),
            "stop_loss": ml.get("stop_loss"),
            "rr_ratio": ml.get("rr_ratio"),
            "position_advice": ml.get("position_advice"),
        },
        "auxiliary": {
            "support": ml.get("support"),
            "resistance": ml.get("resistance"),
            "volume_status": _volume_status(daily_df) if daily_df is not None else "未知",
        },
        "disclaimer": disclaimer,
        "data_insufficient": bool(daily.get("error")),
    }
    return report


def render_plain(report: dict) -> str:
    """结构化降级渲染(LLM 超时降级时作为分析正文展示, 自动省去为空的字段)。"""
    b = report["basic"]
    c = report["chan_signal"]
    o = report["operation"]
    a = report["auxiliary"]

    if report.get("data_insufficient"):
        return (f"【{b['name']} {b['code']}】\n"
                f"数据不足，建议观望。\n{report['disclaimer']}")

    seg = c.get("segment_direction") or "震荡"
    bi = c.get("bi_direction") or "未知"
    bars = c.get("bi_bars")
    bars_txt = f"（已运行 {bars} 根K线）" if bars else ""
    zs = c.get("zhongshu") or "暂未形成显著中枢"
    bsp = c.get("buy_sell_point") or "无信号"
    macd = "已出现背驰迹象" if c.get("macd_divergence") else "尚未出现背驰"
    ml_conc = c.get("multi_level_conclusion") or ""
    sup, res = a.get("support"), a.get("resistance")
    vol = a.get("volume_status") or "平量"
    direction = o.get("direction") or "观望"

    lines = [
        "#### 💡 走势结构与深度解读",
        f"- **趋势与笔段结构**：日线级别当前线段处于**{seg}**趋势，最新一笔方向**{bi}**{bars_txt}；"
        f"{zs}，成交量呈**{vol}**状态。",
        f"- **动力学与多级别共振**：日线买卖点状态为**{bsp}**（MACD{macd}）。{ml_conc}",
        "#### 🎯 关键信号与操作预案",
    ]

    ez = o.get("entry_zone")
    if ez or direction in ("买入", "卖出"):
        plan_parts = [f"当前操作方向为**{direction}**"]
        if ez:
            plan_parts.append(f"建议关注介入区间 **{ez[0]}-{ez[1]}**")
        if o.get("stop_loss") is not None:
            plan_parts.append(f"风控止损锚定 **{o['stop_loss']}**")
        if o.get("target_price") is not None:
            plan_parts.append(f"目标位看至 **{o['target_price']}**")
        if o.get("rr_ratio"):
            plan_parts.append(f"预估盈亏比 **{o['rr_ratio']}**")
        if o.get("position_advice"):
            plan_parts.append(f"仓位参考：{o['position_advice']}")
        lines.append("- " + "；".join(plan_parts) + "。")
    else:
        lines.append(
            f"- **耐心等待右侧信号**：当前未出现日线级别买点且次级别尚未形成向上共振，保持**观望**，"
            "避免在趋势未企稳前盲目左侧介入。"
        )
        if sup is not None:
            lines.append(f"- **下方防守观察位（{sup}）**：关注回踩该支撑区域时能否出现底分型停顿及 MACD 绿柱缩短的企稳信号。")
        if res is not None:
            lines.append(f"- **上方突破观察位（{res}）**：关注放量突破该阻力位后回抽不破的结构确认机会。")

    lines.append(f"\n{report['disclaimer']}")
    return "\n".join(lines)
