"""指令路由: 把解析后的指令分发到对应处理逻辑, 返回回复文本。

当前实现: Q 即时查询全链路。B/S/C/L 任务系统(Sprint3)暂为占位。
M 可查看当前模型; 切换(热配置)待任务系统接入 SQLite 后支持。
"""
from __future__ import annotations

import asyncio

from app.commands.parser import HELP_TEXT, parse
from app.config import get_config
from app.data import fetcher
from app.engine.chan_analyzer import ChanAnalyzer
from app.engine.multi_level import multi_level_analyze
from app.engine.report_builder import build_report
from app.llm.report_generator import generate_report
from app.logging_utils import get_logger

logger = get_logger("commands.router")

_analyzer = ChanAnalyzer()


async def handle_q(code: str) -> str:
    logger.info("Q 查询 %s", code)
    quote = await asyncio.to_thread(fetcher.fetch_quote, code)
    if not quote or quote.get("price") is None:
        return f"未查询到 {code} 的行情，请确认股票代码是否正确。"

    data = await fetcher.fetch_all_levels(code)
    daily = _analyzer.analyze(data["daily"], "daily")
    m30 = _analyzer.analyze(data["m30"], "30m")
    m5 = _analyzer.analyze(data["m5"], "5m")
    ml = multi_level_analyze(daily, m30, m5, quote.get("price"))
    report = build_report(quote, daily, m30, m5, ml, data["daily"])
    markdown = await generate_report(report)

    name = quote.get("name") or code
    direction = ml.get("operation_direction")
    template = {"买入": "red", "卖出": "green"}.get(direction, "blue")
    return {"card": {"title": f"📊 {name} {code}", "markdown": markdown, "template": template}}


def _handle_m(provider: str | None, model: str | None) -> str:
    cfg = get_config()
    if not provider:
        return (f"当前模型：{cfg.llm.get('default_provider')} / {cfg.llm.get('default_model')}\n"
                f"(运行时热切换将随任务系统接入，敬请期待)")
    return f"模型切换功能开发中（Sprint 3 接入热配置）。当前仍为 {cfg.llm.get('default_provider')}/{cfg.llm.get('default_model')}。"


async def handle_async(text: str, ctx: dict | None = None, notify=None) -> str:
    cmd = parse(text)
    if cmd.kind == "Q":
        if notify:
            notify(f"📊 正在分析 {cmd.code}，预计 10-15 秒...")
        return await handle_q(cmd.code)
    if cmd.kind in ("B", "S", "C", "L"):
        return "任务跟踪功能开发中（Sprint 3）。当前可用 Q 做即时查询，例如：Q 000001"
    if cmd.kind == "M":
        return _handle_m(cmd.provider, cmd.model)
    return HELP_TEXT


def handle(text: str, ctx: dict | None = None, notify=None) -> str:
    """同步入口(供飞书回调在工作线程中调用)。"""
    return asyncio.run(handle_async(text, ctx or {}, notify))
