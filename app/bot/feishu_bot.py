"""飞书机器人接入层 (lark-oapi WebSocket 长连接)。

- WebSocket 长连接接收 im.message.receive_v1 事件 (无需公网IP, 内置断线重连+心跳)
- REST 客户端用于回复/主动推送消息
- on_text 回调: 把收到的文本交给上层(命令解析层)处理并返回回复文本

当前为最小联调版: 默认回调回显收到的消息。命令路由后续接入。
"""
from __future__ import annotations

import json
import threading
from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from app.logging_utils import get_logger

logger = get_logger("bot.feishu")

# 回调签名: (text, ctx, notify) -> 回复
# 回复可为: str(纯文本) 或 dict{"card": {"title":..,"markdown":..,"template":..}}(卡片)
# notify(str) 可发送中间态消息(如"正在分析...")。ctx 含 chat_id/open_id/message_id 等。
Notify = Callable[[str], None]
Reply = "str | dict"
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

    # ---- 事件处理 ----
    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        msg = data.event.message
        sender = data.event.sender
        open_id = getattr(getattr(sender, "sender_id", None), "open_id", None)
        ctx = {
            "message_id": msg.message_id,
            "chat_id": msg.chat_id,
            "chat_type": msg.chat_type,
            "open_id": open_id,
            "message_type": msg.message_type,
        }
        text = ""
        if msg.message_type == "text":
            try:
                text = (json.loads(msg.content) or {}).get("text", "").strip()
            except Exception:
                text = ""
        logger.info("收到消息 chat=%s type=%s: %r", msg.chat_id, msg.message_type, text)

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

    # ---- 发送 ----
    def reply_text(self, message_id: str, text: str) -> None:
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": text}))
                .msg_type("text")
                .build()
            )
            .build()
        )
        resp = self._rest.im.v1.message.reply(req)
        if not resp.success():
            logger.error("回复失败 code=%s msg=%s log_id=%s", resp.code, resp.msg, resp.get_log_id())

    def reply_card(self, message_id: str, title: str, markdown: str,
                   template: str = "blue") -> None:
        card = _build_card(title, markdown, template)
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps(card))
                .msg_type("interactive")
                .build()
            )
            .build()
        )
        resp = self._rest.im.v1.message.reply(req)
        if not resp.success():
            logger.error("卡片回复失败 code=%s msg=%s log_id=%s", resp.code, resp.msg, resp.get_log_id())

    def send_text(self, open_id: str, text: str) -> None:
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = self._rest.im.v1.message.create(req)
        if not resp.success():
            logger.error("发送失败 code=%s msg=%s log_id=%s", resp.code, resp.msg, resp.get_log_id())

    # ---- 启动 (阻塞) ----
    def start(self) -> None:
        logger.info("飞书 WebSocket 长连接启动中... (Ctrl+C 退出)")
        self._ws.start()
