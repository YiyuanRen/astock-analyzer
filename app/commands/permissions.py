"""权限: M 切换全局 LLM 模型的管理员白名单(.env 的 ADMIN_OPEN_IDS, 逗号分隔)。"""
from __future__ import annotations

import os


def admin_ids() -> set[str]:
    raw = os.getenv("ADMIN_OPEN_IDS", "")
    return {x.strip() for x in raw.replace("，", ",").split(",") if x.strip()}


def can_switch_model(open_id: str | None) -> bool:
    """未配置白名单 → 不限制; 配置后仅白名单成员可切换。"""
    ids = admin_ids()
    return True if not ids else (open_id in ids)
