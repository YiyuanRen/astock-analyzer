import asyncio
from datetime import date, datetime

import pytest

from app.data import trading_calendar as cal
from app.models.database import TZ
from app.services import analysis_service as svc

# 模拟国庆: 9/29 9/30 为交易日, 10/1-10/7 休市, 10/8 恢复(2026)
DATES = {date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8)}


def dt(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


@pytest.fixture(autouse=True)
def fake_calendar():
    cal.set_dates_provider(lambda: set(DATES))
    yield
    cal.set_dates_provider(None)


def test_trading_day_follows_data_not_weekday():
    now = dt(2026, 10, 8, 16)
    assert cal.is_trading_day(date(2026, 10, 8), now)
    assert not cal.is_trading_day(date(2026, 10, 5), now)  # 周一但国庆休市
    assert not cal.is_trading_day(date(2026, 10, 3), now)  # 周六
    assert cal.prev_trading_day(date(2026, 10, 8), now) == date(2026, 9, 30)


def test_trading_time_windows():
    ok = [dt(2026, 10, 8, 9, 30), dt(2026, 10, 8, 11, 30), dt(2026, 10, 8, 13, 0), dt(2026, 10, 8, 15, 0)]
    no = [dt(2026, 10, 8, 9, 29), dt(2026, 10, 8, 12, 0), dt(2026, 10, 8, 15, 1)]
    assert all(cal.is_trading_time(x) for x in ok)
    assert not any(cal.is_trading_time(x) for x in no)


def test_market_open_requires_trading_day():
    assert cal.is_market_open(dt(2026, 10, 8, 10, 0))
    assert not cal.is_market_open(dt(2026, 10, 5, 10, 0))  # 节假日工作日钟点


def test_session_keys():
    assert cal.session_key(dt(2026, 10, 8, 10, 0)) == "2026-10-08-open"
    assert cal.session_key(dt(2026, 10, 8, 12, 0)) == "2026-10-08-open"   # 午休仍属盘中会话
    assert cal.session_key(dt(2026, 10, 8, 15, 30)) == "2026-10-08-close"
    assert cal.session_key(dt(2026, 10, 8, 8, 0)) == "2026-09-30-close"   # 开盘前 -> 上一交易日
    assert cal.session_key(dt(2026, 10, 5, 11, 0)) == "2026-09-30-close"  # 节假日
    assert cal.session_key(dt(2026, 10, 10, 11, 0)) == "2026-10-08-close"  # 周六


def test_provider_failure_falls_back_to_weekday():
    def boom():
        raise RuntimeError("network down")

    cal.set_dates_provider(boom)
    assert cal.is_trading_day(date(2026, 10, 8), dt(2026, 10, 8, 10))       # 周四
    assert not cal.is_trading_day(date(2026, 10, 10), dt(2026, 10, 10, 10))  # 周六


# ---------------- 分析服务缓存 ----------------
@pytest.fixture
def counting_compute(monkeypatch, tmp_db):
    calls = []

    async def fake(code):
        calls.append(code)
        return {"code": code, "name": "平安银行", "quote": {"price": 11.5}, "daily": {}, "m30": {},
                "m5": {}, "ml": {"operation_direction": "观望"}, "report": {},
                "markdown": f"报告#{len(calls)}", "template": "blue", "title": "📊 平安银行 000001"}

    monkeypatch.setattr(svc, "_compute", fake)
    return calls


def run(coro):
    return asyncio.run(coro)


def test_cache_hit_within_ttl_during_session(counting_compute):
    r1 = run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 10, 0)))
    r2 = run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 11, 0)))     # 1h < 2h
    assert counting_compute == ["000001"] and not r1.from_cache and r2.from_cache
    assert r2.markdown == "报告#1"
    assert "缓存于 10:00" in r2.to_card()["card"]["markdown"]


def test_cache_expires_after_ttl_in_session(counting_compute):
    run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 9, 40)))
    r = run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 14, 0)))      # >2h
    assert len(counting_compute) == 2 and not r.from_cache


def test_intraday_cache_invalid_after_close(counting_compute):
    run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 14, 50)))
    r = run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 15, 10)))     # 跨收盘 -> 失效
    assert len(counting_compute) == 2 and not r.from_cache


def test_after_close_cache_valid_until_next_open(counting_compute):
    run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 15, 10)))
    r = run(svc.analyze_stock("000001", current=dt(2026, 10, 9, 8, 0)))       # 次日开盘前, 未开盘
    # 10/9 数据里没有 -> 按工作日推断; 开盘前 session 为 上一交易日(10/8)-close, 与缓存一致
    assert len(counting_compute) == 1 and r.from_cache


def test_force_recomputes_but_reuses_very_fresh(counting_compute):
    run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 10, 0)))
    r = run(svc.analyze_stock("000001", force=True, current=dt(2026, 10, 8, 10, 1)))
    assert len(counting_compute) == 2 and not r.from_cache
    r = run(svc.analyze_stock("000001", force=True, fresh_within_sec=120, current=dt(2026, 10, 8, 10, 2)))
    assert len(counting_compute) == 2 and r.from_cache                       # 1 分钟前刚算过 -> 复用


def test_stock_not_found_propagates(monkeypatch, tmp_db):
    async def nf(code):
        raise svc.StockNotFound(code)

    monkeypatch.setattr(svc, "_compute", nf)
    with pytest.raises(svc.StockNotFound):
        run(svc.analyze_stock("999999", current=dt(2026, 10, 8, 10, 0)))


def test_to_card_prepends_header(counting_compute):
    r = run(svc.analyze_stock("000001", current=dt(2026, 10, 8, 10, 0)))
    card = r.to_card(header_md="**已创建买入跟踪**", template="red")["card"]
    assert card["markdown"].startswith("**已创建买入跟踪**") and card["template"] == "red"
