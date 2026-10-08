"""LLM 客户端工厂。

根据 provider 名称, 从 config.yaml 读取 base_url / env_key, 从环境变量读取 API Key,
返回对应的 BaseLLMClient 实例。

当前启用: deepseek, mimo (均走 OpenAI 兼容协议)。
其余 provider 配置保留, enabled=false, 需要时填 key 并开启。
"""
from __future__ import annotations

from app.config import get_config
from app.llm.base import BaseLLMClient


class LLMConfigError(Exception):
    pass


def get_llm_client(provider: str, model: str) -> BaseLLMClient:
    cfg = get_config()
    pconf = cfg.provider_conf(provider)
    if not pconf:
        raise LLMConfigError(f"未知的 provider: {provider}")
    if not pconf.get("enabled", False):
        raise LLMConfigError(f"provider '{provider}' 未启用 (config.yaml enabled=false)")

    api_key = cfg.api_key(provider)
    if not api_key:
        env_key = pconf.get("env_key")
        raise LLMConfigError(f"provider '{provider}' 缺少 API Key (环境变量 {env_key} 未设置)")

    timeout = float(cfg.llm.get("timeout_seconds", 15))

    if provider == "anthropic":
        raise LLMConfigError("anthropic 适配器暂未启用")

    from app.llm.providers.openai_compatible import OpenAICompatibleClient

    return OpenAICompatibleClient(
        api_key=api_key,
        model=model,
        base_url=pconf.get("base_url"),
        timeout=timeout,
    )


def get_default_client() -> BaseLLMClient:
    """返回当前生效模型的客户端: 每次调用都读 SQLite 热配置(M 指令切换), 无记录回落 config.yaml。"""
    from app.llm.config_store import get_llm_config

    provider, model, _ = get_llm_config()
    return get_llm_client(provider, model)
