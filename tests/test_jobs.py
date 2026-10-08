import asyncio
from datetime import datetime

import pytest

from app.data import trading_calendar as cal
from app.models import task_repo
from app.scheduler import jobs
from app.services.analysis_service import StockNotFound
from app.services.notifier import RecordingNotifier
from tests.helpers import DATES, dt, make_result

PRIV = "oc_private"
GRP = "oc_group"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def env(tmp_db):
    cal.set_dates_provider(lambda: set(DATES))
    yield
    cal.set_dates_provider(None)


@pytest.fixture
def analysis(monkeypatch):
    """可编程的假分析: results[code] = AnalysisResult 或 Exception; calls 记录调用。"""
    results, calls = {}, []

    async def fake(code, **kw):
        calls.append((code, kw))
        r = results.get(code) or make_result(code)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(jobs, "analyze_stock", fake)
    return type("A", (), {"results": results, "calls": calls})


def mk(chat, code, typ, **kw):
    return task_repo.create_task(chat, "group" if chat == GRP else "p2p", "ou_x", code, "平安银行", typ, **kw)


# ======================= 15:05 日报 =======================
def test_daily_pushes_to_each_tasks_own_chat(analysis):
    mk(PRIV, "000001", "B")
    mk(GRP, "000001", "S", buy_price=10.45, buy_quantity=100, stop_loss=9.5)
    mk(GRP, "600519", "B")
    n = RecordingNotifier()
    stats = run(jobs.daily_analysis(n, current=dt(2026, 10, 8, 15, 5)))
    assert stats["stocks"] == 2 and stats["pushed"] == 3 and stats["failed"] == 0
    assert len(n.to(PRIV)) == 1 and len(n.to(GRP)) == 2
    assert "收盘日报 · 买入跟踪" in n.to(PRIV)[0]["text"]
    assert any("收盘日报 · 持仓跟踪" in m["text"] for m in n.to(GRP))
    # 000001 被两个会话跟踪, 只分析一次
    assert [c for c, _ in analysis.calls].count("000001") == 1
    assert all(kw["force"] for _, kw in analysis.calls)


def test_daily_skips_non_trading_day_unless_forced(analysis):
    mk(PRIV, "000001", "B")
    n = RecordingNotifier()
    assert run(jobs.daily_analysis(n, current=dt(2026, 10, 5, 15, 5)))["skipped"] == "非交易日"
    assert n.sent == [] and analysis.calls == []
    run(jobs.daily_analysis(n, current=dt(2026, 10, 5, 15, 5), force=True))
    assert len(n.sent) == 1


def test_daily_isolates_per_stock_failures(analysis):
    mk(PRIV, "000001", "B")
    mk(PRIV, "600519", "B")
    mk(PRIV, "999999", "B")
    analysis.results["000001"] = RuntimeError("boom")
    analysis.results["999999"] = StockNotFound("999999")
    n = RecordingNotifier()
    stats = run(jobs.daily_analysis(n, current=dt(2026, 10, 8, 15, 5)))
    assert stats["pushed"] == 1 and stats["failed"] == 2
    assert [m["title"] for m in n.sent] == ["📊 平安银行 600519"]


def test_daily_s_ratchet_never_lowers_stop(analysis):
    # 新结构止损 = 10.3*0.985=10.15; 原止损 11.0 更高 -> 保持 11.0
    mk(PRIV, "000001", "S", buy_price=None, stop_loss=11.0)
    n = RecordingNotifier()
    run(jobs.daily_analysis(n, current=dt(2026, 10, 8, 15, 5)))
    t = task_repo.get_active(PRIV, "000001")
    assert t.stop_loss == 11.0
    assert "沿用原止损位" in n.sent[0]["text"] and "止损上移" not in n.sent[0]["text"]


def test_daily_s_ratchet_raises_and_notes(analysis):
    t0 = mk(PRIV, "000001", "S", stop_loss=9.0)
    task_repo.update_fields(t0.id, stop_alert_state="alerted", stop_alert_at="2026-10-07T14:00:00+08:00")
    n = RecordingNotifier()
    run(jobs.daily_analysis(n, current=dt(2026, 10, 8, 15, 5)))
    t = task_repo.get_active(PRIV, "000001")
    assert t.stop_loss == round(10.3 * 0.985, 2)
    assert "🔒 止损上移：9 → 10.15" in n.sent[0]["text"]
    assert t.stop_alert_state is None                      # 新的一天重置提醒状态


def test_daily_s_sell_signal_marks_green_and_warns(analysis):
    mk(PRIV, "000001", "S", stop_loss=9.0)
    analysis.results["000001"] = make_result(direction="卖出", bsp="二类卖点", is_buy=False)
    n = RecordingNotifier()
    run(jobs.daily_analysis(n, current=dt(2026, 10, 8, 15, 5)))
    assert n.sent[0]["template"] == "green" and "二类卖点" in n.sent[0]["text"]


def test_daily_push_failure_counted(analysis):
    mk(PRIV, "000001", "B")
    stats = run(jobs.daily_analysis(RecordingNotifier(fail=True), current=dt(2026, 10, 8, 15, 5)))
    assert stats["failed"] == 1 and stats["pushed"] == 0


# ======================= B 盘中 =======================
def buy_result(direction="观望", bi_idx=40):
    return make_result(direction=direction, bsp="二类买点", bi_idx=bi_idx)


def test_intraday_silent_without_signal(analysis):
    mk(PRIV, "000001", "B")
    n = RecordingNotifier()
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 10, 1)))
    assert n.sent == []


def test_intraday_pending_then_confirmed_then_dedupe(analysis):
    mk(PRIV, "000001", "B")
    n = RecordingNotifier()
    analysis.results["000001"] = buy_result("观望")                 # 日线买点, 30m 未共振
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 10, 1)))
    assert len(n.sent) == 1 and n.sent[0]["template"] == "orange" and "尚未确认" in n.sent[0]["text"]
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 10, 31)))        # 同一信号 -> 去重
    assert len(n.sent) == 1
    analysis.results["000001"] = buy_result("买入")                 # 共振确认 -> 升级再推一次
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 11, 1)))
    assert len(n.sent) == 2 and n.sent[1]["template"] == "red" and "已确认" in n.sent[1]["text"]
    assert "盘中日线尚未收盘" in n.sent[1]["text"]
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 11, 31)))
    assert len(n.sent) == 2


def test_intraday_new_bi_is_new_signal(analysis):
    mk(PRIV, "000001", "B")
    n = RecordingNotifier()
    analysis.results["000001"] = buy_result("买入", bi_idx=40)
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 10, 1)))
    analysis.results["000001"] = buy_result("买入", bi_idx=42)
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 14, 1)))
    assert len(n.sent) == 2


def test_intraday_push_failure_not_marked_so_it_retries(analysis):
    t = mk(PRIV, "000001", "B")
    analysis.results["000001"] = buy_result("买入")
    run(jobs.intraday_b(RecordingNotifier(fail=True), current=dt(2026, 10, 8, 10, 1)))
    assert task_repo.get_by_id(t.id).last_signal_key is None
    n = RecordingNotifier()
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 10, 31)))
    assert len(n.sent) == 1


def test_intraday_only_b_tasks_and_session_guard(analysis):
    mk(PRIV, "000001", "S", stop_loss=9.0)
    analysis.results["000001"] = buy_result("买入")
    n = RecordingNotifier()
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 10, 1)))
    assert n.sent == [] and analysis.calls == []                    # S 不跑盘中分析
    mk(PRIV, "600519", "B")
    analysis.results["600519"] = buy_result("买入")
    assert run(jobs.intraday_b(n, current=dt(2026, 10, 8, 16, 0)))["skipped"] == "非交易时段"
    assert run(jobs.intraday_b(n, current=dt(2026, 10, 5, 10, 1)))["skipped"] == "非交易时段"  # 节假日
    assert n.sent == []
    assert run(jobs.intraday_b(n, current=dt(2026, 10, 8, 11, 31)))["skipped"] is None        # 11:31 允许


def test_intraday_multiple_chats_each_pushed_once(analysis):
    mk(PRIV, "000001", "B")
    mk(GRP, "000001", "B")
    analysis.results["000001"] = buy_result("买入")
    n = RecordingNotifier()
    run(jobs.intraday_b(n, current=dt(2026, 10, 8, 10, 1)))
    assert len(n.to(PRIV)) == 1 and len(n.to(GRP)) == 1
    assert [c for c, _ in analysis.calls].count("000001") == 1


def test_daily_refreshes_signal_key_so_next_intraday_does_not_repeat(analysis):
    t = mk(PRIV, "000001", "B")
    analysis.results["000001"] = buy_result("买入")
    n = RecordingNotifier()
    run(jobs.daily_analysis(n, current=dt(2026, 10, 8, 15, 5)))
    assert task_repo.get_by_id(t.id).last_signal_key == "二类买点|40|confirmed"
    n2 = RecordingNotifier()
    run(jobs.intraday_b(n2, current=dt(2026, 10, 9, 10, 1), force=True))
    assert n2.sent == []


# ======================= S 止损监控 =======================
def test_stop_loss_no_alert_when_above(analysis):
    mk(PRIV, "000001", "S", stop_loss=10.0)
    n = RecordingNotifier()
    s = jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 0), prices={"000001": 10.5})
    assert s["checked"] == 1 and n.sent == []


def test_stop_loss_first_alert_goes_to_owner_chat_and_repeats_every_30min():
    mk(PRIV, "000001", "S", buy_price=10.45, buy_quantity=100, stop_loss=10.0, target_price=11.8)
    mk(GRP, "000001", "S", stop_loss=9.0)         # 群里的止损更低, 价格 9.8 不触发
    n = RecordingNotifier()
    p = {"000001": 9.8}
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 0), prices=p)
    assert len(n.to(PRIV)) == 1 and n.to(GRP) == []
    assert "止损触发" in n.sent[0]["title"] and n.sent[0]["template"] == "orange"
    assert "浮盈：**-6.22%**" in n.sent[0]["text"] and "仅供参考" in n.sent[0]["text"]
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 3), prices=p)       # 3 分钟后: 不重复
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 29), prices=p)
    assert len(n.sent) == 1
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 30), prices=p)      # 满 30 分钟: 持续提醒
    assert len(n.sent) == 2 and "持续" in n.sent[1]["title"]
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 45), prices=p)
    assert len(n.sent) == 2
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 11, 0), prices=p)
    assert len(n.sent) == 3


def test_stop_loss_recovery_resets_then_new_alert_is_first_again():
    t = mk(PRIV, "000001", "S", stop_loss=10.0)
    n = RecordingNotifier()
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 0), prices={"000001": 9.9})
    s = jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 3), prices={"000001": 10.2})
    assert s["recovered"] == 1 and task_repo.get_by_id(t.id).stop_alert_state is None
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 6), prices={"000001": 9.9})
    assert len(n.sent) == 2 and "止损触发" in n.sent[1]["title"]


def test_stop_loss_new_day_alerts_again_immediately():
    mk(PRIV, "000001", "S", stop_loss=10.0)
    n = RecordingNotifier()
    jobs.stop_loss_check(n, current=dt(2026, 9, 30, 14, 50), prices={"000001": 9.9}, force=True)
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 9, 31), prices={"000001": 9.9})
    assert len(n.sent) == 2 and "止损触发" in n.sent[1]["title"]


def test_stop_loss_push_failure_retries_next_time():
    t = mk(PRIV, "000001", "S", stop_loss=10.0)
    jobs.stop_loss_check(RecordingNotifier(fail=True), current=dt(2026, 10, 8, 10, 0), prices={"000001": 9.9})
    assert task_repo.get_by_id(t.id).stop_alert_state is None
    n = RecordingNotifier()
    jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 3), prices={"000001": 9.9})
    assert len(n.sent) == 1


def test_stop_loss_guard_and_force_and_missing_price(monkeypatch):
    mk(PRIV, "000001", "S", stop_loss=10.0)
    mk(PRIV, "600519", "S", stop_loss=1000.0)
    n = RecordingNotifier()
    assert jobs.stop_loss_check(n, current=dt(2026, 10, 8, 12, 0), prices={"000001": 9})["skipped"]   # 午休
    assert jobs.stop_loss_check(n, current=dt(2026, 10, 5, 10, 0), prices={"000001": 9})["skipped"]   # 节假日
    assert n.sent == []
    monkeypatch.setattr(jobs.fetcher, "fetch_realtime_price", lambda c: None if c == "600519" else 9.0)
    s = jobs.stop_loss_check(n, current=dt(2026, 10, 8, 12, 0), force=True)       # 强制 + 走行情; 600519 取不到价
    assert s["checked"] == 1 and len(n.sent) == 1


def test_stop_loss_ignores_b_tasks_and_tasks_without_stop():
    mk(PRIV, "000001", "B")
    mk(PRIV, "600519", "S")                       # 无止损位
    n = RecordingNotifier()
    s = jobs.stop_loss_check(n, current=dt(2026, 10, 8, 10, 0), prices={"000001": 1, "600519": 1})
    assert s["checked"] == 0 and n.sent == []


# ======================= 调度器 =======================
def test_build_scheduler_registers_expected_jobs():
    sched = jobs.build_scheduler(RecordingNotifier())
    ids = {j.id for j in sched.get_jobs()}
    assert "daily_analysis" in ids and "stop_loss" in ids
    assert {i for i in ids if i.startswith("intraday_b_")} == {
        f"intraday_b_{t}" for t in ["10:01", "10:31", "11:01", "11:31", "13:31", "14:01", "14:31"]}


def test_cron_fire_times_weekdays_only():
    sched = jobs.build_scheduler(RecordingNotifier())
    daily = next(j for j in sched.get_jobs() if j.id == "daily_analysis")
    stop = next(j for j in sched.get_jobs() if j.id == "stop_loss")
    sat = dt(2026, 10, 10, 9, 0)
    assert daily.trigger.get_next_fire_time(None, sat) == dt(2026, 10, 12, 15, 5)       # 跳到周一
    assert stop.trigger.get_next_fire_time(None, dt(2026, 10, 8, 9, 29)) == dt(2026, 10, 8, 9, 30)
    assert stop.trigger.get_next_fire_time(None, dt(2026, 10, 8, 11, 58)) == dt(2026, 10, 8, 13, 0)  # 午休后
