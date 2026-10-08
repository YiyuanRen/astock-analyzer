"""分析服务: 一只股票的完整分析流水线 + 缓存, 供 Q / B / S / 调度任务共用。

流水线: 行情快照 -> 三周期K线 -> 缠论(日线/30分/5分) -> 多级别联立 -> 报告字典 -> LLM 报告文本
缓存(analysis_cache): 命中规则见 _cache_valid
  - 收盘/非交易时段: 同一 session_key 内一直有效(数据静止)
  - 盘中: 同一 session_key 且 age < cache_ttl_hours(默认2h)
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime

from app.config import get_config
from app.data import fetcher, trading_calendar
from app.engine.chan_analyzer import ChanAnalyzer
from app.engine.multi_level import multi_level_analyze
from app.engine.report_builder import build_report
from app.llm.report_generator import generate_report
from app.logging_utils import get_logger
from app.models import cache_repo
from app.models.database import TZ, now

logger = get_logger("services.analysis")

_analyzer = ChanAnalyzer()
_chan_lock = threading.Lock()  # chan.py 计算串行化(约1-2s/次), 避免多线程并发踩共享状态


class StockNotFound(Exception):
    pass


@dataclass
class AnalysisResult:
    code: str
    name: str
    payload: dict            # quote/daily/m30/m5/ml/report/markdown/template/title
    analyzed_at: str
    from_cache: bool

    @property
    def quote(self) -> dict:
        return self.payload["quote"]

    @property
    def ml(self) -> dict:
        return self.payload["ml"]

    @property
    def daily(self) -> dict:
        return self.payload["daily"]

    @property
    def report(self) -> dict:
        return self.payload["report"]

    @property
    def markdown(self) -> str:
        return self.payload["markdown"]

    @property
    def direction(self) -> str:
        return self.ml.get("operation_direction") or "观望"

    @property
    def price(self) -> float | None:
        return self.quote.get("price")

    def to_card(self, header_md: str = "", template: str | None = None,
                show_cache_note: bool = True) -> dict:
        """生成飞书卡片回复; header_md 为代码确定性生成的头部区块(持仓/任务状态等)。"""
        body = (header_md + "\n\n---\n\n" if header_md else "") + self.markdown
        if self.from_cache and show_cache_note:
            hhmm = datetime.fromisoformat(self.analyzed_at).strftime("%H:%M")
            body += f"\n\n_(缓存于 {hhmm}，非盘中实时)_"
        return {"card": {"title": self.payload["title"], "markdown": body,
                         "template": template or self.payload["template"]}}


async def _compute(code: str) -> dict:
    quote = await asyncio.to_thread(fetcher.fetch_quote, code)
    if not quote or quote.get("price") is None:
        raise StockNotFound(code)

    data = await fetcher.fetch_all_levels(code)

    def run_chan():
        with _chan_lock:
            return (_analyzer.analyze(data["daily"], "daily"),
                    _analyzer.analyze(data["m30"], "30m"),
                    _analyzer.analyze(data["m5"], "5m"))

    daily, m30, m5 = await asyncio.to_thread(run_chan)
    ml = multi_level_analyze(daily, m30, m5, quote.get("price"))
    report = build_report(quote, daily, m30, m5, ml, data["daily"])
    markdown = await generate_report(report)

    name = quote.get("name") or code
    template = {"买入": "red", "卖出": "green"}.get(ml.get("operation_direction"), "blue")
    return {"code": code, "name": name, "quote": quote, "daily": daily, "m30": m30, "m5": m5,
            "ml": ml, "report": report, "markdown": markdown, "template": template,
            "title": f"📊 {name} {code}"}


def _cache_valid(entry: dict, current: datetime) -> bool:
    if entry["session_key"] != trading_calendar.session_key(current):
        return False
    if entry["session_key"].endswith("-close"):
        return True                         # 数据静止, 直到下次开盘
    ttl = float(get_config().data.get("cache_ttl_hours", 2)) * 3600
    age = (current - datetime.fromisoformat(entry["analyzed_at"])).total_seconds()
    return age < ttl


async def analyze_stock(code: str, *, force: bool = False, fresh_within_sec: int = 0,
                        current: datetime | None = None) -> AnalysisResult:
    """分析一只股票。

    force=False: 走缓存规则; force=True: 强制重算(定时任务用)。
    fresh_within_sec: force 时若缓存是这么多秒内刚算的就复用(同一轮任务里多个会话跟踪同一只股票时避免重复分析)。
    抛 StockNotFound: 行情查不到(代码错误/停牌无数据)。
    """
    current = current or now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    cached = cache_repo.get(code)
    if cached:
        age = (current - datetime.fromisoformat(cached["analyzed_at"])).total_seconds()
        if (not force and _cache_valid(cached, current)) or (force and 0 <= age <= fresh_within_sec):
            p = cached["payload"]
            return AnalysisResult(code, p.get("name") or code, p, cached["analyzed_at"], True)

    logger.info("分析 %s (force=%s)", code, force)
    payload = await _compute(code)
    analyzed_at = current.astimezone(TZ).isoformat(timespec="seconds")
    cache_repo.put(code, payload, trading_calendar.session_key(current), analyzed_at)
    return AnalysisResult(code, payload["name"], payload, analyzed_at, False)
