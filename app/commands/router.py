"""指令路由: 把解析后的指令分发到对应处理逻辑, 返回回复(文本或卡片字典)。

ctx: message_id / chat_id / chat_type / is_group / sender_open_id (任务按 chat_id 归属)
"""
from __future__ import annotations

import asyncio

from app.commands.model_cmd import handle_m
from app.commands.parser import HELP_TEXT, parse
from app.logging_utils import get_logger
from app.services import task_service
from app.services.analysis_service import StockNotFound, analyze_stock

logger = get_logger("commands.router")

NOT_FOUND = "未查询到 {code} 的行情，请确认股票代码是否正确。"


async def handle_q(code: str):
    logger.info("Q 查询 %s", code)
    try:
        result = await analyze_stock(code)
    except StockNotFound:
        return NOT_FOUND.format(code=code)
    return result.to_card()


async def handle_async(text: str, ctx: dict | None = None, notify=None):
    ctx = ctx or {}
    cmd = parse(text)
    if cmd.kind == "Q":
        if notify:
            notify(f"📊 正在分析 {cmd.code}，预计 10-15 秒...")
        return await handle_q(cmd.code)
    if cmd.kind in ("B", "S"):
        if notify:
            notify(f"📊 正在分析 {cmd.code}，预计 10-15 秒...")
        if cmd.kind == "B":
            return await task_service.create_b(ctx, cmd.code)
        return await task_service.create_s(ctx, cmd.code, cmd.buy_price, cmd.quantity)
    if cmd.kind == "C":
        return task_service.cancel(ctx, cmd.code)
    if cmd.kind == "L":
        return await task_service.list_tasks(ctx)
    if cmd.kind == "M":
        return await handle_m(ctx, cmd.provider, cmd.model)
    return HELP_TEXT


def handle(text: str, ctx: dict | None = None, notify=None):
    """同步入口(供飞书回调在工作线程中调用)。"""
    return asyncio.run(handle_async(text, ctx or {}, notify))
