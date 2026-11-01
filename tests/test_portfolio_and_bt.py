import asyncio
import math

import pandas as pd
import pytest

from app.commands import router
from app.commands.parser import parse
from app.engine.backtester import run_chan_backtest
from app.models import trade_repo
from app.models.trade_repo import InsufficientFundsError, TPlusOneLockedError
from app.services import backtest_service, portfolio_service, screener_service, task_service
from tests.helpers import make_result


def run(coro):
    return asyncio.run(coro)


def test_parse_new_commands_x_p_t_bt_pick():
    assert (parse("X 000001").kind, parse("X 000001").code) == ("X", "000001")
    c_x = parse("X 00700.HK @420.5 100")
    assert (c_x.kind, c_x.code, c_x.buy_price, c_x.quantity) == ("X", "00700", 420.5, 100)
    assert parse("P").kind == "P"
    assert parse("T").kind == "T" and parse("T").code is None
    assert parse("T 600519").code == "600519"
    c_bt = parse("BT 000001 3")
    assert (c_bt.kind, c_bt.code, c_bt.quantity) == ("BT", "000001", 3)
    c_pick = parse("PICK 半导体")
    assert (c_pick.kind, c_pick.text) == ("PICK", "半导体")


def test_100k_initial_capital_and_insufficient_funds(tmp_db):
    chat = "oc_paper"
    acct = trade_repo.get_or_create_account(chat)
    assert acct.initial_capital == 100000.0 and acct.cash_balance == 100000.0

    # 买入超过 10 万 RMB 抛 InsufficientFundsError
    with pytest.raises(InsufficientFundsError) as exc:
        trade_repo.record_buy(chat, "600519", "贵州茅台", 1500.0, 100, trade_date="2026-10-20")
    assert exc.value.max_qty == 66


def test_a_share_t_plus_1_vs_hk_t_plus_0(tmp_db):
    chat = "oc_t1"
    # 1) A股 000001 当日买入 200 股 -> 当日可卖 0 股, 同日卖出抛 TPlusOneLockedError
    b1 = trade_repo.record_buy(chat, "000001", "平安银行", 10.0, 200, trade_date="2026-10-20")
    assert b1["sellable_qty"] == 0 and b1["frozen_qty"] == 200
    with pytest.raises(TPlusOneLockedError):
        trade_repo.record_sell(chat, "000001", 10.5, 100, trade_date="2026-10-20")

    # 2) 次日(2026-10-21)早盘又加仓 100 股 @11.0 -> 总持仓 300 股(均价 10.333), 其中昨仓 200 可卖, 今买 100 冻结
    b2 = trade_repo.record_buy(chat, "000001", "平安银行", 11.0, 100, trade_date="2026-10-21")
    assert b2["position"].current_qty == 300
    assert b2["sellable_qty"] == 200 and b2["frozen_qty"] == 100

    # 尝试在 10-21 卖出 250 股(> 可卖底仓 200 股) -> 被 T+1 拦截
    with pytest.raises(TPlusOneLockedError) as exc2:
        trade_repo.record_sell(chat, "000001", 11.5, 250, trade_date="2026-10-21")
    assert exc2.value.sellable_qty == 200

    # 在 10-21 卖出 150 股(<= 可卖底仓 200 股) -> 成功部分卖出!
    s_part = trade_repo.record_sell(chat, "000001", 11.5, 150, trade_date="2026-10-21")
    assert s_part["action"] == "SELL_PART" and s_part["remain_qty"] == 150 and not s_part["closed"]

    # 3) 港股 00700 当日买入 100 股 -> T+0 当日立即可卖!
    b_hk = trade_repo.record_buy(chat, "00700", "腾讯控股", 400.0, 100, trade_date="2026-10-21")
    assert b_hk["sellable_qty"] == 100 and b_hk["frozen_qty"] == 0
    s_hk = trade_repo.record_sell(chat, "00700", 420.0, None, trade_date="2026-10-21")
    assert s_hk["action"] == "SELL_ALL" and s_hk["closed"] is True
    assert s_hk["realized_pnl_rmb"] == round((420.0 - 400.0) * 100 * trade_repo.HKD_TO_RMB, 2)


def test_router_x_p_t_workflow(monkeypatch, tmp_db):
    ctx = {"chat_id": "oc_flow", "chat_type": "p2p"}
    monkeypatch.setattr(task_service, "analyze_stock", lambda code, **kw: asyncio.sleep(0, result=make_result(code, "平安银行", 10.0)))
    monkeypatch.setattr(task_service.fetcher, "fetch_realtime_price", lambda c: 10.0)
    monkeypatch.setattr(portfolio_service.fetcher, "fetch_realtime_price", lambda c: 11.0)
    monkeypatch.setattr(portfolio_service.fetcher, "fetch_quote", lambda c: {"price": 11.0, "change_pct": 2.0, "name": "平安银行"})

    # 昨天买入 200 股 @10.0
    trade_repo.record_buy("oc_flow", "000001", "平安银行", 10.0, 200, trade_date="2026-10-15")
    # 发出 X 000001 50 (不填价格自动取现价 11.0, 部分卖出 50 股)
    r_x1 = run(router.handle_async("X 000001 50", ctx))
    assert "部分减仓" in r_x1["card"]["subtitle"] and "+50.00 元" in r_x1["card"]["subtitle"]

    # P 查看账户总览
    r_p = run(router.handle_async("P", ctx))
    assert "100,200.00" in r_p["card"]["subtitle"]  # 现金 98550 + 剩余150股*11.0(1650) = 100200

    # X 000001 (不填价格不填股数 -> 全部卖出剩余 150 股)
    r_x2 = run(router.handle_async("X 000001", ctx))
    assert "全部平仓" in r_x2["card"]["subtitle"]

    # T 查看交易流水
    r_t = run(router.handle_async("T 000001", ctx))
    assert "最近 3 笔" in r_t["card"]["title"]


def test_no_lookahead_chan_backtester(tmp_db):
    # 构造 120 根带明显波段起伏的合成日线 K 线
    rows = []
    for i in range(120):
        wave = 1.8 * math.sin(i / 4.0) + 0.02 * i
        c = round(10.0 + wave, 2)
        o = round(c - 0.15 * math.cos(i / 4.0), 2)
        h = round(max(o, c) + 0.25, 2)
        low = round(min(o, c) - 0.25, 2)
        m = 1 + (i // 28)
        d = 1 + (i % 28)
        rows.append({"date": f"2025-{m:02d}-{d:02d}", "open": o, "high": h, "low": low, "close": c, "volume": 10000.0})
    df = pd.DataFrame(rows)

    bt = run_chan_backtest(df, "000001", "合成测试股", initial_capital=100000.0)
    assert "error" not in bt
    assert bt["bars_count"] == 120
    assert len(bt["equity_curve"]) > 0
    html_doc = backtest_service.render_backtest_html(bt)
    assert "缠论无未来函数回测报告" in html_doc
