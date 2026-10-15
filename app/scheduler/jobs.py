"""定时任务: 日报(A股 15:05 / 港股 16:10) / B 盘中跟踪 / S 盘中止损监控, 以及调度器构建。

推送目标一律是任务所属会话 chat_id(群里创建推群, 私聊创建推私聊)。
所有 job 函数都接受 current(便于测试注入时间)与 force(忽略交易时段, 供手动触发)。

去重与节奏:
- B 盘中: 同一买点信号(类型|所在笔|是否30分钟共振)只推一次; 信号键在收盘日报时按收盘结果刷新
- S 止损: 当日首次触及立即推; 仍低于止损位则每 stop_alert_repeat_min 分钟重复提醒; 回升后重置
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.bot.card_builder import build_stop_alert_card
from app.config import get_config
from app.data import fetcher
from app.data import trading_calendar as cal
from app.data.market import daily_report_time_str, decimals_of, market_of
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


def _push(notifier, chat_id: str, title: str, markdown: str, template: str,
          raw_card: dict | None = None) -> bool:
    try:
        ok = notifier.send_card(chat_id, title, markdown, template, raw_card=raw_card)
    except TypeError:
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


def _in_intraday_b_window(current: datetime, market: str) -> bool:
    """判断 current 是否处于指定市场(CN/HK)的 B 任务盘中检查有效时段。

    30m K线收盘后 1 分钟检查:
    - CN: 09:30~11:31, 13:00~15:00 (排除 11:32~12:59 午休与 15:00 后)
    - HK: 09:30~12:01, 13:00~16:00 (排除 12:02~12:59 午休与 16:00 后)
    """
    if not cal.is_trading_day(current.date(), current, market=market):
        return False
    t = current.time()
    if market == "HK":
        return (time(9, 30) <= t <= time(12, 1)) or (time(13, 0) <= t <= time(16, 0))
    return (time(9, 30) <= t <= time(11, 31)) or (time(13, 0) <= t <= time(15, 0))


# ---------------- 收盘日报 (B + S, 支持按 market 分流) ----------------
async def daily_analysis(notifier, *, current: datetime | None = None, force: bool = False,
                         market: str | None = None) -> dict:
    current = _norm(current)
    stats = {"stocks": 0, "pushed": 0, "failed": 0, "skipped": None}
    all_tasks = task_repo.list_active()
    if market:
        mkt = market.upper()
        if not force and not cal.is_trading_day(current.date(), current, market=mkt):
            stats["skipped"] = "非交易日"
            return stats
        tasks = [t for t in all_tasks if market_of(t.stock_code) == mkt]
    else:
        if not force:
            tasks = [t for t in all_tasks
                     if cal.is_trading_day(current.date(), current, market=market_of(t.stock_code))]
            if not tasks and (not all_tasks and not cal.is_trading_day(current.date(), current, market="CN")
                              or all_tasks):
                stats["skipped"] = "非交易日"
                return stats
        else:
            tasks = all_tasks

    for code, group in _group_by_code(tasks).items():
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
                    card = res.to_card(header, template, show_cache_note=False)["card"]
                else:
                    card = _daily_s_card(t, res)["card"]
                ok = _push(notifier, t.chat_id, card["title"], card["markdown"],
                           card["template"], raw_card=card.get("raw_card"))
            except Exception:  # noqa: BLE001
                logger.exception("日报: 处理任务 %s/%s 失败", t.chat_id, code)
                ok = False
            stats["pushed" if ok else "failed"] += 1
    logger.info("日报完成(market=%s): %s", market or "ALL", stats)
    return stats


def _daily_b(t, res: AnalysisResult) -> tuple[str, str]:
    sig = res.daily.get("buy_sell_point") or "无信号"
    # 收盘后以日线确认结果刷新信号键: 明日盘中不会重复推同一个已在日报里出现的信号
    task_repo.update_fields(t.id, last_signal_key=buy_signal_key(res), last_signal_at=now_str())
    header = f"**📅 收盘日报 · 买入跟踪**\n- 结论：**{res.direction}**｜日线信号：{sig}"
    return header, res.payload["template"]


def _daily_s_card(t, res: AnalysisResult) -> dict:
    price = res.price
    dec = decimals_of(t.stock_code)
    lv = holding.compute_levels(res.daily, res.ml, price, t.buy_price, _params(), decimals=dec)
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
    return res.to_holding_card(
        banner_title="📅 收盘日报 · 持仓跟踪",
        price=price,
        cost=t.buy_price,
        qty=t.buy_quantity,
        stop=stop,
        stop_source=source,
        target=lv.target_price,
        notes=notes,
        sell_signal=sell,
    )


# ---------------- B 盘中跟踪 ----------------
async def intraday_b(notifier, *, current: datetime | None = None, force: bool = False) -> dict:
    current = _norm(current)
    stats = {"stocks": 0, "pushed": 0, "deduped": 0, "skipped": None}
    b_tasks = task_repo.list_active(task_type="B")
    if not force:
        eligible = [t for t in b_tasks if _in_intraday_b_window(current, market_of(t.stock_code))]
        if not eligible and (all(not _in_intraday_b_window(current, m) for m in ("CN", "HK")) or b_tasks):
            stats["skipped"] = "非交易时段"
            return stats
    else:
        eligible = b_tasks

    for code, group in _group_by_code(eligible).items():
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
        rpt_time = daily_report_time_str(code)
        for t in group:
            if t.last_signal_key == key:
                stats["deduped"] += 1
                continue
            bsp = res.daily.get("buy_sell_point")
            header = (f"**⏱ 盘中买点信号（{current.strftime('%H:%M')}）**\n"
                      f"- 日线：**{bsp}**｜30分钟共振：{'✅ 已确认' if confirmed else '⏳ 尚未确认，继续观察'}\n"
                      f"- ⚠️ 盘中日线尚未收盘，信号可能变化，以 {rpt_time} 日线确认为准")
            card = res.to_card(header, "red" if confirmed else "orange", show_cache_note=False)["card"]
            if _push(notifier, t.chat_id, card["title"], card["markdown"],
                     card["template"], raw_card=card.get("raw_card")):
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
    s_tasks = [t for t in task_repo.list_active(task_type="S") if t.stop_loss]
    if not force:
        eligible = [t for t in s_tasks if cal.is_market_open(current, market=market_of(t.stock_code))]
        if not eligible and (all(not cal.is_market_open(current, market=m) for m in ("CN", "HK")) or s_tasks):
            stats["skipped"] = "非交易时段"
            return stats
    else:
        eligible = s_tasks

    repeat_sec = float(get_config().scheduler.get("stop_alert_repeat_min", 30)) * 60
    price_cache: dict[str, float | None] = dict(prices or {})

    for t in eligible:
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

        card = build_stop_alert_card(t, price, first_today, get_config().risk_disclaimer)["card"]
        if _push(notifier, t.chat_id, card["title"], card["markdown"],
                 card["template"], raw_card=card.get("raw_card")):
            task_repo.update_fields(t.id, stop_alert_state="alerted", stop_alert_at=current.isoformat(timespec="seconds"))
            stats["alerted" if first_today else "reminded"] += 1
    if stats["alerted"] or stats["reminded"] or stats["recovered"]:
        logger.info("止损监控: %s", stats)
    return stats


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
    sched.add_job(_runner("daily_analysis", lambda: asyncio.run(daily_analysis(notifier, market="CN"))),
                  CronTrigger(day_of_week="mon-fri", hour=da["hour"], minute=da["minute"], timezone=TZ),
                  id="daily_analysis", name="15:05 A股日报")

    da_hk = cfg.get("daily_analysis_hk", {"hour": 16, "minute": 10})
    sched.add_job(_runner("daily_analysis_hk", lambda: asyncio.run(daily_analysis(notifier, market="HK"))),
                  CronTrigger(day_of_week="mon-fri", hour=da_hk["hour"], minute=da_hk["minute"], timezone=TZ),
                  id="daily_analysis_hk", name="16:10 港股日报")

    for hhmm in cfg.get("intraday_b_times", []):
        h, m = hhmm.split(":")
        sched.add_job(_runner(f"intraday_b@{hhmm}", lambda: asyncio.run(intraday_b(notifier))),
                      CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m), timezone=TZ),
                      id=f"intraday_b_{hhmm}", name=f"B盘中跟踪 {hhmm}")

    every = int(cfg.get("stop_loss_interval_min", 3))
    sched.add_job(_runner("stop_loss", lambda: stop_loss_check(notifier)),
                  CronTrigger(day_of_week="mon-fri", hour="9-12,13-16", minute=f"*/{every}", timezone=TZ),
                  id="stop_loss", name="S止损监控", misfire_grace_time=120)
    return sched
