"""ECS 远程 SQLite 数据库同步(供本机 Navicat 打开)与只读 SQL 查询工具。

用法:
1. 同步线上数据库到本地镜像文件 (Navicat 直接连接 E:\\Project\\astock-analyzer\\data\\ecs_mirror.db):
   .venv\\Scripts\\python.exe scripts\\ecs_db.py sync

2. 在本机或 Agent 中直接执行远程 SQL 查询:
   .venv\\Scripts\\python.exe scripts\\ecs_db.py query "SELECT * FROM positions"
   .venv\\Scripts\\python.exe scripts\\ecs_db.py tables
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
MIRROR_PATH = ROOT / "data" / "ecs_mirror.db"
ECS_HOST_ALIAS = os.getenv("ECS_SSH_ALIAS", "astock-ecs")
REMOTE_DB = "/opt/astock-analyzer/data/astock.db"


def sync_db() -> Path:
    """使用 sqlite3 backup API 在 ECS 上生成一致性快照并拉取到本地 data/ecs_mirror.db。"""
    MIRROR_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_cmd = (
        "docker exec astock-analyzer python -c \""
        "import sqlite3; "
        "src=sqlite3.connect('/app/data/astock.db'); "
        "dst=sqlite3.connect('/app/data/ecs_snapshot.db'); "
        "src.backup(dst); dst.close(); src.close()\""
    )
    subprocess.run(["ssh", "-o", "BatchMode=yes", ECS_HOST_ALIAS, backup_cmd], check=True)
    subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes", f"{ECS_HOST_ALIAS}:/opt/astock-analyzer/data/ecs_snapshot.db", str(MIRROR_PATH)],
        check=True,
    )
    print(f"✅ 已同步线上数据库至本地: {MIRROR_PATH}")
    print("💡 您可以在 Navicat 中新建 SQLite 连接，文件路径选择上述 ecs_mirror.db 即可可视化查看全部表。")
    return MIRROR_PATH


def remote_query(sql: str) -> list[dict]:
    """先同步最新快照到本地镜像, 再在本地镜像上安全执行只读 SQL 查询(零引号转义问题)。"""
    sync_db()
    import sqlite3
    conn = sqlite3.connect(f"file:{MIRROR_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] == "sync":
        sync_db()
        return
    cmd = sys.argv[1].lower()
    if cmd == "tables":
        rows = remote_query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        print("线上数据库表:", [r["name"] for r in rows])
        return
    if cmd == "query" and len(sys.argv) >= 3:
        rows = remote_query(sys.argv[2])
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
