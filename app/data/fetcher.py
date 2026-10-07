"""A股行情数据拉取层。

数据源: 东方财富 (eastmoney) 公开行情 API —— 免费、无 token, 与 AKShare 同源。
HTTP 引擎: curl_cffi (impersonate=chrome), 以浏览器 TLS 指纹访问, 规避部分网络环境
对 urllib3/requests 的 TLS 指纹拦截 (本机实测直连 requests 会被 RST, curl_cffi 正常)。

统一输出 pandas DataFrame, 列: date, open, high, low, close, volume
(date: 日线 'YYYY-MM-DD'; 分钟线 'YYYY-MM-DD HH:MM:SS')
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pandas as pd
from curl_cffi import requests as cr

from app.config import get_config

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_IMPERSONATE = "chrome"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57"  # time,open,close,high,low,volume,amount
_STD_COLS = ["date", "open", "high", "low", "close", "volume"]

# klt: 101=日线, 5/15/30/60=分钟线; fqt: 0=不复权,1=前复权,2=后复权
_KLT_DAILY = 101


def _secid(code: str) -> str:
    """6位代码 -> eastmoney secid (market.code)。6/9 开头为上交所(1), 其余深/北(0)。"""
    market = 1 if code[0] in ("6", "9") else 0
    return f"{market}.{code}"


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
    r = cr.get(_KLINE_URL, params=params, impersonate=_IMPERSONATE, timeout=15)
    r.raise_for_status()
    return _parse_klines(r.json())


def fetch_daily(code: str, years: int = 3) -> pd.DataFrame:
    return _get_klines(code, _KLT_DAILY, int(years * 366), fqt=1)


def fetch_30min(code: str, months: int = 6) -> pd.DataFrame:
    return _get_klines(code, 30, int(months * 31), fqt=1)


def fetch_5min(code: str, months: int = 1) -> pd.DataFrame:
    return _get_klines(code, 5, int(months * 31), fqt=1)


def fetch_realtime_price(code: str) -> float | None:
    """实时最新价(盘中止损监控用)。f43=最新价, f59=小数位数。"""
    params = {"secid": _secid(code), "fields": "f43,f57,f58,f59,f170"}
    r = cr.get(_QUOTE_URL, params=params, impersonate=_IMPERSONATE, timeout=10)
    r.raise_for_status()
    data = r.json().get("data") or {}
    raw = data.get("f43")
    if raw in (None, "-"):
        return None
    decimals = data.get("f59", 2)
    return round(float(raw) / (10 ** int(decimals)), int(decimals))


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
