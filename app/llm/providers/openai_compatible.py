"""OpenAI 兼容协议客户端。

适用于所有走 OpenAI Chat Completions 协议的 provider:
DeepSeek、MiMo(小米)、Qwen、Grok、Gemini、Kimi、GLM 等。
支持:
- 非流式与流式 chat (当传入 on_reasoning 回调时自动启用 stream=True, 实时推送 reasoning_content 思考增量)
- 流式与非流式下的 tool_calls 完整聚合
"""
from __future__ import annotations

from typing import AsyncIterator, Callable

from openai import AsyncOpenAI

from app.llm.base import BaseLLMClient, LLMResponse


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 15.0,
        extra_headers: dict | None = None,
    ):
        super().__init__(api_key=api_key, model=model, base_url=base_url)
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def chat(
        self,
        messages: list[dict],
        *,
        on_reasoning: Callable[[str], None] | None = None,
        **kwargs,
    ) -> LLMResponse:
        if on_reasoning is not None:
            return await self._chat_stream_with_reasoning(messages, on_reasoning=on_reasoning, **kwargs)

        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            extra_headers=self.extra_headers or None,
            **kwargs,
        )
        choice = resp.choices[0]
        msg = choice.message
        usage = resp.usage
        reasoning = getattr(msg, "reasoning_content", None) or ""
        tool_calls = None
        raw_message = None
        if getattr(msg, "tool_calls", None):
            tool_calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                }
                for tc in msg.tool_calls
            ]
            raw_message = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        return LLMResponse(
            content=msg.content or "",
            model=resp.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            tool_calls=tool_calls,
            raw_message=raw_message,
            reasoning_content=reasoning,
        )

    async def _chat_stream_with_reasoning(
        self,
        messages: list[dict],
        *,
        on_reasoning: Callable[[str], None],
        **kwargs,
    ) -> LLMResponse:
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            extra_headers=self.extra_headers or None,
            **kwargs,
        )
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tc_by_idx: dict[int, dict[str, str]] = {}
        actual_model = self.model

        async for chunk in stream:
            if getattr(chunk, "model", None):
                actual_model = chunk.model
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue

            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_parts.append(rc)
                try:
                    on_reasoning("".join(reasoning_parts))
                except Exception:  # noqa: BLE001
                    pass

            if delta.content:
                content_parts.append(delta.content)

            for tc in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc, "index", 0) or 0
                slot = tc_by_idx.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn:
                    if getattr(fn, "name", None):
                        slot["name"] += fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments

        full_content = "".join(content_parts)
        full_reasoning = "".join(reasoning_parts)
        tool_calls = None
        raw_message = None
        if tc_by_idx:
            ordered = [tc_by_idx[i] for i in sorted(tc_by_idx)]
            tool_calls = [
                {
                    "id": item["id"] or f"call_{idx}",
                    "name": item["name"],
                    "arguments": item["arguments"] or "{}",
                }
                for idx, item in enumerate(ordered)
            ]
            raw_message = {
                "role": "assistant",
                "content": full_content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls
                ],
            }
        return LLMResponse(
            content=full_content,
            model=actual_model,
            tool_calls=tool_calls,
            raw_message=raw_message,
            reasoning_content=full_reasoning,
        )

    async def stream_chat(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            extra_headers=self.extra_headers or None,
            **kwargs,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
