"""任务服务: B / S / C / L 指令的业务逻辑(按 chat_id 会话隔离, 群聊为群共享)。

互斥规则(同一会话内同一只股票最多 1 个活跃任务):
- 发 S: 自动取消同股 B(历史保留); 已有 S 则更新成本/数量并重置止损
- 已有 S 时发 B: 拒绝并提示先 C
- 重发 B: 提示已存在并返回最新分析
返回值为 str(纯文本)或 {"card": {...}}, 由 bot 层决定如何发送。
"""
from __future__ import annotations

import asyncio
import sqlite3

from app.config import get_config
from app.data import fetcher
from app.engine import holding
from app.logging_utils import get_logger
from app.models import cache_repo, task_repo
from app.services.analysis_service import StockNotFound, analyze_stock

logger = get_logger("services.task")

NOT_FOUND = "未查询到 {code} 的行情，请确认股票代码是否正确。"
TYPE_CN = {"B": "买入跟踪", "S": "持仓跟踪"}


def _params() -> dict:
    return get_config().raw.get("task", {}) or {}


def _scope(ctx: dict) -> tuple[str, str, str | None]:
    return ctx["chat_id"], ctx.get("chat_type") or "p2p", ctx.get("sender_open_id")


async def _live_price(code: str, fallback: float | None) -> float | None:
    try:
        p = await asyncio.to_thread(fetcher.fetch_realtime_price, code)
    except Exception as e:  # noqa: BLE001
        logger.warning("实时价获取失败 %s: %s", code, e)
        p = None
    return p or fallback


async def create_b(ctx: dict, code: str):
    chat_id, chat_type, by = _scope(ctx)
    existing = task_repo.get_active(chat_id, code)
    if existing and existing.task_type == "S":
        return f"⚠️ {existing.display} 已有持仓跟踪(S)任务。如需改为买入跟踪，请先发送 C {code} 取消。"
    try:
        res = await analyze_stock(code)
    except StockNotFound:
        return NOT_FOUND.format(code=code)

    signal = res.daily.get("buy_sell_point") or "无"
    if existing:
        header = "**ℹ️ 该股已在买入跟踪中**（以下为最新分析）"
    else:
        try:
            task_repo.create_task(chat_id, chat_type, by, code, res.name, "B")
        except sqlite3.IntegrityError:   # 并发重复创建
            pass
        header = ("**✅ 已创建买入跟踪**\n"
                  "- 每个交易日 15:05 推送日报；盘中每 30 分钟跟踪，出现买点即推送\n"
                  f"- 当前结论：**{res.direction}**｜日线信号：{signal}")
    return res.to_card(header)


async def create_s(ctx: dict, code: str, buy_price: float | None, quantity: int | None):
    chat_id, chat_type, by = _scope(ctx)
    try:
        res = await analyze_stock(code)
    except StockNotFound:
        return NOT_FOUND.format(code=code)
    price = await _live_price(code, res.price)
    params = _params()
    existing = task_repo.get_active(chat_id, code)

    switched = False
    if existing and existing.task_type == "B":
        task_repo.cancel(existing.id)
        existing, switched = None, True

    if existing:  # 已有 S: 更新成本/数量(未提供则沿用), 并按最新结构重置止损
        cost = buy_price if buy_price is not None else existing.buy_price
        qty = quantity if quantity is not None else existing.buy_quantity
        lv = holding.compute_levels(res.daily, res.ml, price, cost, params)
        task_repo.update_fields(existing.id, buy_price=cost, buy_quantity=qty, stop_loss=lv.stop_loss,
                                target_price=lv.target_price, stop_alert_state=None, stop_alert_at=None)
        title, note = "🔄 已更新持仓跟踪（止损已按最新结构重置）", ""
    else:
        cost, qty = buy_price, quantity
        lv = holding.compute_levels(res.daily, res.ml, price, cost, params)
        try:
            task_repo.create_task(chat_id, chat_type, by, code, res.name, "S", cost, qty,
                                  lv.stop_loss, lv.target_price)
        except sqlite3.IntegrityError:
            pass
        title = "💼 已建立持仓跟踪"
        note = "已自动取消该股的买入跟踪" if switched else ""

    header = holding.holding_header_md(title, price, cost, qty, lv.stop_loss, lv.stop_source, lv.target_price, note)
    header += ("\n- 监控：盘中每 3 分钟比价，触及止损立即提醒；每日 15:05 重算止损（只上移不下移）并推送日报")
    return res.to_card(header)


def cancel(ctx: dict, code: str) -> str:
    chat_id, _, _ = _scope(ctx)
    t = task_repo.cancel_active(chat_id, code)
    if not t:
        return f"当前会话没有 {code} 的活跃任务。"
    return f"✅ 已取消 {t.display} 的{TYPE_CN[t.task_type]}任务。"


def _last_signal(code: str) -> str:
    cached = cache_repo.get(code)
    if not cached:
        return "尚无分析"
    p = cached["payload"]
    bsp = (p.get("daily") or {}).get("buy_sell_point") or "无信号"
    hhmm = cached["analyzed_at"][11:16]
    return f"{(p.get('ml') or {}).get('operation_direction', '观望')}｜{bsp}（{hhmm}）"


async def _quotes(codes: list[str]) -> dict[str, dict]:
    sem = asyncio.Semaphore(3)

    async def one(c: str):
        async with sem:
            try:
                return c, await asyncio.to_thread(fetcher.fetch_quote, c)
            except Exception as e:  # noqa: BLE001
                logger.warning("L 取行情失败 %s: %s", c, e)
                return c, {}

    return dict(await asyncio.gather(*(one(c) for c in codes)))


async def list_tasks(ctx: dict):
    chat_id, chat_type, _ = _scope(ctx)
    tasks = task_repo.list_active(chat_id)
    scope = "本群" if chat_type == "group" else "本会话"
    if not tasks:
        return f"{scope}暂无活跃任务。发送 B 000001 创建买入跟踪，或 S 000001 @成本 数量 创建持仓跟踪。"

    quotes = await _quotes(sorted({t.stock_code for t in tasks}))
    lines: list[str] = []
    for t in tasks:
        q = quotes.get(t.stock_code) or {}
        price, pct = q.get("price"), q.get("change_pct")
        px = f"{price:g}（{pct:+.2f}%）" if price is not None and pct is not None else "—"
        if t.task_type == "B":
            lines.append(f"**{t.display}**｜B 买入跟踪\n- 现价：{px}\n- 最近结论：{_last_signal(t.stock_code)}")
        else:
            row = [f"**{t.display}**｜S 持仓跟踪", f"- 现价：{px}"]
            if t.buy_price:
                d = holding.pnl(t.buy_price, t.buy_quantity, price) if price else {}
                pnl_txt = f"｜浮盈 {d['pnl_pct']:+.2f}%" if d.get("pnl_pct") is not None else ""
                qty_txt = f" × {t.buy_quantity}" if t.buy_quantity else ""
                row.append(f"- 成本：{t.buy_price:g}{qty_txt}{pnl_txt}")
            if t.stop_loss and price:
                row.append(f"- 止损：{t.stop_loss:g}（距现价 {holding.dist_to_stop_pct(price, t.stop_loss):+.2f}%）"
                           f"｜目标：{t.target_price:g}" if t.target_price else f"- 止损：{t.stop_loss:g}")
            row.append(f"- 最近结论：{_last_signal(t.stock_code)}")
            lines.append("\n".join(row))
    return {"card": {"title": f"📋 任务列表（{scope}，共 {len(tasks)} 个）",
                     "markdown": "\n\n".join(lines), "template": "blue"}}
