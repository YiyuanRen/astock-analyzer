"""A股行情数据拉取层。

数据源: 东方财富 (eastmoney) 公开行情 API —— 免费、无 token, 与 AKShare 同源。
HTTP 引擎: curl_cffi (impersonate=chrome), 以浏览器 TLS 指纹访问, 规避部分网络环境
对 urllib3/requests 的 TLS 指纹拦截 (本机实测直连 requests 会被 RST, curl_cffi 正常)。

统一输出 pandas DataFrame, 列: date, open, high, low, close, volume
(date: 日线 'YYYY-MM-DD'; 分钟线 'YYYY-MM-DD HH:MM:SS')
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta

import pandas as pd
from curl_cffi import requests as cr

from app.config import get_config
from app.logging_utils import get_logger

logger = get_logger("data.fetcher")

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_IMPERSONATE = "chrome"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57"  # time,open,close,high,low,volume,amount
_STD_COLS = ["date", "open", "high", "low", "close", "volume"]

# klt: 101=日线, 5/15/30/60=分钟线; fqt: 0=不复权,1=前复权,2=后复权
_KLT_DAILY = 101


def _secid(code: str) -> str:
    """6位代码 -> eastmoney secid (market.code)。5/6/9 开头为上交所(1, 含沪市ETF), 其余深/北(0)。"""
    market = 1 if code[0] in ("5", "6", "9") else 0
    return f"{market}.{code}"


def _cr_get(url: str, params: dict, timeout: int, retries: int = 3):
    """带重试的 curl_cffi GET, 应对偶发的连接中断(curl 56 等)。"""
    last_err = None
    for attempt in range(retries):
        try:
            r = cr.get(url, params=params, impersonate=_IMPERSONATE, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries - 1:
                logger.warning("请求失败(第%d次重试): %s", attempt + 1, e)
                time.sleep(0.8 * (attempt + 1))
    raise last_err


def _parse_klines(payload: dict) -> pd.DataFrame:
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    rows = []
    for line in klines:
        parts = line.split(",")
        # f51..f56 => date, open, close, high, low, volume
        rows.append({
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
        })
    df = pd.DataFrame(rows, columns=_STD_COLS)
    return df


def _get_klines(code: str, klt: int, lookback_days: int, fqt: int = 1) -> pd.DataFrame:
    """拉取 [今天-lookback_days, 今天] 区间的K线。

    用 beg 日期限定范围: eastmoney 在 beg=0 时会忽略 lmt 返回全历史(日线可达1991年,
    且前复权会出现负价), 故必须用 beg 日期收窄。分钟线历史由服务端自身上限决定。
    """
    beg = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    params = {
        "secid": _secid(code),
        "klt": klt,
        "fqt": fqt,
        "beg": beg,
        "end": "20500000",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": _FIELDS2,
    }
    r = _cr_get(_KLINE_URL, params, timeout=15)
    return _parse_klines(r.json())


def fetch_daily(code: str, years: int = 3) -> pd.DataFrame:
    return _get_klines(code, _KLT_DAILY, int(years * 366), fqt=1)


def fetch_30min(code: str, months: int = 6) -> pd.DataFrame:
    return _get_klines(code, 30, int(months * 31), fqt=1)


def fetch_5min(code: str, months: int = 1) -> pd.DataFrame:
    return _get_klines(code, 5, int(months * 31), fqt=1)


def fetch_index_trade_dates(days: int = 30) -> set[date]:
    """上证指数(1.000001)最近 N 天的日K日期集合 —— 数据驱动的交易日历(自动跳过节假日)。"""
    beg = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    params = {"secid": "1.000001", "klt": _KLT_DAILY, "fqt": 0, "beg": beg, "end": "20500000",
              "fields1": "f1", "fields2": "f51"}
    klines = (_cr_get(_KLINE_URL, params, timeout=15).json().get("data") or {}).get("klines") or []
    return {date.fromisoformat(k[:10]) for k in klines}


def fetch_realtime_price(code: str) -> float | None:
    """实时最新价(盘中止损监控用)。f43=最新价, f59=小数位数。"""
    params = {"secid": _secid(code), "fields": "f43,f59"}
    r = _cr_get(_QUOTE_URL, params, timeout=10)
    data = r.json().get("data") or {}
    raw = data.get("f43")
    if raw in (None, "-", 0):
        return None
    decimals = int(data.get("f59", 2))
    return round(float(raw) / (10 ** decimals), decimals)


def fetch_quote(code: str) -> dict:
    """实时盘口快照: 名称/最新价/涨跌幅/涨跌额/今开/最高/最低/昨收/成交量。

    eastmoney 价格类字段需除以 10^f59; f170(涨跌幅%) 固定除以 100。
    """
    fields = "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f169,f170"
    r = _cr_get(_QUOTE_URL, {"secid": _secid(code), "fields": fields}, timeout=10)
    d = r.json().get("data") or {}
    if not d:
        return {}
    dec = int(d.get("f59", 2))
    scale = 10 ** dec

    def px(v):
        return round(float(v) / scale, dec) if v not in (None, "-") else None

    return {
        "code": d.get("f57", code),
        "name": d.get("f58"),
        "price": px(d.get("f43")),
        "change": px(d.get("f169")),
        "change_pct": round(float(d["f170"]) / 100, 2) if d.get("f170") not in (None, "-") else None,
        "open": px(d.get("f46")),
        "high": px(d.get("f44")),
        "low": px(d.get("f45")),
        "prev_close": px(d.get("f60")),
        "volume": float(d["f47"]) if d.get("f47") not in (None, "-") else None,
    }


async def fetch_all_levels(code: str) -> dict[str, pd.DataFrame]:
    """并发拉取日线/30分/5分三个周期。"""
    cfg = get_config().data
    sem = asyncio.Semaphore(int(cfg.get("max_concurrency", 3)))
    interval = float(cfg.get("request_interval_sec", 1.5))

    async def _guarded(fn, *args):
        async with sem:
            result = await asyncio.to_thread(fn, *args)
            await asyncio.sleep(interval)  # 温和限速
            return result

    daily, m30, m5 = await asyncio.gather(
        _guarded(fetch_daily, code, int(cfg.get("daily_years", 3))),
        _guarded(fetch_30min, code, int(cfg.get("m30_months", 6))),
        _guarded(fetch_5min, code, int(cfg.get("m5_months", 1))),
    )
    return {"daily": daily, "m30": m30, "m5": m5}
