"""会话多轮记忆管理(按 chat_id 保存最近 N 轮对话, 默认 20 轮 = 40 条消息)。

同时支持在用户执行 Q / B / S 分析某只股票后, 将该股的核心缠论结构摘要写入该会话的记忆中,
使用户紧接着追问"那它跌破支撑怎么办"/"为什么还没出现背驰"时无需重复输入股票代码。
"""
from __future__ import annotations

from app.config import get_config
from app.models.database import get_conn, now_str


def max_messages() -> int:
    agent_cfg = get_config().raw.get("agent") or {}
    rounds = int(agent_cfg.get("max_history_rounds", 20))
    return max(2, rounds * 2)


def add_message(chat_id: str, role: str, content: str) -> None:
    """追加一条消息(role: 'user' | 'assistant'), 并裁剪超出 20 轮的旧记录。"""
    if not chat_id or not content:
        return
    limit = max_messages()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_history(chat_id, role, content, created_at) VALUES (?,?,?,?)",
            (chat_id, role, content[:4000], now_str()),
        )
        conn.execute(
            "DELETE FROM chat_history WHERE chat_id=? AND id NOT IN ("
            "  SELECT id FROM chat_history WHERE chat_id=? ORDER BY id DESC LIMIT ?"
            ")",
            (chat_id, chat_id, limit),
        )


def record_command_analysis(chat_id: str, user_cmd: str, res) -> None:
    """当用户在会话中运行 Q/B/S 分析股票后, 自动将分析摘要记入会话历史供后续追问。"""
    if not chat_id or res is None:
        return
    r = res.report or {}
    b = r.get("basic") or {}
    c = r.get("chan_signal") or res.daily or {}
    o = r.get("operation") or {}
    a = r.get("auxiliary") or res.ml or {}
    name = b.get("name") or getattr(res, "name", None)
    code = b.get("code") or getattr(res, "code", None)
    price = b.get("price") if b.get("price") is not None else getattr(res, "price", None)
    direction = o.get("direction") or getattr(res, "direction", None)
    summary = (
        f"[系统记录：刚完成 {name}({code}) 多级别缠论分析] "
        f"现价 {price} {b.get('currency', 'CNY')} ({b.get('change_pct')}%)；"
        f"日线线段：{c.get('segment_direction')}，笔：{c.get('bi_direction')}({c.get('bi_bars')}根K线)，"
        f"中枢：{c.get('zhongshu') or c.get('zhongshu_range')}，买卖点：{c.get('buy_sell_point')}，"
        f"背驰：{c.get('macd_divergence')}；多级别结论：{c.get('multi_level_conclusion')}；"
        f"操作方向：{direction}，入场：{o.get('entry_zone')}，止损：{o.get('stop_loss')}，"
        f"目标：{o.get('target_price')}，支撑：{a.get('support')}，压力：{a.get('resistance')}。"
    )
    add_message(chat_id, "user", user_cmd)
    add_message(chat_id, "assistant", summary)


def get_history(chat_id: str, limit: int | None = None) -> list[dict]:
    """按时间正序返回该 chat_id 的最近消息列表 [{'role': ..., 'content': ...}, ...]。"""
    if not chat_id:
        return []
    lim = limit or max_messages()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM ("
            "  SELECT id, role, content FROM chat_history WHERE chat_id=? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (chat_id, lim),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def clear_history(chat_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_history WHERE chat_id=?", (chat_id,))
