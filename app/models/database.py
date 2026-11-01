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

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_history_chat ON chat_history(chat_id, id);

CREATE TABLE IF NOT EXISTS morning_briefings (
    report_date TEXT PRIMARY KEY,
    article_id TEXT,
    source_title TEXT,
    source_uri TEXT,
    published_at TEXT,
    structured_json TEXT NOT NULL,
    html_content TEXT NOT NULL,
    pushed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefing_subscriptions (
    chat_id TEXT PRIMARY KEY,
    chat_type TEXT NOT NULL DEFAULT 'p2p',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO briefing_subscriptions(chat_id, chat_type, enabled, updated_at)
SELECT DISTINCT chat_id, chat_type, 1, MAX(created_at) FROM tasks
WHERE chat_id != 'cli_chat' GROUP BY chat_id;

INSERT OR IGNORE INTO briefing_subscriptions(chat_id, chat_type, enabled, updated_at)
SELECT DISTINCT chat_id, 'p2p', 1, MAX(created_at) FROM chat_history
WHERE chat_id != 'cli_chat' AND (content LIKE 'Q %' OR content LIKE 'B %' OR content LIKE 'S %')
GROUP BY chat_id;

CREATE TABLE IF NOT EXISTS accounts (
    chat_id TEXT PRIMARY KEY,
    initial_capital REAL NOT NULL DEFAULT 100000.0,
    cash_balance REAL NOT NULL DEFAULT 100000.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT NOT NULL DEFAULT 'CN',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    avg_cost REAL NOT NULL,
    current_qty INTEGER NOT NULL,
    total_buy_qty INTEGER NOT NULL,
    realized_pnl_rmb REAL NOT NULL DEFAULT 0.0,
    entry_bsp TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_chat_status ON positions(chat_id, status, stock_code);

CREATE TABLE IF NOT EXISTS trade_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    market TEXT NOT NULL DEFAULT 'CN',
    action TEXT NOT NULL CHECK (action IN ('BUY', 'ADD', 'SELL_PART', 'SELL_ALL')),
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    amount_rmb REAL NOT NULL,
    realized_pnl_rmb REAL,
    pnl_pct REAL,
    chan_bsp TEXT,
    trade_date TEXT NOT NULL,
    traded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_logs_chat ON trade_logs(chat_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_pos_date ON trade_logs(position_id, trade_date);

CREATE TABLE IF NOT EXISTS backtest_reports (
    report_id TEXT PRIMARY KEY,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    years INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    html_content TEXT NOT NULL,
    created_at TEXT NOT NULL
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
        _backfill_legacy_s_tasks(conn)


def _backfill_legacy_s_tasks(conn: sqlite3.Connection) -> None:
    """将 tasks 表中历史已存在但尚未进入 positions 账本的 S 任务自动迁移为初始持仓与 BUY 流水。"""
    rows = conn.execute(
        "SELECT * FROM tasks WHERE task_type='S' AND buy_price IS NOT NULL AND buy_price > 0"
        " ORDER BY id ASC"
    ).fetchall()
    for r in rows:
        chat_id = r["chat_id"]
        code = r["stock_code"]
        exists = conn.execute(
            "SELECT 1 FROM positions WHERE chat_id=? AND stock_code=?", (chat_id, code)
        ).fetchone()
        if exists:
            continue
        mkt = "HK" if len(code) == 5 else "CN"
        fx = 0.92 if mkt == "HK" else 1.0
        price = float(r["buy_price"])
        qty = int(r["buy_quantity"] or 100)
        amt_rmb = round(price * qty * fx, 2)
        created_at = r["created_at"] or now_str()
        trade_date = created_at[:10]
        status = "open" if r["status"] == "active" else "closed"

        conn.execute(
            "INSERT OR IGNORE INTO accounts(chat_id, initial_capital, cash_balance, created_at, updated_at)"
            " VALUES (?, 100000.0, 100000.0, ?, ?)",
            (chat_id, created_at, created_at),
        )
        if status == "open":
            conn.execute(
                "UPDATE accounts SET cash_balance = MAX(0.0, ROUND(cash_balance - ?, 2)), updated_at=?"
                " WHERE chat_id=?",
                (amt_rmb, created_at, chat_id),
            )
        cur = conn.execute(
            "INSERT INTO positions(chat_id, stock_code, stock_name, market, status, avg_cost,"
            " current_qty, total_buy_qty, realized_pnl_rmb, entry_bsp, opened_at, closed_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,0.0,'历史持仓迁移',?,?,?)",
            (
                chat_id, code, r["stock_name"], mkt, status, price,
                qty if status == "open" else 0, qty,
                created_at, (r["updated_at"] if status == "closed" else None), created_at,
            ),
        )
        conn.execute(
            "INSERT INTO trade_logs(position_id, chat_id, stock_code, stock_name, market, action,"
            " price, quantity, amount_rmb, realized_pnl_rmb, pnl_pct, chan_bsp, trade_date, traded_at)"
            " VALUES (?,?,?,?,?,'BUY',?,?,?,NULL,NULL,'历史持仓迁移',?,?)",
            (cur.lastrowid, chat_id, code, r["stock_name"], mkt, price, qty, amt_rmb, trade_date, created_at),
        )

