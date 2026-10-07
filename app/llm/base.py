"""LLM 适配层抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class BaseLLMClient(ABC):
    """所有 LLM provider 客户端的统一接口。"""

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> LLMResponse:
        """一次性返回完整回复。"""

    @abstractmethod
    async def stream_chat(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        """流式返回, 用于飞书卡片实时更新 (阶段4后期)。"""
