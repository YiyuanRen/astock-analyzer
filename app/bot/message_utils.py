"""飞书消息解析的纯函数(无 SDK 依赖, 便于单测)。

群聊文本形如 "@_user_1 Q 000001": 其中 @_user_N 是飞书的 @ 占位符, 对应 mentions[].key。
规则:
- 私聊(p2p): 直接取文本
- 群聊(group): 仅当 @ 了机器人才处理; 剥离所有 @ 占位符(含 @_all)后再解析指令
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Mention:
    key: str                 # 如 "@_user_1"
    open_id: str | None = None
    name: str | None = None


def is_bot_mentioned(mentions: list[Mention], bot_open_id: str | None) -> bool:
    """是否 @ 了机器人。bot_open_id 未知时(取身份失败), 退化为"有任意非@all 的 @ 即算"。"""
    real = [m for m in mentions if m.key and m.key != "@_all"]
    if bot_open_id:
        return any(m.open_id == bot_open_id for m in real)
    return bool(real)


def strip_mentions(text: str, mentions: list[Mention]) -> str:
    """去掉文本中所有 @ 占位符并规整空白。"""
    for m in mentions:
        if m.key:
            text = text.replace(m.key, " ")
    text = re.sub(r"@_(?:user_\d+|all)\b", " ", text)  # 兜底: mentions 缺失时的残留占位符
    return re.sub(r"\s+", " ", text).strip()


def extract_command_text(chat_type: str | None, text: str, mentions: list[Mention],
                         bot_open_id: str | None) -> str | None:
    """返回应交给指令解析的文本; 返回 None 表示该消息应被忽略(群里没 @ 机器人)。"""
    text = (text or "").strip()
    if chat_type == "group":
        if not is_bot_mentioned(mentions, bot_open_id):
            return None
        return strip_mentions(text, mentions)
    return text
