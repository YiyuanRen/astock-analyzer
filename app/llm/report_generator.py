"""LLM 报告生成: 结构化缠论结果 -> 自然语言报告。

- LLM 只做"结构化数据 -> 通顺中文报告"的组装, 不做缠论计算
- 超时(config.llm.timeout_seconds)或失败时降级为纯文本(report_builder.render_plain)
- 报告末尾必带风险提示
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
    "你是一个专业的缠论(缠中说禅理论)技术分析助手。"
    "根据输入的结构化分析数据, 生成清晰、专业的A股分析报告。"
    "要求:\n"
    "1. 使用简体中文, 语言简洁专业, 分【基本信息】【缠论信号】【操作建议】【辅助信息】四部分;\n"
    "2. 只客观呈现数据与缠论逻辑, 不做主观涨跌预测, 不编造数据里没有的数字;\n"
    "3. 若方向为观望, 要说明在等待什么信号、关注哪些价位;\n"
    "4. 报告末尾必须原样包含这句风险提示。\n"
    "格式要求(用于飞书卡片渲染, 必须遵守):\n"
    "- 用 **粗体** 表示小节标题(如 **【缠论信号】**), 不要使用 # 号标题;\n"
    "- 列表用 '- ' 开头; 不要使用 Markdown 表格(不要用 | 竖线);\n"
    "- 关键数字可加粗; 保持紧凑, 避免空行过多。"
)


def build_user_prompt(report: dict) -> str:
    return (
        "请根据以下结构化缠论分析数据生成报告。\n"
        "务必在末尾包含风险提示原文: " + report.get("disclaimer", "") + "\n\n"
        "数据(JSON):\n" + json.dumps(report, ensure_ascii=False, indent=2)
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
