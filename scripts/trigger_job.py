"""手动触发定时任务(联调/验证用, 不必等盘中)。

用法:
  trigger_job.py daily                       # 15:05 日报
  trigger_job.py intraday                    # B 盘中跟踪
  trigger_job.py stoploss --price 600519=1100   # S 止损监控, 注入价格
选项:
  --force            忽略交易日/交易时段限制
  --price CODE=PX    注入价格(可多个), 未注入的走实时行情
  --send             真实推送到飞书(各任务所属会话); 默认只在控制台打印(dry-run)
"""
import argparse
import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402

from app.models.database import init_db  # noqa: E402
from app.scheduler import jobs  # noqa: E402
from app.services.notifier import ConsoleNotifier  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["daily", "intraday", "stoploss"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--price", action="append", default=[], metavar="CODE=PX")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    load_dotenv()
    init_db()
    if a.send:
        from app.bot.feishu_bot import FeishuBot
        notifier = FeishuBot(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"])
        print("== 真实推送模式 ==")
    else:
        notifier = ConsoleNotifier()
        print("== dry-run: 仅打印, 不推送 (加 --send 真实推送) ==")

    prices = {k: float(v) for k, v in (p.split("=") for p in a.price)}
    if a.job == "daily":
        result = asyncio.run(jobs.daily_analysis(notifier, force=a.force))
    elif a.job == "intraday":
        result = asyncio.run(jobs.intraday_b(notifier, force=a.force))
    else:
        result = jobs.stop_loss_check(notifier, force=a.force, prices=prices or None)
    print("结果:", result)


if __name__ == "__main__":
    main()
