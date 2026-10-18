"""9 大内置投研工具 (100% 复用东方财富公开接口 + 本地缠论引擎 + SQLite 会话状态)。

1. search_security:        按中文名称/拼音/简称查询 A股、港股或板块代码
2. analyze_stock_chan:     对指定 A股/港股代码执行多级别(日线/30m/5m)缠论分析
3. get_stock_fundamentals: 获取个股基本面估值(PE/PB/市值/ROE)、换手量比、今日主力资金流向与所属行业/概念题材
4. get_market_overview:    获取 A股+港股核心大盘指数实时快照 + 沪深两市总成交额 + 涨跌家数市场情绪温度计
5. analyze_index_chan:     对大盘核心指数(上证/深成/创业板/科创50/恒指/恒科)执行多级别缠论分析
6. get_sector_hotspots:    获取今日行业板块或概念板块排行(支持涨幅榜/跌幅榜/主力资金净流入榜)
7. get_sector_stocks:      下钻指定行业或概念板块内的领涨/主力流入成分股列表
8. search_financial_news:  按关键词实时检索东方财富财经资讯与行业新闻
9. get_chat_watchlist:     查看当前会话(chat_id)所有活跃 B/S 跟踪任务及实时浮盈/止损安全垫
"""
from __future__ import annotations

import asyncio

from app.agent.tool_registry import FunctionTool, ToolRegistry
from app.data import fetcher
from app.data.market import display_code, parse_code
from app.engine import holding
from app.engine.chan_analyzer import ChanAnalyzer
from app.engine.multi_level import multi_level_analyze
from app.models import briefing_repo, cache_repo, task_repo
from app.services import briefing_service
from app.services.analysis_service import StockNotFound, analyze_stock

_index_analyzer = ChanAnalyzer()


async def _tool_search_security(keyword: str) -> dict:
    items = await asyncio.to_thread(fetcher.search_security_suggest, keyword, 6)
    return {"keyword": keyword, "matches": items}


async def _tool_analyze_stock_chan(code_or_name: str) -> dict:
    raw = (code_or_name or "").strip()
    parsed = parse_code(raw)
    code = parsed[0] if parsed else None
    if not code:
        matches = await asyncio.to_thread(fetcher.search_security_suggest, raw, 5)
        for m in matches:
            p = parse_code(str(m.get("code") or ""))
            if p:
                code = p[0]
                break
    if not code:
        return {"error": f"未能识别股票代码或名称: {code_or_name}"}
    try:
        res = await analyze_stock(code)
    except StockNotFound:
        return {"error": f"未查询到 {display_code(code)} 的行情数据"}
    r = res.report
    return {
        "basic": r.get("basic"),
        "chan_signal": r.get("chan_signal"),
        "operation": r.get("operation"),
        "auxiliary": r.get("auxiliary"),
        "daily_detail": res.daily,
    }


async def _tool_get_stock_fundamentals(code_or_name: str) -> dict:
    raw = (code_or_name or "").strip()
    parsed = parse_code(raw)
    code = parsed[0] if parsed else None
    if not code:
        matches = await asyncio.to_thread(fetcher.search_security_suggest, raw, 5)
        for m in matches:
            p = parse_code(str(m.get("code") or ""))
            if p:
                code = p[0]
                break
    if not code:
        return {"error": f"未能识别股票代码或名称: {code_or_name}"}
    data = await asyncio.to_thread(fetcher.fetch_stock_fundamentals, code)
    if not data:
        return {"error": f"未查询到 {display_code(code)} 的基本面与资金流数据"}
    data["code"] = display_code(code)
    return data


async def _tool_get_market_overview() -> dict:
    return await asyncio.to_thread(fetcher.fetch_market_overview)


async def _tool_analyze_index_chan(index_name_or_code: str = "上证指数") -> dict:
    secid, name = fetcher.resolve_index_secid(index_name_or_code)
    data = await fetcher.fetch_all_levels(secid)
    daily = await asyncio.to_thread(_index_analyzer.analyze, data["daily"], "daily", 2)
    m30 = await asyncio.to_thread(_index_analyzer.analyze, data["m30"], "30m", 2)
    m5 = await asyncio.to_thread(_index_analyzer.analyze, data["m5"], "5m", 2)
    cur_price = daily.get("last_close")
    ml = multi_level_analyze(daily, m30, m5, cur_price, decimals=2)
    return {
        "index_name": name,
        "secid": secid,
        "last_close": cur_price,
        "daily_chan": {
            "segment_direction": daily.get("segment_direction"),
            "bi_direction": daily.get("bi_direction"),
            "bi_bars": daily.get("bi_bars"),
            "zhongshu_range": daily.get("zhongshu_range"),
            "price_vs_zhongshu": daily.get("price_vs_zhongshu"),
            "buy_sell_point": daily.get("buy_sell_point"),
            "macd_divergence": daily.get("macd_divergence"),
        },
        "m30_chan": {
            "segment_direction": m30.get("segment_direction"),
            "bi_direction": m30.get("bi_direction"),
            "zhongshu_range": m30.get("zhongshu_range"),
            "buy_sell_point": m30.get("buy_sell_point"),
        },
        "multi_level_conclusion": ml.get("multi_level_conclusion"),
        "support": ml.get("support"),
        "resistance": ml.get("resistance"),
    }


async def _tool_get_sector_hotspots(
    board_type: str = "industry",
    sort_by: str = "change_pct",
    ascending: bool = False,
    limit: int = 8,
) -> dict:
    b_type = "concept" if "concept" in (board_type or "").lower() or "概念" in (board_type or "") else "industry"
    s_by = "main_inflow" if "inflow" in (sort_by or "").lower() or "资金" in (sort_by or "") else "change_pct"
    rows = await asyncio.to_thread(
        fetcher.fetch_sector_hotspots, b_type, s_by, max(1, min(int(limit or 8), 15)), bool(ascending)
    )
    return {"board_type": b_type, "sort_by": s_by, "ascending": bool(ascending), "sectors": rows}


async def _tool_get_sector_stocks(board_code_or_name: str, sort_by: str = "change_pct", limit: int = 8) -> dict:
    s_by = "main_inflow" if "inflow" in (sort_by or "").lower() or "资金" in (sort_by or "") else "change_pct"
    return await asyncio.to_thread(
        fetcher.fetch_sector_stocks, board_code_or_name, s_by, max(1, min(int(limit or 8), 15))
    )


async def _tool_search_financial_news(keyword: str, limit: int = 6) -> dict:
    news = await asyncio.to_thread(fetcher.search_financial_news, keyword, max(1, min(int(limit or 6), 10)))
    return {"keyword": keyword, "news": news}


async def _tool_get_chat_watchlist(ctx: dict) -> dict:
    chat_id = (ctx or {}).get("chat_id") or ""
    if not chat_id:
        return {"tasks": []}
    tasks = task_repo.list_active(chat_id)
    if not tasks:
        return {"tasks": [], "note": "当前会话暂无活跃的 B/S 跟踪任务"}
    out = []
    for t in tasks:
        try:
            q = await asyncio.to_thread(fetcher.fetch_quote, t.stock_code)
        except Exception:
            q = {}
        price = q.get("price")
        pct = q.get("change_pct")
        cached = cache_repo.get(t.stock_code)
        c_payload = (cached or {}).get("payload") or {}
        daily = c_payload.get("daily") or {}
        ml = c_payload.get("ml") or {}
        pnl_info = holding.pnl(t.buy_price, t.buy_quantity, price) if (t.buy_price and price) else {}
        dist_stop = holding.dist_to_stop_pct(price, t.stop_loss) if (t.stop_loss and price) else None
        out.append({
            "code": display_code(t.stock_code),
            "name": t.stock_name or q.get("name") or t.stock_code,
            "task_type": t.task_type,
            "price": price,
            "change_pct": pct,
            "buy_price": t.buy_price,
            "buy_quantity": t.buy_quantity,
            "pnl_pct": pnl_info.get("pnl_pct"),
            "stop_loss": t.stop_loss,
            "dist_to_stop_pct": dist_stop,
            "target_price": t.target_price,
            "chan_direction": ml.get("operation_direction"),
            "daily_bsp": daily.get("buy_sell_point"),
            "support": ml.get("support"),
            "resistance": ml.get("resistance"),
        })
    return {"tasks": out}


async def _tool_manage_morning_briefing(ctx: dict, action: str = "status") -> dict:
    """管理当前会话(私聊或群聊)的每日 08:00 市场信息早报订阅状态或获取最新早报链接。"""
    chat_id = (ctx or {}).get("chat_id") or ""
    chat_type = (ctx or {}).get("chat_type") or "p2p"
    act = (action or "status").strip().lower()
    if act == "enable":
        briefing_repo.set_subscription(chat_id, True, chat_type)
        return {
            "status": "enabled",
            "message": "已为当前会话开启每日北京时间 08:00 市场信息早报推送。",
            "archive_url": briefing_service.briefing_archive_url(),
        }
    if act == "disable":
        briefing_repo.set_subscription(chat_id, False, chat_type)
        return {
            "status": "disabled",
            "message": "已为当前会话关闭每日市场信息早报推送（随时可重新开启）。",
        }
    if act == "get_latest":
        rec = await briefing_service.get_or_generate_briefing(allow_latest_fallback=True)
        url = briefing_service.briefing_page_url(rec.report_date)
        return {
            "report_date": rec.report_date,
            "source_title": rec.source_title,
            "page_url": url,
            "archive_url": briefing_service.briefing_archive_url(),
            "headline_summary": rec.structured.get("headline_summary"),
            "events_table": rec.structured.get("events_table"),
        }
    enabled = briefing_repo.is_subscribed(chat_id)
    latest = briefing_repo.get_latest_briefing()
    return {
        "subscribed": enabled,
        "latest_report_date": latest.report_date if latest else None,
        "latest_page_url": briefing_service.briefing_page_url(latest.report_date) if latest else None,
        "archive_url": briefing_service.briefing_archive_url(),
    }


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register(FunctionTool(
        name="search_security",
        label="证券/板块代码查询",
        description="根据股票或板块的中文名称、简称或拼音，查询标准的 A股(6位)、港股(5位)或板块(BKxxxx)代码。",
        parameters={
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "股票或板块名称/简称，如'小米'、'宁德时代'、'券商'"}},
            "required": ["keyword"],
        },
        fn=_tool_search_security,
    ))
    registry.register(FunctionTool(
        name="analyze_stock_chan",
        label="个股多级别缠论分析",
        description="对指定的 A股 或 港股（支持直接传代码如 000001/00700 或股票中文名如 小米集团）执行多级别（日线+30分+5分）缠论计算，返回笔、线段、中枢区间、买卖点、MACD背驰、支撑压力与操作建议。",
        parameters={
            "type": "object",
            "properties": {"code_or_name": {"type": "string", "description": "股票代码(如 000001, 00700.HK)或中文名称"}},
            "required": ["code_or_name"],
        },
        fn=_tool_analyze_stock_chan,
    ))
    registry.register(FunctionTool(
        name="get_stock_fundamentals",
        label="个股估值与主力资金画像",
        description="获取个股的基本面估值(PE-TTM/PB/总市值/流通市值/ROE/营收/净利)、换手率、量比、今日主力资金净流入额与占比、以及所属行业板块与概念题材标签。",
        parameters={
            "type": "object",
            "properties": {"code_or_name": {"type": "string", "description": "股票代码或中文名称"}},
            "required": ["code_or_name"],
        },
        fn=_tool_get_stock_fundamentals,
    ))
    registry.register(FunctionTool(
        name="get_market_overview",
        label="大盘全景与情绪温度计",
        description="获取 A股与港股核心大盘指数(上证指数/深证成指/创业板指/科创50/恒生指数/恒生科技)实时涨跌快照、沪深两市总成交额(亿元)、以及全市场上涨/下跌/平盘家数比与市场情绪评价。",
        parameters={"type": "object", "properties": {}},
        fn=_tool_get_market_overview,
    ))
    registry.register(FunctionTool(
        name="analyze_index_chan",
        label="大盘指数缠论趋势",
        description="对核心大盘指数（上证指数、深证成指、创业板指、科创50、恒生指数、恒生科技指数）执行日线+30分+5分多级别缠论分析，判断指数当前笔段方向、中枢位置、是否出现买卖点及关键支撑压力位。",
        parameters={
            "type": "object",
            "properties": {
                "index_name_or_code": {
                    "type": "string",
                    "description": "指数名称或代码，如 '上证指数'、'创业板指'、'科创50'、'恒生指数'、'恒生科技'",
                }
            },
            "required": ["index_name_or_code"],
        },
        fn=_tool_analyze_index_chan,
    ))
    registry.register(FunctionTool(
        name="get_sector_hotspots",
        label="板块热点与资金流向榜",
        description="获取今日 A股 行业板块(industry)或概念板块(concept)热点排行，支持按涨跌幅(change_pct)或主力资金净流入额(main_inflow)排序，含各板块领涨龙头股。",
        parameters={
            "type": "object",
            "properties": {
                "board_type": {"type": "string", "enum": ["industry", "concept"], "description": "industry=行业板块, concept=概念板块"},
                "sort_by": {"type": "string", "enum": ["change_pct", "main_inflow"], "description": "change_pct=按涨跌幅, main_inflow=按主力资金净流入"},
                "ascending": {"type": "boolean", "description": "False=涨幅榜/净流入榜(默认), True=跌幅榜/净流出榜"},
                "limit": {"type": "integer", "description": "返回条数(默认8)"},
            },
        },
        fn=_tool_get_sector_hotspots,
    ))
    registry.register(FunctionTool(
        name="get_sector_stocks",
        label="板块成分股龙头下钻",
        description="下钻查询指定行业板块或概念板块（支持传板块中文名如'半导体'、'券商概念'或板块代码 BKxxxx）内的领涨或主力资金净流入前 N 的成分股列表。",
        parameters={
            "type": "object",
            "properties": {
                "board_code_or_name": {"type": "string", "description": "板块中文名称(如'白酒'、'小米汽车')或 BK 代码"},
                "sort_by": {"type": "string", "enum": ["change_pct", "main_inflow"], "description": "change_pct=按涨幅, main_inflow=按主力净流入"},
                "limit": {"type": "integer", "description": "返回成分股数量(默认8)"},
            },
            "required": ["board_code_or_name"],
        },
        fn=_tool_get_sector_stocks,
    ))
    registry.register(FunctionTool(
        name="search_financial_news",
        label="实时财经资讯检索",
        description="按关键词（股票名称、行业名称、宏观事件等）实时检索最新财经新闻标题、时间与摘要，用于分析板块催化剂、个股利好利空或市场动态。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "检索关键词，如 '半导体'、'小米汽车'、'降准'"},
                "limit": {"type": "integer", "description": "返回条数(默认6)"},
            },
            "required": ["keyword"],
        },
        fn=_tool_search_financial_news,
    ))
    registry.register(FunctionTool(
        name="get_chat_watchlist",
        label="当前会话跟踪池体检",
        description="获取当前聊天会话(群聊或私聊)中所有活跃的买入跟踪(B)与持仓跟踪(S)任务列表，包含实时现价、涨跌幅、持仓浮盈、距止损安全垫及缠论信号摘要。",
        parameters={"type": "object", "properties": {}},
        fn=_tool_get_chat_watchlist,
        pass_ctx=True,
    ))
    registry.register(FunctionTool(
        name="manage_morning_briefing",
        label="市场早报订阅与查阅",
        description="开启(enable)、关闭(disable)、查询状态(status)当前会话的每日 08:00 市场信息早报推送，或立即获取最新一期早报及 HTML 市场地图链接(get_latest)。",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["enable", "disable", "status", "get_latest"],
                    "description": "enable=开启早报推送, disable=关闭早报推送, status=查询当前订阅状态, get_latest=获取最新一期早报与HTML地图链接",
                }
            },
            "required": ["action"],
        },
        fn=_tool_manage_morning_briefing,
        pass_ctx=True,
    ))
