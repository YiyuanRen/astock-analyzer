"""全局配置加载: 合并 config/config.yaml 与 .env 环境变量。

用法:
    from app.config import get_config
    cfg = get_config()
    cfg.llm["default_provider"]
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass
class AppConfig:
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def llm(self) -> dict[str, Any]:
        return self.raw.get("llm", {})

    @property
    def data(self) -> dict[str, Any]:
        return self.raw.get("data", {})

    @property
    def chan(self) -> dict[str, Any]:
        return self.raw.get("chan", {})

    @property
    def scheduler(self) -> dict[str, Any]:
        return self.raw.get("scheduler", {})

    @property
    def storage(self) -> dict[str, Any]:
        return self.raw.get("storage", {})

    @property
    def risk_disclaimer(self) -> str:
        return self.raw.get("risk_disclaimer", "⚠️ 仅供参考，不构成投资建议")

    def provider_conf(self, provider: str) -> dict[str, Any]:
        return self.llm.get("providers", {}).get(provider, {})

    def api_key(self, provider: str) -> str | None:
        """按 provider 的 env_key 读取对应 API Key。"""
        env_key = self.provider_conf(provider).get("env_key")
        return os.getenv(env_key) if env_key else None


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    load_dotenv(ENV_PATH, override=False)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig(raw=raw)
