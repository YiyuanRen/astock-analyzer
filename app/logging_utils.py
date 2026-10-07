"""统一日志配置。

- 输出到 stderr, 行缓冲, 避免后台进程 stdout 块缓冲导致日志不实时
- 统一格式, 中文友好
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    # stderr 尽量用 utf-8, 避免 Windows GBK 控制台报错
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
