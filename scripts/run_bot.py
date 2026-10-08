"""启动飞书机器人 (最小联调)。

运行: .venv\\Scripts\\python.exe scripts\\run_bot.py
然后在飞书里单聊机器人发送任意消息, 终端应打印收到内容, 机器人回显。
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402

from app.bot.feishu_bot import FeishuBot  # noqa: E402
from app.commands.router import handle  # noqa: E402
from app.models.database import init_db  # noqa: E402


def main():
    load_dotenv()
    init_db()
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        print("错误: .env 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        sys.exit(1)

    bot = FeishuBot(app_id, app_secret, on_text=handle)
    bot.start()


if __name__ == "__main__":
    main()
