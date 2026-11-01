"""飞书指令解析 (快捷指令集 + H 帮助 + CHAT 自然语言问答兜底)。

支持指令:
Q 000001 / Q 00700.HK            即时缠论分析
B 000001 / B 00700               创建买入跟踪任务
S 000001 [@买入价] [股数]        模拟买入建仓/加仓并开启持仓止损跟踪(10万RMB初始本金)
X 000001 [@卖出价] [股数]        模拟卖出减仓/全部平仓(A股遵循T+1, 港股T+0; 股价为空取现价, 股数为空全卖)
P                                查看模拟账户总资产、持仓明细、总收益与实操胜率
T [代码]                         查看模拟交易交割流水
BT 000001 [年数]                 执行 chan.py 逐根K线无未来函数历史回测(默认近2年)
PICK [板块名/港股]               两级漏斗缠论智能选股(默认扫今日主力流入与强势板块龙头)
C 000001 / C 00700               取消跟踪任务
L                                列出当前会话活跃跟踪任务
M [provider] [model]             查看/切换 LLM 模型
H / help / 帮助 / ?              查看指令说明
其他任意自然语言输入             进入智能投研 Agent 自由问答
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.data.market import parse_code

_PATTERNS = {
    "Q": re.compile(r"^Q\s+(\S+)$", re.I),
    "B": re.compile(r"^B\s+(\S+)$", re.I),
    "S": re.compile(r"^S\s+(\S+?)(?:\s+@([\d.]+))?(?:\s+(\d+))?$", re.I),
    "X": re.compile(r"^X\s+(\S+?)(?:\s+@([\d.]+))?(?:\s+(\d+))?$", re.I),
    "C": re.compile(r"^C\s+(\S+)$", re.I),
    "L": re.compile(r"^L$", re.I),
    "P": re.compile(r"^P$", re.I),
    "T": re.compile(r"^T(?:\s+(\S+))?$", re.I),
    "M": re.compile(r"^M(?:\s+(\w+)(?:\s+(\S+))?)?$", re.I),
}
_BT_RE = re.compile(r"^BT\s+(\S+?)(?:\s+(\d+))?$", re.I)
_PICK_RE = re.compile(r"^PICK(?:\s+(.+))?$", re.I)

_HELP_KEYWORDS = {"h", "help", "帮助", "?", "？", "指令", "菜单"}
_TYPO_CMD = re.compile(r"^(?:[QBSCX]\d{4,7}|[A-Z]\s+\d{1,8})$", re.I)

HELP_TEXT = (
    "📌 快捷指令格式（支持 A股 6位 如 000001，港股 5位 如 00700 / 00700.HK / HK00700）:\n"
    "Q 000001 / Q 00700.HK      即时缠论分析\n"
    "B 000001 / B 00700         创建买入观察跟踪\n"
    "S 000001 @10.45 100        模拟买入建仓/加仓并开启止损监控(10万本金)\n"
    "X 000001 [@卖出价] [股数]  模拟卖出减仓/平仓(A股T+1, 不填价取现价, 不填量全卖)\n"
    "P                          查看账户总资产、持仓与总收益胜率\n"
    "T [代码]                   查看模拟交易交割流水\n"
    "BT 000001 [年数]           单股缠论历史回测(无未来函数, 默认2年)\n"
    "PICK [板块名/港股]         两级漏斗缠论智能选股\n"
    "C 000001 / L               取消跟踪任务 / 列出活跃任务\n"
    "M [provider] [model]       查看/切换模型\n"
    "今日早报 / 开启早报 / 关闭早报  管理每日08:00市场早报\n\n"
    "💬 自由提问：直接发送问题即可对话（支持股票追问、大盘趋势、板块热点、主力资金、行业资讯等）"
)


@dataclass
class Command:
    kind: str  # Q/B/S/X/P/T/BT/PICK/C/L/M/HELP/CHAT
    code: str | None = None
    buy_price: float | None = None
    quantity: int | None = None
    provider: str | None = None
    model: str | None = None
    text: str | None = None


def parse(text: str) -> Command:
    """解析一条输入文本。"""
    if text is None:
        return Command(kind="HELP")
    s = re.sub(r"\[([^\[\]]+)\]\([^()\s]+\)", r"\1", text.strip())
    s = s.replace("\u3000", " ").replace("＠", "@")
    s = re.sub(r"\s+", " ", s)
    if not s or s.lower() in _HELP_KEYWORDS:
        return Command(kind="HELP")

    # 多字母指令优先匹配: BT / PICK
    m_bt = _BT_RE.match(s)
    if m_bt:
        parsed = parse_code(m_bt.group(1))
        if not parsed:
            return Command(kind="HELP")
        years = int(m_bt.group(2)) if m_bt.group(2) else 2
        return Command(kind="BT", code=parsed[0], quantity=years)

    m_pick = _PICK_RE.match(s)
    if m_pick:
        scope = (m_pick.group(1) or "").strip() or None
        return Command(kind="PICK", text=scope)

    head = s[0].upper()
    pat = _PATTERNS.get(head)
    if pat:
        m = pat.match(s)
        if m:
            if head in ("Q", "B", "C"):
                parsed = parse_code(m.group(1))
                if parsed:
                    return Command(kind=head, code=parsed[0])
                return Command(kind="HELP")
            if head in ("S", "X"):
                parsed = parse_code(m.group(1))
                if parsed:
                    price = float(m.group(2)) if m.group(2) else None
                    qty = int(m.group(3)) if m.group(3) else None
                    return Command(kind=head, code=parsed[0], buy_price=price, quantity=qty)
                return Command(kind="HELP")
            if head in ("L", "P"):
                return Command(kind=head)
            if head == "T":
                raw_c = m.group(1)
                if not raw_c:
                    return Command(kind="T", code=None)
                parsed = parse_code(raw_c)
                if parsed:
                    return Command(kind="T", code=parsed[0])
                return Command(kind="HELP")
            if head == "M":
                return Command(kind="M", provider=m.group(1), model=m.group(2))

    if _TYPO_CMD.match(s):
        return Command(kind="HELP")

    return Command(kind="CHAT", text=s)
