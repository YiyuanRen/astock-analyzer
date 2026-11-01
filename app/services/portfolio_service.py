"""模拟交易实操账本服务 (X 卖出平仓/减仓 · P 持仓与总收益总览 · T 交易流水 · /portfolio HTML 看板)。"""
from __future__ import annotations

import asyncio
import html

from app.bot.card_builder import (
    banner_box,
    fmt_amt,
    fmt_pct,
    grey_row,
    kpi_col,
    kpi_row,
    pct_theme,
    schema2_card,
)
from app.config import get_config
from app.data import fetcher
from app.data.market import currency_of, display_code
from app.logging_utils import get_logger
from app.models import cache_repo, task_repo, trade_repo
from app.models.trade_repo import TradeError

logger = get_logger("services.portfolio")


async def _get_live_price(code: str) -> float | None:
    try:
        p = await asyncio.to_thread(fetcher.fetch_realtime_price, code)
        if p:
            return p
    except Exception as e:  # noqa: BLE001
        logger.warning("获取实时价失败 %s: %s", code, e)
    cached = cache_repo.get(code)
    if cached:
        return ((cached.get("payload") or {}).get("quote") or {}).get("price")
    return None


async def execute_sell_x(
    ctx: dict,
    code: str,
    sell_price: float | None = None,
    quantity: int | None = None,
) -> dict | str:
    """执行 X <代码> [@卖出价] [股数] 模拟卖出(支持部分减仓与全部清仓, 严格校验 A股 T+1)。"""
    chat_id = ctx.get("chat_id") or "cli_chat"
    pos = trade_repo.get_open_position(chat_id, code)
    if not pos:
        return f"⚠️ 当前会话没有 {display_code(code)} 的持仓记录，无法卖出（发送 P 查看当前持仓）。"

    actual_price = sell_price
    if actual_price is None:
        actual_price = await _get_live_price(code)
    if not actual_price or actual_price <= 0:
        return f"⚠️ 未能获取 {display_code(code)} 的实时价格，请使用 `X {display_code(code)} @卖出价` 指定价格。"

    cached = cache_repo.get(code)
    bsp = (((cached or {}).get("payload") or {}).get("daily") or {}).get("buy_sell_point") or "手动卖出"

    try:
        res = trade_repo.record_sell(chat_id, code, actual_price, quantity, chan_bsp=bsp)
    except TradeError as e:
        return str(e)

    # 同步更新或关闭 tasks 表中的 S 持仓监控任务
    s_task = task_repo.get_active(chat_id, code)
    if s_task and s_task.task_type == "S":
        if res["closed"]:
            task_repo.cancel(s_task.id)
        else:
            task_repo.update_fields(s_task.id, buy_quantity=res["remain_qty"])

    disp = display_code(code)
    name = res["stock_name"] or disp
    cur = currency_of(code)
    act_cn = "全部平仓(清仓)" if res["closed"] else f"部分减仓（剩余 {res['remain_qty']} 股继续监控）"
    title = f"📤 模拟卖出交割 · {name} {disp}"
    pnl_rmb = res["realized_pnl_rmb"]
    pnl_pct = res["pnl_pct"]
    _, p_bg = pct_theme(pnl_rmb)
    tpl = "red" if pnl_rmb > 0 else ("green" if pnl_rmb < 0 else "blue")
    subtitle = f"{act_cn} · 成交 {actual_price:g} {cur} × {res['sell_qty']} 股 · 本笔盈亏 {pnl_rmb:+,.2f} 元 ({pnl_pct:+.2f}%)"

    cols = [
        kpi_col(f"📤 卖出价 × 股数 ({cur})",
                f"**<font color='blue'>{actual_price:g} × {res['sell_qty']} 股</font>**",
                f"<font color='grey'>持仓均价 {res['avg_cost']:g} · 回笼 {res['amount_rmb']:,.2f} 元</font>",
                bg="blue-50"),
        kpi_col("💰 本笔实现盈亏 (RMB)",
                f"{fmt_amt(pnl_rmb)} 元（{fmt_pct(pnl_pct)}）",
                f"<font color='grey'>该股累计已实现 {res['total_position_realized_rmb']:+,.2f} 元</font>",
                bg=p_bg),
        kpi_col("🏦 交易后账户现金",
                f"**<font color='violet'>{res['cash_balance']:,.2f} 元</font>**",
                f"<font color='grey'>剩余持仓 {res['remain_qty']} 股（冻结 {res['frozen_qty']} 股）</font>",
                bg="violet-50"),
    ]
    note_md = (
        f"- **交割状态**：**{act_cn}**"
        + ("，已自动关闭该股的 S 盘中止损监控。" if res["closed"] else f"，剩余 **{res['remain_qty']}** 股将继续执行盘中止损与收盘日报监控。")
        + "\n- 发送 `P` 查看账户总资产与胜率，发送 `T` 查看交易流水明细。"
    )
    disclaimer = get_config().risk_disclaimer
    elements = [
        kpi_row(cols, flex_mode="trisect"),
        {"tag": "markdown", "content": note_md},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"<font color='grey'>模拟实操交割记录 · {disclaimer}</font>"},
    ]
    flat = f"**{title}**\n{subtitle}\n{note_md}\n{disclaimer}"
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": tpl,
            "markdown": flat,
            "raw_card": schema2_card(title, subtitle, tpl, elements),
        }
    }


async def get_portfolio_summary(chat_id: str) -> dict:
    """计算指定会话(或全局)的 10 万本金实操账户总览数据。"""
    acct = trade_repo.get_or_create_account(chat_id)
    open_pos = trade_repo.list_open_positions(chat_id)
    closed_pos = trade_repo.list_closed_positions(chat_id, limit=100)

    # 并发获取当前持仓实时价格
    codes = sorted({p.stock_code for p in open_pos})
    sem = asyncio.Semaphore(4)

    async def _one_q(c: str):
        async with sem:
            try:
                return c, await asyncio.to_thread(fetcher.fetch_quote, c)
            except Exception:
                return c, {}

    quotes = dict(await asyncio.gather(*(_one_q(c) for c in codes))) if codes else {}

    open_items = []
    total_mv_rmb = 0.0
    total_unrealized_rmb = 0.0
    for p in open_pos:
        q = quotes.get(p.stock_code) or {}
        cur_px = q.get("price") or p.avg_cost
        fx = trade_repo.fx_rate(p.market)
        mv_rmb = round(cur_px * p.current_qty * fx, 2)
        unr_rmb = round((cur_px - p.avg_cost) * p.current_qty * fx, 2)
        unr_pct = round((cur_px / p.avg_cost - 1) * 100, 2) if p.avg_cost > 0 else 0.0
        can_sell, frozen = trade_repo.get_sellable_qty(p)
        s_task = task_repo.get_active(chat_id, p.stock_code)
        total_mv_rmb += mv_rmb
        total_unrealized_rmb += unr_rmb
        open_items.append({
            "position": p,
            "code": display_code(p.stock_code),
            "name": p.stock_name or q.get("name") or display_code(p.stock_code),
            "market": p.market,
            "avg_cost": p.avg_cost,
            "current_price": cur_px,
            "change_pct": q.get("change_pct"),
            "current_qty": p.current_qty,
            "sellable_qty": can_sell,
            "frozen_qty": frozen,
            "mv_rmb": mv_rmb,
            "unrealized_pnl_rmb": unr_rmb,
            "unrealized_pct": unr_pct,
            "realized_pnl_rmb": p.realized_pnl_rmb,
            "stop_loss": s_task.stop_loss if s_task else None,
            "target_price": s_task.target_price if s_task else None,
        })

    realized_total_rmb = round(
        sum(p.realized_pnl_rmb for p in open_pos) + sum(p.realized_pnl_rmb for p in closed_pos), 2
    )
    total_equity_rmb = round(acct.cash_balance + total_mv_rmb, 2)
    total_pnl_rmb = round(total_equity_rmb - acct.initial_capital, 2)
    total_return_pct = round(total_pnl_rmb / acct.initial_capital * 100, 2)

    wins = [p for p in closed_pos if p.realized_pnl_rmb > 0]
    win_rate = round(len(wins) / len(closed_pos) * 100, 1) if closed_pos else None

    return {
        "chat_id": chat_id,
        "initial_capital": acct.initial_capital,
        "cash_balance": round(acct.cash_balance, 2),
        "market_value_rmb": round(total_mv_rmb, 2),
        "total_equity_rmb": total_equity_rmb,
        "total_pnl_rmb": total_pnl_rmb,
        "total_return_pct": total_return_pct,
        "realized_pnl_rmb": realized_total_rmb,
        "unrealized_pnl_rmb": round(total_unrealized_rmb, 2),
        "closed_count": len(closed_pos),
        "win_count": len(wins),
        "win_rate_pct": win_rate,
        "open_positions": open_items,
        "closed_positions": closed_pos[:15],
    }


async def handle_portfolio_p(ctx: dict) -> dict:
    """P 指令: 生成飞书 Schema 2.0 账户实操总览卡片。"""
    from app.services.briefing_service import public_base_url

    chat_id = ctx.get("chat_id") or "cli_chat"
    summary = await get_portfolio_summary(chat_id)
    init_cap = summary["initial_capital"]
    equity = summary["total_equity_rmb"]
    cash = summary["cash_balance"]
    mv = summary["market_value_rmb"]
    tot_pnl = summary["total_pnl_rmb"]
    tot_ret = summary["total_return_pct"]
    win_rate = summary["win_rate_pct"]
    win_str = f"{win_rate:.1f}% ({summary['win_count']}/{summary['closed_count']})" if win_rate is not None else "暂无已平仓记录"

    _, p_bg = pct_theme(tot_pnl)
    tpl = "red" if tot_pnl > 0 else ("green" if tot_pnl < 0 else "blue")
    title = "🏦 模拟实操账户总览（10万RMB本金）"
    subtitle = f"总资产 {equity:,.2f} 元 ({tot_ret:+.2f}%) · 可用现金 {cash:,.2f} 元 · 持仓 {len(summary['open_positions'])} 只"

    cols = [
        kpi_col("🏦 账户总资产 (RMB)",
                f"**<font color='blue'>{equity:,.2f}</font>**",
                f"<font color='grey'>初始本金 {init_cap:,.0f} · 仓位 {mv / equity * 100 if equity else 0:.1f}%</font>",
                bg="blue-50"),
        kpi_col("💰 累计总盈亏",
                f"{fmt_amt(tot_pnl)}（{fmt_pct(tot_ret)}）",
                f"<font color='grey'>已实现 {summary['realized_pnl_rmb']:+,.2f} · 浮动 {summary['unrealized_pnl_rmb']:+,.2f}</font>",
                bg=p_bg),
        kpi_col("💵 可用现金 & 实操胜率",
                f"**<font color='violet'>{cash:,.2f} 元</font>**",
                f"<font color='grey'>平仓胜率：{win_str}</font>",
                bg="violet-50"),
    ]
    elements: list[dict] = [kpi_row(cols, flex_mode="trisect")]
    flat_lines = [f"**{title}**\n{subtitle}"]

    open_items = summary["open_positions"]
    if open_items:
        elements.append({"tag": "markdown", "content": "#### 📌 当前持仓明细（A股实行 T+1 · 港股 T+0）"})
        elements.append(grey_row([
            ("**标的**", 2),
            ("**持仓 / 可卖**", 2),
            ("**成本 → 现价**", 2),
            ("**浮动盈亏(RMB)**", 2),
            ("**止损 / 目标**", 2),
        ]))
        elements.append({"tag": "hr"})
        for it in open_items:
            t1_note = f" (🔒今买{it['frozen_qty']})" if it["frozen_qty"] > 0 else ""
            qty_cell = f"**{it['current_qty']}** 股\n<font color='grey'>可卖 {it['sellable_qty']}{t1_note}</font>"
            px_cell = f"{it['avg_cost']:g} → **{it['current_price']:g}**\n{fmt_pct(it.get('change_pct'), bold=False)}"
            pnl_cell = f"{fmt_amt(it['unrealized_pnl_rmb'])}\n{fmt_pct(it['unrealized_pct'])}"
            sl_cell = f"🛑 {it['stop_loss'] or '—'}\n🎯 {it['target_price'] or '—'}"
            elements.append(grey_row([
                (f"**{it['name']}**\n<font color='grey'>{it['code']}</font>", 2),
                (qty_cell, 2),
                (px_cell, 2),
                (pnl_cell, 2),
                (sl_cell, 2),
            ]))
            flat_lines.append(
                f"- **{it['name']} ({it['code']})**：持仓 {it['current_qty']} 股（可卖 {it['sellable_qty']} 股），"
                f"成本 {it['avg_cost']:g} → 现价 {it['current_price']:g}，浮盈 {it['unrealized_pnl_rmb']:+,.2f} 元 ({it['unrealized_pct']:+.2f}%)"
            )
    else:
        elements.append(banner_box("当前暂无持仓，可用现金 **100%**。发送 `S 代码 [@买入价] [股数]` 即可模拟建仓。", bg="grey-50"))
        flat_lines.append("当前暂无持仓。")

    web_url = f"{public_base_url()}/portfolio"
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": (
            f"📊 **网页版实操看板**：[点击打开《账户持仓与历史交割全景看板》]({web_url})\n"
            f"<font color='grey'>指令提示：S 代码 [@价] [量] 买入/加仓 · X 代码 [@价] [量] 卖出减仓/平仓 · T 查看交易流水 · {get_config().risk_disclaimer}</font>"
        ),
    })
    raw_card = schema2_card(title, subtitle, tpl, elements)
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": tpl,
            "markdown": "\n".join(flat_lines),
            "raw_card": raw_card,
        }
    }


def handle_trades_t(ctx: dict, stock_code: str | None = None) -> dict | str:
    """T [代码] 指令: 查看最近模拟交易交割流水卡片。"""
    chat_id = ctx.get("chat_id") or "cli_chat"
    logs = trade_repo.list_trade_logs(chat_id, stock_code=stock_code, limit=20)
    if not logs:
        suffix = f" {display_code(stock_code)}" if stock_code else ""
        return f"当前会话暂无{suffix}模拟交易流水记录。发送 `S 代码 @价格 股数` 建仓或 `X 代码` 卖出后将自动记录。"

    act_map = {
        "BUY": "🔵 建仓买入",
        "ADD": "🟣 加仓买入",
        "SELL_PART": "🟠 部分减仓",
        "SELL_ALL": "🟢 全部平仓",
    }
    title_suffix = f" · {display_code(stock_code)}" if stock_code else ""
    title = f"📜 模拟交易流水{title_suffix}（最近 {len(logs)} 笔）"
    subtitle = "包含建仓、加仓、部分减仓与全部平仓的完整交割记录"

    elements: list[dict] = [
        grey_row([
            ("**时间 / 标的**", 2),
            ("**动作**", 1),
            ("**成交价 × 股数**", 2),
            ("**发生金额(RMB)**", 2),
            ("**实现盈亏 / 信号**", 2),
        ]),
        {"tag": "hr"},
    ]
    flat_lines = [f"**{title}**"]

    for lg in logs:
        disp = display_code(lg.stock_code)
        t_short = lg.traded_at[:16].replace("T", " ")
        act_label = act_map.get(lg.action, lg.action)
        if lg.realized_pnl_rmb is not None:
            pnl_cell = f"{fmt_amt(lg.realized_pnl_rmb)}\n{fmt_pct(lg.pnl_pct, bold=False)}"
        else:
            pnl_cell = f"<font color='grey'>{lg.chan_bsp or '建仓'}</font>"
        elements.append(grey_row([
            (f"**{lg.stock_name or disp}** ({disp})\n<font color='grey'>{t_short}</font>", 2),
            (f"**{act_label}**", 1),
            (f"**{lg.price:g}** × {lg.quantity}股", 2),
            (f"{lg.amount_rmb:,.2f} 元", 2),
            (pnl_cell, 2),
        ]))
        flat_lines.append(
            f"- {t_short} {act_label} **{lg.stock_name or disp} ({disp})** {lg.price:g} × {lg.quantity}股（{lg.amount_rmb:,.2f}元）"
        )

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": f"<font color='grey'>发送 P 查看账户持仓与总收益 · {get_config().risk_disclaimer}</font>",
    })
    raw_card = schema2_card(title, subtitle, "blue", elements)
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": "blue",
            "markdown": "\n".join(flat_lines),
            "raw_card": raw_card,
        }
    }


def render_portfolio_html(summary: dict, logs: list) -> str:
    """渲染 /portfolio 网页端响应式实操账本看板。"""
    def _e(v) -> str:
        return html.escape(str(v if v is not None else ""))

    pos_rows = []
    for it in summary.get("open_positions") or []:
        cls = "pos" if it["unrealized_pnl_rmb"] >= 0 else "neg"
        pos_rows.append(
            f"<tr>"
            f"<td><strong>{_e(it['name'])}</strong> ({_e(it['code'])})</td>"
            f"<td>{it['current_qty']} 股 (可卖 {it['sellable_qty']} / 今买冻结 {it['frozen_qty']})</td>"
            f"<td>{it['avg_cost']:g} → <strong>{it['current_price']:g}</strong></td>"
            f"<td>{it['mv_rmb']:,.2f}</td>"
            f"<td class='{cls}'><strong>{it['unrealized_pnl_rmb']:+,.2f}</strong> ({it['unrealized_pct']:+.2f}%)</td>"
            f"<td>止损 {it['stop_loss'] or '—'} / 目标 {it['target_price'] or '—'}</td>"
            f"</tr>"
        )
    pos_html = "".join(pos_rows) or "<tr><td colspan='6' style='color:#64748b;'>当前暂无持仓</td></tr>"

    log_rows = []
    for lg in logs:
        pnl_txt = f"{lg.realized_pnl_rmb:+,.2f} ({lg.pnl_pct:+.2f}%)" if lg.realized_pnl_rmb is not None else "—"
        log_rows.append(
            f"<tr>"
            f"<td>{_e(lg.traded_at[:19].replace('T', ' '))}</td>"
            f"<td><strong>{_e(lg.stock_name or lg.stock_code)}</strong> ({_e(display_code(lg.stock_code))})</td>"
            f"<td>{_e(lg.action)}</td>"
            f"<td>{lg.price:g} × {lg.quantity}</td>"
            f"<td>{lg.amount_rmb:,.2f}</td>"
            f"<td>{pnl_txt}</td>"
            f"</tr>"
        )
    log_html = "".join(log_rows) or "<tr><td colspan='6' style='color:#64748b;'>暂无交易流水</td></tr>"

    tot_cls = "pos" if summary["total_pnl_rmb"] >= 0 else "neg"
    win_txt = f"{summary['win_rate_pct']:.1f}%" if summary.get("win_rate_pct") is not None else "—"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>模拟实操账户看板 · 10万本金</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background:#f4f6fb; color:#1e293b; margin:0; padding:20px 12px 48px; }}
    .wrap {{ max-width: 1080px; margin: 0 auto; }}
    .hero {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color:#fff; border-radius:16px; padding:22px; margin-bottom:20px; }}
    .kpis {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-top:14px; }}
    .kpi {{ background:rgba(255,255,255,0.1); border-radius:10px; padding:12px 14px; }}
    .kpi-label {{ font-size:12.5px; color:#cbd5e1; }}
    .kpi-val {{ font-size:20px; font-weight:700; margin-top:4px; }}
    .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:18px; margin-bottom:18px; overflow-x:auto; }}
    h2 {{ font-size:17px; margin:0 0 12px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
    th, td {{ padding:10px 8px; border-bottom:1px solid #f1f5f9; text-align:left; }}
    th {{ background:#f8fafc; color:#475569; }}
    .pos {{ color:#dc2626; }}
    .neg {{ color:#16a34a; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;">
        <h1 style="font-size:21px;margin:0;">🏦 缠论模拟实操账户看板（初始本金 100,000 RMB）</h1>
        <a href="/briefings" style="color:#bfdbfe;font-size:13px;">🗂️ 早报归档 →</a>
      </div>
      <div class="kpis">
        <div class="kpi"><div class="kpi-label">账户总资产 (RMB)</div><div class="kpi-val">{summary['total_equity_rmb']:,.2f}</div></div>
        <div class="kpi"><div class="kpi-label">累计总盈亏 / 收益率</div><div class="kpi-val">{summary['total_pnl_rmb']:+,.2f} ({summary['total_return_pct']:+.2f}%)</div></div>
        <div class="kpi"><div class="kpi-label">可用现金 / 持仓市值</div><div class="kpi-val">{summary['cash_balance']:,.2f} / {summary['market_value_rmb']:,.2f}</div></div>
        <div class="kpi"><div class="kpi-label">已平仓胜率</div><div class="kpi-val">{win_txt} ({summary['win_count']}/{summary['closed_count']})</div></div>
      </div>
    </div>
    <div class="card">
      <h2>📌 当前持仓明细（A股 T+1 / 港股 T+0）</h2>
      <table>
        <thead><tr><th>标的</th><th>持仓 / 可卖</th><th>成本 → 现价</th><th>市值(RMB)</th><th>浮动盈亏</th><th>止损 / 目标</th></tr></thead>
        <tbody>{pos_html}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>📜 最近交易交割流水</h2>
      <table>
        <thead><tr><th>时间</th><th>标的</th><th>动作</th><th>成交价 × 股数</th><th>金额(RMB)</th><th>实现盈亏</th></tr></thead>
        <tbody>{log_html}</tbody>
      </table>
    </div>
  </div>
</body>
</html>"""
