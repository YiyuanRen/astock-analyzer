"""当前 LLM 模型配置的持久化(热切换)。

每次调用 LLM 前读取; 没有记录时回落到 config.yaml 的默认 provider/model。
"""
from __future__ import annotations

from app.config import get_config
from app.models.database import get_conn, now_str


def get_llm_config() -> tuple[str, str, str]:
    """返回 (provider, model, source); source 为 'db' 或 'default'。"""
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT provider, model FROM llm_config WHERE id=1").fetchone()
        if row:
            return row["provider"], row["model"], "db"
    except Exception:  # noqa: BLE001  表尚未初始化等, 回落默认
        pass
    llm = get_config().llm
    return llm.get("default_provider"), llm.get("default_model"), "default"


def set_llm_config(provider: str, model: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO llm_config(id, provider, model, updated_at) VALUES (1,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, model=excluded.model,"
            " updated_at=excluded.updated_at",
            (provider, model, now_str()))
