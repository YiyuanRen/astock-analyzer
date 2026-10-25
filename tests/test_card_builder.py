import json

from app.bot.card_builder import (
    build_agent_chat_card,
    build_analysis_card,
    build_holding_card,
    build_stop_alert_card,
    build_task_list_card,
    build_thinking_card,
    fmt_pct,
)
from app.engine.report_builder import render_plain
from app.llm.report_generator import SYSTEM_PROMPT, build_user_prompt


def _sample_payload(direction="观望", pct=-1.32, entry_zone=None, stop=None, target=None, rr=None):
    report = {
        "basic": {
            "name": "腾讯控股",
            "code": "00700.HK",
            "market": "港股",
            "currency": "HKD",
            "price": 417.8,
            "change_pct": pct,
            "period": "多级别（日线/30分/5分）",
            "time": "2026-10-15 23:45",
        },
        "chan_signal": {
            "bi_direction": "向下",
            "segment_direction": "下降",
            "zhongshu": "价格在中枢下方（中枢 493.5-521.2）",
            "buy_sell_point": "无信号" if direction == "观望" else "二类买点",
            "macd_divergence": False,
            "bi_bars": 29,
            "multi_level_conclusion": "日线无明确买点，关注支撑 493.5 / 压力 521.2。",
        },
        "operation": {
            "direction": direction,
            "entry_zone": entry_zone,
            "target_price": target,
            "stop_loss": stop,
            "rr_ratio": rr,
            "position_advice": "二买较稳，可加仓(20-40%)" if entry_zone else None,
        },
        "auxiliary": {
            "support": 493.5,
            "resistance": 521.2,
            "volume_status": "平量",
        },
        "disclaimer": "⚠️ 仅供参考，不构成投资建议",
        "data_insufficient": False,
    }
    return {
        "code": "00700",
        "name": "腾讯控股",
        "title": "📊 腾讯控股 00700.HK",
        "template": "red" if direction == "买入" else "blue",
        "report": report,
        "markdown": "#### 💡 走势结构与深度解读\n- 分析内容\n\n#### 🎯 关键信号与操作预案\n- 预案内容\n\n⚠️ 仅供参考，不构成投资建议",
    }


def test_fmt_pct_red_up_green_down():
    assert "<font color='red'>**+1.86%**</font>" == fmt_pct(1.86)
    assert "<font color='green'>**-1.32%**</font>" == fmt_pct(-1.32)
    assert "<font color='grey'>**+0.00%**</font>" == fmt_pct(0.0)


def test_a1_watch_mode_hides_empty_operation_fields_and_colors_green():
    card = build_analysis_card(_sample_payload(direction="观望", pct=-1.32))["card"]
    raw = card["raw_card"]
    assert raw["schema"] == "2.0"
    assert "观望等待" in raw["header"]["subtitle"]["content"]
    els = raw["body"]["elements"]
    # 第一个元素为 3 列 trisect KPI (最新价 / 下方支撑 / 上方压力), 不含入场区间/止损/目标空列
    kpi = els[0]
    assert kpi["tag"] == "column_set" and kpi["flex_mode"] == "trisect"
    assert len(kpi["columns"]) == 3
    assert kpi["columns"][0]["background_style"] == "green-50"
    raw_str = json.dumps(raw, ensure_ascii=False)
    assert "<font color='green'>**-1.32%**</font>" in raw_str
    assert "暂无" not in raw_str


def test_a1_buy_mode_renders_four_action_kpis():
    p = _sample_payload(direction="买入", pct=2.15, entry_zone=(410.0, 418.0),
                        stop=398.0, target=460.0, rr="1:3.2")
    raw = build_analysis_card(p)["card"]["raw_card"]
    kpi = raw["body"]["elements"][0]
    assert kpi["flex_mode"] == "flow" and len(kpi["columns"]) == 4
    assert kpi["columns"][0]["background_style"] == "red-50"
    raw_str = json.dumps(raw, ensure_ascii=False)
    assert "410-418" in raw_str and "1:3.2" in raw_str


def test_b1_holding_card_with_and_without_cost():
    p = _sample_payload(direction="观望", pct=1.5)
    # 带成本(盈利 -> red-50, 亏损 -> green-50)
    c_win = build_holding_card(
        p, banner_title="💼 已建立持仓跟踪", price=417.8, cost=400.0, qty=100,
        stop=385.0, stop_source="结构止损", target=460.0
    )["card"]["raw_card"]
    kpis_win = [e for e in c_win["body"]["elements"] if e["tag"] == "column_set" and e["background_style"] == "default"]
    # banner + row1(账本 bisect) + row2(风控 trisect)
    assert len(kpis_win) == 3
    assert kpis_win[1]["flex_mode"] == "bisect"
    assert kpis_win[1]["columns"][1]["background_style"] == "red-50"
    assert "<font color='red'>**+4.45%**</font>" in json.dumps(kpis_win[1], ensure_ascii=False)

    # 不带成本 -> 自动省去第 1 排账本 KPI
    c_nocost = build_holding_card(
        p, banner_title="💼 已建立持仓跟踪", price=417.8, cost=None, qty=None,
        stop=385.0, stop_source="结构止损", target=460.0
    )["card"]["raw_card"]
    kpis_nocost = [e for e in c_nocost["body"]["elements"] if e["tag"] == "column_set" and e["background_style"] == "default"]
    assert len(kpis_nocost) == 2 and kpis_nocost[1]["flex_mode"] == "trisect"


def test_c1_task_list_card_table_and_urgent_sorting():
    items = [
        {
            "display": "腾讯控股 00700.HK", "name": "腾讯控股", "disp_code": "00700.HK",
            "task_type": "B", "price": 417.8, "change_pct": -1.32,
            "buy_price": None, "buy_quantity": None, "pnl_pct": None,
            "stop_loss": None, "target_price": None, "dist_to_stop": None,
            "support": 493.5, "resistance": 521.2, "last_signal": "观望｜无信号（16:10）",
            "urgent": False, "legacy_block": "B block",
        },
        {
            "display": "贵州茅台 600519", "name": "贵州茅台", "disp_code": "600519",
            "task_type": "S", "price": 1220.0, "change_pct": -0.8,
            "buy_price": 1250.0, "buy_quantity": 100, "pnl_pct": -2.4,
            "stop_loss": 1209.68, "target_price": 1338.86, "dist_to_stop": 0.85,
            "support": None, "resistance": None, "last_signal": "观望｜无信号（15:05）",
            "urgent": True, "legacy_block": "S block",
        },
    ]
    raw = build_task_list_card("本群", items)["card"]["raw_card"]
    grey_rows = [e for e in raw["body"]["elements"] if e.get("background_style") == "grey"]
    assert len(grey_rows) == 3  # 表头 + 2 行数据
    # 紧急项(贵州茅台)排在第一行数据并带 ❗, 浮亏 -2.40% 为绿色
    first_data_str = json.dumps(grey_rows[1], ensure_ascii=False)
    assert "❗ **贵州茅台**" in first_data_str
    assert "<font color='green'>**-2.40%**</font>" in first_data_str


def test_d1_stop_alert_card():
    t = type("T", (), {
        "stock_code": "00700", "display": "腾讯控股 00700.HK",
        "stop_loss": 400.0, "buy_price": 420.0, "buy_quantity": 100, "target_price": 460.0,
    })()
    raw = build_stop_alert_card(t, 395.0, True, "⚠️ 仅供参考，不构成投资建议")["card"]["raw_card"]
    assert raw["schema"] == "2.0"
    raw_str = json.dumps(raw, ensure_ascii=False)
    assert "盘中破位应对参考" in raw_str and "C 00700.HK" in raw_str
    assert "<font color='green'>**-5.95%**</font>" in raw_str


def test_prompt_and_fallback_contain_no_placeholder_junk():
    p = _sample_payload(direction="观望")
    user_prompt = build_user_prompt(p["report"])
    assert "entry_zone" not in user_prompt and "stop_loss" not in user_prompt
    assert "白话" in SYSTEM_PROMPT  # 红线明确禁止出现白话/小白字样
    plain = render_plain(p["report"])
    assert "暂无" not in plain and "#### 💡 走势结构与深度解读" in plain


def test_thinking_card_and_collapsible_panel():
    t_card = build_thinking_card(
        "🤖 正在思考并调取实时投研数据...",
        thinking_text="第一步：先看上证指数日线笔段结构...",
        used_tools=["大盘全景与情绪温度计"],
    )["card"]
    assert "大盘全景与情绪温度计" in t_card["subtitle"]
    assert "第一步：先看上证指数日线笔段结构" in t_card["markdown"]

    final_card = build_agent_chat_card(
        "今天大盘怎么样？",
        "#### 📊 结论\n- 震荡",
        used_tools=["大盘全景与情绪温度计"],
        reasoning_text="这是完整的模型深度思考过程内容",
    )["card"]["raw_card"]
    panels = [e for e in final_card["body"]["elements"] if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 1 and panels[0]["expanded"] is False
    assert "这是完整的模型深度思考过程内容" in json.dumps(panels[0], ensure_ascii=False)
