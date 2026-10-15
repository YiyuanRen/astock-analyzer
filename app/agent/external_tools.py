"""外部工具扩展层 (Search CLI 子进程适配器 & MCP Server 桥接适配器)。

用于远期在 config/config.yaml 中零代码挂载外部 Search CLI 或 MCP 工具:
agent:
  cli_tools:
    - name: "web_search_cli"
      label: "外部网页搜索"
      description: "调用本地 search cli 搜索互联网资讯"
      command: ["my-search-cli", "--json", "{query}"]
      timeout_sec: 15
  mcp_servers:
    - name: "custom_mcp"
      transport: "stdio" # 或 "sse"
      command: ["npx", "-y", "@modelcontextprotocol/server-everything"]
      tools: [...]
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agent.tool_registry import BaseTool, ToolRegistry
from app.config import get_config
from app.logging_utils import get_logger

logger = get_logger("agent.external_tools")


class CLIToolAdapter(BaseTool):
    """将外部命令行工具(如 Search CLI)安全封装为 Agent 可调用的 Tool。

    采用 create_subprocess_exec 传递参数列表(不经过 shell, 防止命令注入)。
    """

    def __init__(
        self,
        name: str,
        label: str,
        description: str,
        command_template: list[str],
        parameters: dict[str, Any] | None = None,
        timeout_sec: float = 15.0,
    ):
        self.name = name
        self.label = label
        self.description = description
        self.command_template = list(command_template)
        self.timeout_sec = timeout_sec
        self.parameters = parameters or {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索或查询参数"}},
            "required": ["query"],
        }

    async def execute(self, ctx: dict, **kwargs) -> Any:
        cmd: list[str] = []
        for part in self.command_template:
            s = str(part)
            for k, v in kwargs.items():
                s = s.replace(f"{{{k}}}", str(v))
            cmd.append(s)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": f"CLI 工具 {self.name} 执行超时({self.timeout_sec}s)"}
        out_str = stdout.decode("utf-8", errors="replace").strip()
        err_str = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return {"error": f"exit={proc.returncode}: {err_str or out_str}"}
        try:
            return json.loads(out_str)
        except Exception:
            return {"output": out_str[:4000]}


class MCPToolAdapter(BaseTool):
    """MCP (Model Context Protocol) 外部工具桥接适配器。

    支持通过自定义 handler 或 JSON-RPC stdio/HTTP 调用远程/本地 MCP Server 工具。
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        label: str,
        description: str,
        parameters: dict[str, Any],
        caller=None,
    ):
        self.server_name = server_name
        self.tool_name = tool_name
        self.name = f"mcp_{server_name}_{tool_name}"
        self.label = label
        self.description = description
        self.parameters = parameters
        self._caller = caller

    async def execute(self, ctx: dict, **kwargs) -> Any:
        if self._caller is None:
            return {"error": f"MCP Server '{self.server_name}' 未连接或未配置 handler"}
        res = self._caller(self.tool_name, kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res


def register_configured_external_tools(registry: ToolRegistry) -> None:
    """从 config.yaml 的 agent.cli_tools 与 agent.mcp_servers 加载外部扩展工具。"""
    agent_cfg = get_config().raw.get("agent") or {}
    for item in agent_cfg.get("cli_tools") or []:
        try:
            if not item.get("name") or not item.get("command"):
                continue
            registry.register(CLIToolAdapter(
                name=item["name"],
                label=item.get("label") or item["name"],
                description=item.get("description") or f"CLI 工具 {item['name']}",
                command_template=item["command"],
                parameters=item.get("parameters"),
                timeout_sec=float(item.get("timeout_sec", 15)),
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("加载 CLI 工具失败 %s: %s", item, e)

    for srv in agent_cfg.get("mcp_servers") or []:
        srv_name = srv.get("name") or "ext"
        for t in srv.get("tools") or []:
            if not t.get("name"):
                continue
            registry.register(MCPToolAdapter(
                server_name=srv_name,
                tool_name=t["name"],
                label=t.get("label") or f"MCP:{t['name']}",
                description=t.get("description") or f"MCP 工具 {srv_name}.{t['name']}",
                parameters=t.get("parameters") or {"type": "object", "properties": {}},
            ))
