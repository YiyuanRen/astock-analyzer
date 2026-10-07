"""OpenAI 兼容协议客户端。

适用于所有走 OpenAI Chat Completions 协议的 provider:
DeepSeek、MiMo(小米)、Qwen、Grok、Gemini、Kimi、GLM 等。
Anthropic 不走此类(单独 SDK)。
"""
from __future__ import annotations

from typing import AsyncIterator

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

    async def chat(self, messages: list[dict], **kwargs) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            extra_headers=self.extra_headers or None,
            **kwargs,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
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
