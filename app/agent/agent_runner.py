"""智能投研 Agent 运行引擎 (OpenAI Tool-Calling 多步循环 + 20 轮会话记忆)。"""
from __future__ import annotations

import asyncio
import json

from app.agent import memory
from app.agent.builtin_tools import register_builtin_tools
from app.agent.external_tools import register_configured_external_tools
from app.agent.tool_registry import ToolRegistry
from app.agent.workflow import build_default_workflows
from app.bot.card_builder import build_agent_chat_card
from app.config import get_config
from app.llm.factory import get_default_client
from app.logging_utils import get_logger
from app.models.database import now_str

logger = get_logger("agent.runner")

AGENT_SYSTEM_PROMPT = (
    "你是一位兼具深厚缠论(缠中说禅理论)功底与全市场视野的专业投研助手，支持 A股(沪深京)与港股市场。\n"
    "你可以回答用户的个股追问、大盘趋势、市场情绪与量能、板块热点与主力资金流向、行业催化资讯、以及持仓跟踪池体检等问题。\n\n"
    "工具使用原则：\n"
    "1. 当用户询问具体股票、大盘指数、今日热点板块、主力资金流向、行业新闻或当前跟踪任务时，**必须优先调用对应工具获取实时数据**，严禁凭空捏造价格或涨跌幅；\n"
    "2. 若用户在追问刚刚分析过的股票（会话历史中已有该股的缠论结构摘要），可直接基于历史上下文深入解答，必要时再补充调用 `get_stock_fundamentals` 或 `search_financial_news`；\n"
    "3. 当用户询问“今天最热的板块里有哪些好票”或“扫描热点龙头”时，可调用 `workflow_scan_hotspot_leaders` 或组合调用板块与个股工具。\n\n"
    "回答风格与排版规范（用于飞书 Schema 2.0 卡片渲染）：\n"
    "1. **专业与通俗自然融合**：保留严谨的缠论与金融专业分析（笔、线段、中枢、背驰、PE/PB、主力净流入），并紧接着用直观通俗的语言阐明其盘面博弈含义与应对逻辑；**文中绝对不要出现“白话”、“大白话”、“小白”、“新手”等字样**；\n"
    "2. **严禁输出空值废话行**：没有数据的字段不要写“xxx：暂无”；\n"
    "3. 使用 `#### ` 加 emoji 作为小节标题（如 `#### 📊 盘面核心结论`、`#### 💡 结构与资金深度解读`、`#### 🎯 观察信号与应对参考`），列表用 `- ` 开头，不使用 Markdown 表格（不要用 `|` 竖线）；\n"
    "4. 涨跌幅与正负盈亏可使用 `<font color='red'>**+x.xx%**</font>`（红涨）与 `<font color='green'>**-x.xx%**</font>`（绿跌）高亮。"
)

_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        reg = ToolRegistry()
        register_builtin_tools(reg)
        register_configured_external_tools(reg)
        build_default_workflows().mount_to_tools(reg)
        _registry = reg
    return _registry


def reset_tool_registry() -> None:
    """测试或配置热更新时重置工具注册表。"""
    global _registry
    _registry = None


async def run_agent_chat(question: str, ctx: dict | None = None) -> dict:
    """处理用户的自由提问，执行多步工具调用并返回 Schema 2.0 卡片字典。"""
    ctx = ctx or {}
    chat_id = ctx.get("chat_id") or "default_chat"
    cfg = get_config()
    agent_cfg = cfg.raw.get("agent") or {}
    max_steps = int(agent_cfg.get("max_tool_steps", 5))
    step_timeout = float(agent_cfg.get("step_timeout_sec", 25))
    disclaimer = cfg.risk_disclaimer

    reg = get_tool_registry()
    history = memory.get_history(chat_id)

    messages: list[dict] = [
        {"role": "system", "content": f"{AGENT_SYSTEM_PROMPT}\n当前时间：{now_str()}"},
        *history,
        {"role": "user", "content": question},
    ]

    used_labels: list[str] = []
    answer_text = ""

    try:
        client = get_default_client()
        schemas = reg.openai_schemas()

        for _ in range(max_steps):
            resp = await asyncio.wait_for(
                client.chat(messages, tools=schemas, tool_choice="auto", temperature=0.4),
                timeout=step_timeout,
            )
            if not resp.tool_calls:
                answer_text = (resp.content or "").strip()
                break

            messages.append(resp.raw_message or {"role": "assistant", "content": resp.content or ""})
            results = await asyncio.gather(
                *(reg.call(tc["name"], tc["arguments"], ctx) for tc in resp.tool_calls)
            )
            for tc, r in zip(resp.tool_calls, results):
                used_labels.append(r.label)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(r.output, ensure_ascii=False, default=str),
                })

        if not answer_text:
            # 达到最大工具步数后强制汇总回答
            resp_final = await asyncio.wait_for(
                client.chat(messages, temperature=0.4),
                timeout=step_timeout,
            )
            answer_text = (resp_final.content or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("Agent 问答执行异常: %s: %s", type(e).__name__, e)
        answer_text = (
            "#### ⚠️ 智能分析暂时繁忙\n"
            f"- 处理您的问题时遇到网络或服务波动（`{type(e).__name__}`），请稍后重试。\n"
            "- 您也可以直接使用快捷指令查询：如 `Q 000001`、`Q 00700.HK`、`L`，或发送 `H` 查看全部指令。"
        )

    if not answer_text:
        answer_text = "未能生成有效回答，请换个方式提问或发送 `H` 查看快捷指令。"

    memory.add_message(chat_id, "user", question)
    memory.add_message(chat_id, "assistant", answer_text)

    return build_agent_chat_card(
        question=question,
        answer_md=answer_text,
        used_tools=used_labels,
        disclaimer=disclaimer,
    )
