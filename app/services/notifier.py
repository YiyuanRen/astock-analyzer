"""主动推送通知器。

协议: send_text / send_card 按 chat_id 推送(私聊 chat_id 推私聊, 群 chat_id 推群)。
- FeishuBot 本身即满足该协议(直接传给调度器)
- ConsoleNotifier: 打印到控制台(CLI/dry-run)
- RecordingNotifier: 记录推送内容(单测断言推送目标/内容)
"""
from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def send_text(self, chat_id: str, text: str) -> bool: ...

    def send_card(self, chat_id: str, title: str, markdown: str, template: str = "blue") -> bool: ...


class ConsoleNotifier:
    def send_text(self, chat_id: str, text: str) -> bool:
        print(f"\n[推送 → {chat_id}] {text}\n")
        return True

    def send_card(self, chat_id: str, title: str, markdown: str, template: str = "blue") -> bool:
        print(f"\n[推送 → {chat_id}] 卡片「{title}」template={template}\n{markdown}\n")
        return True


class RecordingNotifier:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    def send_text(self, chat_id: str, text: str) -> bool:
        self.sent.append({"chat_id": chat_id, "kind": "text", "title": None, "text": text, "template": None})
        return not self.fail

    def send_card(self, chat_id: str, title: str, markdown: str, template: str = "blue") -> bool:
        self.sent.append({"chat_id": chat_id, "kind": "card", "title": title, "text": markdown,
                          "template": template})
        return not self.fail

    def to(self, chat_id: str) -> list[dict]:
        return [m for m in self.sent if m["chat_id"] == chat_id]
