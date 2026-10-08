import sqlite3

import pytest

from app.llm import config_store
from app.models import cache_repo, task_repo


def mk(chat="c1", code="000001", typ="B", **kw):
    return task_repo.create_task(chat, "p2p", "ou_a", code, "平安银行", typ, **kw)


def test_create_and_get(tmp_db):
    t = mk(buy_price=10.45, buy_quantity=100, typ="S")
    got = task_repo.get_active("c1", "000001")
    assert got.id == t.id and got.task_type == "S"
    assert (got.buy_price, got.buy_quantity) == (10.45, 100)
    assert got.status == "active"


def test_unique_active_per_chat_and_code(tmp_db):
    mk()
    with pytest.raises(sqlite3.IntegrityError):
        mk()


def test_same_stock_in_different_chats_is_independent(tmp_db):
    mk(chat="private")
    mk(chat="group1")  # 不冲突
    assert len(task_repo.list_active()) == 2
    assert len(task_repo.list_active("group1")) == 1
    task_repo.cancel_active("group1", "000001")
    assert task_repo.get_active("private", "000001") is not None
    assert task_repo.get_active("group1", "000001") is None


def test_cancel_keeps_history_and_allows_recreate(tmp_db):
    t = mk()
    task_repo.cancel(t.id)
    assert task_repo.get_by_id(t.id).status == "cancelled"
    t2 = mk(typ="S")  # 取消后可重建
    assert t2.id != t.id


def test_list_active_filters(tmp_db):
    mk(code="000001", typ="B")
    mk(code="600519", typ="S")
    assert [t.stock_code for t in task_repo.list_active(task_type="S")] == ["600519"]
    assert len(task_repo.list_active("c1")) == 2


def test_update_fields_whitelist(tmp_db):
    t = mk()
    task_repo.update_fields(t.id, stop_loss=9.5, last_signal_key="二类买点|3")
    got = task_repo.get_by_id(t.id)
    assert (got.stop_loss, got.last_signal_key) == (9.5, "二类买点|3")
    with pytest.raises(ValueError):
        task_repo.update_fields(t.id, status="cancelled")


def test_cache_roundtrip_and_overwrite(tmp_db):
    assert cache_repo.get("000001") is None
    cache_repo.put("000001", {"a": 1, "名称": "平安"}, "s1")
    got = cache_repo.get("000001")
    assert got["payload"] == {"a": 1, "名称": "平安"} and got["session_key"] == "s1"
    cache_repo.put("000001", {"a": 2}, "s2")
    assert cache_repo.get("000001")["payload"] == {"a": 2}


def test_llm_config_falls_back_to_default_then_persists(tmp_db):
    provider, model, source = config_store.get_llm_config()
    assert source == "default" and provider and model
    config_store.set_llm_config("mimo", "mimo-v2.6-flash")
    assert config_store.get_llm_config() == ("mimo", "mimo-v2.6-flash", "db")
    config_store.set_llm_config("deepseek", "deepseek-chat")  # 覆盖(单例行)
    assert config_store.get_llm_config() == ("deepseek", "deepseek-chat", "db")
