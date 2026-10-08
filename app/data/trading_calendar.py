"""A股交易日历与交易时段。

交易日来自上证指数日K的日期集合(数据驱动, 无需硬编码节假日); 拉取失败时退化为"周一至周五"。
注意: 当日的日K要开盘后才出现, 故 9:30 前对"今天是否交易日"只能按工作日推断。

session_key 用于分析缓存: 同一 key 内行情结构不会发生本质变化。
  - 盘中(9:30-15:00)        -> "<日期>-open"   (还需叠加 TTL 判断)
  - 收盘后/开盘前/非交易日    -> "<上一交易日>-close" 或 "<今日>-close" (数据静止, 直到下次开盘)
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta
from typing import Callable

from app.logging_utils import get_logger
from app.models.database import now as _now

logger = get_logger("data.calendar")

MORNING = (time(9, 30), time(11, 30))
AFTERNOON = (time(13, 0), time(15, 0))
OPEN_TIME = time(9, 30)
CLOSE_TIME = time(15, 0)
_DATES_TTL = 6 * 3600       # 日历集合刷新周期
_NEG_TTL = 60               # 今天尚不在集合内时的重试间隔(应对 9:30 刚开盘日K还没出)

_provider: Callable[[], set[date]] | None = None
_cache: dict = {"at": 0.0, "dates": set()}


def set_dates_provider(fn: Callable[[], set[date]] | None) -> None:
    """测试用: 注入交易日集合提供者并清缓存。"""
    global _provider
    _provider = fn
    _cache.update(at=0.0, dates=set())


def _load() -> set[date]:
    if _provider:
        return _provider()
    from app.data.fetcher import fetch_index_trade_dates
    return fetch_index_trade_dates(30)


def trade_dates(today: date | None = None) -> set[date]:
    today = today or _now().date()
    age = _time.time() - _cache["at"]
    stale = age > _DATES_TTL or (today not in _cache["dates"] and age > _NEG_TTL)
    if stale:
        try:
            _cache["dates"] = _load()
        except Exception as e:  # noqa: BLE001
            logger.warning("获取交易日历失败, 退化为工作日判断: %s", e)
        _cache["at"] = _time.time()
    return _cache["dates"]


def _weekday(d: date) -> bool:
    return d.weekday() < 5


def is_trading_day(d: date | None = None, now: datetime | None = None) -> bool:
    now = now or _now()
    d = d or now.date()
    dates = trade_dates(now.date())
    if not dates:
        return _weekday(d)
    if d in dates:
        return True
    if d < min(dates) or d > now.date():
        return _weekday(d)            # 超出已知窗口: 工作日推断
    if d == now.date() and now.time() < OPEN_TIME:
        return _weekday(d)            # 今天还没开盘, 日K尚不存在
    return False


def prev_trading_day(d: date, now: datetime | None = None) -> date:
    dates = trade_dates((now or _now()).date())
    earlier = [x for x in dates if x < d]
    if earlier:
        return max(earlier)
    x = d - timedelta(days=1)
    while not _weekday(x):
        x -= timedelta(days=1)
    return x


def is_trading_time(now: datetime | None = None) -> bool:
    """仅按钟点判断(9:30-11:30, 13:00-15:00), 不含是否交易日。"""
    t = (now or _now()).time()
    return MORNING[0] <= t <= MORNING[1] or AFTERNOON[0] <= t <= AFTERNOON[1]


def is_market_open(now: datetime | None = None) -> bool:
    now = now or _now()
    return is_trading_day(now.date(), now) and is_trading_time(now)


def session_key(now: datetime | None = None) -> str:
    now = now or _now()
    d, t = now.date(), now.time()
    if is_trading_day(d, now):
        if t < OPEN_TIME:
            return f"{prev_trading_day(d, now)}-close"
        if t < CLOSE_TIME:
            return f"{d}-open"
        return f"{d}-close"
    return f"{prev_trading_day(d, now)}-close"
