import asyncio

import pytest

from app.commands import router
from app.models import cache_repo, task_repo
from app.services import task_service as ts
from app.services.analysis_service import AnalysisResult, StockNotFound

PRIV = {"chat_id": "oc_private", "chat_type": "p2p", "sender_open_id": "ou_a"}
GROUP = {"chat_id": "oc_group", "chat_type": "group", "sender_open_id": "ou_b"}


def run(coro):
    return asyncio.run(coro)


def make_result(code="000001", name="平安银行", price=11.5, direction="观望", bsp="无信号"):
    payload = {
        "code": code, "name": name, "quote": {"price": price, "name": name},
        "daily": {"zhongshu_range": [10.0, 10.6], "seg_low": 10.2, "seg_high": 12.0,
                  "latest_bsp": {"is_buy": True, "price": 10.3}, "buy_sell_point": bsp},
        "ml": {"operation_direction": direction, "resistance": 11.8},
        "report": {}, "markdown": "LLM正文", "template": "blue", "title": f"📊 {name} {code}",
    }
    return AnalysisResult(code, name, payload, "2026-10-08T10:00:00+08:00", False)


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_db):
    async def fake_analyze(code, **kw):
        if code == "999999":
            raise StockNotFound(code)
        return make_result(code, name={"600519": "贵州茅台"}.get(code, "平安银行"),
                           price={"600519": 1258.62}.get(code, 11.5))

    monkeypatch.setattr(ts, "analyze_stock", fake_analyze)
    monkeypatch.setattr(ts.fetcher, "fetch_realtime_price",
                        lambda code: {"600519": 1258.62}.get(code, 11.5))
    monkeypatch.setattr(ts.fetcher, "fetch_quote", lambda code: {
        "price": {"600519": 1258.62}.get(code, 11.5), "change_pct": 1.2, "name": "x"})


def md(reply):
    return reply["card"]["markdown"]


# ---------------- B ----------------
def test_b_creates_task_and_card():
    r = run(ts.create_b(PRIV, "000001"))
    assert "已创建买入跟踪" in md(r) and "LLM正文" in md(r)
    t = task_repo.get_active("oc_private", "000001")
    assert t.task_type == "B" and t.stock_name == "平安银行" and t.created_by == "ou_a"


def test_b_repeat_is_idempotent():
    run(ts.create_b(PRIV, "000001"))
    r = run(ts.create_b(PRIV, "000001"))
    assert "已在买入跟踪中" in md(r) and len(task_repo.list_active("oc_private")) == 1


def test_b_rejected_when_s_exists():
    run(ts.create_s(PRIV, "000001", 10.45, 100))
    r = run(ts.create_b(PRIV, "000001"))
    assert isinstance(r, str) and "C 000001" in r
    assert task_repo.get_active("oc_private", "000001").task_type == "S"


def test_invalid_code_creates_nothing():
    assert "未查询到 999999" in run(ts.create_b(PRIV, "999999"))
    assert "未查询到 999999" in run(ts.create_s(PRIV, "999999", None, None))
    assert task_repo.list_active() == []


# ---------------- S ----------------
def test_s_full_creates_levels_and_header():
    r = run(ts.create_s(PRIV, "000001", 10.45, 100))
    t = task_repo.get_active("oc_private", "000001")
    assert (t.task_type, t.buy_price, t.buy_quantity) == ("S", 10.45, 100)
    # 结构止损: 候选 10.3(买点)/10.2/10.0 中最近且<=11.27 的 10.3 -> 10.15; 成本保护 10.45*0.92=9.61 -> 取结构
    assert t.stop_loss == round(10.3 * 0.985, 2)
    assert t.target_price == 11.8
    m = md(r)
    assert "已建立持仓跟踪" in m and "成本：10.45 × 100 股" in m and "止损" in m and "LLM正文" in m


def test_s_without_cost():
    r = run(ts.create_s(PRIV, "000001", None, None))
    t = task_repo.get_active("oc_private", "000001")
    assert t.buy_price is None and t.stop_loss is not None
    assert "未提供成本价" in md(r)


def test_s_cancels_existing_b_and_keeps_history():
    run(ts.create_b(PRIV, "000001"))
    b_id = task_repo.get_active("oc_private", "000001").id
    r = run(ts.create_s(PRIV, "000001", 10.45, 100))
    assert "已自动取消该股的买入跟踪" in md(r)
    assert task_repo.get_by_id(b_id).status == "cancelled"
    assert task_repo.get_active("oc_private", "000001").task_type == "S"


def test_s_reissue_updates_and_resets_stop():
    run(ts.create_s(PRIV, "000001", 10.45, 100))
    t = task_repo.get_active("oc_private", "000001")
    task_repo.update_fields(t.id, stop_loss=99.0, stop_alert_state="alerted", stop_alert_at="x")
    r = run(ts.create_s(PRIV, "000001", 10.6, None))     # 数量沿用
    t2 = task_repo.get_active("oc_private", "000001")
    assert t2.id == t.id and (t2.buy_price, t2.buy_quantity) == (10.6, 100)
    assert t2.stop_loss != 99.0 and t2.stop_alert_state is None   # 重置(允许下调)
    assert "已更新持仓跟踪" in md(r)


def test_s_cost_protection_wins_when_higher():
    # 成本 12.5 -> 个人止损 11.5(8%)>结构止损 10.15 -> 取成本保护; 现价 11.5 => 恰在止损位
    run(ts.create_s(PRIV, "000001", 12.5, 100))
    t = task_repo.get_active("oc_private", "000001")
    assert t.stop_loss == 11.5


# ---------------- C ----------------
def test_cancel():
    run(ts.create_b(PRIV, "000001"))
    msg = ts.cancel(PRIV, "000001")
    assert "已取消" in msg and "买入跟踪" in msg
    assert task_repo.get_active("oc_private", "000001") is None
    assert "没有 000001 的活跃任务" in ts.cancel(PRIV, "000001")


# ---------------- 会话隔离 / 群共享 ----------------
def test_private_and_group_independent():
    run(ts.create_b(PRIV, "000001"))
    run(ts.create_b(GROUP, "000001"))
    assert len(task_repo.list_active()) == 2
    ts.cancel(GROUP, "000001")
    assert task_repo.get_active("oc_private", "000001") is not None


def test_group_tasks_shared_among_members():
    run(ts.create_b(GROUP, "000001"))                       # ou_b 创建
    other = {**GROUP, "sender_open_id": "ou_c"}             # 群里另一人
    assert "000001" in md(run(ts.list_tasks(other)))        # 能看到
    assert "已取消" in ts.cancel(other, "000001")             # 能取消


# ---------------- L ----------------
def test_list_empty_and_scoped():
    assert "暂无活跃任务" in run(ts.list_tasks(PRIV))
    run(ts.create_b(GROUP, "000001"))
    assert "暂无活跃任务" in run(ts.list_tasks(PRIV))        # 私聊看不到群任务


def test_list_contents(monkeypatch):
    run(ts.create_b(PRIV, "000001"))
    run(ts.create_s(PRIV, "600519", 1200.0, 100))
    cache_repo.put("000001", make_result().payload, "k", "2026-10-08T10:00:00+08:00")
    r = run(ts.list_tasks(PRIV))
    m = md(r)
    assert "共 2 个" in r["card"]["title"]
    assert "**平安银行 000001**｜B 买入跟踪" in m and "最近结论：观望｜无信号（10:00）" in m
    assert "**贵州茅台 600519**｜S 持仓跟踪" in m and "成本：1200 × 100" in m and "止损：" in m and "浮盈 +4.88%" in m
    assert "最近结论：尚无分析" in m   # 600519 没有缓存


# ---------------- router 集成 ----------------
def test_router_dispatch_and_notify():
    notes = []
    r = run(router.handle_async("B 000001", PRIV, notify=notes.append))
    assert "已创建买入跟踪" in md(r) and notes and "正在分析" in notes[0]
    assert "已取消" in run(router.handle_async("C 000001", PRIV))
    assert "暂无活跃任务" in run(router.handle_async("L", PRIV))
    r = run(router.handle_async("S 000001 @10.45 100", PRIV, notify=notes.append))
    assert "已建立持仓跟踪" in md(r)
    assert "指令格式" in run(router.handle_async("你好", PRIV))
