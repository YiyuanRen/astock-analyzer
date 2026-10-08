"""tasks 表的读写(按 chat_id 隔离: 私聊/群聊各自独立)。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.models.database import get_conn, now_str

_UPDATABLE = {
    "stock_name", "buy_price", "buy_quantity", "stop_loss", "target_price",
    "last_signal_key", "last_signal_at", "stop_alert_state", "stop_alert_at",
}


@dataclass
class Task:
    id: int
    chat_id: str
    chat_type: str
    created_by: str | None
    stock_code: str
    stock_name: str | None
    task_type: str
    status: str
    buy_price: float | None
    buy_quantity: int | None
    stop_loss: float | None
    target_price: float | None
    last_signal_key: str | None
    last_signal_at: str | None
    stop_alert_state: str | None
    stop_alert_at: str | None
    created_at: str
    updated_at: str

    @property
    def display(self) -> str:
        return f"{self.stock_name or self.stock_code} {self.stock_code}"


def _to_task(row: sqlite3.Row | None) -> Task | None:
    return Task(**dict(row)) if row else None


def create_task(chat_id: str, chat_type: str, created_by: str | None, stock_code: str,
                stock_name: str | None, task_type: str, buy_price: float | None = None,
                buy_quantity: int | None = None, stop_loss: float | None = None,
                target_price: float | None = None) -> Task:
    ts = now_str()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(chat_id, chat_type, created_by, stock_code, stock_name, task_type,"
            " buy_price, buy_quantity, stop_loss, target_price, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, chat_type, created_by, stock_code, stock_name, task_type,
             buy_price, buy_quantity, stop_loss, target_price, ts, ts))
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
    return _to_task(row)


def get_active(chat_id: str, stock_code: str) -> Task | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE chat_id=? AND stock_code=? AND status='active'",
            (chat_id, stock_code)).fetchone()
    return _to_task(row)


def get_by_id(task_id: int) -> Task | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _to_task(row)


def list_active(chat_id: str | None = None, task_type: str | None = None) -> list[Task]:
    sql, args = "SELECT * FROM tasks WHERE status='active'", []
    if chat_id:
        sql += " AND chat_id=?"
        args.append(chat_id)
    if task_type:
        sql += " AND task_type=?"
        args.append(task_type)
    sql += " ORDER BY created_at, id"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_to_task(r) for r in rows]


def cancel(task_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET status='cancelled', updated_at=? WHERE id=?",
                     (now_str(), task_id))


def cancel_active(chat_id: str, stock_code: str) -> Task | None:
    """取消该会话该股票的活跃任务, 返回被取消的任务(无则 None)。"""
    t = get_active(chat_id, stock_code)
    if t:
        cancel(t.id)
    return t


def update_fields(task_id: int, **fields) -> None:
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"不允许更新的字段: {bad}")
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {sets}, updated_at=? WHERE id=?",
                     (*fields.values(), now_str(), task_id))
