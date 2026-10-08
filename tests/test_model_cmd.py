import asyncio

import pytest

from app.commands import model_cmd
from app.commands.parser import parse
from app.commands.permissions import can_switch_model
from app.llm.config_store import get_llm_config


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ok_probe(monkeypatch):
    calls = []

    async def fake(provider, model):
        calls.append((provider, model))

    monkeypatch.setattr(model_cmd, "probe", fake)
    return calls


def test_parser_m_variants():
    assert (parse("M qwen").provider, parse("M qwen").model) == ("qwen", None)
    c = parse("M mimo mimo-v2.6-flash")
    assert (c.provider, c.model) == ("mimo", "mimo-v2.6-flash")


def test_permissions(monkeypatch):
    monkeypatch.delenv("ADMIN_OPEN_IDS", raising=False)
    assert can_switch_model("anyone")  # 未配置 → 不限制
    monkeypatch.setenv("ADMIN_OPEN_IDS", "ou_a, ou_b")
    assert can_switch_model("ou_a") and not can_switch_model("ou_x") and not can_switch_model(None)


def test_show_current(tmp_db):
    text = run(model_cmd.handle_m({}, None, None))
    assert "当前模型" in text and "切换：M" in text


def test_switch_success_persists(tmp_db, ok_probe, monkeypatch):
    monkeypatch.delenv("ADMIN_OPEN_IDS", raising=False)
    out = run(model_cmd.handle_m({"sender_open_id": "ou_a"}, "deepseek", "deepseek-chat"))
    assert "已切换" in out
    assert get_llm_config()[:3] == ("deepseek", "deepseek-chat", "db")
    assert ok_probe == [("deepseek", "deepseek-chat")]


def test_switch_defaults_model_to_first_of_provider(tmp_db, ok_probe, monkeypatch):
    monkeypatch.delenv("ADMIN_OPEN_IDS", raising=False)
    run(model_cmd.handle_m({}, "mimo", None))
    assert get_llm_config()[:2] == ("mimo", "mimo-v2.6-pro")


def test_non_admin_rejected_and_shown_open_id(tmp_db, ok_probe, monkeypatch):
    monkeypatch.setenv("ADMIN_OPEN_IDS", "ou_admin")
    out = run(model_cmd.handle_m({"sender_open_id": "ou_guest"}, "mimo", "mimo-v2.6-flash"))
    assert "无权限" in out and "ou_guest" in out
    assert get_llm_config()[2] == "default" and ok_probe == []


def test_admin_allowed_when_whitelisted(tmp_db, ok_probe, monkeypatch):
    monkeypatch.setenv("ADMIN_OPEN_IDS", "ou_admin")
    out = run(model_cmd.handle_m({"sender_open_id": "ou_admin"}, "mimo", "mimo-v2.6-flash"))
    assert "已切换" in out


def test_unknown_or_disabled_provider_rejected(tmp_db, monkeypatch):
    monkeypatch.delenv("ADMIN_OPEN_IDS", raising=False)
    assert "未知 provider" in run(model_cmd.handle_m({}, "nope", "x"))
    # openai 在 config 里存在但 enabled=false: 探活会因 LLMConfigError 被拒, 且不落库
    out = run(model_cmd.handle_m({}, "openai", "gpt-4o-mini"))
    assert "切换失败" in out and get_llm_config()[2] == "default"


def test_probe_failure_keeps_old_model(tmp_db, monkeypatch):
    monkeypatch.delenv("ADMIN_OPEN_IDS", raising=False)

    async def boom(provider, model):
        raise TimeoutError()

    monkeypatch.setattr(model_cmd, "probe", boom)
    out = run(model_cmd.handle_m({}, "deepseek", "deepseek-chat"))
    assert "探活未通过" in out and get_llm_config()[2] == "default"
