"""analysis_cache 表: 按股票缓存最近一次分析结果(JSON)。"""
from __future__ import annotations

import json

from app.models.database import get_conn, now_str


def get(stock_code: str) -> dict | None:
    """返回 {"payload": dict, "analyzed_at": str, "session_key": str|None}。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result_json, analyzed_at, session_key FROM analysis_cache WHERE stock_code=?",
            (stock_code,)).fetchone()
    if not row:
        return None
    return {"payload": json.loads(row["result_json"]), "analyzed_at": row["analyzed_at"],
            "session_key": row["session_key"]}


def put(stock_code: str, payload: dict, session_key: str | None,
        analyzed_at: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO analysis_cache(stock_code, result_json, analyzed_at, session_key)"
            " VALUES (?,?,?,?) ON CONFLICT(stock_code) DO UPDATE SET"
            " result_json=excluded.result_json, analyzed_at=excluded.analyzed_at,"
            " session_key=excluded.session_key",
            (stock_code, json.dumps(payload, ensure_ascii=False, default=str),
             analyzed_at or now_str(), session_key))
