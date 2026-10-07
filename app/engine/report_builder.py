"""报告组装: 把多级别缠论结果 + 实时行情 组装成标准报告字典(对应产品规格字段),
并提供降级用的纯文本渲染。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.config import get_config


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
            "code": code,
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
    """纯文本渲染(LLM 超时降级 / CLI 展示)。"""
    b = report["basic"]
    c = report["chan_signal"]
    o = report["operation"]
    a = report["auxiliary"]

    if report.get("data_insufficient"):
        return (f"【{b['name']} {b['code']}】\n"
                f"数据不足，建议观望。\n{report['disclaimer']}")

    def fmt(v, suffix=""):
        return f"{v}{suffix}" if v not in (None, "") else "-"

    pct = b.get("change_pct")
    pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "-"
    ez = o.get("entry_zone")
    ez_str = f"{ez[0]}-{ez[1]}" if ez else "-"
    macd = c.get("macd_divergence")
    macd_str = "是" if macd else "否"

    lines = [
        f"📊 {b['name']} {b['code']}  |  {b['time']}",
        f"当前价 {fmt(b.get('price'))}  涨跌 {pct_str}",
        "—" * 12,
        "【缠论信号】",
        f"趋势(线段)：{fmt(c.get('segment_direction'))}  |  笔方向：{fmt(c.get('bi_direction'))}",
        f"中枢：{fmt(c.get('zhongshu'))}",
        f"买卖点：{fmt(c.get('buy_sell_point'))}  |  MACD背驰：{macd_str}",
        f"当前笔持续：{fmt(c.get('bi_bars'))} 根K线",
        f"多级别联立：{fmt(c.get('multi_level_conclusion'))}",
        "—" * 12,
        "【操作建议】",
        f"方向：{fmt(o.get('direction'))}  |  仓位：{fmt(o.get('position_advice'))}",
        f"介入价：{ez_str}",
        f"止损：{fmt(o.get('stop_loss'))}  |  目标：{fmt(o.get('target_price'))}",
        f"赢赔比：{fmt(o.get('rr_ratio'))}",
        "—" * 12,
        f"支撑：{fmt(a.get('support'))}  压力：{fmt(a.get('resistance'))}",
        f"成交量：{fmt(a.get('volume_status'))}",
        "—" * 12,
        report["disclaimer"],
    ]
    return "\n".join(lines)
