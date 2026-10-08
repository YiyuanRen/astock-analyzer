"""SQLite 连接与建表。

- 每次操作新建连接(多线程安全: 飞书工作线程 + 调度器线程), WAL + busy_timeout
- 时间统一用 Asia/Shanghai 的 ISO 字符串
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT, get_config

TZ = ZoneInfo("Asia/Shanghai")
_db_path_override: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    chat_type TEXT NOT NULL DEFAULT 'p2p',
    created_by TEXT,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    task_type TEXT NOT NULL CHECK (task_type IN ('B','S')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
    buy_price REAL,
    buy_quantity INTEGER,
    stop_loss REAL,
    target_price REAL,
    last_signal_key TEXT,
    last_signal_at TEXT,
    stop_alert_state TEXT,
    stop_alert_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_active
    ON tasks(chat_id, stock_code) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    stock_code TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    session_key TEXT
);
"""


def now() -> datetime:
    return datetime.now(TZ)


def now_str() -> str:
    return now().isoformat(timespec="seconds")


def set_db_path(path: str | Path | None) -> None:
    """测试用: 覆盖数据库路径; 传 None 恢复默认。"""
    global _db_path_override
    _db_path_override = Path(path) if path else None


def db_path() -> Path:
    if _db_path_override:
        return _db_path_override
    rel = get_config().storage.get("db_path", "data/astock.db")
    p = Path(rel)
    return p if p.is_absolute() else PROJECT_ROOT / p


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
