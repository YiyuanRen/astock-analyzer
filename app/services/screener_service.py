"""两级漏斗缠论智能选股服务 (PICK [板块名/港股/主力])。

两级漏斗架构:
- 第一级(1秒行情/资金宽筛):
  - 默认(无参/主力/热点): 取今日主力资金净流入前 4 行业板块 + 涨幅前 4 行业板块的领涨股与前排成分股(去重约 10~12 只)
  - 指定板块(如 'PICK 半导体' / 'PICK 券商'): 下钻该板块内主力净流入与涨幅前 10 的成分股
  - 港股模式('PICK 港股' / 'PICK HK'): 扫描港股核心科技与蓝筹活跃池
- 第二级(并发缠论精筛 + 历史回测胜率校验):
  - 对候选股并发拉取日线与 30 分钟 K 线, 运行 ChanAnalyzer + multi_level_analyze + run_chan_backtest
  - 按「是否出现买点 > 价格在中枢上方/上升趋势 > 历史买点回测胜率」排序, 输出飞书 Schema 2.0 选股雷达卡片
"""
from __future__ import annotations

import asyncio

from app.bot.card_builder import (
    banner_box,
    fmt_pct,
    grey_row,
    kpi_col,
    kpi_row,
    schema2_card,
)
from app.config import get_config
from app.data import fetcher
from app.data.market import decimals_of, display_code, parse_code
from app.engine.backtester import run_chan_backtest
from app.engine.chan_analyzer import ChanAnalyzer
from app.engine.multi_level import multi_level_analyze
from app.logging_utils import get_logger

logger = get_logger("services.screener")

_az = ChanAnalyzer()

HK_CORE_POOL = [
    ("00700", "腾讯控股"),
    ("01810", "小米集团-W"),
    ("09988", "阿里巴巴-W"),
    ("03690", "美团-W"),
    ("00981", "中芯国际"),
    ("01211", "比亚迪股份"),
    ("09618", "京东集团-SW"),
    ("01024", "快手-W"),
]


async def _build_candidate_pool(scope: str | None, max_candidates: int = 10) -> tuple[str, list[dict]]:
    q = (scope or "").strip()
    if q.upper() in ("HK", "港股", "港股科技", "恒科"):
        return "港股核心活跃池", [{"code": c, "name": n, "board": "港股核心"} for c, n in HK_CORE_POOL[:max_candidates]]

    if q and q not in ("热点", "主力", "全市场", "默认"):
        sec = await asyncio.to_thread(fetcher.fetch_sector_stocks, q, "main_inflow", max_candidates)
        stocks = sec.get("stocks") or []
        b_name = sec.get("board_name") or q
        pool = []
        for s in stocks:
            parsed = parse_code(str(s.get("code") or ""))
            if parsed and "ST" not in str(s.get("name") or "").upper():
                pool.append({
                    "code": parsed[0],
                    "name": s.get("name"),
                    "board": b_name,
                    "main_inflow_wan": s.get("main_net_inflow_wan"),
                })
        return f"板块精筛（{b_name}）", pool[:max_candidates]

    # 默认: 今日主力净流入前 4 行业 + 涨幅前 4 行业的龙头与前排股
    inflow_boards, gain_boards = await asyncio.gather(
        asyncio.to_thread(fetcher.fetch_sector_hotspots, "industry", "main_inflow", 4, False),
        asyncio.to_thread(fetcher.fetch_sector_hotspots, "industry", "change_pct", 4, False),
    )
    seen: set[str] = set()
    pool: list[dict] = []
    for b in [*inflow_boards, *gain_boards]:
        l_code = str(b.get("leader_code") or "")
        l_name = str(b.get("leader_name") or "")
        parsed = parse_code(l_code)
        if parsed and parsed[0] not in seen and "ST" not in l_name.upper():
            seen.add(parsed[0])
            pool.append({
                "code": parsed[0],
                "name": l_name,
                "board": b.get("board_name") or "热门行业",
            })
        if len(pool) >= max_candidates:
            break

    # 若不足则下钻排名第 1 的主力流入板块补齐
    if len(pool) < max_candidates and inflow_boards:
        top_bk = inflow_boards[0].get("board_code")
        top_name = inflow_boards[0].get("board_name")
        if top_bk:
            sub = await asyncio.to_thread(fetcher.fetch_sector_stocks, top_bk, "main_inflow", max_candidates)
            for s in (sub.get("stocks") or []):
                parsed = parse_code(str(s.get("code") or ""))
                if parsed and parsed[0] not in seen and "ST" not in str(s.get("name") or "").upper():
                    seen.add(parsed[0])
                    pool.append({"code": parsed[0], "name": s.get("name"), "board": top_name})
                if len(pool) >= max_candidates:
                    break

    return "全市场主力净流入 & 强势板块龙头池", pool[:max_candidates]


async def _evaluate_one_candidate(item: dict) -> dict | None:
    code = item["code"]
    dec = decimals_of(code)
    try:
        quote, daily_df, m30_df = await asyncio.gather(
            asyncio.to_thread(fetcher.fetch_quote, code),
            asyncio.to_thread(fetcher.fetch_daily, code, 2),
            asyncio.to_thread(fetcher.fetch_30min, code, 3),
        )
        if not quote or quote.get("price") is None or len(daily_df) < 35:
            return None
        daily = await asyncio.to_thread(_az.analyze, daily_df, "daily", dec)
        m30 = await asyncio.to_thread(_az.analyze, m30_df, "30m", dec)
        ml = multi_level_analyze(daily, m30, {}, quote.get("price"), decimals=dec)
        bt = await asyncio.to_thread(run_chan_backtest, daily_df, code, quote.get("name") or item.get("name") or code)

        daily_bsp = daily.get("buy_sell_point") or "无信号"
        m30_bsp = m30.get("buy_sell_point") or "无信号"
        has_buy_bsp = ("买" in daily_bsp) or ("买" in m30_bsp)
        zs_pos = daily.get("price_vs_zhongshu") or "—"
        seg_dir = daily.get("segment_direction") or "—"

        # 评分排序: 日线买点(+100) > 30m买点(+60) > 中枢上方/上升线段(+30) + 历史胜率权重
        score = 0.0
        if "买" in daily_bsp:
            score += 100.0
        elif "买" in m30_bsp:
            score += 60.0
        if seg_dir == "上升":
            score += 20.0
        if zs_pos == "上方":
            score += 15.0
        score += float(bt.get("win_rate_pct") or 0.0) * 0.3

        return {
            "code": display_code(code),
            "raw_code": code,
            "name": quote.get("name") or item.get("name") or display_code(code),
            "board": item.get("board") or "—",
            "price": quote.get("price"),
            "change_pct": quote.get("change_pct"),
            "daily_bsp": daily_bsp,
            "m30_bsp": m30_bsp,
            "segment_direction": seg_dir,
            "zhongshu_pos": zs_pos,
            "operation_direction": ml.get("operation_direction") or "观望",
            "support": ml.get("support"),
            "resistance": ml.get("resistance"),
            "entry_zone": ml.get("entry_zone"),
            "bt_win_rate_pct": bt.get("win_rate_pct", 0.0),
            "bt_trades": bt.get("trade_count", 0),
            "bt_return_pct": bt.get("total_return_pct", 0.0),
            "has_buy_signal": has_buy_bsp,
            "score": round(score, 2),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("选股扫描 %s 异常: %s", code, e)
        return None


async def run_chan_screener(scope: str | None = None, max_candidates: int = 8) -> dict:
    pool_label, pool = await _build_candidate_pool(scope, max_candidates=max_candidates)
    if not pool:
        return {"pool_label": pool_label, "scanned_count": 0, "picks": [], "error": f"未能找到符合条件({scope})的候选股票池"}

    sem = asyncio.Semaphore(2)

    async def _guarded(it):
        async with sem:
            return await _evaluate_one_candidate(it)

    evaluated = [x for x in await asyncio.gather(*(_guarded(it) for it in pool)) if x is not None]
    evaluated.sort(key=lambda x: x["score"], reverse=True)
    return {
        "pool_label": pool_label,
        "scanned_count": len(evaluated),
        "buy_signal_count": sum(1 for x in evaluated if x["has_buy_signal"]),
        "picks": evaluated,
    }


async def execute_pick_card(scope: str | None = None) -> dict | str:
    res = await run_chan_screener(scope, max_candidates=8)
    if res.get("error") or not res.get("picks"):
        return f"⚠️ 选股扫描未找到有效结果：{res.get('error') or '候选池为空'}（可尝试 `PICK`、`PICK 半导体` 或 `PICK 港股`）。"

    picks = res["picks"]
    n_sig = res["buy_signal_count"]
    pool_label = res["pool_label"]
    title = f"📡 缠论两级漏斗选股 · {pool_label}"
    subtitle = f"已精筛 {res['scanned_count']} 只活跃标的 · 命中买点信号 {n_sig} 只 · 附近2年缠论回测胜率"
    tpl = "red" if n_sig > 0 else "blue"

    cols = [
        kpi_col("🔎 扫描候选池", f"**<font color='blue'>{res['scanned_count']} 只</font>**",
                f"<font color='grey'>{pool_label}</font>", bg="blue-50"),
        kpi_col("🔴 命中日线/30m买点", f"**<font color='red'>{n_sig} 只</font>**",
                "<font color='grey'>一买/二买/三买共振</font>", bg="red-50" if n_sig > 0 else "grey-50"),
        kpi_col("🏆 榜首标的", f"**<font color='violet'>{picks[0]['name']}</font>**",
                f"<font color='grey'>{picks[0]['code']} · 历史胜率 {picks[0]['bt_win_rate_pct']:.0f}%</font>",
                bg="violet-50"),
    ]
    elements: list[dict] = [
        kpi_row(cols, flex_mode="trisect"),
        {"tag": "markdown", "content": "#### 📋 缠论结构评分排行（优先展示买点与多头结构标的）"},
        grey_row([
            ("**标的 / 板块**", 2),
            ("**现价 / 涨跌**", 2),
            ("**缠论结构 & 信号**", 3),
            ("**支撑 / 压力**", 2),
            ("**近2年回测胜率**", 2),
        ]),
        {"tag": "hr"},
    ]
    flat_lines = [f"**{title}**\n{subtitle}"]

    for it in picks:
        badge = "🔴 " if it["has_buy_signal"] else ("🟢 " if it["segment_direction"] == "上升" else "🟠 ")
        sig_str = f"日线:{it['daily_bsp']} · 30m:{it['m30_bsp']}"
        struct_cell = f"{badge}**{sig_str}**\n<font color='grey'>{it['segment_direction']}线段 · 中枢{it['zhongshu_pos']}</font>"
        sr_cell = f"支撑 **{it['support'] or '—'}**\n压力 {it['resistance'] or '—'}"
        bt_cell = f"**{it['bt_win_rate_pct']:.1f}%** ({it['bt_trades']}笔)\n<font color='grey'>累计 {it['bt_return_pct']:+.1f}%</font>"
        elements.append(grey_row([
            (f"**{it['name']}** ({it['code']})\n<font color='grey'>{it['board']}</font>", 2),
            (f"**{it['price']:g}**\n{fmt_pct(it['change_pct'])}", 2),
            (struct_cell, 3),
            (sr_cell, 2),
            (bt_cell, 2),
        ]))
        flat_lines.append(
            f"- {badge}**{it['name']} ({it['code']})** [{it['board']}]：现价 {it['price']:g} ({it['change_pct']:+.2f}%)｜"
            f"{sig_str}｜支撑 {it['support']}｜历史胜率 {it['bt_win_rate_pct']:.1f}%"
        )

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": (
            "<font color='grey'>💡 操作衔接：看中标的发送 `B 代码` 开启买入盯盘，发送 `BT 代码` 查看完整回测净值曲线，"
            f"发送 `S 代码 @价格 股数` 记入10万实操账本 · {get_config().risk_disclaimer}</font>"
        ),
    })
    raw_card = schema2_card(title, subtitle, tpl, elements)
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": tpl,
            "markdown": "\n".join(flat_lines),
            "raw_card": raw_card,
        }
    }
