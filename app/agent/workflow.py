"""多步工作流编排扩展层 (WorkflowRegistry)。

远期用于将多步投研 SOP (例如: "热点板块识别 -> 龙头成分股提取 -> 多级别缠论买点扫描")
封装为可复用的复合工作流, 并自动作为高级复合工具暴露给 Agent 调用。
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from app.agent.tool_registry import BaseTool, ToolRegistry
from app.data import fetcher
from app.services.analysis_service import analyze_stock


class BaseWorkflow(ABC):
    name: str
    label: str
    description: str
    parameters: dict[str, Any]

    @abstractmethod
    async def run(self, ctx: dict, **kwargs) -> dict[str, Any]:
        """执行多步工作流并返回结构化汇总结果。"""


class WorkflowToolAdapter(BaseTool):
    """将 BaseWorkflow 包装为 ToolRegistry 中的可调用工具。"""

    def __init__(self, workflow: BaseWorkflow):
        self._wf = workflow
        self.name = f"workflow_{workflow.name}"
        self.label = workflow.label
        self.description = workflow.description
        self.parameters = workflow.parameters

    async def execute(self, ctx: dict, **kwargs) -> Any:
        return await self._wf.run(ctx, **kwargs)


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[str, BaseWorkflow] = {}

    def register(self, wf: BaseWorkflow) -> None:
        self._workflows[wf.name] = wf

    def get(self, name: str) -> BaseWorkflow | None:
        return self._workflows.get(name)

    def mount_to_tools(self, tool_registry: ToolRegistry) -> None:
        for wf in self._workflows.values():
            tool_registry.register(WorkflowToolAdapter(wf))


class ScanHotspotLeadersWorkflow(BaseWorkflow):
    """内置示范工作流: 热门板块龙头股缠论扫描。

    步骤:
    1. 获取今日涨幅或主力资金净流入排名靠前的热门板块
    2. 下钻提取该板块内排名前 N 的龙头成分股
    3. 并发对龙头成分股跑多级别缠论分析, 汇总各股趋势、中枢与买卖点
    """

    name = "scan_hotspot_leaders"
    label = "热点龙头缠论扫描工作流"
    description = (
        "复合投研工作流：自动获取今日最强板块(或指定板块)的前 N 只龙头成分股，"
        "并批量执行多级别缠论分析，一站式筛查强势板块中的缠论买点机会。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "board_name": {
                "type": "string",
                "description": "可选：指定板块名称(如'半导体'、'券商')；不传则自动选取今日行业涨幅第1的板块",
            },
            "top_stocks": {
                "type": "integer",
                "description": "扫描该板块内前几只龙头股(默认2，最多3)",
            },
        },
    }

    async def run(self, ctx: dict, board_name: str = "", top_stocks: int = 2) -> dict[str, Any]:
        n_stocks = max(1, min(int(top_stocks or 2), 3))
        target_board = (board_name or "").strip()
        if not target_board:
            hot = await asyncio.to_thread(fetcher.fetch_sector_hotspots, "industry", "change_pct", 3, False)
            if not hot:
                return {"error": "未能获取今日热门板块"}
            target_board = hot[0].get("board_code") or hot[0].get("board_name") or ""

        sec_info = await asyncio.to_thread(fetcher.fetch_sector_stocks, target_board, "change_pct", n_stocks)
        stocks = sec_info.get("stocks") or []
        results = []
        for s in stocks[:n_stocks]:
            code = str(s.get("code") or "")
            if not code:
                continue
            try:
                res = await analyze_stock(code)
                r = res.report
                results.append({
                    "code": r["basic"]["code"],
                    "name": r["basic"]["name"],
                    "price": r["basic"]["price"],
                    "change_pct": r["basic"]["change_pct"],
                    "main_net_inflow_wan": s.get("main_net_inflow_wan"),
                    "chan_signal": r["chan_signal"],
                    "operation": r["operation"],
                    "auxiliary": r["auxiliary"],
                })
            except Exception as e:  # noqa: BLE001
                results.append({"code": code, "name": s.get("name"), "error": str(e)})
        return {
            "workflow": self.name,
            "board_code": sec_info.get("board_code"),
            "board_name": sec_info.get("board_name"),
            "scanned_leaders": results,
        }


def build_default_workflows() -> WorkflowRegistry:
    wf_reg = WorkflowRegistry()
    wf_reg.register(ScanHotspotLeadersWorkflow())
    return wf_reg
