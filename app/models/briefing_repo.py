"""市场早报(morning_briefings)与早报订阅(briefing_subscriptions)数据仓库。

双重持久化策略:
- 早报 HTML 与结构化数据同时写入 SQLite `morning_briefings` 表和 `data/briefings/YYYY-MM-DD.html` 磁盘文件。
- 即使容器重建或磁盘静态文件缺失, 也能从 SQLite 无损恢复全部历史页面。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.models.database import db_path, get_conn, now_str


@dataclass
class BriefingRecord:
    report_date: str
    article_id: str | None
    source_title: str | None
    source_uri: str | None
    published_at: str | None
    structured: dict
    html_content: str
    pushed_at: str | None
    created_at: str


def briefings_dir() -> Path:
    """静态 HTML 文件目录(与 SQLite 同在持久化 data/ 卷下)。"""
    d = db_path().parent / "briefings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def html_file_path(report_date: str) -> Path:
    return briefings_dir() / f"{report_date}.html"


def save_briefing(
    report_date: str,
    article_id: str | None,
    source_title: str | None,
    source_uri: str | None,
    published_at: str | None,
    structured: dict,
    html_content: str,
) -> BriefingRecord:
    ts = now_str()
    s_json = json.dumps(structured, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO morning_briefings("
            "  report_date, article_id, source_title, source_uri, published_at,"
            "  structured_json, html_content, created_at"
            ") VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(report_date) DO UPDATE SET"
            "  article_id=excluded.article_id, source_title=excluded.source_title,"
            "  source_uri=excluded.source_uri, published_at=excluded.published_at,"
            "  structured_json=excluded.structured_json, html_content=excluded.html_content",
            (report_date, str(article_id or ""), source_title, source_uri, published_at, s_json, html_content, ts),
        )
    # 同步写入磁盘静态文件
    try:
        html_file_path(report_date).write_text(html_content, encoding="utf-8")
    except Exception:
        pass
    return get_briefing(report_date)


def mark_pushed(report_date: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE morning_briefings SET pushed_at=? WHERE report_date=?",
            (now_str(), report_date),
        )


def get_briefing(report_date: str) -> BriefingRecord | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM morning_briefings WHERE report_date=?", (report_date,)
        ).fetchone()
    if not row:
        return None
    return BriefingRecord(
        report_date=row["report_date"],
        article_id=row["article_id"],
        source_title=row["source_title"],
        source_uri=row["source_uri"],
        published_at=row["published_at"],
        structured=json.loads(row["structured_json"] or "{}"),
        html_content=row["html_content"],
        pushed_at=row["pushed_at"],
        created_at=row["created_at"],
    )


def get_latest_briefing() -> BriefingRecord | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT report_date FROM morning_briefings ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
    return get_briefing(row["report_date"]) if row else None


def list_briefings(limit: int = 60) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT report_date, source_title, published_at, structured_json, pushed_at, created_at"
            " FROM morning_briefings ORDER BY report_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        st = json.loads(r["structured_json"] or "{}")
        out.append({
            "report_date": r["report_date"],
            "source_title": r["source_title"],
            "published_at": r["published_at"],
            "headline": st.get("headline_summary") or "",
            "events_count": len(st.get("events_table") or []),
            "chains_count": len(st.get("causal_chains") or []),
        })
    return out


def get_or_restore_html(report_date: str) -> str | None:
    """优先读磁盘 HTML, 若磁盘文件缺失则从 SQLite 自动恢复并写回磁盘。"""
    p = html_file_path(report_date)
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            pass
    rec = get_briefing(report_date)
    if not rec:
        return None
    try:
        p.write_text(rec.html_content, encoding="utf-8")
    except Exception:
        pass
    return rec.html_content


# ---------------- 订阅管理 (briefing_subscriptions) ----------------
def ensure_subscribed(chat_id: str, chat_type: str = "p2p") -> None:
    """首次使用 Q/B/S 的会话自动加入早报订阅(enabled=1); 若用户曾主动关闭(enabled=0)则保持不变。"""
    if not chat_id or chat_id == "cli_chat":
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO briefing_subscriptions(chat_id, chat_type, enabled, updated_at)"
            " VALUES (?,?,1,?)",
            (chat_id, chat_type or "p2p", now_str()),
        )


def set_subscription(chat_id: str, enabled: bool, chat_type: str = "p2p") -> None:
    """用户主动开启或关闭早报推送。"""
    if not chat_id:
        return
    val = 1 if enabled else 0
    ts = now_str()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO briefing_subscriptions(chat_id, chat_type, enabled, updated_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
            (chat_id, chat_type or "p2p", val, ts),
        )


def is_subscribed(chat_id: str) -> bool:
    if not chat_id:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT enabled FROM briefing_subscriptions WHERE chat_id=?", (chat_id,)
        ).fetchone()
    return bool(row and row["enabled"] == 1)


def list_enabled_subscribers() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id, chat_type FROM briefing_subscriptions WHERE enabled=1 ORDER BY updated_at"
        ).fetchall()
    return [{"chat_id": r["chat_id"], "chat_type": r["chat_type"]} for r in rows]
