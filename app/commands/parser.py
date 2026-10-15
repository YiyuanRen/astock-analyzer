"""飞书指令解析 (6条快捷指令 + H 帮助 + CHAT 自然语言问答兜底)。

Q 000001 / Q 00700.HK       即时查询
B 000001 / B 00700          创建买入跟踪任务
S 000001 [@价格] [数量]     创建卖出跟踪任务 (价格/数量可选)
C 000001 / C 00700          取消任务
L                           列出任务
M [provider] [model]        查看/切换 LLM 模型
H / help / 帮助 / ?         查看指令说明
其他任意自然语言输入        进入智能投研 Agent 自由问答(支持股票追问/大盘/板块/资讯)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.data.market import parse_code

_PATTERNS = {
    "Q": re.compile(r"^Q\s+(\S+)$", re.I),
    "B": re.compile(r"^B\s+(\S+)$", re.I),
    "S": re.compile(r"^S\s+(\S+?)(?:\s+@([\d.]+))?(?:\s+(\d+))?$", re.I),
    "C": re.compile(r"^C\s+(\S+)$", re.I),
    "L": re.compile(r"^L$", re.I),
    "M": re.compile(r"^M(?:\s+(\w+)(?:\s+(\S+))?)?$", re.I),
}

_HELP_KEYWORDS = {"h", "help", "帮助", "?", "？", "指令", "菜单"}
# 疑似敲错格式的指令(如 Q000001 漏空格、Q 700 位数不对、X 000001 未知单字母+代码): 提示指令帮助
_TYPO_CMD = re.compile(r"^(?:[QBSC]\d{4,7}|[A-Z]\s+\d{1,8})$", re.I)

HELP_TEXT = (
    "📌 快捷指令格式（支持 A股 6位 如 000001，港股 5位 如 00700 / 00700.HK / HK00700）:\n"
    "Q 000001 / Q 00700.HK   即时缠论分析\n"
    "B 000001 / B 00700      创建买入跟踪\n"
    "S 000001 @10.45 100     创建持仓跟踪(价格/数量可选)\n"
    "C 000001                取消跟踪任务\n"
    "L                       列出当前会话任务\n"
    "M [provider] [model]    查看/切换模型\n"
    "H / 帮助                显示本指令说明\n\n"
    "💬 自由提问：直接发送问题即可对话（支持刚查股票追问、大盘趋势、板块热点、主力资金、行业资讯等）"
)


@dataclass
class Command:
    kind: str  # Q/B/S/C/L/M/HELP/CHAT
    code: str | None = None
    buy_price: float | None = None
    quantity: int | None = None
    provider: str | None = None
    model: str | None = None
    text: str | None = None


def parse(text: str) -> Command:
    """解析一条输入文本: 匹配快捷指令 -> Q/B/S/C/L/M; 显式帮助或指令拼写错误 -> HELP; 其余自由文本 -> CHAT。"""
    if text is None:
        return Command(kind="HELP")
    # 归一化: 去首尾空白, 剥离飞书把 01810.HK 自动转成的 Markdown 链接 [01810.HK](http://01810.hk/),
    # 全角空格/@ 转半角, 合并多空格
    s = re.sub(r"\[([^\[\]]+)\]\([^()\s]+\)", r"\1", text.strip())
    s = s.replace("\u3000", " ").replace("＠", "@")
    s = re.sub(r"\s+", " ", s)
    if not s or s.lower() in _HELP_KEYWORDS:
        return Command(kind="HELP")

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
            if head == "S":
                parsed = parse_code(m.group(1))
                if parsed:
                    price = float(m.group(2)) if m.group(2) else None
                    qty = int(m.group(3)) if m.group(3) else None
                    return Command(kind="S", code=parsed[0], buy_price=price, quantity=qty)
                return Command(kind="HELP")
            if head == "L":
                return Command(kind="L")
            if head == "M":
                return Command(kind="M", provider=m.group(1), model=m.group(2))

    if _TYPO_CMD.match(s):
        return Command(kind="HELP")

    return Command(kind="CHAT", text=s)
