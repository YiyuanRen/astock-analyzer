"""市场早报生成与推送编排服务。

流程:
1. 从华尔街见闻抓取指定日期的「早餐FM-Radio」全文
2. 使用资深宏观交易员+财经科普作者 Prompt 调用 LLM 产出结构化市场地图数据:
   - 三层漏斗筛选 6-10 条四列表(事件/影响资产/方向/传导逻辑)
   - 因果链逐条拆解 3-7 条(流程图 + 术语白话 + 市场共识/待验证)
   - 待验证清单(盯盘 3-4 个变量 + 金融历史类比)
3. 渲染响应式 HTML 可视化市场地图并双重持久化(SQLite + data/briefings/YYYY-MM-DD.html)
"""
from __future__ import annotations

import asyncio
import json
import os
import re

from app.config import get_config
from app.llm.factory import get_default_client
from app.logging_utils import get_logger
from app.models import briefing_repo
from app.models.briefing_repo import BriefingRecord
from app.services.briefing_fetcher import (
    BriefingArticle,
    fetch_breakfast_for_date,
    search_breakfast_items,
)
from app.services.briefing_html import render_market_map_html

logger = get_logger("services.briefing")

BRIEFING_SYSTEM_PROMPT = """你是资深宏观交易员+财经科普作者。
我会给你华尔街见闻「财经早餐FM-Radio」的当日要闻汇总全文，请你严格按以下【流程】提炼并输出 JSON 格式的【市场地图结构化数据】（系统将用该 JSON 同时渲染一页 HTML 可视化市场地图与飞书早报汇总卡片）：

【流程】
1. 三层漏斗筛选：从上百条要闻里只留价格动了 / 新信息（超预期/低于预期） / 宏观 / 资产相关的，筛到 6-10 条核心要闻。
2. 四列表（events_table）：1.事件(event) 2.影响资产(asset) 3.方向(direction：只能取 "涨" / "跌" / "中性" / "分化" 之一) 4.传导逻辑(logic)。这是骨架。
3. 因果链逐条拆解（causal_chains，数量按当天实际重要性定，不固定几条——重要就放、碎就砍，通常 3-7 条）：
   - 格式：[事件A] → [事件B] → ... → [资产价格变动]（放在 flow_nodes 数组中）
   - 每条标 status 为「市场共识」或「待验证」
   - 每条标 category（常见链类型参考，按当天实际出现为准：地缘→能源、央行→流动性、AI capex→硬件/信用层、半导体涨价、内需政策→消费/地产 等）
   - 每条必须包含术语白话（jargon_plain）：这条链里每个专业词用一句话讲清（数组，每项含 term 与 explanation）
4. 待验证清单（verification_list，通常 3-5 条）：每条两层——
   - 需思考的细节（watch_variables）：具体盯哪 3-4 个变量（字符串数组）
   - 金融历史类比（historical_analogy）：真实历史事件(event) + 一句话教训(lesson) + 关键区别(difference)

请严格只输出合法的 JSON 对象（不要用 markdown 代码块包裹之外的额外解释文字），JSON 结构如下：
{
  "headline_summary": "一句话概括今日全球宏观与市场主线（40~70字）",
  "events_table": [
    {
      "event": "核心事件简述",
      "asset": "受影响的核心资产（如 美债/黄金/纳指/半导体/原油/港股科网）",
      "direction": "涨 | 跌 | 中性 | 分化",
      "logic": "一句话讲清事件如何传导至资产价格"
    }
  ],
  "causal_chains": [
    {
      "title": "链条主题名称",
      "category": "如 AI capex→硬件/信用层",
      "status": "市场共识 | 待验证",
      "flow_nodes": ["事件起点A", "中间传导机制B", "终端影响C", "资产价格变动结果"],
      "jargon_plain": [
        {"term": "专业术语名", "explanation": "用一句话大白话讲清楚这个词是什么意思"}
      ]
    }
  ],
  "verification_list": [
    {
      "topic": "待验证的核心命题",
      "watch_variables": ["具体盯盘变量1", "具体盯盘变量2", "具体盯盘变量3"],
      "historical_analogy": {
        "event": "真实金融历史事件（含年份）",
        "lesson": "一句话核心历史教训",
        "difference": "本次与当年最关键的结构性区别"
      }
    }
  ]
}
"""


def public_base_url() -> str:
    """获取对外访问的 Web 根地址(优先读 PUBLIC_BASE_URL, 其次 ECS_HOST, 兜底 8.153.91.185)。"""
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    cfg_base = ((get_config().raw.get("briefing") or {}).get("public_base_url") or "").strip().rstrip("/")
    if cfg_base:
        return cfg_base
    host = (os.getenv("ECS_HOST") or "8.153.91.185").strip()
    return f"http://{host}"


def briefing_page_url(report_date: str) -> str:
    return f"{public_base_url()}/briefings/{report_date}"


def briefing_archive_url() -> str:
    return f"{public_base_url()}/briefings"


def _extract_json_dict(raw_text: str) -> dict:
    s = (raw_text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, flags=re.S)
    if m:
        s = m.group(1)
    else:
        l_idx, r_idx = s.find("{"), s.rfind("}")
        if l_idx != -1 and r_idx != -1 and r_idx > l_idx:
            s = s[l_idx:r_idx + 1]
    return json.loads(s)


async def generate_structured_map(article: BriefingArticle) -> dict:
    """调用 LLM 将早餐全文提炼为市场地图结构化数据。"""
    client = get_default_client()
    user_prompt = (
        f"报告日期：{article.report_date}\n"
        f"文章标题：{article.title}\n"
        f"发布时间：{article.published_at}\n"
        f"原文链接：{article.uri}\n\n"
        f"=== 华尔街见闻早餐全文 ===\n{article.plain_text[:18000]}"
    )
    messages = [
        {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    # 早报内容较丰富, 给予 90 秒超时
    resp = await asyncio.wait_for(
        client.chat(messages, temperature=0.3, max_tokens=4000),
        timeout=90.0,
    )
    if not resp.content:
        raise RuntimeError("LLM 返回早报内容为空")
    data = _extract_json_dict(resp.content)
    if not data.get("events_table"):
        raise ValueError("LLM 返回的 JSON 缺少 events_table 字段")
    return data


async def get_or_generate_briefing(
    target_date: str | None = None,
    *,
    force: bool = False,
    allow_latest_fallback: bool = False,
) -> BriefingRecord:
    """获取或生成指定日期的早报记录。

    - 若数据库中已有该日期记录且 force=False, 直接返回。
    - allow_latest_fallback=True 时(供用户手动测试/预览最近一期), 若当日未更新则取源站最新一期生成。
    """
    if target_date and not force:
        existing = briefing_repo.get_briefing(target_date)
        if existing:
            return existing

    try:
        article = await asyncio.to_thread(fetch_breakfast_for_date, target_date)
    except Exception:
        if not allow_latest_fallback:
            raise
        items = await asyncio.to_thread(search_breakfast_items, 5)
        if not items:
            raise
        latest_meta = items[0]
        if not force:
            existing = briefing_repo.get_briefing(latest_meta["report_date"])
            if existing:
                return existing
        from app.services.briefing_fetcher import fetch_article_detail
        article = await asyncio.to_thread(fetch_article_detail, latest_meta["article_id"], latest_meta)

    if not force:
        existing = briefing_repo.get_briefing(article.report_date)
        if existing:
            return existing

    logger.info("开始生成 %s 市场早报 (article_id=%s)...", article.report_date, article.article_id)
    structured = await generate_structured_map(article)
    html_content = render_market_map_html(
        report_date=article.report_date,
        source_title=article.title,
        source_uri=article.uri,
        published_at=article.published_at,
        structured=structured,
    )
    return briefing_repo.save_briefing(
        report_date=article.report_date,
        article_id=article.article_id,
        source_title=article.title,
        source_uri=article.uri,
        published_at=article.published_at,
        structured=structured,
        html_content=html_content,
    )
