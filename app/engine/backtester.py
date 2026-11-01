"""基于 chan.py 逐根 K 线回放 (step_load) 的无未来函数缠论回测引擎。

核心规则:
1. 开启 CChanConfig({"trigger_step": True}), 每喂入一根历史日线 K 线生成当时的静态快照, 严格杜绝未来函数。
2. 初始资金: 100,000.00 元(以标的本币计价核算净值与收益率)。
3. 开仓条件(空仓时):
   - 当下帧最新买卖点为一买/二买/三买(is_buy=True), 且该买点位于最新一笔或倒数第二笔并形成底分型确认,
     按当日收盘价全仓买入(整手或整股)。
4. 平仓条件(持仓时, A股严格遵守 T+1 次一交易日方可卖出):
   - 止损离场: 当日最低价/收盘价跌破当前生效止损位(建仓时取结构止损与 -8% 成本保护较高者, 持仓期间每日按结构棘轮只上移不下移);
   - 卖点止盈: 当下帧出现一卖/二卖/三卖(is_buy=False)且位于最新一笔顶分型确认, 按当日收盘价平仓。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE

from app.data.market import decimals_of, display_code, market_of
from app.engine import holding
from app.engine.chan_analyzer import _bsp_text, _df_to_klus


@dataclass
class BacktestTrade:
    buy_date: str
    buy_price: float
    buy_bsp: str
    sell_date: str
    sell_price: float
    sell_reason: str
    quantity: int
    pnl_amount: float
    pnl_pct: float
    hold_days: int


def _klu_date_str(klu) -> str:
    t = klu.time
    return f"{t.year:04d}-{t.month:02d}-{t.day:02d}"


def _snapshot_daily_dict(kl, last_close: float, dec: int = 2) -> dict:
    zs_range = None
    if len(kl.zs_list) > 0:
        zs = kl.zs_list[-1]
        zs_range = (round(float(zs.low), dec), round(float(zs.high), dec))
    seg_low = seg_high = None
    if len(kl.seg_list) > 0:
        last_seg = kl.seg_list[-1]
        try:
            seg_low = round(float(last_seg._low()), dec)
            seg_high = round(float(last_seg._high()), dec)
        except Exception:
            pass
    bsps = kl.bs_point_lst.getSortedBspList()
    latest_bsp = None
    cur_bsp_text = "无信号"
    if bsps:
        latest = bsps[-1]
        b_txt = _bsp_text(latest)
        latest_bsp = {
            "text": b_txt,
            "is_buy": bool(latest.is_buy),
            "bi_idx": latest.bi.idx,
            "price": round(float(latest.bi.get_end_val()), dec),
        }
        if latest.bi.idx >= (len(kl.bi_list) - 1):
            cur_bsp_text = b_txt
    return {
        "last_close": last_close,
        "zhongshu_range": zs_range,
        "seg_low": seg_low,
        "seg_high": seg_high,
        "latest_bsp": latest_bsp,
        "buy_sell_point": cur_bsp_text,
    }


def run_chan_backtest(
    df: pd.DataFrame,
    code: str = "000001",
    name: str = "",
    initial_capital: float = 100000.0,
) -> dict:
    """对给定日线 DataFrame 执行逐根 K 线无未来函数缠论回测。"""
    disp = display_code(code)
    mkt = market_of(code)
    dec = decimals_of(code)
    if df is None or len(df) < 40:
        return {
            "code": disp,
            "name": name or disp,
            "error": "历史 K 线不足 40 根，无法执行有效缠论回测",
        }

    conf = CChanConfig({
        "trigger_step": True,
        "skip_step": 30,
        "bi_strict": True,
        "macd_algo": "peak",
        "print_warning": False,
    })
    chan = CChan(
        code=code,
        data_src=DATA_SRC.BAO_STOCK,
        lv_list=[KL_TYPE.K_DAY],
        config=conf,
        autype=AUTYPE.QFQ,
    )
    klus = _df_to_klus(df)
    for idx, klu in enumerate(klus):
        klu.set_idx(idx)
        klu.kl_type = KL_TYPE.K_DAY

    # 先喂入前 30 根 K 线建立初始笔段结构, 随后逐根 K 线调用 trigger_load 增量回放(无未来函数且不依赖外部 DataAPI)
    warmup = min(30, len(klus) - 1)
    if warmup > 0:
        chan.trigger_load({KL_TYPE.K_DAY: klus[:warmup]})

    cash = float(initial_capital)
    pos_qty = 0
    pos_cost = 0.0
    pos_buy_date = ""
    pos_buy_idx = -1
    pos_buy_bsp = ""
    pos_stop = 0.0

    trades: list[BacktestTrade] = []
    equity_curve: list[dict] = []

    first_close = float(df["close"].iloc[warmup])
    last_close = float(df["close"].iloc[-1])

    for step_idx, next_klu in enumerate(klus[warmup:], start=warmup):
        chan.trigger_load({KL_TYPE.K_DAY: [next_klu]})
        kl = chan.kl_datas[KL_TYPE.K_DAY]
        if len(kl.lst) == 0 or len(kl.bi_list) == 0:
            continue
        cur_klu = kl.lst[-1].lst[-1]
        cur_date = _klu_date_str(cur_klu)
        cur_close = float(cur_klu.close)
        cur_low = float(cur_klu.low)
        daily_info = _snapshot_daily_dict(kl, cur_close, dec=dec)
        bsp_txt = daily_info["buy_sell_point"]
        latest_bsp = daily_info["latest_bsp"] or {}

        if pos_qty > 0:
            # A股 T+1: 买入当日(cur_date == pos_buy_date)不可卖出; 次一交易日起可卖
            can_sell_today = (mkt == "HK") or (cur_date > pos_buy_date)
            if can_sell_today:
                sell_reason = None
                sell_px = cur_close
                if cur_low <= pos_stop or cur_close <= pos_stop:
                    sell_reason = f"触及止损({pos_stop:g})"
                    sell_px = round(min(cur_close, pos_stop), dec)
                elif bsp_txt != "无信号" and latest_bsp.get("is_buy") is False:
                    sell_reason = f"出现{bsp_txt}"
                    sell_px = round(cur_close, dec)

                if sell_reason:
                    proceeds = round(sell_px * pos_qty, 2)
                    pnl_amt = round((sell_px - pos_cost) * pos_qty, 2)
                    pnl_pct = round((sell_px / pos_cost - 1) * 100, 2)
                    cash = round(cash + proceeds, 2)
                    trades.append(BacktestTrade(
                        buy_date=pos_buy_date,
                        buy_price=pos_cost,
                        buy_bsp=pos_buy_bsp,
                        sell_date=cur_date,
                        sell_price=sell_px,
                        sell_reason=sell_reason,
                        quantity=pos_qty,
                        pnl_amount=pnl_amt,
                        pnl_pct=pnl_pct,
                        hold_days=max(1, step_idx - pos_buy_idx),
                    ))
                    pos_qty = 0
                else:
                    # 盘后按最新结构棘轮上移止损(只上移不下移)
                    lv = holding.compute_levels(daily_info, None, cur_close, pos_cost, decimals=dec)
                    pos_stop, _ = holding.ratchet(pos_stop, lv.stop_loss)
        else:
            # 空仓: 检查是否出现日线买点(一买/二买/三买)
            if bsp_txt != "无信号" and latest_bsp.get("is_buy") is True:
                lot = 100 if cur_close * 100 <= cash else 1
                qty = int(cash // (cur_close * lot)) * lot
                if qty > 0:
                    pos_qty = qty
                    pos_cost = round(cur_close, dec)
                    pos_buy_date = cur_date
                    pos_buy_idx = step_idx
                    pos_buy_bsp = bsp_txt
                    lv = holding.compute_levels(daily_info, None, cur_close, pos_cost, decimals=dec)
                    pos_stop = lv.stop_loss
                    cash = round(cash - pos_cost * pos_qty, 2)

        equity = round(cash + pos_qty * cur_close, 2)
        bench_equity = round(initial_capital * (cur_close / first_close), 2) if first_close > 0 else initial_capital
        equity_curve.append({
            "date": cur_date,
            "equity": equity,
            "benchmark": bench_equity,
            "price": round(cur_close, dec),
        })

    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
    total_return_pct = round((final_equity / initial_capital - 1) * 100, 2)
    benchmark_return_pct = round((last_close / first_close - 1) * 100, 2) if first_close > 0 else 0.0
    alpha_pct = round(total_return_pct - benchmark_return_pct, 2)

    # 最大回撤计算
    peak = initial_capital
    max_dd_pct = 0.0
    for pt in equity_curve:
        eq = pt["equity"]
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    wins = [t for t in trades if t.pnl_amount > 0]
    losses = [t for t in trades if t.pnl_amount <= 0]
    win_rate_pct = round(len(wins) / len(trades) * 100, 1) if trades else 0.0
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t.pnl_pct for t in losses) / len(losses)) if losses else 0.0
    profit_factor = f"1:{round(avg_win / avg_loss, 2)}" if avg_loss > 0 else ("∞" if avg_win > 0 else "—")

    # 按买点类型拆解统计
    by_bsp: dict[str, dict] = {}
    for t in trades:
        slot = by_bsp.setdefault(t.buy_bsp, {"count": 0, "wins": 0, "sum_pct": 0.0})
        slot["count"] += 1
        if t.pnl_amount > 0:
            slot["wins"] += 1
        slot["sum_pct"] += t.pnl_pct
    bsp_breakdown = [
        {
            "bsp": k,
            "count": v["count"],
            "win_rate_pct": round(v["wins"] / v["count"] * 100, 1),
            "avg_pnl_pct": round(v["sum_pct"] / v["count"], 2),
        }
        for k, v in sorted(by_bsp.items())
    ]

    return {
        "code": disp,
        "raw_code": code,
        "name": name or disp,
        "market": mkt,
        "start_date": str(df["date"].iloc[0])[:10],
        "end_date": str(df["date"].iloc[-1])[:10],
        "bars_count": len(df),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "benchmark_return_pct": benchmark_return_pct,
        "alpha_pct": alpha_pct,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "trade_count": len(trades),
        "win_count": len(wins),
        "win_rate_pct": win_rate_pct,
        "profit_loss_ratio": profit_factor,
        "bsp_breakdown": bsp_breakdown,
        "open_position": (
            {"buy_date": pos_buy_date, "buy_price": pos_cost, "buy_bsp": pos_buy_bsp,
             "quantity": pos_qty, "stop_loss": pos_stop,
             "unrealized_pct": round((last_close / pos_cost - 1) * 100, 2)}
            if pos_qty > 0 else None
        ),
        "trades": [t.__dict__ for t in trades],
        "equity_curve": equity_curve[::max(1, len(equity_curve) // 120)],  # 采样 <=120 点供 HTML 曲线渲染
    }
