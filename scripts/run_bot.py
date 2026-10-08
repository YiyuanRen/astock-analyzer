"""启动飞书机器人 + 定时任务调度器。

运行: .venv\\Scripts\\python.exe scripts\\run_bot.py
- 私聊/群里 @机器人 发 Q/B/S/C/L/M 指令
- 调度器: 15:05 日报 / B 盘中跟踪 / S 止损监控, 推送回任务所属会话
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402

from app.bot.feishu_bot import FeishuBot  # noqa: E402
from app.commands.router import handle  # noqa: E402
from app.logging_utils import get_logger  # noqa: E402
from app.models.database import init_db  # noqa: E402
from app.scheduler.jobs import build_scheduler  # noqa: E402

logger = get_logger("run_bot")


def main():
    load_dotenv()
    init_db()
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        print("错误: .env 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        sys.exit(1)

    bot = FeishuBot(app_id, app_secret, on_text=handle)
    sched = build_scheduler(bot)      # FeishuBot 本身即通知器(按 chat_id 推送)
    sched.start()
    for j in sched.get_jobs():
        logger.info("已注册定时任务: %-14s 下次运行 %s", j.id, j.next_run_time)
    bot.start()                       # 阻塞


if __name__ == "__main__":
    main()
