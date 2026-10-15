"""LLM 报告生成: 结构化缠论结果 -> 卡片深度解读与操作预案。

- 顶部数值(现价/涨跌/支撑/压力/入场/止损/目标)与缠论结构紧凑条已由 card_builder 确定性渲染为卡片组件
- LLM 专注生成两部分正文:「#### 💡 走势结构与深度解读」与「#### 🎯 关键信号与操作预案」
- 完整保留专业缠论逻辑, 并自然融入通俗直观的多空博弈与操作原因阐释(禁止出现"白话/小白"字样, 禁止输出"xxx：暂无"行)
"""
from __future__ import annotations

import asyncio
import json

from app.config import get_config
from app.engine.report_builder import render_plain
from app.llm.factory import get_default_client, get_llm_client
from app.logging_utils import get_logger

logger = get_logger("llm.report")

SYSTEM_PROMPT = (
    "你是一位资深的缠论(缠中说禅理论)投研分析师。\n"
    "卡片顶部已经由系统用可视化指标卡展示了【现价、涨跌幅、支撑位、压力位、入场/止损/目标价】以及【笔、线段、中枢、买卖点、MACD背驰】的数值摘要。\n"
    "你的任务是根据输入的结构化缠论数据，撰写卡片下半部分的深度分析正文，固定分为以下两个小节：\n\n"
    "#### 💡 走势结构与深度解读\n"
    "- 先给出严谨的缠论专业结构分析（日线线段方向、当前笔运行周期、价格与中枢【区间】的相对位置关系、MACD背驰动力学状态、日线-30分-5分多级别联立结论）；\n"
    "- 紧接着用清晰、直观、通俗易懂的语言阐明该结构背后的多空博弈含义与市场状态（例如：为什么价格在中枢下方且向下笔未背驰意味着空方动能尚未宣泄完毕、过早抄底的风险在哪里；或者为什么价格在中枢上方回踩不入中枢构成三买顺势机会），让不同经验水平的读者都能一眼理解背后的逻辑。\n\n"
    "#### 🎯 关键信号与操作预案\n"
    "- 若操作方向为「买入」或「卖出」：结合建议入场区间、止损位、目标位、盈亏比与仓位建议，给出具体的执行节奏与风控条件；\n"
    "- 若操作方向为「观望」：围绕下方支撑位与上方压力位，用（1）（2）（3）分条列出接下来需要重点盯盘的演化路径与触发条件（如：回踩某支撑位出现底分型+MACD背驰时的企稳信号、放量突破某压力位并回抽确认的转强信号、跌破某支撑位后的风险规避动作）。\n\n"
    "严格遵守以下红线要求：\n"
    "1. **严禁输出任何空值占位行**：当某项数据为空（如观望状态下无入场区间、无目标价、无止损位、无盈亏比）时，绝对不要写出“入场区间：暂无 / 目标价：无 / 止损：暂无”之类的条目；\n"
    "2. **严禁重复罗列顶部基本信息表**：不要再写“【基本信息】股票名称/现价/分析周期”等重复段落，直接从 `#### 💡 走势结构与深度解读` 开始输出；\n"
    "3. **保持专业投研口吻**：解释要通俗透彻，但文中**绝对不要出现“白话”、“大白话”、“小白”、“新手”**等字样；\n"
    "4. 价格货币单位遵循 basic.currency（港股为 HKD/港元，A股为元，切勿将港股写成人民币）；不编造输入 JSON 中不存在的价格数字；\n"
    "5. 仅使用 `#### ` 作为两个小节标题，列表用 `- ` 开头，关键数字与结论加粗，不使用 Markdown 表格（不要用 `|` 竖线），末尾原样附上风险提示。"
)


def build_user_prompt(report: dict) -> str:
    # 剔除值为 None 的 operation 字段, 从源头防止 LLM 写出"xxx：暂无"
    cleaned = dict(report)
    if isinstance(cleaned.get("operation"), dict):
        cleaned["operation"] = {k: v for k, v in cleaned["operation"].items() if v is not None}
    return (
        "请根据以下结构化缠论分析数据，直接生成 `#### 💡 走势结构与深度解读` 与 `#### 🎯 关键信号与操作预案` 两部分正文。\n"
        "务必在末尾包含风险提示原文: " + report.get("disclaimer", "") + "\n\n"
        "数据(JSON):\n" + json.dumps(cleaned, ensure_ascii=False, indent=2)
    )


async def generate_report(report: dict, provider: str | None = None,
                          model: str | None = None) -> str:
    """生成自然语言报告; 任何异常/超时都降级为纯文本。"""
    # 数据不足直接走纯文本, 不浪费 LLM 调用
    if report.get("data_insufficient"):
        return render_plain(report)

    cfg = get_config()
    timeout = float(cfg.llm.get("timeout_seconds", 15))

    try:
        client = get_llm_client(provider, model) if provider and model else get_default_client()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(report)},
        ]
        resp = await asyncio.wait_for(client.chat(messages, temperature=0.4), timeout=timeout)
        content = (resp.content or "").strip()
        if not content:
            raise ValueError("LLM 返回空内容")
        # 兜底: 确保风险提示存在
        disclaimer = report.get("disclaimer", "")
        if disclaimer and disclaimer not in content:
            content += "\n\n" + disclaimer
        return content
    except Exception as e:
        logger.warning("LLM 报告生成失败/超时, 降级纯文本: %s: %s", type(e).__name__, e)
        return render_plain(report)
