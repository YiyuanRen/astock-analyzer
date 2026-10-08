"""飞书机器人接入层 (lark-oapi WebSocket 长连接)。

- WebSocket 长连接接收 im.message.receive_v1 事件 (无需公网IP, 内置断线重连+心跳)
- 会话以 chat_id 统一标识: 私聊(p2p)与群聊(group)都有 chat_id,
  在哪个会话里提交指令, 回复与主动推送就发回哪个会话
- 群聊仅处理 @机器人 的消息; 机器人自身 open_id 启动时通过 /bot/v3/info 获取
- on_text 回调在工作线程执行, 避免阻塞 WebSocket 事件循环
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.channel.bot_identity import fetch_bot_identity

from app.bot.message_utils import Mention, extract_command_text
from app.logging_utils import get_logger

logger = get_logger("bot.feishu")

# 回调签名: (text, ctx, notify) -> 回复
# 回复可为: str(纯文本) 或 dict{"card": {"title":..,"markdown":..,"template":..}}(卡片)
# notify(str) 可发送中间态消息(如"正在分析...")。
# ctx: message_id / chat_id / chat_type / is_group / sender_open_id / message_type
Notify = Callable[[str], None]
TextHandler = Callable[[str, dict, Notify], "str | dict"]


def _default_handler(text: str, ctx: dict, notify: Notify) -> str:
    return f"收到: {text}" if text else "收到(非文本消息)"


def _build_card(title: str, markdown: str, template: str = "blue") -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": template,
                   "title": {"tag": "plain_text", "content": title}},
        "elements": [{"tag": "markdown", "content": markdown}],
    }


class FeishuBot:
    def __init__(self, app_id: str, app_secret: str, on_text: TextHandler | None = None,
                 log_level: lark.LogLevel = lark.LogLevel.INFO):
        self._app_id = app_id
        self._on_text = on_text or _default_handler
        self.bot_open_id: str | None = None
        self._rest = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(log_level)
            .build()
        )
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )
        self._ws = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=log_level)

    # ---- 机器人身份 ----
    def _load_bot_identity(self) -> None:
        """取机器人自身 open_id(群里判断是否被 @ 用)。在独立线程跑 asyncio, 不干扰 ws 事件循环。"""
        result: dict = {}

        def run():
            try:
                result["identity"] = asyncio.run(fetch_bot_identity(self._rest.config))
            except Exception as e:  # noqa: BLE001
                logger.warning("获取机器人身份异常: %s", e)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=20)
        ident = result.get("identity")
        if ident and ident.open_id:
            self.bot_open_id = ident.open_id
            logger.info("机器人身份: name=%s open_id=%s", ident.name, ident.open_id)
        else:
            logger.warning("未能获取机器人 open_id, 群聊将退化为'任意 @ 即响应'")

    # ---- 事件处理 ----
    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        msg = data.event.message
        sender = data.event.sender
        sender_open_id = getattr(getattr(sender, "sender_id", None), "open_id", None)
        chat_type = msg.chat_type

        raw_text = ""
        if msg.message_type == "text":
            try:
                raw_text = (json.loads(msg.content) or {}).get("text", "")
            except Exception:  # noqa: BLE001
                raw_text = ""

        mentions = [
            Mention(key=m.key or "", open_id=getattr(m.id, "open_id", None) if m.id else None, name=m.name)
            for m in (msg.mentions or [])
        ]
        text = extract_command_text(chat_type, raw_text, mentions, self.bot_open_id)
        logger.info("收到消息 chat_type=%s chat=%s sender=%s type=%s raw=%r -> %s",
                    chat_type, msg.chat_id, sender_open_id, msg.message_type, raw_text,
                    "忽略(群里未@机器人)" if text is None else repr(text))
        if text is None:
            return

        ctx = {
            "message_id": msg.message_id,
            "chat_id": msg.chat_id,
            "chat_type": chat_type,
            "is_group": chat_type == "group",
            "sender_open_id": sender_open_id,
            "message_type": msg.message_type,
        }
        # 在工作线程中处理, 避免阻塞 WebSocket 事件循环(分析耗时约10-15s)
        threading.Thread(target=self._dispatch, args=(text, ctx), daemon=True).start()

    def _dispatch(self, text: str, ctx: dict) -> None:
        message_id = ctx["message_id"]

        def notify(t: str) -> None:
            if t:
                self.reply_text(message_id, t)

        try:
            reply = self._on_text(text, ctx, notify)
        except Exception as e:  # 回调异常不应断开连接
            logger.exception("处理消息异常: %s", e)
            reply = "处理出错，请稍后再试。"
        self._send_reply(message_id, reply)

    def _send_reply(self, message_id: str, reply) -> None:
        if not reply:
            return
        if isinstance(reply, dict) and reply.get("card"):
            c = reply["card"]
            self.reply_card(message_id, c.get("title", ""), c.get("markdown", ""),
                            c.get("template", "blue"))
        else:
            self.reply_text(message_id, str(reply))

    # ---- 回复(针对某条消息) ----
    def reply_text(self, message_id: str, text: str) -> None:
        self._reply(message_id, "text", json.dumps({"text": text}))

    def reply_card(self, message_id: str, title: str, markdown: str, template: str = "blue") -> None:
        self._reply(message_id, "interactive", json.dumps(_build_card(title, markdown, template)))

    def _reply(self, message_id: str, msg_type: str, content: str) -> None:
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(ReplyMessageRequestBody.builder().content(content).msg_type(msg_type).build())
            .build()
        )
        resp = self._rest.im.v1.message.reply(req)
        if not resp.success():
            logger.error("回复失败 type=%s code=%s msg=%s log_id=%s",
                         msg_type, resp.code, resp.msg, resp.get_log_id())

    # ---- 主动推送(按 chat_id: 群聊推群, 私聊推私聊) ----
    def send_text(self, chat_id: str, text: str) -> bool:
        return self._create(chat_id, "text", json.dumps({"text": text}))

    def send_card(self, chat_id: str, title: str, markdown: str, template: str = "blue") -> bool:
        return self._create(chat_id, "interactive", json.dumps(_build_card(title, markdown, template)))

    def _create(self, chat_id: str, msg_type: str, content: str, retries: int = 2) -> bool:
        for attempt in range(retries):
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id).msg_type(msg_type).content(content).build()
                )
                .build()
            )
            resp = self._rest.im.v1.message.create(req)
            if resp.success():
                return True
            logger.error("推送失败(第%d次) chat=%s type=%s code=%s msg=%s log_id=%s",
                         attempt + 1, chat_id, msg_type, resp.code, resp.msg, resp.get_log_id())
            if attempt < retries - 1:
                time.sleep(1.5)
        return False

    # ---- 启动 (阻塞) ----
    def start(self) -> None:
        self._load_bot_identity()
        logger.info("飞书 WebSocket 长连接启动中... (Ctrl+C 退出)")
        self._ws.start()
