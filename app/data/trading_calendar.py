"""A股(CN)与港股(HK)交易日历与交易时段。

交易日来自基准指数日K的日期集合(CN=上证 1.000001, HK=恒指 100.HSI, 数据驱动, 无需硬编码节假日);
拉取失败时退化为"周一至周五"。当日的日K要开盘后才出现, 故 9:30 前对"今天是否交易日"按工作日推断。

交易时段(北京时间):
- CN(A股): 09:30-11:30, 13:00-15:00
- HK(港股): 09:30-12:00, 13:00-16:00

session_key 用于分析缓存: 同一 key 内行情结构不会发生本质变化。
  - 盘中        -> "<日期>-open"   (还需叠加 TTL 判断)
  - 收盘后/休市 -> "<上一交易日或今日>-close" (数据静止, 直到下次开盘)
"""
from __future__ import annotations

import inspect
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

_SESSIONS = {
    "CN": {"morning": (time(9, 30), time(11, 30)), "afternoon": (time(13, 0), time(15, 0)),
           "open": time(9, 30), "close": time(15, 0)},
    "HK": {"morning": (time(9, 30), time(12, 0)), "afternoon": (time(13, 0), time(16, 0)),
           "open": time(9, 30), "close": time(16, 0)},
}

_DATES_TTL = 6 * 3600       # 日历集合刷新周期
_NEG_TTL = 60               # 今天尚不在集合内时的重试间隔(应对 9:30 刚开盘日K还没出)

_provider: Callable | None = None
_cache: dict[str, dict] = {
    "CN": {"at": 0.0, "dates": set()},
    "HK": {"at": 0.0, "dates": set()},
}


def _sess(market: str) -> dict:
    return _SESSIONS.get(market.upper(), _SESSIONS["CN"])


def set_dates_provider(fn: Callable | None) -> None:
    """测试用: 注入交易日集合提供者(支持无参 fn() 或单参 fn(market))并清缓存。"""
    global _provider
    _provider = fn
    for m in _cache.values():
        m.update(at=0.0, dates=set())


def _load(market: str) -> set[date]:
    if _provider:
        params = inspect.signature(_provider).parameters
        return _provider(market) if len(params) >= 1 else _provider()
    from app.data.fetcher import fetch_index_trade_dates
    return fetch_index_trade_dates(30, market=market)


def trade_dates(today: date | None = None, market: str = "CN") -> set[date]:
    market = market.upper()
    bucket = _cache.setdefault(market, {"at": 0.0, "dates": set()})
    today = today or _now().date()
    age = _time.time() - bucket["at"]
    stale = age > _DATES_TTL or (today not in bucket["dates"] and age > _NEG_TTL)
    if stale:
        try:
            bucket["dates"] = _load(market)
        except Exception as e:  # noqa: BLE001
            logger.warning("获取 %s 交易日历失败, 退化为工作日判断: %s", market, e)
        bucket["at"] = _time.time()
    return bucket["dates"]


def _weekday(d: date) -> bool:
    return d.weekday() < 5


def is_trading_day(d: date | None = None, now: datetime | None = None, market: str = "CN") -> bool:
    now = now or _now()
    d = d or now.date()
    dates = trade_dates(now.date(), market=market)
    if not dates:
        return _weekday(d)
    if d in dates:
        return True
    if d < min(dates) or d > now.date():
        return _weekday(d)            # 超出已知窗口: 工作日推断
    if d == now.date() and now.time() < _sess(market)["open"]:
        return _weekday(d)            # 今天还没开盘, 日K尚不存在
    return False


def prev_trading_day(d: date, now: datetime | None = None, market: str = "CN") -> date:
    dates = trade_dates((now or _now()).date(), market=market)
    earlier = [x for x in dates if x < d]
    if earlier:
        return max(earlier)
    x = d - timedelta(days=1)
    while not _weekday(x):
        x -= timedelta(days=1)
    return x


def is_trading_time(now: datetime | None = None, market: str = "CN") -> bool:
    """仅按钟点判断(CN: 9:30-11:30/13:00-15:00; HK: 9:30-12:00/13:00-16:00), 不含是否交易日。"""
    t = (now or _now()).time()
    s = _sess(market)
    m0, m1 = s["morning"]
    a0, a1 = s["afternoon"]
    return m0 <= t <= m1 or a0 <= t <= a1


def in_session_hours(now: datetime | None = None, market: str = "CN") -> bool:
    """开盘至收盘的宽口径(含午休), 用于 B 盘中跟踪。"""
    t = (now or _now()).time()
    s = _sess(market)
    return s["open"] <= t <= s["close"]


def is_market_open(now: datetime | None = None, market: str = "CN") -> bool:
    now = now or _now()
    return is_trading_day(now.date(), now, market=market) and is_trading_time(now, market=market)


def session_key(now: datetime | None = None, market: str = "CN") -> str:
    now = now or _now()
    d, t = now.date(), now.time()
    s = _sess(market)
    if is_trading_day(d, now, market=market):
        if t < s["open"]:
            return f"{prev_trading_day(d, now, market=market)}-close"
        if t < s["close"]:
            return f"{d}-open"
        return f"{d}-close"
    return f"{prev_trading_day(d, now, market=market)}-close"
