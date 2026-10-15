"""A股行情数据拉取层。

数据源: 东方财富 (eastmoney) 公开行情 API —— 免费、无 token, 与 AKShare 同源。
HTTP 引擎: curl_cffi (impersonate=chrome), 以浏览器 TLS 指纹访问, 规避部分网络环境
对 urllib3/requests 的 TLS 指纹拦截 (本机实测直连 requests 会被 RST, curl_cffi 正常)。

统一输出 pandas DataFrame, 列: date, open, high, low, close, volume
(date: 日线 'YYYY-MM-DD'; 分钟线 'YYYY-MM-DD HH:MM:SS')
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd
from curl_cffi import requests as cr

from app.config import get_config
from app.data.market import secid_of
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
    """兼容旧调用: 规范化代码 -> eastmoney secid (A股 0/1.xxxxxx, 港股 116.xxxxx, 或已带 '.' 的原始 secid)。"""
    if "." in code:
        return code
    return secid_of(code)


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


_INDEX_SECID = {"CN": "1.000001", "HK": "100.HSI"}


def fetch_index_trade_dates(days: int = 30, market: str = "CN") -> set[date]:
    """基准指数(CN=上证 1.000001, HK=恒指 100.HSI)最近 N 天的日K日期集合 —— 数据驱动的交易日历。"""
    beg = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    secid = _INDEX_SECID.get(market, "1.000001")
    params = {"secid": secid, "klt": _KLT_DAILY, "fqt": 0, "beg": beg, "end": "20500000",
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


# ---------------- Agent 投研扩展接口 (全部同源东方财富公开 API) ----------------
_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_NEWS_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"

# 常用核心大盘指数映射(支持代码或中文名/简称)
CORE_INDICES: dict[str, tuple[str, str]] = {
    "000001": ("1.000001", "上证指数"),
    "1.000001": ("1.000001", "上证指数"),
    "sh": ("1.000001", "上证指数"),
    "上证": ("1.000001", "上证指数"),
    "上证指数": ("1.000001", "上证指数"),
    "沪指": ("1.000001", "上证指数"),
    "大盘": ("1.000001", "上证指数"),
    "399001": ("0.399001", "深证成指"),
    "0.399001": ("0.399001", "深证成指"),
    "深成指": ("0.399001", "深证成指"),
    "深证成指": ("0.399001", "深证成指"),
    "399006": ("0.399006", "创业板指"),
    "0.399006": ("0.399006", "创业板指"),
    "创业板": ("0.399006", "创业板指"),
    "创业板指": ("0.399006", "创业板指"),
    "000688": ("1.000688", "科创50"),
    "1.000688": ("1.000688", "科创50"),
    "科创50": ("1.000688", "科创50"),
    "hsi": ("100.HSI", "恒生指数"),
    "100.hsi": ("100.HSI", "恒生指数"),
    "恒指": ("100.HSI", "恒生指数"),
    "恒生指数": ("100.HSI", "恒生指数"),
    "hstech": ("124.HSTECH", "恒生科技指数"),
    "124.hstech": ("124.HSTECH", "恒生科技指数"),
    "恒科": ("124.HSTECH", "恒生科技指数"),
    "恒生科技": ("124.HSTECH", "恒生科技指数"),
    "恒生科技指数": ("124.HSTECH", "恒生科技指数"),
}


def resolve_index_secid(query: str) -> tuple[str, str]:
    """将指数名称/简称/代码解析为 (secid, 指数中文名)。默认返回上证指数。"""
    q = (query or "").strip().lower()
    if q in CORE_INDICES:
        return CORE_INDICES[q]
    for k, val in CORE_INDICES.items():
        if k in q or q in val[1].lower():
            return val
    return "1.000001", "上证指数"


def _num(v, scale: float = 1.0, ndigits: int = 2) -> float | None:
    if v in (None, "-", ""):
        return None
    try:
        return round(float(v) / scale, ndigits)
    except Exception:
        return None


def search_security_suggest(keyword: str, limit: int = 6) -> list[dict]:
    """按中文名称/拼音/简称查询 A股、港股或板块代码。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    r = _cr_get(_SUGGEST_URL, {"input": kw, "type": 14, "count": max(limit * 2, 10)}, timeout=10)
    rows = ((r.json().get("QuotationCodeTable") or {}).get("Data")) or []
    out: list[dict] = []
    for item in rows:
        code = str(item.get("Code") or "")
        name = str(item.get("Name") or "")
        sec_type = str(item.get("SecurityTypeName") or "")
        quote_id = str(item.get("QuoteID") or "")
        jys = str(item.get("JYS") or "")
        # 过滤掉港股衍生涡轮牛熊证(5位但非主板正股通常 SecurityTypeName!='港股' 或长度为5且以1/2开头涡轮)
        if any(k in sec_type for k in ("A", "沪", "深", "京", "港股", "板块", "指数")) or code.startswith("BK"):
            out.append({
                "code": code,
                "name": name,
                "category": sec_type or jys,
                "secid": quote_id,
            })
        if len(out) >= limit:
            break
    return out


def fetch_stock_fundamentals(code: str) -> dict:
    """获取个股基本面估值、市值、换手量比、今日主力资金流向及所属行业/概念题材。"""
    fields = "f57,f58,f43,f170,f162,f163,f164,f167,f50,f116,f117,f168,f137,f184,f173,f105,f183,f127,f128,f129"
    r = _cr_get(_QUOTE_URL, {"fltt": 2, "secid": _secid(code), "fields": fields}, timeout=10)
    d = r.json().get("data") or {}
    if not d or d.get("f43") in (None, "-"):
        return {}
    concepts = [x.strip() for x in str(d.get("f129") or "").split(",") if x.strip()]
    return {
        "code": d.get("f57", code),
        "name": d.get("f58"),
        "price": _num(d.get("f43"), ndigits=3),
        "change_pct": _num(d.get("f170")),
        "pe_ttm": _num(d.get("f164")),
        "pe_dynamic": _num(d.get("f162")),
        "pb": _num(d.get("f167")),
        "total_mv_yi": _num(d.get("f116"), scale=1e8),
        "circ_mv_yi": _num(d.get("f117"), scale=1e8),
        "roe_pct": _num(d.get("f173")),
        "revenue_yi": _num(d.get("f183"), scale=1e8),
        "net_profit_yi": _num(d.get("f105"), scale=1e8),
        "turnover_rate_pct": _num(d.get("f168")),
        "volume_ratio": _num(d.get("f50")),
        "main_net_inflow_wan": _num(d.get("f137"), scale=1e4),
        "main_net_ratio_pct": _num(d.get("f184")),
        "industry": d.get("f127") or None,
        "region": d.get("f128") or None,
        "concepts": concepts[:15],
    }


def fetch_market_overview() -> dict:
    """获取 A股+港股核心大盘指数实时快照 + 沪深两市总成交额 + 涨跌家数情绪温度计。"""
    secids = "1.000001,0.399001,0.399006,1.000688,100.HSI,124.HSTECH"
    fields = "f12,f14,f2,f3,f4,f6,f8,f104,f105,f106"
    r = _cr_get(_ULIST_URL, {"fltt": 2, "secids": secids, "fields": fields}, timeout=10)
    diffs = ((r.json().get("data") or {}).get("diff")) or []
    indices: list[dict] = []
    sh_sz_turnover = 0.0
    up_cnt = down_cnt = flat_cnt = 0
    for item in diffs:
        code = str(item.get("f12") or "")
        amt_yi = _num(item.get("f6"), scale=1e8) or 0.0
        indices.append({
            "code": code,
            "name": item.get("f14"),
            "price": _num(item.get("f2")),
            "change_pct": _num(item.get("f3")),
            "change": _num(item.get("f4")),
            "turnover_yi": amt_yi,
        })
        if code in ("000001", "399001"):
            sh_sz_turnover += amt_yi
            up_cnt += int(item.get("f104") or 0)
            down_cnt += int(item.get("f105") or 0)
            flat_cnt += int(item.get("f106") or 0)
    total_stocks = up_cnt + down_cnt + flat_cnt
    sentiment = "均衡震荡"
    if total_stocks > 0:
        up_ratio = up_cnt / total_stocks
        if up_ratio >= 0.7:
            sentiment = "普涨赚钱效应强"
        elif up_ratio >= 0.55:
            sentiment = "偏暖多头占优"
        elif up_ratio <= 0.25:
            sentiment = "普跌亏钱效应明显"
        elif up_ratio <= 0.42:
            sentiment = "偏弱空头占优"
    return {
        "indices": indices,
        "a_share_turnover_yi": round(sh_sz_turnover, 2),
        "breadth": {"up": up_cnt, "down": down_cnt, "flat": flat_cnt, "sentiment": sentiment},
    }


def fetch_sector_hotspots(
    board_type: str = "industry",
    sort_by: str = "change_pct",
    limit: int = 8,
    ascending: bool = False,
) -> list[dict]:
    """获取行业板块(industry)或概念板块(concept)热点排行。

    sort_by: 'change_pct'(涨跌幅 f3) | 'main_inflow'(主力净流入 f62)
    ascending: False=涨幅榜/净流入榜, True=跌幅榜/净流出榜
    """
    fs = "m:90+t:3+f:!50" if board_type == "concept" else "m:90+t:2+f:!50"
    fid = "f62" if sort_by == "main_inflow" else "f3"
    params = {
        "pn": 1, "pz": max(1, min(limit, 20)), "po": 0 if ascending else 1,
        "np": 1, "fltt": 2, "invt": 2, "fid": fid, "fs": fs,
        "fields": "f12,f14,f2,f3,f62,f184,f128,f140,f136",
    }
    r = _cr_get(_CLIST_URL, params, timeout=10)
    diffs = ((r.json().get("data") or {}).get("diff")) or []
    return [
        {
            "board_code": x.get("f12"),
            "board_name": x.get("f14"),
            "change_pct": _num(x.get("f3")),
            "main_net_inflow_yi": _num(x.get("f62"), scale=1e8),
            "main_net_ratio_pct": _num(x.get("f184")),
            "leader_name": x.get("f128"),
            "leader_code": x.get("f140"),
            "leader_change_pct": _num(x.get("f136")),
        }
        for x in diffs
    ]


def fetch_sector_stocks(board_code_or_name: str, sort_by: str = "change_pct", limit: int = 8) -> dict:
    """下钻指定行业或概念板块(支持 BKxxxx 或板块中文名)内的领涨/主力流入成分股。"""
    q = (board_code_or_name or "").strip()
    bk_code = q.upper() if q.upper().startswith("BK") else ""
    bk_name = q
    if not bk_code:
        for item in search_security_suggest(q, limit=8):
            if str(item.get("code") or "").startswith("BK"):
                bk_code = item["code"]
                bk_name = item.get("name") or q
                break
    if not bk_code:
        return {"board_code": None, "board_name": q, "stocks": [], "error": f"未找到板块: {q}"}

    fid = "f62" if sort_by == "main_inflow" else "f3"
    params = {
        "pn": 1, "pz": max(1, min(limit, 20)), "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": fid, "fs": f"b:{bk_code}+f:!50",
        "fields": "f12,f14,f2,f3,f62,f184,f168",
    }
    r = _cr_get(_CLIST_URL, params, timeout=10)
    diffs = ((r.json().get("data") or {}).get("diff")) or []
    stocks = [
        {
            "code": x.get("f12"),
            "name": x.get("f14"),
            "price": _num(x.get("f2")),
            "change_pct": _num(x.get("f3")),
            "turnover_rate_pct": _num(x.get("f168")),
            "main_net_inflow_wan": _num(x.get("f62"), scale=1e4),
            "main_net_ratio_pct": _num(x.get("f184")),
        }
        for x in diffs
    ]
    return {"board_code": bk_code, "board_name": bk_name, "stocks": stocks}


def search_financial_news(keyword: str, limit: int = 6) -> list[dict]:
    """按关键词检索东方财富最新财经资讯与行业新闻。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    param = {
        "uid": "", "keyword": kw, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default", "sort": "default",
                "pageIndex": 1, "pageSize": max(1, min(limit, 12)),
                "preTag": "", "postTag": "",
            }
        },
    }
    r = cr.get(
        _NEWS_SEARCH_URL,
        params={"cb": "j", "param": json.dumps(param, ensure_ascii=False)},
        headers={"Referer": "https://so.eastmoney.com/"},
        impersonate=_IMPERSONATE,
        timeout=10,
    )
    r.raise_for_status()
    txt = r.text
    l_idx, r_idx = txt.find("("), txt.rfind(")")
    if l_idx == -1 or r_idx == -1:
        return []
    payload = json.loads(txt[l_idx + 1:r_idx])
    items = ((payload.get("result") or {}).get("cmsArticleWebOld")) or []
    out: list[dict] = []
    for x in items[:limit]:
        summary = re.sub(r"<[^>]+>", "", str(x.get("content") or "")).strip()
        title = re.sub(r"<[^>]+>", "", str(x.get("title") or "")).strip()
        out.append({
            "date": x.get("date"),
            "title": title,
            "summary": summary[:160],
            "media": x.get("mediaName"),
            "url": x.get("url"),
        })
    return out

