"""市场与证券编码规则(A股 CN / 港股 HK 的单一事实来源)。

编码规范化约定:
- A股(沪/深/京): 固定 6 位数字 (如 '000001', '600519'), 支持可选 SH/SZ/BJ 前后缀
- 港股:         固定 5 位数字 (如 '00700', '09988'), 支持 5 位纯数字或带 HK 前后缀(1~5位自动补零)
- 无前缀的 1~4 位纯数字视为非法(防止输错 A 股位数被误判为港股)

由于规范化后 A股=6位、港股=5位, stock_code 本身即可唯一区分市场, 无需改动数据库表结构。
"""
from __future__ import annotations

import re

_CN_PURE = re.compile(r"^(\d{6})$")
_CN_AFFIX = re.compile(r"^(?:(?:SH|SZ|BJ)\.?(\d{6})|(\d{6})\.(?:SH|SZ|BJ))$", re.I)
_HK_PURE = re.compile(r"^(\d{5})$")
_HK_AFFIX = re.compile(r"^(?:HK\.?(\d{1,5})|(\d{1,5})\.HK)$", re.I)

# 各市场 B 任务盘中跟踪的检查时点(对齐 30m K线收盘后 1 分钟; 收盘最后一根由日报覆盖)
INTRADAY_B_SLOTS: dict[str, set[str]] = {
    "CN": {"10:01", "10:31", "11:01", "11:31", "13:31", "14:01", "14:31"},
    "HK": {"10:01", "10:31", "11:01", "11:31", "12:01", "13:31", "14:01", "14:31", "15:01", "15:31"},
}


def parse_code(raw: str | None) -> tuple[str, str] | None:
    """解析用户输入的代码, 返回 (规范化代码, 市场 'CN'|'HK'); 非法返回 None。"""
    if not raw:
        return None
    s = raw.strip()
    m = _CN_PURE.match(s)
    if m:
        return m.group(1), "CN"
    m = _CN_AFFIX.match(s)
    if m:
        return (m.group(1) or m.group(2)), "CN"
    m = _HK_PURE.match(s)
    if m:
        return m.group(1), "HK"
    m = _HK_AFFIX.match(s)
    if m:
        digits = m.group(1) or m.group(2)
        return digits.zfill(5), "HK"
    return None


def market_of(code: str) -> str:
    """由规范化代码判断市场: 5位='HK', 其余(6位)='CN'。"""
    return "HK" if len(code) == 5 else "CN"


def is_hk(code: str) -> bool:
    return market_of(code) == "HK"


def secid_of(code: str) -> str:
    """规范化代码 -> 东方财富 secid (market.code)。

    港股: 116.00700; 上交所(5/6/9开头, 含沪市ETF): 1.xxxxxx; 深/北交所: 0.xxxxxx。
    """
    if is_hk(code):
        return f"116.{code}"
    market = 1 if code[0] in ("5", "6", "9") else 0
    return f"{market}.{code}"


def display_code(code: str) -> str:
    """面向用户展示的代码: 港股带 .HK 后缀(00700.HK), A股保持 6 位。"""
    return f"{code}.HK" if is_hk(code) else code


def decimals_of(code: str) -> int:
    """默认价格小数位: 港股 3 位(兼容仙股 0.001), A股 2 位。"""
    return 3 if is_hk(code) else 2


def market_label(code: str) -> str:
    return "港股" if is_hk(code) else "A股"


def currency_of(code: str) -> str:
    return "HKD" if is_hk(code) else "CNY"


def daily_report_time_str(code: str) -> str:
    """该股票所属市场的收盘日报推送时间文本。"""
    return "16:10" if is_hk(code) else "15:05"
