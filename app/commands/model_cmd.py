"""M 指令: 查看 / 热切换当前 LLM 模型。

M                      查看当前模型与可用 provider
M <provider> [model]   切换(需管理员白名单; 切换前做 1 次探活, 失败则保持原模型)
API Key 不通过飞书传入, 仍从 .env 读取。
"""
from __future__ import annotations

import asyncio

from app.commands.permissions import can_switch_model
from app.config import get_config
from app.llm import factory
from app.llm.config_store import get_llm_config, set_llm_config
from app.logging_utils import get_logger

logger = get_logger("commands.model")

PROBE_TIMEOUT = 10.0


async def probe(provider: str, model: str) -> None:
    """用 1 次极小调用验证 provider/model/key 可用; 失败抛异常。"""
    client = factory.get_llm_client(provider, model)
    await asyncio.wait_for(
        client.chat([{"role": "user", "content": "ping"}], max_tokens=8), timeout=PROBE_TIMEOUT)


def _available_providers() -> list[str]:
    cfg = get_config()
    out = []
    for name, p in cfg.llm.get("providers", {}).items():
        if p.get("enabled") and cfg.api_key(name):
            out.append(f"{name}({', '.join(p.get('models', [])[:3])})" if p.get("models") else name)
    return out


def show_current() -> str:
    provider, model, source = get_llm_config()
    src = "已手动切换" if source == "db" else "默认配置"
    return (f"当前模型：{provider} / {model}（{src}）\n"
            f"可用：{'；'.join(_available_providers()) or '无'}\n"
            f"切换：M <provider> [model]，例如 M deepseek deepseek-chat")


async def handle_m(ctx: dict, provider: str | None, model: str | None) -> str:
    if not provider:
        return show_current()

    sender = ctx.get("sender_open_id")
    if not can_switch_model(sender):
        return f"无权限：仅管理员可切换模型。你的 open_id：{sender}（可让管理员加入 ADMIN_OPEN_IDS）。\n\n{show_current()}"

    provider = provider.lower()
    pconf = get_config().provider_conf(provider)
    if not pconf:
        return f"未知 provider：{provider}。\n{show_current()}"
    if not model:
        models = pconf.get("models") or []
        if not models:
            return f"请指定模型名：M {provider} <model>"
        model = models[0]

    try:
        await probe(provider, model)
    except factory.LLMConfigError as e:
        return f"切换失败：{e}"
    except Exception as e:  # noqa: BLE001
        logger.warning("模型探活失败 %s/%s: %s: %s", provider, model, type(e).__name__, e)
        return f"切换失败：{provider}/{model} 探活未通过（{type(e).__name__}），已保持原模型。"

    set_llm_config(provider, model)
    logger.info("LLM 已切换 -> %s/%s by %s", provider, model, sender)
    return f"✅ 已切换模型：{provider} / {model}，立即生效。"
