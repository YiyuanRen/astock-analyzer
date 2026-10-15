"""指令路由: 把解析后的指令分发到对应处理逻辑, 返回回复(文本或卡片字典)。

- Q / B / S / C / L / M: 走确定性指令处理链路(其中 Q/B/S 会自动将分析摘要记入会话 memory)
- HELP (H / help / 帮助 / ?): 返回指令帮助说明
- CHAT (任意自然语言提问): 路由给智能投研 Agent (支持多步工具调用 + 20 轮会话记忆)
"""
from __future__ import annotations

import asyncio

from app.agent import memory
from app.agent.agent_runner import run_agent_chat
from app.commands.model_cmd import handle_m
from app.commands.parser import HELP_TEXT, parse
from app.data.market import display_code
from app.logging_utils import get_logger
from app.services import task_service
from app.services.analysis_service import StockNotFound, analyze_stock

logger = get_logger("commands.router")

NOT_FOUND = "未查询到 {code} 的行情，请确认股票代码是否正确。"


async def handle_q(code: str, chat_id: str | None = None):
    logger.info("Q 查询 %s", code)
    try:
        result = await analyze_stock(code)
    except StockNotFound:
        return NOT_FOUND.format(code=code)
    if chat_id:
        memory.record_command_analysis(chat_id, f"Q {display_code(code)}", result)
    return result.to_card()


async def handle_async(text: str, ctx: dict | None = None, notify=None):
    ctx = ctx or {}
    chat_id = ctx.get("chat_id")
    cmd = parse(text)
    if cmd.kind == "Q":
        if notify:
            notify(f"📊 正在分析 {display_code(cmd.code)}，预计 10-15 秒...")
        return await handle_q(cmd.code, chat_id=chat_id)
    if cmd.kind in ("B", "S"):
        if notify:
            notify(f"📊 正在分析 {display_code(cmd.code)}，预计 10-15 秒...")
        if cmd.kind == "B":
            return await task_service.create_b(ctx, cmd.code)
        return await task_service.create_s(ctx, cmd.code, cmd.buy_price, cmd.quantity)
    if cmd.kind == "C":
        return task_service.cancel(ctx, cmd.code)
    if cmd.kind == "L":
        return await task_service.list_tasks(ctx)
    if cmd.kind == "M":
        return await handle_m(ctx, cmd.provider, cmd.model)
    if cmd.kind == "CHAT":
        if notify:
            notify("🤖 正在思考并调取实时投研数据...")
        return await run_agent_chat(cmd.text or text, ctx)
    return HELP_TEXT


def handle(text: str, ctx: dict | None = None, notify=None):
    """同步入口(供飞书回调在工作线程中调用)。"""
    return asyncio.run(handle_async(text, ctx or {}, notify))
