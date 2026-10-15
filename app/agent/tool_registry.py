"""统一工具注册与执行抽象层 (ToolRegistry)。

支持三类工具同构接入:
1. 内置 Python 异步/同步工具 (FunctionTool / BaseTool)
2. 外部命令行工具 (CLIToolAdapter, 如 Search CLI)
3. 外部 MCP Server 工具 (MCPToolAdapter) 与多步工作流 (WorkflowToolAdapter)
"""
from __future__ import annotations

import inspect
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from app.logging_utils import get_logger

logger = get_logger("agent.tools")


@dataclass
class ToolExecutionResult:
    name: str
    label: str
    output: Any
    ok: bool = True


class BaseTool(ABC):
    name: str
    label: str                  # 中文短标签, 用于飞书卡片副标题展示"已调用：xxx"
    description: str
    parameters: dict[str, Any]  # JSON Schema object

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    async def execute(self, ctx: dict, **kwargs) -> Any:
        """执行工具并返回可 JSON 序列化的结果。"""


class FunctionTool(BaseTool):
    def __init__(
        self,
        name: str,
        label: str,
        description: str,
        parameters: dict[str, Any],
        fn: Callable,
        pass_ctx: bool = False,
    ):
        self.name = name
        self.label = label
        self.description = description
        self.parameters = parameters
        self._fn = fn
        self._pass_ctx = pass_ctx

    async def execute(self, ctx: dict, **kwargs) -> Any:
        res = self._fn(ctx, **kwargs) if self._pass_ctx else self._fn(**kwargs)
        if inspect.isawaitable(res):
            return await res
        return res


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    async def call(self, name: str, raw_arguments: str | dict, ctx: dict) -> ToolExecutionResult:
        tool = self.get(name)
        if not tool:
            return ToolExecutionResult(name=name, label=name, output={"error": f"未知工具: {name}"}, ok=False)
        try:
            args = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
            if not isinstance(args, dict):
                args = {}
        except Exception as e:  # noqa: BLE001
            return ToolExecutionResult(
                name=name, label=tool.label, output={"error": f"参数 JSON 解析失败: {e}"}, ok=False
            )
        try:
            logger.info("Agent 调用工具 %s(%s) args=%s", name, tool.label, args)
            out = await tool.execute(ctx, **args)
            return ToolExecutionResult(name=name, label=tool.label, output=out, ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("工具 %s 执行异常: %s: %s", name, type(e).__name__, e)
            return ToolExecutionResult(
                name=name, label=tool.label, output={"error": f"{type(e).__name__}: {e}"}, ok=False
            )
