"""飞书指令解析 (6条指令)。

Q 000001              即时查询
B 000001              创建买入跟踪任务
S 000001 [@价格] [数量] 创建卖出跟踪任务 (价格/数量可选)
C 000001              取消任务
L                     列出任务
M [provider] [model]  查看/切换 LLM 模型
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS = {
    "Q": re.compile(r"^Q\s+(\d{6})$", re.I),
    "B": re.compile(r"^B\s+(\d{6})$", re.I),
    "S": re.compile(r"^S\s+(\d{6})(?:\s+@([\d.]+))?(?:\s+(\d+))?$", re.I),
    "C": re.compile(r"^C\s+(\d{6})$", re.I),
    "L": re.compile(r"^L$", re.I),
    "M": re.compile(r"^M(?:\s+(\w+)\s+(\S+))?$", re.I),
}

HELP_TEXT = (
    "指令格式:\n"
    "Q 000001            即时查询\n"
    "B 000001            买入跟踪\n"
    "S 000001 @10.45 100 卖出跟踪(价格/数量可选)\n"
    "C 000001            取消任务\n"
    "L                   列出任务\n"
    "M [provider] [model] 查看/切换模型"
)


@dataclass
class Command:
    kind: str  # Q/B/S/C/L/M/HELP
    code: str | None = None
    buy_price: float | None = None
    quantity: int | None = None
    provider: str | None = None
    model: str | None = None


def parse(text: str) -> Command:
    """解析一条指令文本。无法识别返回 kind='HELP'。"""
    if text is None:
        return Command(kind="HELP")
    # 归一化: 去首尾空白, 全角空格/@ 转半角, 合并多空格
    s = text.strip().replace("\u3000", " ").replace("＠", "@")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return Command(kind="HELP")

    head = s[0].upper()
    pat = _PATTERNS.get(head)
    if not pat:
        return Command(kind="HELP")
    m = pat.match(s)
    if not m:
        return Command(kind="HELP")

    if head in ("Q", "B", "C"):
        return Command(kind=head, code=m.group(1))
    if head == "S":
        price = float(m.group(2)) if m.group(2) else None
        qty = int(m.group(3)) if m.group(3) else None
        return Command(kind="S", code=m.group(1), buy_price=price, quantity=qty)
    if head == "L":
        return Command(kind="L")
    if head == "M":
        return Command(kind="M", provider=m.group(1), model=m.group(2))
    return Command(kind="HELP")
