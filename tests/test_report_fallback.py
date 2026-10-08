import asyncio

import pandas as pd
import pytest

from app.engine.chan_analyzer import ChanAnalyzer
from app.engine.multi_level import multi_level_analyze
from app.engine.report_builder import build_report, render_plain
from app.llm import report_generator as rg
from app.llm.base import LLMResponse

DISCLAIMER = "⚠️ 仅供参考，不构成投资建议"


def make_report(insufficient=False):
    return {
        "basic": {"name": "平安银行", "code": "000001", "price": 11.5, "change_pct": 1.2,
                  "period": "多级别", "time": "2026-10-08 10:00"},
        "chan_signal": {"bi_direction": "向上", "segment_direction": "上升", "zhongshu": "中枢 10-10.6",
                        "buy_sell_point": "无信号", "macd_divergence": False, "bi_bars": 8,
                        "multi_level_conclusion": "观望"},
        "operation": {"direction": "观望", "entry_zone": None, "target_price": None, "stop_loss": None,
                      "rr_ratio": None, "position_advice": None},
        "auxiliary": {"support": 10.0, "resistance": 11.8, "volume_status": "平量"},
        "disclaimer": DISCLAIMER, "data_insufficient": insufficient,
    }


class FakeClient:
    def __init__(self, behavior):
        self.behavior, self.calls = behavior, 0

    async def chat(self, messages, **kw):
        self.calls += 1
        return await self.behavior()


def run_with(monkeypatch, behavior, report=None):
    client = FakeClient(behavior)
    monkeypatch.setattr(rg, "get_default_client", lambda: client)
    out = asyncio.run(rg.generate_report(report or make_report()))
    return out, client


def test_llm_timeout_degrades_to_plain_text(monkeypatch):
    monkeypatch.setattr(rg, "get_config", lambda: type("C", (), {"llm": {"timeout_seconds": 0.05}})())

    async def slow():
        await asyncio.sleep(1)

    out, _ = run_with(monkeypatch, slow)
    assert out == render_plain(make_report()) and DISCLAIMER in out


def test_llm_exception_degrades(monkeypatch):
    async def boom():
        raise RuntimeError("api down")

    out, _ = run_with(monkeypatch, boom)
    assert out == render_plain(make_report())


def test_llm_empty_content_degrades(monkeypatch):
    async def empty():
        return LLMResponse(content="  ", model="m")

    out, _ = run_with(monkeypatch, empty)
    assert out == render_plain(make_report())


def test_llm_missing_disclaimer_is_appended(monkeypatch):
    async def ok():
        return LLMResponse(content="分析正文", model="m")

    out, _ = run_with(monkeypatch, ok)
    assert out.startswith("分析正文") and out.endswith(DISCLAIMER)


def test_data_insufficient_skips_llm(monkeypatch):
    async def never():
        raise AssertionError("不应调用 LLM")

    out, client = run_with(monkeypatch, never, make_report(insufficient=True))
    assert client.calls == 0 and "数据不足，建议观望" in out and DISCLAIMER in out


def test_new_stock_with_few_bars_flows_to_insufficient_report():
    df = pd.DataFrame({"date": [f"2026-09-{d:02d}" for d in range(1, 11)], "open": 10.0, "high": 10.5,
                       "low": 9.8, "close": 10.2, "volume": 1000.0})
    az = ChanAnalyzer()
    daily, m30, m5 = az.analyze(df, "daily"), az.analyze(df, "30m"), az.analyze(df, "5m")
    assert daily["error"] == "数据不足，建议观望"
    ml = multi_level_analyze(daily, m30, m5, 10.2)
    assert ml["operation_direction"] == "观望" and "数据不足" in ml["multi_level_conclusion"]
    report = build_report({"name": "新股", "code": "301999", "price": 10.2, "change_pct": 0.0},
                          daily, m30, m5, ml, df)
    assert report["data_insufficient"] is True
    assert "数据不足，建议观望" in render_plain(report)
