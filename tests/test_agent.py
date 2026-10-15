import asyncio
import sys

from app.agent import agent_runner, memory
from app.agent.external_tools import CLIToolAdapter, MCPToolAdapter
from app.agent.tool_registry import FunctionTool, ToolRegistry
from app.commands import router
from app.llm.base import LLMResponse
from tests.helpers import make_result


def run(coro):
    return asyncio.run(coro)


def test_memory_sliding_window_20_rounds(tmp_db):
    chat_id = "oc_mem_test"
    # 写入 25 轮 (50 条消息), 应自动裁剪保留最近 20 轮 (40 条)
    for i in range(25):
        memory.add_message(chat_id, "user", f"问题 {i}")
        memory.add_message(chat_id, "assistant", f"回答 {i}")
    hist = memory.get_history(chat_id)
    assert len(hist) == 40
    assert hist[0]["content"] == "问题 5"
    assert hist[-1]["content"] == "回答 24"


def test_record_command_analysis_injects_stock_summary(tmp_db):
    chat_id = "oc_follow_up"
    res = make_result("00700", "腾讯控股", 417.8, "观望", "无信号")
    memory.record_command_analysis(chat_id, "Q 00700.HK", res)
    hist = memory.get_history(chat_id)
    assert len(hist) == 2
    assert hist[0] == {"role": "user", "content": "Q 00700.HK"}
    assert "腾讯控股" in hist[1]["content"] and "417.8" in hist[1]["content"]


def test_default_registry_has_9_builtin_tools_plus_workflow():
    agent_runner.reset_tool_registry()
    reg = agent_runner.get_tool_registry()
    names = {t.name for t in reg.all_tools()}
    expected = {
        "search_security",
        "analyze_stock_chan",
        "get_stock_fundamentals",
        "get_market_overview",
        "analyze_index_chan",
        "get_sector_hotspots",
        "get_sector_stocks",
        "search_financial_news",
        "get_chat_watchlist",
        "workflow_scan_hotspot_leaders",
    }
    assert expected.issubset(names)
    assert len(reg.openai_schemas()) >= 10


def test_cli_and_mcp_tool_adapters():
    reg = ToolRegistry()
    cli_tool = CLIToolAdapter(
        name="echo_search_cli",
        label="CLI搜索测试",
        description="测试 CLI 子进程工具",
        command_template=[sys.executable, "-c", "import json,sys; print(json.dumps({'q': sys.argv[1]}))", "{query}"],
    )
    mcp_tool = MCPToolAdapter(
        server_name="fin",
        tool_name="macro",
        label="MCP宏观数据",
        description="测试 MCP 桥接工具",
        parameters={"type": "object", "properties": {"country": {"type": "string"}}},
        caller=lambda name, args: {"tool": name, "country": args.get("country")},
    )
    reg.register(cli_tool)
    reg.register(mcp_tool)

    r_cli = run(reg.call("echo_search_cli", '{"query": "半导体"}', {}))
    assert r_cli.ok and r_cli.output == {"q": "半导体"}

    r_mcp = run(reg.call("mcp_fin_macro", '{"country": "CN"}', {}))
    assert r_mcp.ok and r_mcp.output == {"tool": "macro", "country": "CN"}


def test_agent_runner_multi_step_tool_calling_and_card(monkeypatch, tmp_db):
    reg = ToolRegistry()
    reg.register(FunctionTool(
        name="get_market_overview",
        label="大盘全景与情绪温度计",
        description="获取大盘全景",
        parameters={"type": "object", "properties": {}},
        fn=lambda: {"indices": [{"name": "上证指数", "price": 3707.48, "change_pct": -1.59}],
                    "breadth": {"up": 400, "down": 4500, "sentiment": "普跌"}},
    ))
    monkeypatch.setattr(agent_runner, "get_tool_registry", lambda: reg)

    class FakeToolClient:
        def __init__(self):
            self.step = 0

        async def chat(self, messages, **kwargs):
            self.step += 1
            if self.step == 1:
                tc = [{"id": "call_1", "name": "get_market_overview", "arguments": "{}"}]
                raw = {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": "call_1", "type": "function",
                                    "function": {"name": "get_market_overview", "arguments": "{}"}}],
                }
                return LLMResponse(content="", model="fake", tool_calls=tc, raw_message=raw)
            # 验证第二轮 messages 中包含了 tool 返回的数据
            assert any(m.get("role") == "tool" and "3707.48" in m.get("content", "") for m in messages)
            return LLMResponse(
                content="#### 📊 盘面核心结论\n- 上证指数报 **3707.48**（<font color='green'>**-1.59%**</font>），全市场普跌。",
                model="fake",
            )

    monkeypatch.setattr(agent_runner, "get_default_client", lambda: FakeToolClient())

    ctx = {"chat_id": "oc_agent_chat", "chat_type": "p2p"}
    notes = []
    reply = run(router.handle_async("今天大盘趋势怎么样？", ctx, notify=notes.append))
    assert notes and "正在思考" in notes[0]
    card = reply["card"]
    assert card["template"] == "purple"
    assert "大盘全景与情绪温度计" in card["subtitle"]
    assert "3707.48" in card["markdown"]
    assert card["raw_card"]["schema"] == "2.0"

    # 验证问答已被记录进 20 轮会话记忆
    hist = memory.get_history("oc_agent_chat")
    assert len(hist) == 2 and hist[0]["content"] == "今天大盘趋势怎么样？"
