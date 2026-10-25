"""指令路由: 把解析后的指令分发到对应处理逻辑, 返回回复(文本或卡片字典)。

- Q / B / S / C / L / M: 走确定性指令处理链路(其中 Q/B/S 会自动将分析摘要记入会话 memory)
- HELP (H / help / 帮助 / ?): 返回指令帮助说明
- CHAT (任意自然语言提问): 路由给智能投研 Agent (支持多步工具调用 + 20 轮会话记忆)
"""
from __future__ import annotations

import asyncio

import re

from app.agent import memory
from app.agent.agent_runner import run_agent_chat
from app.bot.card_builder import build_briefing_status_card, build_morning_briefing_card
from app.commands.model_cmd import handle_m
from app.commands.parser import HELP_TEXT, parse
from app.config import get_config
from app.data.market import display_code
from app.logging_utils import get_logger
from app.models import briefing_repo
from app.services import briefing_service, task_service
from app.services.analysis_service import StockNotFound, analyze_stock

logger = get_logger("commands.router")

NOT_FOUND = "未查询到 {code} 的行情，请确认股票代码是否正确。"

_BRIEFING_ENABLE_RE = re.compile(r"^(?:开启|打开|订阅|恢复)(?:每日|市场)?早报(?:推送)?$", re.I)
_BRIEFING_DISABLE_RE = re.compile(r"^(?:关闭|取消|退订|停止)(?:每日|市场)?早报(?:推送)?$", re.I)
_BRIEFING_NOW_RE = re.compile(r"^(?:今日|最新|查看|看看)?(?:市场)?(?:早报|早餐)$", re.I)


async def handle_q(code: str, chat_id: str | None = None, chat_type: str = "p2p", notify=None):
    logger.info("Q 查询 %s", code)
    try:
        result = await analyze_stock(code, notify=notify)
    except StockNotFound:
        return NOT_FOUND.format(code=code)
    if chat_id:
        briefing_repo.ensure_subscribed(chat_id, chat_type)
        memory.record_command_analysis(chat_id, f"Q {display_code(code)}", result)
    return result.to_card()


async def _handle_briefing_now():
    disclaimer = get_config().risk_disclaimer
    try:
        rec = await briefing_service.get_or_generate_briefing(allow_latest_fallback=True)
        return build_morning_briefing_card(
            rec,
            page_url=briefing_service.briefing_page_url(rec.report_date),
            archive_url=briefing_service.briefing_archive_url(),
            disclaimer=disclaimer,
        )
    except Exception as e:  # noqa: BLE001
        latest = briefing_repo.get_latest_briefing()
        return build_briefing_status_card(
            report_date=latest.report_date if latest else "今日",
            reason=f"{type(e).__name__}: {e}",
            latest_date=latest.report_date if latest else None,
            latest_url=briefing_service.briefing_page_url(latest.report_date) if latest else None,
            archive_url=briefing_service.briefing_archive_url(),
            disclaimer=disclaimer,
        )


async def handle_async(text: str, ctx: dict | None = None, notify=None):
    ctx = ctx or {}
    chat_id = ctx.get("chat_id")
    chat_type = ctx.get("chat_type") or "p2p"
    raw_s = (text or "").strip()

    # 快捷匹配早报开启/关闭/立即查看
    if _BRIEFING_ENABLE_RE.match(raw_s):
        if chat_id:
            briefing_repo.set_subscription(chat_id, True, chat_type)
        return (
            "✅ 已为当前会话**开启**每日 08:00 市场信息早报推送。\n"
            f"🗂️ 往期早报归档：{briefing_service.briefing_archive_url()}\n"
            "（如需关闭，随时发送「关闭早报」即可）"
        )
    if _BRIEFING_DISABLE_RE.match(raw_s):
        if chat_id:
            briefing_repo.set_subscription(chat_id, False, chat_type)
        return "🔕 已为当前会话**关闭**每日市场信息早报推送（随时发送「开启早报」可重新开启）。"
    if _BRIEFING_NOW_RE.match(raw_s):
        if notify:
            notify("🗺️ 正在生成市场信息早报与一页 HTML 可视化地图，预计 15-30 秒...")
        return await _handle_briefing_now()

    cmd = parse(text)
    if cmd.kind == "Q":
        if notify:
            notify(f"📊 正在分析 {display_code(cmd.code)}，预计 10-15 秒...")
        return await handle_q(cmd.code, chat_id=chat_id, chat_type=chat_type, notify=notify)
    if cmd.kind in ("B", "S"):
        if chat_id:
            briefing_repo.ensure_subscribed(chat_id, chat_type)
        if notify:
            notify(f"📊 正在分析 {display_code(cmd.code)}，预计 10-15 秒...")
        if cmd.kind == "B":
            return await task_service.create_b(ctx, cmd.code, notify=notify)
        return await task_service.create_s(ctx, cmd.code, cmd.buy_price, cmd.quantity, notify=notify)
    if cmd.kind == "C":
        return task_service.cancel(ctx, cmd.code)
    if cmd.kind == "L":
        return await task_service.list_tasks(ctx)
    if cmd.kind == "M":
        return await handle_m(ctx, cmd.provider, cmd.model)
    if cmd.kind == "CHAT":
        if notify:
            notify("🤖 正在思考并调取实时投研数据...")
        return await run_agent_chat(cmd.text or text, ctx, notify=notify)
    return HELP_TEXT


def handle(text: str, ctx: dict | None = None, notify=None):
    """同步入口(供飞书回调在工作线程中调用)。"""
    return asyncio.run(handle_async(text, ctx or {}, notify))
