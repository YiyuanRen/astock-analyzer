"""定时任务: 15:05 日报 / B 盘中跟踪 / S 盘中止损监控, 以及调度器构建。

推送目标一律是任务所属会话 chat_id(群里创建推群, 私聊创建推私聊)。
所有 job 函数都接受 current(便于测试注入时间)与 force(忽略交易时段, 供手动触发)。

去重与节奏:
- B 盘中: 同一买点信号(类型|所在笔|是否30分钟共振)只推一次; 信号键在 15:05 日报时按收盘结果刷新
- S 止损: 当日首次触及立即推; 仍低于止损位则每 stop_alert_repeat_min 分钟重复提醒; 回升后重置
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_config
from app.data import fetcher
from app.data import trading_calendar as cal
from app.engine import holding
from app.logging_utils import get_logger
from app.models import task_repo
from app.models.database import TZ, now, now_str
from app.services.analysis_service import AnalysisResult, StockNotFound, analyze_stock

logger = get_logger("scheduler.jobs")


# ---------------- 公共 ----------------
def _params() -> dict:
    return get_config().raw.get("task", {}) or {}


def _norm(current: datetime | None) -> datetime:
    current = current or now()
    return current if current.tzinfo else current.replace(tzinfo=TZ)


def _group_by_code(tasks) -> dict[str, list]:
    g: dict[str, list] = defaultdict(list)
    for t in tasks:
        g[t.stock_code].append(t)
    return g


def _push(notifier, chat_id: str, title: str, markdown: str, template: str) -> bool:
    try:
        ok = notifier.send_card(chat_id, title, markdown, template)
    except Exception:  # noqa: BLE001
        logger.exception("推送异常 chat=%s title=%s", chat_id, title)
        return False
    if not ok:
        logger.error("推送失败 chat=%s title=%s", chat_id, title)
    return bool(ok)


def buy_signal_key(res: AnalysisResult) -> str | None:
    """日线当前买点的信号键; 无买点返回 None。包含是否已有 30 分钟共振, 共振确认时会再推一次。"""
    bsp = res.daily.get("buy_sell_point")
    latest = res.daily.get("latest_bsp") or {}
    if bsp and bsp != "无信号" and latest.get("is_buy"):
        state = "confirmed" if res.direction == "买入" else "pending"
        return f"{bsp}|{latest.get('bi_idx')}|{state}"
    return None


def _sell_signal(res: AnalysisResult) -> str | None:
    bsp = res.daily.get("buy_sell_point")
    latest = res.daily.get("latest_bsp") or {}
    if bsp and bsp != "无信号" and latest.get("is_buy") is False:
        return bsp
    return None


# ---------------- 15:05 日报 (B + S) ----------------
async def daily_analysis(notifier, *, current: datetime | None = None, force: bool = False) -> dict:
    current = _norm(current)
    stats = {"stocks": 0, "pushed": 0, "failed": 0, "skipped": None}
    if not force and not cal.is_trading_day(current.date(), current):
        stats["skipped"] = "非交易日"
        return stats

    for code, group in _group_by_code(task_repo.list_active()).items():
        stats["stocks"] += 1
        try:
            res = await analyze_stock(code, force=True, fresh_within_sec=300, current=current)
        except StockNotFound:
            logger.warning("日报: %s 查不到行情, 跳过", code)
            stats["failed"] += len(group)
            continue
        except Exception:  # noqa: BLE001  单只失败不影响其他
            logger.exception("日报: 分析 %s 失败", code)
            stats["failed"] += len(group)
            continue

        for t in group:
            try:
                if t.task_type == "B":
                    header, template = _daily_b(t, res)
                else:
                    header, template = _daily_s(t, res)
                card = res.to_card(header, template, show_cache_note=False)["card"]
                ok = _push(notifier, t.chat_id, card["title"], card["markdown"], card["template"])
            except Exception:  # noqa: BLE001
                logger.exception("日报: 处理任务 %s/%s 失败", t.chat_id, code)
                ok = False
            stats["pushed" if ok else "failed"] += 1
    logger.info("日报完成: %s", stats)
    return stats


def _daily_b(t, res: AnalysisResult) -> tuple[str, str]:
    sig = res.daily.get("buy_sell_point") or "无信号"
    # 收盘后以日线确认结果刷新信号键: 明日盘中不会重复推同一个已在日报里出现的信号
    task_repo.update_fields(t.id, last_signal_key=buy_signal_key(res), last_signal_at=now_str())
    header = f"**📅 收盘日报 · 买入跟踪**\n- 结论：**{res.direction}**｜日线信号：{sig}"
    return header, res.payload["template"]


def _daily_s(t, res: AnalysisResult) -> tuple[str, str]:
    price = res.price
    lv = holding.compute_levels(res.daily, res.ml, price, t.buy_price, _params())
    stop, raised = holding.ratchet(t.stop_loss, lv.stop_loss)
    source = lv.stop_source if stop == lv.stop_loss else "沿用原止损位(只上移不下移)"
    task_repo.update_fields(t.id, stop_loss=stop, target_price=lv.target_price,
                            stop_alert_state=None, stop_alert_at=None)
    notes = []
    if raised:
        notes.append(f"🔒 止损上移：{t.stop_loss:g} → {stop:g}")
    sell = _sell_signal(res)
    if sell:
        notes.append(f"⚠️ 日线出现**{sell}**，请关注减仓")
    header = holding.holding_header_md("📅 收盘日报 · 持仓跟踪", price, t.buy_price, t.buy_quantity,
                                       stop, source, lv.target_price, "\n- ".join(notes))
    return header, ("green" if sell else res.payload["template"])


# ---------------- B 盘中跟踪 ----------------
async def intraday_b(notifier, *, current: datetime | None = None, force: bool = False) -> dict:
    current = _norm(current)
    stats = {"stocks": 0, "pushed": 0, "deduped": 0, "skipped": None}
    if not force and not (cal.is_trading_day(current.date(), current) and cal.in_session_hours(current)):
        stats["skipped"] = "非交易时段"
        return stats

    for code, group in _group_by_code(task_repo.list_active(task_type="B")).items():
        stats["stocks"] += 1
        try:
            res = await analyze_stock(code, force=True, fresh_within_sec=120, current=current)
        except Exception:  # noqa: BLE001
            logger.exception("盘中: 分析 %s 失败", code)
            continue
        key = buy_signal_key(res)
        if key is None:
            continue  # 无买点: 静默
        confirmed = key.endswith("confirmed")
        for t in group:
            if t.last_signal_key == key:
                stats["deduped"] += 1
                continue
            bsp = res.daily.get("buy_sell_point")
            header = (f"**⏱ 盘中买点信号（{current.strftime('%H:%M')}）**\n"
                      f"- 日线：**{bsp}**｜30分钟共振：{'✅ 已确认' if confirmed else '⏳ 尚未确认，继续观察'}\n"
                      "- ⚠️ 盘中日线尚未收盘，信号可能变化，以 15:05 日线确认为准")
            card = res.to_card(header, "red" if confirmed else "orange", show_cache_note=False)["card"]
            if _push(notifier, t.chat_id, card["title"], card["markdown"], card["template"]):
                task_repo.update_fields(t.id, last_signal_key=key, last_signal_at=now_str())
                stats["pushed"] += 1
    logger.info("B 盘中跟踪完成: %s", stats)
    return stats


# ---------------- S 止损监控 ----------------
def stop_loss_check(notifier, *, current: datetime | None = None, force: bool = False,
                    prices: dict[str, float] | None = None) -> dict:
    """prices: 测试/手动触发时注入 {code: price}, 未注入的股票走实时行情。"""
    current = _norm(current)
    stats = {"checked": 0, "alerted": 0, "reminded": 0, "recovered": 0, "skipped": None}
    if not force and not cal.is_market_open(current):
        stats["skipped"] = "非交易时段"
        return stats

    repeat_sec = float(get_config().scheduler.get("stop_alert_repeat_min", 30)) * 60
    price_cache: dict[str, float | None] = dict(prices or {})

    for t in task_repo.list_active(task_type="S"):
        if not t.stop_loss:
            continue
        if t.stock_code not in price_cache:
            try:
                price_cache[t.stock_code] = fetcher.fetch_realtime_price(t.stock_code)
            except Exception as e:  # noqa: BLE001
                logger.warning("止损监控: 取价失败 %s: %s", t.stock_code, e)
                price_cache[t.stock_code] = None
        price = price_cache[t.stock_code]
        if price is None:
            continue
        stats["checked"] += 1

        if price > t.stop_loss:
            if t.stop_alert_state == "alerted":      # 回升: 重置
                task_repo.update_fields(t.id, stop_alert_state=None, stop_alert_at=None)
                stats["recovered"] += 1
            continue

        last = datetime.fromisoformat(t.stop_alert_at) if t.stop_alert_at else None
        first_today = t.stop_alert_state != "alerted" or last is None or last.date() != current.date()
        due = (not first_today) and (current - last).total_seconds() >= repeat_sec
        if not (first_today or due):
            continue

        title = f"🚨 止损触发 {t.display}" if first_today else f"⏰ 止损提醒（持续）{t.display}"
        if _push(notifier, t.chat_id, title, _stop_alert_md(t, price, first_today), "orange"):
            task_repo.update_fields(t.id, stop_alert_state="alerted", stop_alert_at=current.isoformat(timespec="seconds"))
            stats["alerted" if first_today else "reminded"] += 1
    if stats["alerted"] or stats["reminded"] or stats["recovered"]:
        logger.info("止损监控: %s", stats)
    return stats


def _stop_alert_md(t, price: float, first: bool) -> str:
    dist = holding.dist_to_stop_pct(price, t.stop_loss)
    lines = [f"**现价 {price:g} ≤ 止损位 {t.stop_loss:g}**（低于止损位 {abs(dist):.2f}%）"
             if first else f"**仍低于止损位**：现价 {price:g}｜止损位 {t.stop_loss:g}（{abs(dist):.2f}%）"]
    if t.buy_price:
        d = holding.pnl(t.buy_price, t.buy_quantity, price)
        row = f"- 成本：{t.buy_price:g}" + (f" × {t.buy_quantity} 股" if t.buy_quantity else "") + f"｜浮盈：**{d['pnl_pct']:+.2f}%**"
        if d["pnl_amount"] is not None:
            row += f"（{d['pnl_amount']:+,.2f}）"
        lines.append(row)
    if t.target_price:
        lines.append(f"- 目标位：{t.target_price:g}")
    lines.append("- 这是价格提醒，不是操作指令；请结合自身情况决策。")
    lines.append(f"\n{get_config().risk_disclaimer}")
    return "\n".join(lines)


# ---------------- 调度器 ----------------
def _runner(name: str, fn):
    def run():
        try:
            logger.info("任务 %s 开始", name)
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("任务 %s 异常", name)
    return run


def build_scheduler(notifier) -> BackgroundScheduler:
    cfg = get_config().scheduler
    sched = BackgroundScheduler(
        timezone=TZ,
        job_defaults={"coalesce": True, "max_instances": 1,
                      "misfire_grace_time": int(cfg.get("misfire_grace_sec", 1800))})

    da = cfg.get("daily_analysis", {"hour": 15, "minute": 5})
    sched.add_job(_runner("daily_analysis", lambda: asyncio.run(daily_analysis(notifier))),
                  CronTrigger(day_of_week="mon-fri", hour=da["hour"], minute=da["minute"], timezone=TZ),
                  id="daily_analysis", name="15:05 日报")

    for hhmm in cfg.get("intraday_b_times", []):
        h, m = hhmm.split(":")
        sched.add_job(_runner(f"intraday_b@{hhmm}", lambda: asyncio.run(intraday_b(notifier))),
                      CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m), timezone=TZ),
                      id=f"intraday_b_{hhmm}", name=f"B盘中跟踪 {hhmm}")

    every = int(cfg.get("stop_loss_interval_min", 3))
    sched.add_job(_runner("stop_loss", lambda: stop_loss_check(notifier)),
                  CronTrigger(day_of_week="mon-fri", hour="9-11,13-15", minute=f"*/{every}", timezone=TZ),
                  id="stop_loss", name="S止损监控", misfire_grace_time=120)
    return sched
