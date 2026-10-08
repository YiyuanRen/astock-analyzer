"""查看数据库中的任务(调试用): .venv\\Scripts\\python.exe scripts\\show_tasks.py [--all]"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.database import get_conn  # noqa: E402

where = "" if "--all" in sys.argv else "WHERE status='active'"
with get_conn() as conn:
    rows = conn.execute(
        "SELECT id, chat_type, chat_id, stock_code, task_type, status, buy_price, buy_quantity, "
        f"stop_loss, target_price, stop_alert_state, last_signal_key FROM tasks {where} ORDER BY id").fetchall()
for r in rows:
    print(dict(r))
print(f"共 {len(rows)} 条")
